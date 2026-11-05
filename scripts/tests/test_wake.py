import fcntl
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../adapters/opencode"))
import inbox  # noqa: E402
import wake  # noqa: E402


class WakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.tmp.name, "proj")
        self.root = os.path.join(self.project, "inbox")
        os.makedirs(self.root)
        wake.DB = os.path.join(self.tmp.name, "opencode.db")
        self.db = sqlite3.connect(wake.DB)
        self.db.executescript(
            "create table session_v2 (id text, parent_id text, directory text, time_archived int,"
            " time_busy int, time_idle int);"
            "create table session_message (session_id text, type text, time_created int, data text);")
        self.sent = []

    def tearDown(self):
        self.db.close()
        self.tmp.cleanup()

    def session(self, sid, command, t, parent=None, busy=False, to=None):
        self.db.execute("insert into session_v2 values (?,?,?,null,?,?)",
                        (sid, parent, self.project, 2 if busy else 1, 1 if busy else 2))
        if to:
            self.db.execute("insert into session_message values (?,'user',?,?)",
                            (sid, t - 0.5, json.dumps({"content": [{"type": "text", "text": f"TO: {to} (opencode)"}]})))
        tool = {"type": "tool", "state": {"input": {"command": command},
                                          "content": [{"type": "text", "text": "output"}]}}
        self.db.execute("insert into session_message values (?,'assistant',?,?)",
                        (sid, t, json.dumps({"content": [tool]})))
        self.db.commit()

    def send(self, sid, text):
        self.sent.append((sid, text))

    def wake(self, **kw):
        return wake.run(self.root, "PM-ui", send=self.send, wait=0, poll=0, **kw)

    def test_finds_the_session_that_read_the_mailbox(self):
        self.session("ses_ui", "python3 inbox.py read inbox PM-ui", 1)        # relative path
        self.session("ses_ui2", "python3 inbox.py read inbox/ PM-ui-2", 2)    # another mailbox
        self.session("ses_dev", f"inbox.py read {self.root} PM-ui", 3, parent="ses_ui")
        self.session("ses_other", "inbox.py read /elsewhere/inbox PM-ui", 4)  # another project
        pending = {"type": "tool", "state": {"input": {"command": "inbox.py pending inbox"},
                   "content": [{"type": "text", "text": f"inbox.py read {self.root} PM-ui"}]}}
        self.session("ses_pending", "true", 5)                                 # output only
        self.db.execute("insert into session_message values ('ses_pending','assistant',6,?)",
                        (json.dumps({"content": [pending]}),))
        self.db.commit()
        inbox.send(self.root, "PM-ui", "PO", "SIM-26 closed")
        self.assertEqual(self.wake(), "nothing new unread")
        self.assertEqual([s for s, _ in self.sent], ["ses_ui"])
        self.assertIn("M-1", self.sent[0][1])

    def test_another_pm_reading_the_mailbox_does_not_take_it(self):
        # 2026-10-30: PM-grupos read PM-ui's mailbox to check on it, was the newest reader,
        # and got PM-ui's wakes; PM-ui never woke.
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        self.session("ses_grupos", f"inbox.py read {self.root} PM-ui", 2, to="PM-grupos")
        inbox.send(self.root, "PM-ui", "PO", "R-9")
        self.wake()
        self.assertEqual([s for s, _ in self.sent], ["ses_ui"])

    def test_a_session_that_signs_as_another_pm_does_not_take_it(self):
        # 2026-11-03: the owner opened the PO by hand («toma el papel de PO…», no TO:). It read
        # PM-ui's mailbox to check on it, was the newest reader, and got PM-ui's wakes from 14:55
        # on; PM-ui, PM-ui-2 and PM-ui-3 never woke. It signs its sends `--from PO`.
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1)
        self.session("ses_po", f"inbox.py read {self.root} PM-ui", 2)
        tool = {"type": "tool", "state": {"input": {"command": "inbox.py send --to PM-ui --from PO inbox 'R-9'"}}}
        self.db.execute("insert into session_message values ('ses_po','assistant',3,?)",
                        (json.dumps({"content": [tool]}),))
        self.db.commit()
        inbox.send(self.root, "PM-ui", "PO", "R-9")
        self.wake()
        self.assertEqual([s for s, _ in self.sent], ["ses_ui"])

    def test_a_wake_that_went_to_another_session_is_sent_again(self):
        # 2026-11-03: M-57..M-59 woke the PO in PM-ui's place; once routing was right, the log
        # still said they were woken, and PM-ui would never have heard of them.
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        inbox.send(self.root, "PM-ui", "PO", "R-9")
        wake.log(self.root, "PM-ui", "woke ses_po for M-1")
        self.wake()
        self.assertEqual([s for s, _ in self.sent], ["ses_ui"])
        self.sent.clear()
        self.wake()
        self.assertEqual(self.sent, [])

    def test_a_read_from_before_the_move_still_finds_the_pm(self):
        # 2026-11-05 13:13: after gestion/ moved to a sibling repo, PM-calidad had only read
        # proj/gestion/inbox (gone); every wake said «no opencode session» and it never woke.
        os.makedirs(os.path.join(self.project, ".git"))
        nuevo = os.path.join(self.tmp.name, "proj-gestion", "inbox")
        os.makedirs(nuevo)
        with open(os.path.join(self.project, ".scrum.toml"), "w") as f:
            f.write(f'gestion = "{os.path.dirname(nuevo)}"\n')
        viejo = os.path.join(self.project, "gestion", "inbox")
        self.session("ses_ui", f"inbox.py read {viejo} PM-ui", 1)
        self.session("ses_otro", f"inbox.py read {self.tmp.name}/otro/inbox PM-ui", 2)  # other project
        self.assertEqual(wake.find_session(self.db, nuevo, "PM-ui"), "ses_ui")
        self.session("ses_repo", f"inbox.py read {self.project} PM-ui", 3)  # the repo as argument
        self.assertEqual(wake.find_session(self.db, nuevo, "PM-ui"), "ses_repo")

    def test_to_counts_only_when_it_opens_the_prompt(self):
        # 2026-11-02: a PM's relaunch prompt said "launch devs with `TO: dev R-<n>`"; read anywhere,
        # that made it a dev session, and the wakes kept going to the stuck old session.
        self.session("ses_old", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        self.session("ses_new", f"inbox.py read {self.root} PM-ui", 2)
        self.db.execute("insert into session_message values ('ses_new','user',1.5,?)",
                        (json.dumps({"text": "Eres PM-ui. Lanza devs con `TO: dev R-<n>`."}),))
        self.db.commit()
        inbox.send(self.root, "PM-ui", "PO", "R-9")
        self.wake()
        self.assertEqual([s for s, _ in self.sent], ["ses_new"])

    def test_accent_in_the_prompt_is_the_same_pm(self):
        self.session("ses_aud", f"inbox.py read {self.root} PM-auditoria", 1, to="PM-auditoría")
        inbox.send(self.root, "PM-auditoria", "PO", "MON-2")
        wake.run(self.root, "PM-auditoria", send=self.send, wait=0, poll=0)
        self.assertEqual([s for s, _ in self.sent], ["ses_aud"])

    def test_wakes_again_only_for_new_ids(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1)
        inbox.send(self.root, "PM-ui", "PO", "one")
        self.wake()  # the PM read M-1 but did not ack it (it waits on someone): no loop
        self.assertEqual(self.wake(), "nothing new unread")  # a later change (an ack) wakes nobody
        self.assertEqual(len(self.sent), 1)
        inbox.send(self.root, "PM-ui", "PO", "two")
        texts = []
        wake.run(self.root, "PM-ui", wait=0, poll=0,
                 send=lambda sid, text: texts.append(text) or
                 (len(texts) == 1 and inbox.send(self.root, "PM-ui", "PO", "three")))
        self.assertEqual(len(texts), 2)  # M-3 arrived during the M-2 turn: one more
        self.assertIn("(M-1, M-2, M-3)", texts[1])

    def test_the_po_wakes_when_it_stops_with_everything(self):
        # Dueño 2026-11-05, «vamos»: que los mensajes le lleguen cuando esté detenido, no cada 2 min.
        self.assertFalse(any(hasattr(wake, a) for a in ("DEBOUNCE", "HOLD", "URGENT", "held")))
        self.session("ses_po", f"inbox.py read {self.root} PO", 1)
        inbox.send(self.root, "PO", "PM-ui", "[listo] SIM-1")
        wake.run(self.root, "PO", send=self.send, wait=0, poll=0)  # idle: at once, no timer
        self.assertEqual(len(self.sent), 1)
        self.sent.clear()
        self.db.execute("update session_v2 set time_busy = 3, time_idle = 2")  # working
        self.db.commit()
        inbox.send(self.root, "PO", "PM-ui", "[aviso] rutina")
        def sleep(_):  # while it works another arrives; then it stops
            inbox.send(self.root, "PO", "PM-ui", "[bloqueo] falta la base")
            self.db.execute("update session_v2 set time_busy = 3, time_idle = 4")
            self.db.commit()
        real = wake.time.sleep
        wake.time.sleep = sleep
        try:
            wake.run(self.root, "PO", send=self.send, wait=60, poll=1)
        finally:
            wake.time.sleep = real
        self.assertEqual(len(self.sent), 1)                 # one turn when it stopped
        self.assertIn("M-2, M-3", self.sent[0][1])          # with what came during the wait

    def test_other_mailboxes_are_not_held(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1)
        inbox.send(self.root, "PM-ui", "PO", "un aviso")
        self.wake()
        self.assertEqual(len(self.sent), 1)

    def test_cap_busy_missing_and_lock(self):
        inbox.send(self.root, "PM-ui", "PO", "one")
        self.assertTrue(self.wake().startswith("skipped: no opencode session"))
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, busy=True)
        self.assertTrue(self.wake().startswith("skipped: ses_ui busy"))
        self.db.execute("update session_v2 set time_busy = 1, time_idle = 2")
        self.db.commit()
        for i in range(3):
            self.wake()
            inbox.send(self.root, "PM-ui", "PO", f"next {i}")
        # Sin tope (dueño, 2026-11-04): el cuarto despertar en la hora también despierta.
        self.wake()
        self.assertEqual(len(self.sent), 4)
        self.assertFalse(hasattr(wake, "CAP"))
        with open(os.path.join(self.root, ".wake-PM-ui.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.wake(), "another waker is on it")


    def nudge(self, minutes):
        return wake.sweep(self.project, now=minutes * 60, send=self.send)

    def test_sweep_nudges_an_idle_pm_once_per_idle_stretch(self):
        # 2026-11-02: PMs ended their turn with free dev slots and launchable tickets, and slept
        # until a message arrived.
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        self.session("ses_dev", f"inbox.py read {self.root} dev-R-1", 2, to="dev-R-1")
        inbox.send(self.root, "PM-ui", "PO", "x")
        inbox.send(self.root, "dev-R-1", "PO", "x")
        self.assertEqual(self.nudge(10), [])                  # idle 10 min: let it be
        self.assertEqual(len(self.nudge(25)), 1)               # devs are never nudged
        self.assertIn("Barrido automático: PM-ui lleva 25 min", self.sent[0][1])
        self.db.execute("insert into session_message values ('ses_ui','user',3,?)",
                        (json.dumps({"content": [{"type": "text", "text": self.sent[0][1]}]}),))
        self.db.commit()
        self.assertIn("already nudged", self.nudge(60)[0])    # it answered: wait for news
        self.assertEqual(len(self.sent), 1)
        self.db.execute("insert into session_message values ('ses_ui','user',4,'Automatic wake')")
        self.db.commit()
        self.nudge(90)                                         # something happened: nudge again
        self.assertEqual(len(self.sent), 2)

    def fail_turn(self, sid, t, message="reasoning `encrypted_content` was not issued"):
        self.db.execute("insert into session_message values (?,'idle',?,?)",
                        (sid, t, json.dumps({"outcome": "failed", "error": {"message": message}})))
        self.db.commit()

    def po(self):
        return [m.text for m in inbox.messages(self.root, "PO")]

    def test_a_pm_whose_turn_fails_is_reported_to_the_po_once(self):
        # 2026-11-02: PM-superficie and PM-calidad failed every wake for hours with a 400 while the
        # log said "woke" and "nudged"; 11 messages sat unread and nobody knew.
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        inbox.send(self.root, "PM-ui", "PO", "one")
        failing = lambda sid, text: self.sent.append(sid) or self.fail_turn(sid, 10 + len(self.sent))
        self.assertIn("the turn failed", wake.run(self.root, "PM-ui", send=failing, wait=0, poll=0))
        self.assertEqual(len(self.po()), 1)
        self.assertIn("PM-ui no responde", self.po()[0])
        self.assertIn("encrypted_content", self.po()[0])
        self.assertIn("Tiene 1 sin leer", self.po()[0])

        self.assertIn("last turn failed", self.nudge(25)[0])   # same failed turn: not told twice
        self.assertEqual(len(self.po()), 1)
        self.assertEqual(len(self.sent), 1)                    # and the sweep does not nudge it

        self.fail_turn("ses_ui", 20)                           # a new failed turn: told again
        self.nudge(30)
        self.assertEqual(len(self.po()), 2)

    def test_a_turn_that_ends_well_raises_no_alarm(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        self.fail_turn("ses_ui", 2)
        self.db.execute("insert into session_message values ('ses_ui','idle',3,?)",
                        (json.dumps({"outcome": "succeeded"}),))
        self.db.commit()
        inbox.send(self.root, "PM-ui", "PO", "one")
        self.assertEqual(self.wake(), "nothing new unread")
        self.assertEqual(self.po(), [])

    def test_a_pause_keeps_the_sweep_on_its_owner(self):
        # 2026-11-05: ORDEN-5 paused by bootout of the sweep. PM-calidad, the owner, went idle with
        # the pause on, nobody nudged it, and four PMs that failed to wake were never reported.
        self.session("ses_cal", f"inbox.py read {self.root} PM-calidad", 1, to="PM-calidad")
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 2, to="PM-ui")
        inbox.send(self.root, "PM-calidad", "PO", "x")
        inbox.send(self.root, "PM-ui", "PO", "x")
        with open(os.path.join(self.root, ".pausa"), "w") as f:
            f.write("PM-calidad ORDEN-5 filter-repo\n")
        self.assertEqual(inbox.pause(self.root), {"owner": "PM-calidad", "ticket": "ORDEN-5"})
        self.nudge(3)
        self.assertEqual(self.sent, [])                                        # 3 min: nobody yet
        self.nudge(6)                                                          # owner: 5 min, not 20
        self.assertEqual([sid for sid, _ in self.sent], ["ses_cal"])          # only the owner
        self.assertIn("la pausa sigue activa (ORDEN-5)", self.sent[0][1])
        self.db.execute("insert into session_message values ('ses_cal','user',3,?)",
                        (json.dumps({"content": [{"type": "text", "text": self.sent[0][1]}]}),))
        self.db.commit()
        self.assertEqual(self.po(), [])
        self.nudge(16)                                                         # still idle: PO told
        out = self.nudge(30)                                                   # once per idle stretch
        self.assertTrue(any("PM-ui ses_ui: paused" in d for d in out), out)  # idle 30 min: held
        self.assertEqual(len(self.po()), 1, self.po())
        self.assertIn("Pausa activa (ORDEN-5) y su dueño PM-calidad", self.po()[0])
        self.assertEqual(len(self.sent), 1)
        self.fail_turn("ses_ui", 4)                                            # paused or not:
        self.nudge(31)                                                         # a failed PM is told
        self.assertTrue(any("PM-ui no responde" in m for m in self.po()), self.po())

    def test_a_pause_without_owner_is_reported_and_holds_everyone(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui")
        inbox.send(self.root, "PM-ui", "PO", "x")
        open(os.path.join(self.root, ".pausa"), "w").close()                  # 2026-11-05: empty
        self.nudge(25)
        self.nudge(60)
        self.assertEqual(self.sent, [])
        self.assertEqual(len(self.po()), 1)
        self.assertIn(".pausa no dice quién la tiene", self.po()[0])

    def test_sweep_skips_busy_and_retired_pms(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui", busy=True)
        inbox.send(self.root, "PM-ui", "PO", "x")
        self.assertEqual(self.nudge(25), [])
        self.db.execute("update session_v2 set time_busy = 1, time_idle = 2")
        self.db.commit()
        self.assertEqual(self.nudge(13 * 60), [])             # idle 13 h: a retired PM
        self.assertEqual(self.sent, [])


class RepoTest(unittest.TestCase):
    """The CLI takes the repo and finds the mailbox; the repo of a moved mailbox is still the repo."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.realpath(os.path.join(self.tmp.name, "proj"))
        os.makedirs(os.path.join(self.repo, ".git"))
        os.makedirs(os.path.join(self.repo, "gestion", "inbox"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_mailbox_and_repo_follow_scrum_toml(self):
        # 2026-11-05: ORDEN-4 movió inbox/ a gestion/ y el barrido de launchd, con la ruta vieja,
        # recreó inbox/ en la raíz: nadie despertó y sus alarmas no las leyó nadie.
        with open(os.path.join(self.repo, ".scrum.toml"), "w") as f:
            f.write('gestion = "gestion"\n')
        box = os.path.join(self.repo, "gestion", "inbox")
        self.assertEqual(wake.mailbox_root(self.repo), box)
        self.assertEqual(wake.mailbox_root(box), box)        # a mailbox path still works
        with self.assertRaises(SystemExit):
            wake.repo_arg(box)                                # --sweep/--reap: only the repo
        self.assertEqual(wake.repo_arg(self.repo), self.repo)

    def test_activos_counts_only_recent_sessions_of_the_repo(self):
        db_path = os.path.join(self.tmp.name, "oc.db")
        db = sqlite3.connect(db_path)
        db.execute("create table session_v2 (id text, title text, directory text, time_updated int,"
                   " time_busy int, time_idle int)")
        at = 1_000_000.0
        rows = [("s1", "dev en subcarpeta", os.path.join(self.repo, "gestion"), at - 60, 2, 1),
                ("s2", "PO", self.repo, at - 300, 1, 2),
                ("s3", "viejo", self.repo, at - 3600, 1, 2),
                ("s4", "otro proyecto", self.repo + "-otro", at - 10, 1, 2)]
        db.executemany("insert into session_v2 values (?,?,?,?,?,?)",
                       [(i, t, d, u * 1000, b, idle) for i, t, d, u, b, idle in rows])
        db.commit()
        db.close()
        lines = wake.activos(self.repo, 10, db_path=db_path, at=at)
        self.assertEqual([l.split()[-1] for l in lines], ["s1", "s2"])
        self.assertIn("busy", lines[0])
        self.assertIn("idle", lines[1])
        # 2026-11-05 13:06: the PO passed the gestion repo and read «nadie» as «the dev is idle».
        # A path where no session ever ran is an error, never an empty answer.
        with self.assertRaises(SystemExit):
            wake.activos(self.repo + "-gestion", 10, db_path=db_path, at=at)
        self.assertEqual(wake.activos(self.repo, 0.01, db_path=db_path, at=at), [])  # nobody now: ok


if __name__ == "__main__":
    unittest.main()