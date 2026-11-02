import contextlib
import datetime
import json
import io
import os
import re
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import focus  # noqa: E402

GOOD = """# Focus of line X

**Level:** line
**State:** active

## Objective

> «ship A» (2026-10-01, D-1)

## Directives in force

| Id | Kind | Scope | Human's words | Date | Status |
|---|---|---|---|---|---|
| D-1 | objective | line | «ship A» | 2026-10-01 | in force |
| D-2 | nuance | T-07 | «tweak B» | 2026-10-02 | superseded by D-3 |
| D-3 | nuance | T-07 | «tweak B differently» | 2026-10-03 | in force |

## Archived

| D-0 | nuance | T-01 | «old» | 2026-09-01 | closed |
"""

LEGACY_LOG = GOOD.replace("## Archived", "## Log\n\n### 2026-10-03 10:00 · a turn\n"
                          "- Objective: unchanged (D-1)\n\n## Archived")


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = focus.main(argv)
    return code, out.getvalue()


class FocusTest(unittest.TestCase):
    def test_good_file_passes(self):
        self.assertEqual(focus.check(GOOD, 60), [])

    def test_anchor_stops_at_archived_or_legacy_log(self):
        for text in (GOOD, LEGACY_LOG):
            anchor = "\n".join(focus.anchor_lines(text))
            self.assertIn("## Directives in force", anchor)
            self.assertNotIn("«old»", anchor)
            self.assertNotIn("a turn", anchor)
        self.assertEqual(focus.check(LEGACY_LOG, 60), [])

    def test_directive_table_errors(self):
        text = GOOD.replace("superseded by D-3", "superseded by D-9").replace(
            "| D-3 | nuance |", "| D-1 | hint |")
        flags = focus.check(text, 60)
        self.assertTrue(any("D-9, which does not exist" in f for f in flags))
        self.assertTrue(any("D-1 repeats" in f for f in flags))
        self.assertTrue(any("kind 'hint'" in f for f in flags))

    def test_objectives_in_force(self):
        none = GOOD.replace("2026-10-01 | in force", "2026-10-01 | superseded by D-3")
        self.assertTrue(any("0 objectives in force" in f for f in focus.check(none, 60)))

    def test_anchor_too_long(self):
        self.assertTrue(any("anchor is" in f for f in focus.check(GOOD, 5)))

    def test_anchor_too_heavy_in_bytes(self):
        # One row can hold kilobytes: 60 lines is not a size limit by itself.
        self.assertTrue(any("bytes" in f for f in focus.check(GOOD, 60, max_anchor_bytes=200)))

    def test_one_directive_per_row(self):
        packed = GOOD.replace("«tweak B differently»", "«tweak B» · D-5 «x» " * 120)
        self.assertTrue(any("D-3 row is" in f for f in focus.check(packed, 60)))

    def test_status_vocabulary_and_executed_rows(self):
        bad = GOOD.replace("| 2026-10-03 | in force |", "| 2026-10-03 | done |")
        self.assertTrue(any("status 'done'" in f for f in focus.check(bad, 60)))
        paused = GOOD.replace("| 2026-10-03 | in force |", "| 2026-10-03 | suspended by D-1 |")
        self.assertEqual(focus.check(paused, 60), [])
        done = GOOD.replace("| 2026-10-03 | in force |", "| 2026-10-03 | executed (MIG-1, abc123) |")
        self.assertEqual([f for f in focus.check(done, 60) if "status" in f], [])
        self.assertTrue(any("D-3 is executed: move it to ## Archived" in f
                            for f in focus.check(done, 60)))

    def test_state_line(self):
        self.assertIn('no "**State:**" line',
                      focus.check(GOOD.replace("**State:** active\n", ""), 60)[0])
        self.assertIn("state 'done'", focus.check(GOOD.replace("active", "done", 1), 60)[0])
        closed = GOOD.replace("**State:** active", "**State:** closed (2026-10-20)")
        self.assertEqual(focus.check(closed, 60), [])

    def test_level_line(self):
        self.assertIn("level None", focus.check(GOOD.replace("**Level:** line\n", ""), 60)[0])
        self.assertIn("level 'team'", focus.check(GOOD.replace("line\n", "team\n", 1), 60)[0])
        epic = focus.check(GOOD.replace("**Level:** line", "**Level:** epic"), 60)
        self.assertIn("epic level is gone", epic[0])

    def test_cli(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(GOOD)
        try:
            code, out = run(["anchor", f.name])
            self.assertEqual(code, 0)
            self.assertIn("# Focus of line X", out)
            self.assertNotIn("## Archived", out)
            self.assertEqual(run(["check", f.name]), (0, "ok\n"))
        finally:
            os.unlink(f.name)
        code, out = run(["anchor", "/nonexistent/FOCUS.md"])
        self.assertEqual(code, 0)
        self.assertIn("not found", out)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["check", "/nonexistent/FOCUS.md"])[0], 2)


class AnchorDirTest(unittest.TestCase):
    """focus/PROJECT.md and focus/lines/*.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "lines"))

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("PM_LINE", None)

    def write(self, rel, level, state, objective, first=0):
        text = GOOD.replace("**Level:** line", f"**Level:** {level}").replace(
            "**State:** active", f"**State:** {state}").replace("ship A", objective)
        text = re.sub(r"D-(\d+)", lambda m: f"D-{int(m.group(1)) + first}", text)
        with open(os.path.join(self.root, rel), "w") as f:
            f.write(text)

    def project(self):
        self.write("PROJECT.md", "project", "active", "project goal")
        self.write("lines/ui.md", "line", "active", "ui mandate", first=10)
        self.write("lines/data.md", "line", "paused", "data mandate", first=20)

    def test_line_gets_project_and_itself(self):
        self.project()
        out = focus.anchor(self.root, "ui")
        self.assertIn("project goal", out)
        self.assertIn("ui mandate", out)
        for unwanted in ("data mandate", "[focus]", "## Archived"):
            self.assertNotIn(unwanted, out)
        self.assertLess(out.index("project goal"), out.index("ui mandate"))

    def test_no_line_gets_only_the_project(self):
        self.project()
        out = focus.anchor(self.root, None)
        self.assertIn("project goal", out)
        self.assertNotIn("mandate", out)
        self.assertNotIn("[focus]", out)

    def test_paused_line_warns(self):
        self.project()
        self.assertTrue(focus.anchor(self.root, "data").startswith("[focus] line 'data' is paused"))

    def test_missing_files_warn_without_failing(self):
        out = focus.anchor(self.root, "ui")
        self.assertIn("no " + os.path.join(self.root, "PROJECT.md"), out)
        self.assertIn("line 'ui' has no focus yet", out)

    def test_check_dir_flags_stale_active_lines_only(self):
        self.project()
        self.assertEqual(focus.check_dir(self.root, 60, today=datetime.date(2026, 10, 10)), [])
        flags = focus.check_dir(self.root, 60, today=datetime.date(2026, 11, 1))
        self.assertEqual(len(flags), 1, flags)
        self.assertIn("ui.md: active, but its newest directive is 29 days old", flags[0])

    def test_check_dir_flags_an_id_used_in_two_files_unless_grandfathered(self):
        self.project()
        self.write("lines/data.md", "line", "paused", "data mandate", first=10)
        today = datetime.date(2026, 10, 10)
        flags = focus.check_dir(self.root, 60, today=today)
        self.assertEqual(len(flags), 4, flags)
        self.assertTrue(all("lines/data.md, " in f and "lines/ui.md: renumber" in f for f in flags))
        with open(os.path.join(self.root, focus.REPEATED_OK), "w") as f:
            f.write("# antes de la regla\nD-10\nD-11\nD-12\nD-13\n")
        self.assertEqual(focus.check_dir(self.root, 60, today=today), [])

    def test_cli_line_from_env(self):
        self.project()
        os.environ["PM_LINE"] = "ui"
        code, out = run(["anchor", self.root])
        self.assertEqual(code, 0)
        self.assertIn("ui mandate", out)


class HookTest(unittest.TestCase):
    """SessionStart (also after a compaction) prints the anchors; every other event is silent."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "lines"))
        with open(os.path.join(self.root, "PROJECT.md"), "w") as f:
            f.write(GOOD.replace("**Level:** line", "**Level:** project")
                    .replace("ship A", "project goal"))
        with open(os.path.join(self.root, "lines", "ui.md"), "w") as f:
            f.write(GOOD.replace("ship A", "ui mandate"))

    def tearDown(self):
        self.tmp.cleanup()
        os.environ.pop("PM_LINE", None)

    def hook(self, event):
        stdin = sys.stdin
        sys.stdin = io.StringIO(json.dumps(event))
        try:
            return run(["hook", self.root])
        finally:
            sys.stdin = stdin

    def test_session_start_prints_project_then_line(self):
        code, out = self.hook({"hook_event_name": "SessionStart", "source": "compact"})
        self.assertEqual(code, 0)
        self.assertIn("project goal", out)
        self.assertNotIn("ui mandate", out)
        os.environ["PM_LINE"] = "ui"
        self.assertIn("ui mandate", self.hook({"hook_event_name": "SessionStart"})[1])

    def test_other_events_are_silent(self):
        for name in ("UserPromptSubmit", "SessionEnd"):
            self.assertEqual(self.hook({"hook_event_name": name, "prompt": "hi"}), (0, ""))

    def test_bad_stdin_never_blocks(self):
        stdin = sys.stdin
        sys.stdin = io.StringIO("not json")
        try:
            code, out = run(["hook", self.root])
        finally:
            sys.stdin = stdin
        self.assertEqual(code, 0)
        self.assertIn("project goal", out)


if __name__ == "__main__":
    unittest.main()
