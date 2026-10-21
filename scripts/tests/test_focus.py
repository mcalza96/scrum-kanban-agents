import contextlib
import io
import os
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
| D-1 | objective | epic | «ship A» | 2026-10-01 | in force |
| D-2 | nuance | T-07 | «tweak B» | 2026-10-02 | superseded by D-3 |
| D-3 | nuance | T-07 | «tweak B differently» | 2026-10-03 | in force |

## Log

### 2026-10-02 10:00 · first turn
- Human said: «tweak B» → D-2 (nuance, scope T-07)
- Objective: unchanged (D-1)

### 2026-10-03 10:00 · second turn
- Objective: unchanged (D-1)

## Archived
"""


def run(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = focus.main(argv)
    return code, out.getvalue()


class FocusTest(unittest.TestCase):
    def test_good_file_passes(self):
        self.assertEqual(focus.check(GOOD, 60), [])

    def test_anchor_stops_at_log(self):
        anchor, log = focus.split(GOOD)
        self.assertIn("## Directives in force", "\n".join(anchor))
        self.assertNotIn("first turn", "\n".join(anchor))
        self.assertIn("### 2026-10-03 10:00 · second turn", log)
        self.assertNotIn("## Archived", log)

    def test_entry_without_objective_line(self):
        text = GOOD.replace("- Objective: unchanged (D-1)\n\n### 2026-10-03", "\n### 2026-10-03")
        self.assertEqual(len(focus.check(text, 60)), 1)
        self.assertIn("no 'Objective:' line", focus.check(text, 60)[0])

    def test_objective_change_must_name_objective_directive(self):
        drift = GOOD.replace("- Objective: unchanged (D-1)\n\n## Archived",
                             "- Objective: now B (D-3)\n\n## Archived")
        self.assertIn("without naming an objective directive", focus.check(drift, 60)[0])
        confirmed = GOOD.replace(
            "| D-3 | nuance | T-07 | «tweak B differently» | 2026-10-03 | in force |",
            "| D-3 | nuance | T-07 | «tweak B differently» | 2026-10-03 | in force |\n"
            "| D-4 | objective | epic | «ship B instead» | 2026-10-04 | in force |"
        ).replace("| D-1 | objective | epic | «ship A» | 2026-10-01 | in force |",
                  "| D-1 | objective | epic | «ship A» | 2026-10-01 | superseded by D-4 |"
        ).replace("- Objective: unchanged (D-1)\n\n## Archived",
                  "- Objective: changed to D-4\n\n## Archived")
        self.assertEqual(focus.check(confirmed, 60), [])

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

    def test_cli(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
            f.write(GOOD)
        try:
            code, out = run(["anchor", f.name])
            self.assertEqual(code, 0)
            self.assertIn("# Focus of line X", out)
            self.assertNotIn("## Log", out)
            self.assertEqual(run(["check", f.name]), (0, "ok\n"))
        finally:
            os.unlink(f.name)
        code, out = run(["anchor", "/nonexistent/FOCUS.md"])
        self.assertEqual(code, 0)
        self.assertIn("not found", out)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["check", "/nonexistent/FOCUS.md"])[0], 2)


    def test_state_line(self):
        self.assertIn('no "**State:**" line',
                      focus.check(GOOD.replace("**State:** active\n", ""), 60)[0])
        self.assertIn("state 'done'", focus.check(GOOD.replace("active", "done", 1), 60)[0])
        closed = GOOD.replace("**State:** active", "**State:** closed (2026-10-20)")
        self.assertEqual(focus.check(closed, 60), [])


class LevelTest(unittest.TestCase):
    def test_level_line(self):
        self.assertIn("level None", focus.check(GOOD.replace("**Level:** line\n", ""), 60)[0])
        self.assertIn("level 'team'", focus.check(GOOD.replace("line\n", "team\n", 1), 60)[0])

    def test_epic_needs_lines(self):
        epic = GOOD.replace("**Level:** line", "**Level:** epic")
        self.assertIn('without a "**Lines:**"', focus.check(epic, 60)[0])
        self.assertEqual(focus.check(epic.replace("**State:**", "**Lines:** ui, data\n**State:**"),
                                     60), [])


class AnchorDirTest(unittest.TestCase):
    """focus/PROJECT.md, focus/epics/*.md, focus/lines/*.md."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name
        os.mkdir(os.path.join(self.root, "epics"))
        os.mkdir(os.path.join(self.root, "lines"))

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, level, state, objective, lines=None):
        text = GOOD.replace("**Level:** line", f"**Level:** {level}").replace(
            "**State:** active", f"**State:** {state}").replace("ship A", objective)
        if lines:
            text = text.replace("**State:**", f"**Lines:** {lines}\n**State:**")
        with open(os.path.join(self.root, rel), "w") as f:
            f.write(text)

    def project(self):
        self.write("PROJECT.md", "project", "active", "project goal")
        self.write("lines/ui.md", "line", "active", "ui mandate")
        self.write("lines/data.md", "line", "active", "data mandate")
        self.write("epics/payments.md", "epic", "closed (2026-10-14)", "ship payments", "ui, data")
        self.write("epics/notif.md", "epic", "active", "ship notif", "ui, data")
        self.write("epics/infra.md", "epic", "active", "move infra", "data")

    def test_line_gets_project_its_active_epics_and_itself(self):
        self.project()
        out = focus.anchor(self.root, "ui")
        for want in ("project goal", "ship notif", "ui mandate"):
            self.assertIn(want, out)
        for unwanted in ("ship payments", "move infra", "data mandate", "[focus]", "## Log"):
            self.assertNotIn(unwanted, out)
        self.assertLess(out.index("project goal"), out.index("ship notif"))
        self.assertLess(out.index("ship notif"), out.index("ui mandate"))

    def test_line_in_two_active_epics_warns(self):
        self.project()
        out = focus.anchor(self.root, "data")
        self.assertTrue(out.startswith("[focus] 2 active epics involve line 'data'"))
        self.assertIn("ship notif", out)
        self.assertIn("move infra", out)

    def test_no_line_with_several_lines_warns(self):
        self.project()
        out = focus.anchor(self.root, None)
        self.assertTrue(out.startswith("[focus] 2 active lines (data, ui)"))
        self.assertNotIn("ui mandate", out)
        self.assertIn("move infra", out)

    def test_single_line_is_picked_by_itself(self):
        self.write("PROJECT.md", "project", "active", "project goal")
        self.write("lines/ui.md", "line", "active", "ui mandate")
        self.write("epics/notif.md", "epic", "active", "ship notif", "ui")
        out = focus.anchor(self.root, None)
        self.assertNotIn("[focus]", out)
        self.assertIn("ui mandate", out)

    def test_missing_files_warn_without_failing(self):
        out = focus.anchor(self.root, "ui")
        self.assertIn("no " + os.path.join(self.root, "PROJECT.md"), out)
        self.assertIn("no active epic involves line 'ui'", out)
        self.assertIn("line 'ui' has no focus yet", out)

    def test_check_dir_cross_file(self):
        self.project()
        self.write("epics/ghost.md", "epic", "paused", "x", "ui, mobile")
        flags = focus.check_dir(self.root, 60)
        self.assertTrue(any("line 'mobile' has no lines/mobile.md" in f for f in flags))
        self.assertTrue(any("data.md: 2 active epics" in f for f in flags))
        self.assertEqual(len(flags), 2, flags)

    def test_cli_line_from_env(self):
        self.project()
        os.environ["PM_LINE"] = "ui"
        try:
            code, out = run(["anchor", self.root])
        finally:
            del os.environ["PM_LINE"]
        self.assertEqual(code, 0)
        self.assertIn("ui mandate", out)


if __name__ == "__main__":
    unittest.main()
