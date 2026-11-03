import datetime
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import archivar  # noqa: E402
import inbox  # noqa: E402

NOW = datetime.datetime(2026, 11, 10, 12, 0).astimezone()


def stamp(days_ago: float) -> str:
    return (NOW - datetime.timedelta(days=days_ago)).strftime(inbox.FMT)


class ArchivarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = self.tmp.name
        self.root = os.path.join(self.repo, "inbox")
        os.makedirs(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def read(self, *parts):
        with open(os.path.join(self.repo, *parts), encoding="utf-8") as f:
            return f.read()

    def write(self, text, *parts):
        os.makedirs(os.path.join(self.repo, *parts[:-1]), exist_ok=True)
        with open(os.path.join(self.repo, *parts), "w", encoding="utf-8") as f:
            f.write(text)

    def test_mailbox_moves_old_read_rows_and_keeps_unread_and_the_newest(self):
        inbox.send(self.root, "PO", "PM-ui", "viejo leído", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "viejo sin leer", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "nuevo leído", when=stamp(1))
        inbox.send(self.root, "PO", "PM-ui", "el último, leído", when=stamp(4))
        inbox.ack(self.root, "PO", ["M-1", "M-4"], when=stamp(4))  # M-4 old but the newest
        inbox.ack(self.root, "PO", ["M-3"], when=stamp(0.5))
        before = {m.id for m in inbox.messages(self.root, "PO")}
        self.assertEqual(archivar.archive(self.repo, NOW), ["PO: 1 filas leídas a inbox/archivo/"])
        live = {m.id for m in inbox.messages(self.root, "PO")}
        month = stamp(5)[:7]
        archived = {m.id for m in inbox.parse(self.read("inbox", "archivo", f"PO-{month}.md"))}
        self.assertEqual(archived, {"M-1"})
        self.assertEqual(live, {"M-2", "M-3", "M-4"})  # unread, too recent, the newest
        self.assertEqual(live | archived, before)  # nothing lost
        self.assertEqual(archivar.archive(self.repo, NOW), [])  # idempotent
        self.assertEqual(inbox.send(self.root, "PO", "PM-ui", "otro"), "M-5")  # ids never repeat

    def test_wakes_log_moves_lines_older_than_a_week(self):
        self.write(f"{stamp(9)} PO woke ses_a for M-1\n{stamp(1)} PO woke ses_a for M-2\n",
                   "inbox", ".wakes.log")
        archivar.archive(self.repo, NOW)
        self.assertNotIn("M-1", self.read("inbox", ".wakes.log"))
        self.assertIn("M-2", self.read("inbox", ".wakes.log"))
        self.assertIn("M-1", self.read("inbox", "archivo", f"wakes-{stamp(9)[:7]}.log"))

    def test_status_keeps_the_five_newest_closed_and_every_open_row(self):
        head = "| Ticket | Lane | State | Updated |\n|---|---|---|---|\n"
        rows = [f"| T-{n} | x | closed | 2026-11-{n:02d} |" for n in range(1, 9)]
        rows += ["| T-20 | x | pending | 2026-10-01 |", "| T-21 | x | in progress | 2026-10-01 |"]
        self.write("# Status\n\n" + head + "\n".join(rows) + "\n", "epic-a", "STATUS.md")
        archivar.archive(self.repo, NOW)
        live, arch = self.read("epic-a", "STATUS.md"), self.read("epic-a", "STATUS-archivo.md")
        for n in (1, 2, 3):
            self.assertNotIn(f"| T-{n} |", live)
            self.assertIn(f"| T-{n} |", arch)
        for n in (4, 5, 6, 7, 8, 20, 21):
            self.assertIn(f"| T-{n} |", live)
        self.assertIn("| Ticket | Lane | State | Updated |", arch)
        self.assertEqual(archivar.archive(self.repo, NOW), [])

    def test_done_directives_move_below_archived_and_in_force_stay(self):
        text = ("# Focus\n\n## Directives in force\n\n| Id | Kind | Scope | Human's words | Date | Status |\n"
                "|---|---|---|---|---|---|\n| D-1 | objective | p | «a» | 2026 | in force |\n"
                "| D-2 | decision | p | «b» | 2026 | executed (X-1) |\n"
                "| D-3 | decision | p | «c» | 2026 | superseded by D-4 |\n\n## Log\n\nnada\n")
        self.write(text, "focus", "PROJECT.md")
        archivar.archive(self.repo, NOW)
        out = self.read("focus", "PROJECT.md")
        top, bottom = out.split("## Archived")
        self.assertIn("| D-1 |", top)
        self.assertNotIn("| D-2 |", top)
        self.assertIn("| D-2 |", bottom)
        self.assertIn("| D-3 |", bottom)
        self.assertEqual(archivar.archive(self.repo, NOW), [])

    def test_dry_run_touches_nothing(self):
        inbox.send(self.root, "PO", "PM-ui", "viejo", when=stamp(5))
        inbox.send(self.root, "PO", "PM-ui", "último", when=stamp(5))
        inbox.ack(self.root, "PO", ["M-1", "M-2"], when=stamp(4))
        before = self.read("inbox", "PO.md")
        self.assertEqual(archivar.archive(self.repo, NOW, dry=True), ["PO: 1 filas leídas a inbox/archivo/"])
        self.assertEqual(self.read("inbox", "PO.md"), before)
        self.assertFalse(os.path.exists(os.path.join(self.root, "archivo")))


if __name__ == "__main__":
    unittest.main()
