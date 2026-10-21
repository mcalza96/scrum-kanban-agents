import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import focus  # noqa: E402

GOOD = """# Focus of epic X

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
            self.assertTrue(out.startswith("# Focus of epic X"))
            self.assertNotIn("## Log", out)
            self.assertEqual(run(["check", f.name]), (0, "ok\n"))
        finally:
            os.unlink(f.name)
        code, out = run(["anchor", "/nonexistent/FOCUS.md"])
        self.assertEqual(code, 0)
        self.assertIn("not found", out)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(["check", "/nonexistent/FOCUS.md"])[0], 2)


if __name__ == "__main__":
    unittest.main()
