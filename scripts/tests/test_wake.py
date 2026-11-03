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

    def test_avisos_wait_the_hold_and_a_bloqueo_does_not(self):
        now = inbox.datetime.datetime(2026, 11, 3, 14, 20).astimezone()
        def msg(text, minute):
            sent = now.replace(minute=minute).strftime(inbox.FMT)
            return inbox.Message("M-1", "PM-ui", sent, text, "unread")
        self.assertAlmostEqual(wake.held([msg("listo", 15)], 900, now), 600)  # 5 of 15 min waited
        self.assertEqual(wake.held([msg("listo", 1)], 900, now), 0)  # waited the whole hold
        self.assertEqual(wake.held([msg("listo", 19), msg("[bloqueo] sin base", 19)], 900, now), 0)
        self.assertEqual(wake.held([msg("[Decisión] elige", 19)], 900, now), 0)
        self.assertEqual(wake.held([msg("listo", 19)], 0, now), 0)  # a mailbox without a hold

    def test_the_po_holds_avisos_until_a_bloqueo_arrives(self):
        self.session("ses_po", f"inbox.py read {self.root} PO", 1)
        inbox.send(self.root, "PO", "PM-ui", "SIM-1 cerrado")
        slept = []
        real = wake.time.sleep
        wake.time.sleep = lambda s: slept.append(s) or (
            len(slept) == 2 and inbox.send(self.root, "PO", "PM-ui", "[bloqueo] falta la base"))
        try:
            wake.run(self.root, "PO", send=self.send, wait=0, poll=1)
        finally:
            wake.time.sleep = real
        self.assertEqual(len(self.sent), 1)  # one wake for both, right after the bloqueo
        self.assertIn("(M-1, M-2)", self.sent[0][1])
        with open(wake.log_path(self.root)) as f:
            self.assertEqual(sum(" PO held M-1" in line for line in f), 1)  # logged once

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
        self.assertTrue(self.wake(cap=3).startswith("capped: 3 wakes"))
        self.assertEqual(len(self.sent), 3)
        with open(os.path.join(self.root, ".wake-PM-ui.lock"), "w") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.wake(), "another waker is on it")


    def nudge(self, minutes):
        return wake.sweep(self.root, now=minutes * 60, send=self.send)

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

    def test_sweep_skips_busy_and_retired_pms(self):
        self.session("ses_ui", f"inbox.py read {self.root} PM-ui", 1, to="PM-ui", busy=True)
        inbox.send(self.root, "PM-ui", "PO", "x")
        self.assertEqual(self.nudge(25), [])
        self.db.execute("update session_v2 set time_busy = 1, time_idle = 2")
        self.db.commit()
        self.assertEqual(self.nudge(13 * 60), [])             # idle 13 h: a retired PM
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
