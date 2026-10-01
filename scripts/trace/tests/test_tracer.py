import contextlib
import io
import json
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tracer as tr  # noqa: E402

T0 = 1_790_000_000_000
MIN = 60_000


def make_opencode_db(path):
    db = sqlite3.connect(path)
    db.executescript("""
      create table session (id text, title text, model text, parent_id text, time_created int);
      create table message (id text, session_id text, time_created int, data text);
      create table part (id text, message_id text, session_id text, time_created int, data text);
    """)
    db.execute("insert into session values ('s1','T-7 build the parser','{\"id\":\"m1\"}',null,?)",
               (T0,))

    def msg(mid, t, role, **extra):
        data = {"role": role, "time": {"created": t, "completed": t + 1000}, **extra}
        db.execute("insert into message values (?,?,?,?)", (mid, "s1", t, json.dumps(data)))

    def part(pid, mid, t, data):
        db.execute("insert into part values (?,?,?,?,?)", (pid, mid, "s1", t, json.dumps(data)))

    def tool(pid, mid, t, name, dur_min, status="completed", **inp):
        part(pid, mid, t, {"type": "tool", "tool": name, "state": {
            "status": status, "input": inp, "time": {"start": t, "end": t + int(dur_min * MIN)}}})

    # task 1: a work prompt
    msg("u1", T0, "user")
    part("p0", "u1", T0, {"type": "text", "text": "TO: dev-a\nTicket T-7: build the parser"})
    msg("a1", T0 + 1000, "assistant", tokens={"input": 100, "output": 10, "reasoning": 5,
                                               "cache": {"read": 50}}, cost=0.5)
    tool("t1", "a1", T0 + 2000, "read", 0.01, filePath="/r/x.py")
    tool("t2", "a1", T0 + 1 * MIN, "read", 0.01, filePath="/r/x.py")       # re-read
    tool("t3", "a1", T0 + 2 * MIN, "bash", 3, command="pytest -q")          # suite, 3 min
    tool("t4", "a1", T0 + 10 * MIN, "edit", 0.01, filePath="/r/x.py")       # 5 min gap before
    tool("t5", "a1", T0 + 11 * MIN, "bash", 0.1, status="error",
         command="curl -H 'api_key=abc123' http://x")
    # task 2: a second prompt in the same session (a return)
    msg("u2", T0 + 20 * MIN, "user")
    part("p1", "u2", T0 + 20 * MIN, {"type": "text", "text": "T-7 DEVUELTO: fix the findings"})
    msg("a2", T0 + 20 * MIN + 1000, "assistant", tokens={"input": 7, "output": 3}, cost=0.1)
    tool("t6", "a2", T0 + 21 * MIN, "bash", 1, command="python -m unittest")
    db.commit()
    db.close()


CFG = {"ticket": tr.DEFAULT_TICKET, "agent": tr.DEFAULT_AGENT, "return": tr.DEFAULT_RETURN,
       "suite": tr.DEFAULT_SUITE}


class Normalize(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from sources import opencode
        cls.tmp = tempfile.TemporaryDirectory()
        p = os.path.join(cls.tmp.name, "oc.db")
        make_opencode_db(p)
        cls.rec = tr.normalize(opencode.read_session(opencode.connect(p), "s1"), "proj", CFG)
        cls.t1, cls.t2 = cls.rec["tasks"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_one_task_per_prompt(self):
        self.assertEqual(self.rec["session"]["prompts"], 2)
        self.assertEqual(len(self.rec["tasks"]), 2)

    def test_agent_ticket_kind(self):
        self.assertEqual((self.t1["agent"], self.t1["ticket"], self.t1["kind"]),
                         ("dev-a", "T-7", "work"))
        # no TO: line in the second prompt: agent is inherited
        self.assertEqual((self.t2["agent"], self.t2["ticket"], self.t2["kind"]),
                         ("dev-a", "T-7", "return"))

    def test_metrics(self):
        t = self.t1
        self.assertEqual(t["calls"], 5)
        self.assertEqual((t["reads"], t["edits"], t["shell"]), (2, 1, 2))
        self.assertEqual(t["rereads"], 1)
        self.assertEqual(t["suite_runs"], 1)
        self.assertAlmostEqual(t["suite_min"], 3, places=1)
        self.assertEqual(t["failed"], 1)
        self.assertAlmostEqual(t["longest_call_min"], 3, places=1)
        self.assertAlmostEqual(t["longest_gap_min"], 5, places=1)
        self.assertEqual((t["tokens_in"], t["tokens_out"], t["cost"]), (100, 10, 0.5))
        self.assertEqual(self.t2["suite_runs"], 1)

    def test_secrets_are_redacted_in_steps(self):
        s = [st["summary"] for st in self.rec["steps"] if "curl" in st["summary"]][0]
        self.assertNotIn("abc123", s)
        self.assertIn("<redacted>", s)

    def test_earliest_ticket_mention_wins(self):
        pats = [r"\b([A-Z]-\d+)\b", r"\b(2\.1c)\b"]
        self.assertEqual(tr.first_match(pats, "Ticket 2.1c returned F-4"), "2.1c")


class EndToEnd(unittest.TestCase):
    def test_ingest_event_build_report(self):
        with tempfile.TemporaryDirectory() as d:
            oc = os.path.join(d, "oc.db")
            make_opencode_db(oc)
            data = os.path.join(d, "data")
            run = lambda *a: tr.main(["--data", data, *a])  # noqa: E731
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run("ingest", "--project", "p", "--opencode-db", oc), 0)
                run("event", "verdict", "--project", "p", "--ticket", "T-7", "--round", "0",
                    "--verdict", "returned", "--cause", "evidence")
                run("event", "assign", "--project", "p", "--session", "s1", "--task", "2",
                    "--agent", "dev-b")
                run("event", "log", "--project", "p", "--session", "s1", "--task", "1",
                    "--text", "read, tested, edited")
                self.assertEqual(run("build"), 0)
            # ingest is idempotent: one file per session
            self.assertEqual(os.listdir(os.path.join(data, "p", "sessions")), ["s1.json"])
            db = sqlite3.connect(os.path.join(data, "trace.db"))
            self.assertEqual(db.execute("select agent from task order by n").fetchall(),
                             [("dev-a",), ("dev-b",)])
            self.assertEqual(db.execute("select one_shot, multi_task from v_one_shot").fetchone(),
                             (0, 1))
            self.assertEqual(db.execute("select * from v_return_cause").fetchone(),
                             ("p", "evidence", 1))
            self.assertEqual(db.execute("select count(*) from log").fetchone(), (1,))
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                run("report", "--view", "v_ticket")
            self.assertIn("T-7", out.getvalue())


if __name__ == "__main__":
    unittest.main()
