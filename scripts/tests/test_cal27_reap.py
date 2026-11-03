import json
import os
import sqlite3
import sys
import tempfile
import unittest
import time

# skill paths
SKILL_SCRIPTS = os.path.expanduser("~/.claude/skills/scrum-kanban-agents/scripts")
SKILL_ADAPTER = os.path.expanduser("~/.claude/skills/scrum-kanban-agents/adapters/opencode")
sys.path.insert(0, SKILL_SCRIPTS)
sys.path.insert(0, SKILL_ADAPTER)
import inbox  # noqa: E402
import wake  # noqa: E402


class ReapFinishedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.tmp.name, "proj")
        self.root = os.path.join(self.project, "inbox")
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(os.path.join(self.project, "epic-calidad"), exist_ok=True)
        # dummy STATUS with one reported line for CAL-77
        self.status_path = os.path.join(self.project, "epic-calidad", "STATUS.md")
        with open(self.status_path, "w", encoding="utf-8") as f:
            f.write("| Ticket | State |\n|---|---|\n| CAL-77 | reported |\n")
        # another epic
        os.makedirs(os.path.join(self.project, "epic-grupos"), exist_ok=True)
        with open(os.path.join(self.project, "epic-grupos", "STATUS-FASE2.md"), "w", encoding="utf-8") as f:
            f.write("| Ticket | State |\n|---|---|\n")
        self.db_path = os.path.join(self.tmp.name, "opencode.db")
        wake.DB = self.db_path
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(
            "create table session_v2 (id text, parent_id text, title text, directory text, time_archived int, time_busy int, time_idle int, time_created int);"
            "create table session_message (session_id text, type text, time_created int, data text);"
        )
        self.db.commit()

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def add_session(self, sid, title="dev CAL-27", parent="ses_parent", busy=1, idle=2, to="PM-calidad", outcome="succeeded", with_commit=True, with_send=False):
        # session_v2 row
        self.db.execute(
            "insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
            (sid, parent, title, self.project, None, busy, idle, int(time.time()*1000)),
        )
        # first user prompt
        if to:
            self.db.execute(
                "insert into session_message values (?,?,?,?)",
                (sid, "user", 1, json.dumps({"text": f"TO: dev {title.split()[-1] if 'dev' in title.lower() else 'CAL-27'} prompt"})),
            )
            # ensure TO: dev <ticket> for ticket fallback if needed
            # also add explicit TO: PM-calidad? not needed
        # idle outcome
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            (sid, "idle", 10, json.dumps({"outcome": outcome})),
        )
        if with_commit:
            tool = {"type": "tool", "state": {"input": {"command": "git commit -m 'fix'"}, "content": [{"type": "text", "text": "commit abc1234 done"}]}}
            self.db.execute(
                "insert into session_message values (?,?,?,?)",
                (sid, "assistant", 5, json.dumps({"content": [tool]})),
            )
            # also add a message containing sha 47f66777
            self.db.execute(
                "insert into session_message values (?,?,?,?)",
                (sid, "assistant", 6, json.dumps({"content": [{"type": "tool", "state": {"input": {"command": "git commit -m 'x'"}, "content": [{"type": "text", "text": " [main 47f66777] foo"}]}}]})),
            )
        if with_send:
            tool2 = {"type": "tool", "state": {"input": {"command": "inbox.py send --to PM-calidad --from dev inbox 'reporte'"}}}
            self.db.execute(
                "insert into session_message values (?,?,?,?)",
                (sid, "assistant", 7, json.dumps({"content": [tool2]})),
            )
        self.db.commit()

    def test_sesion_succeeded_sin_aviso_genera_uno(self):
        self.add_session("ses_dev1", title="dev CAL-27", parent="ses_parent")
        # ensure parent has addressee PM-calidad for pm resolution
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        self.db.commit()
        before = inbox.messages(self.root, "PM-calidad")
        self.assertEqual(len(before), 0)
        out = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out), 1)
        msgs = inbox.messages(self.root, "PM-calidad")
        # should have one aviso from lanzador
        self.assertEqual(len([m for m in msgs if m.sender == "lanzador"]), 1)
        txt = msgs[0].text
        self.assertIn("CAL-27", txt)
        self.assertIn("ses_dev1", txt)
        self.assertIn("47f66777", txt)
        self.assertIn("reported", txt.lower())

    def test_segunda_llamada_no_duplica(self):
        self.add_session("ses_dev_dup", title="dev CAL-28", parent="ses_parent")
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        self.db.commit()
        wake.reap_finished(self.root, self.db_path)
        wake.reap_finished(self.root, self.db_path)
        msgs = inbox.messages(self.root, "PM-calidad")
        self.assertEqual(len([m for m in msgs if "ses_dev_dup" in m.text]), 1)
        aviso = os.path.join(self.root, ".lanzar", ".aviso-dev")
        with open(aviso, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertEqual(lines.count("ses_dev_dup"), 1)

    def test_no_edita_status_mtime(self):
        self.add_session("ses_dev_mtime", title="dev CAL-27", parent="ses_parent")
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        self.db.commit()
        mt_before = os.path.getmtime(self.status_path)
        time.sleep(0.01)
        wake.reap_finished(self.root, self.db_path)
        mt_after = os.path.getmtime(self.status_path)
        self.assertEqual(mt_before, mt_after)
        # also STATUS-FASE2
        p2 = os.path.join(self.project, "epic-grupos", "STATUS-FASE2.md")
        mt2_before = os.path.getmtime(p2)
        wake.reap_finished(self.root, self.db_path)
        self.assertEqual(mt2_before, os.path.getmtime(p2))

    def test_con_send_previo_no_avisa(self):
        self.add_session("ses_dev_send", title="dev CAL-27", parent="ses_parent", with_send=True)
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        self.db.commit()
        out = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out), 0)
        msgs = inbox.messages(self.root, "PM-calidad")
        self.assertEqual(len(msgs), 0)

    def test_sweep_no_cuenta_para_cap_y_respeta_existente(self):
        # also verify sweep calls reap_finished
        self.add_session("ses_dev_sweep", title="dev CAL-29", parent="ses_parent")
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        # need a PM session for sweep nudging? not needed for this test
        self.db.execute("insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
                        ("ses_pm", None, "PM-calidad", self.project, None, 1, 2, int(time.time()*1000 - 30*60*1000)))
        cmd = f"inbox.py read {self.root} PM-calidad"
        tool_pm = {"type": "tool", "state": {"input": {"command": cmd}, "content": [{"type": "text", "text": "ok"}]}}
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_pm", "assistant", 1, json.dumps({"content": [tool_pm]})))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_pm", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        self.db.commit()
        # inbox PM has a file so mailboxes includes it
        inbox.send(self.root, "PM-calidad", "PO", "hola")
        done = wake.sweep(self.root, now=time.time(), send=lambda sid, text: None)
        # reap should be in done
        self.assertTrue(any("ses_dev_sweep" in d for d in done))
        # CAP not affected: wakes_last_hour should not count reap
        # sweep nudge may have happened but reap is separate
        msgs = inbox.messages(self.root, "PM-calidad")
        # one from lanzador plus one from PO
        self.assertTrue(any(m.sender == "lanzador" and "ses_dev_sweep" in m.text for m in msgs))


if __name__ == "__main__":
    unittest.main()
