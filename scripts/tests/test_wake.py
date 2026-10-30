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

    def session(self, sid, command, t, parent=None, busy=False):
        self.db.execute("insert into session_v2 values (?,?,?,null,?,?)",
                        (sid, parent, self.project, 2 if busy else 1, 1 if busy else 2))
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


if __name__ == "__main__":
    unittest.main()
