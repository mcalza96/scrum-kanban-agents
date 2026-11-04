import contextlib
import datetime
import io
import json
import os
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import inbox  # noqa: E402

T0 = "2026-10-30 00:40 -0300"


def run(argv, stdin=""):
    out, err, old = io.StringIO(), io.StringIO(), sys.stdin
    sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = inbox.main(argv)
    finally:
        sys.stdin = old
    return code, out.getvalue()


class InboxTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "inbox")

    def tearDown(self):
        self.tmp.cleanup()

    def test_send_creates_the_mailbox_and_numbers_messages(self):
        self.assertEqual(inbox.send(self.root, "PM-ui", "PM-ui-2", "SIM-26 closed", T0), "M-1")
        self.assertEqual(inbox.send(self.root, "PM-ui", "PO", "second", T0), "M-2")
        ms = inbox.messages(self.root, "PM-ui")
        self.assertEqual([(m.id, m.sender, m.text, m.read) for m in ms],
                         [("M-1", "PM-ui-2", "SIM-26 closed", "unread"),
                          ("M-2", "PO", "second", "unread")])

    # Candado 2026-11-04: archivar saca filas del buzón vivo; el id siguiente no puede repetir
    # uno archivado (M-192 quedó dos veces en PO). PM-ui-2 no cuenta para PM-ui.
    def test_id_no_repite_uno_archivado(self):
        arch = os.path.join(self.root, "archivo")
        os.makedirs(arch)
        for name, mid in (("PM-ui-2026-10.md", 7), ("PM-ui-rescate-2026-11-04.md", 9), ("PM-ui-2-2026-10.md", 50)):
            with open(os.path.join(arch, name), "w", encoding="utf-8") as f:
                f.write(inbox.HEADER.format(pm="x") + inbox.Message(f"M-{mid}", "PO", T0, "x", T0).row() + "\n")
        self.assertEqual(inbox.send(self.root, "PM-ui", "PO", "nuevo", T0), "M-10")
        self.assertEqual(inbox.send(self.root, "PM-ui-2", "PO", "nuevo", T0), "M-51")

    def test_pipes_and_newlines_survive(self):
        inbox.send(self.root, "PM-ui", "PO", "a | b\nc", T0)
        self.assertEqual(inbox.messages(self.root, "PM-ui")[0].text, "a \\| b c")
        self.assertIn("a | b c", inbox.read(self.root, "PM-ui"))

    def test_ack_marks_read_and_reports_unknown_ids(self):
        inbox.send(self.root, "PM-ui", "PO", "one", T0)
        inbox.send(self.root, "PM-ui", "PO", "two", T0)
        self.assertEqual(inbox.ack(self.root, "PM-ui", ["M-1", "M-9"], "2026-10-30 01:00 -0300"),
                         ["M-9"])
        ms = inbox.messages(self.root, "PM-ui")
        self.assertEqual([m.read for m in ms], ["2026-10-30 01:00 -0300", "unread"])
        self.assertEqual(inbox.ack(self.root, "PM-ui", ["M-1"]), ["M-1"])  # already read

    def test_pending_names_only_mailboxes_with_unread(self):
        self.assertEqual(inbox.pending(self.root), "")
        inbox.send(self.root, "PM-ui", "PO", "one", T0)
        inbox.send(self.root, "PM-ui-2", "PO", "x", T0)
        inbox.ack(self.root, "PM-ui-2", ["M-1"])
        out = inbox.pending(self.root)
        self.assertIn("[inbox] PM-ui: 1 unread (M-1)", out)
        self.assertNotIn("PM-ui-2", out)

    def test_check_flags_old_unread_only(self):
        inbox.send(self.root, "PM-ui", "PO", "old", T0)
        inbox.send(self.root, "PM-ui", "PO", "read", T0)
        inbox.ack(self.root, "PM-ui", ["M-2"])
        later = datetime.datetime.strptime("2026-10-30 06:40 -0300", inbox.FMT)
        flags = inbox.check(self.root, 4, later)
        self.assertEqual(flags, ["PM-ui: M-1 from PO unread for 6 h"])
        self.assertEqual(inbox.check(self.root, 8, later), [])

    def test_bad_names_are_refused(self):
        with self.assertRaises(ValueError):
            inbox.send(self.root, "../etc", "PO", "x")
        self.assertEqual(run(["send", self.root, "--to", "PM ui", "--from", "PO", "x"])[0], 2)

    def test_concurrent_sends_keep_every_message(self):
        threads = [threading.Thread(target=inbox.send, args=(self.root, "PM-ui", f"PM-{i}", "x"))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        ids = [m.id for m in inbox.messages(self.root, "PM-ui")]
        self.assertEqual(sorted(ids, key=lambda i: int(i[2:])), [f"M-{i}" for i in range(1, 21)])

    def test_cli_round_trip_and_hook(self):
        self.assertEqual(run(["send", self.root, "--to", "PM-ui", "--from", "PO", "hi"]),
                         (0, "M-1\n"))
        code, out = run(["hook", self.root], json.dumps({"hook_event_name": "UserPromptSubmit"}))
        self.assertEqual(code, 0)
        self.assertIn("PM-ui: 1 unread", out)
        self.assertEqual(run(["hook", self.root], "not json")[0], 0)
        self.assertEqual(run(["ack", self.root, "PM-ui", "M-1"]), (0, "ok\n"))
        self.assertEqual(run(["pending", self.root]), (0, ""))
        self.assertEqual(run(["ack", self.root, "PM-ui", "M-1"])[0], 2)
        self.assertEqual(run(["check", self.root]), (0, "ok\n"))

    def test_aviso_con_imperativo_advierte_CAL28(self):
        # CAL-28: [aviso] con imperativo en primeras palabras advierte en stderr y no bloquea
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            mid = inbox.send(self.root, "PM-ui", "PO", "[aviso] avanza tus pending")
        self.assertEqual(mid, "M-1")
        self.assertIn("advertencia", err.getvalue().lower())
        self.assertIn("avanza", err.getvalue().lower())
        self.assertIn("[encargo]", err.getvalue())
        # el mensaje se envía igual
        self.assertEqual(inbox.messages(self.root, "PM-ui")[0].text, "[aviso] avanza tus pending")
        # prueba de que falla: si se quita el aviso check, err sería vacío
        self.assertTrue(err.getvalue() != "", "CAL-28: [aviso] con 'avanza' debe advertir")
        # otros imperativos también advierten
        for verb in inbox.AVISO_IMPERATIVOS:
            tmp = tempfile.TemporaryDirectory()
            root2 = os.path.join(tmp.name, "inbox")
            eout, eerr = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(eout), contextlib.redirect_stderr(eerr):
                inbox.send(root2, "PM-ui", "PO", f"[aviso] {verb} algo")
            self.assertIn(verb, eerr.getvalue().lower(), f"debe advertir con '{verb}'")
            tmp.cleanup()
        # [aviso] sin imperativo no advierte
        eout, eerr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(eout), contextlib.redirect_stderr(eerr):
            inbox.send(self.root, "PM-ui", "PO", "[aviso] SIM-26 cerrado en 53577e73")
        self.assertEqual(eerr.getvalue(), "")
        # [encargo] con verbo no advierte (no es aviso)
        eout, eerr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(eout), contextlib.redirect_stderr(eerr):
            inbox.send(self.root, "PM-ui", "PO", "[encargo] avanza tus pending")
        self.assertEqual(eerr.getvalue(), "")
        # lista en un solo lugar
        self.assertEqual(set(inbox.AVISO_IMPERATIVOS), {"haz", "avanza", "pide", "corrige", "lanza", "revisa", "cierra"})


if __name__ == "__main__":
    unittest.main()
