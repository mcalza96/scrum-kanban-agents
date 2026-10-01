import contextlib
import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import check_report as cr  # noqa: E402

REPORT = """# Report

```
$ echo hello
hello
$ printf 'a\\nb\\n'
a
b
```

Text outside blocks is ignored... even with ellipsis.

```
$ echo one \\
  two
one two
```
"""


def run_main(text, *args):
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write(text)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out):
            code = cr.main([f.name, *args])
    finally:
        os.unlink(f.name)
    return code, out.getvalue()


class Parse(unittest.TestCase):
    def test_commands_and_outputs(self):
        cmds = [c for b in cr.parse(REPORT) for c in b.commands]
        self.assertEqual([c.cmd for c in cmds][:2], ["echo hello", "printf 'a\\nb\\n'"])
        self.assertEqual(cmds[0].expected, ["hello"])
        self.assertEqual(cmds[1].expected, ["a", "b"])

    def test_line_continuation(self):
        cmds = [c for b in cr.parse(REPORT) for c in b.commands]
        self.assertEqual(cmds[2].cmd, "echo one \\\n  two")
        self.assertEqual(cmds[2].expected, ["one two"])

    def test_command_on_the_line_before_the_fence(self):
        cmds = [c for b in cr.parse("$ echo hi\n\n```\nhi\n```\n") for c in b.commands]
        self.assertEqual([(c.cmd, c.expected) for c in cmds], [("echo hi", ["hi"])])

    def test_prose_before_the_fence_is_not_a_command(self):
        blocks = cr.parse("Output:\n```\nhi\n```\n")
        self.assertEqual(blocks[0].commands, [])

    def test_unclosed_fence_is_kept(self):
        self.assertEqual(len(cr.parse("```\n$ echo x\nx\n")), 1)


class StaticFlags(unittest.TestCase):
    def test_clean_report_has_no_flags(self):
        self.assertEqual(cr.static_flags(cr.parse(REPORT)), [])

    def test_ellipsis_inside_block_is_flagged(self):
        flags = cr.static_flags(cr.parse("```\n$ cat f.py\ndef f(): ...\n```\n"))
        self.assertEqual(len(flags), 1)
        self.assertIn("ellipsis", flags[0])

    def test_unicode_ellipsis_is_flagged(self):
        self.assertEqual(len(cr.static_flags(cr.parse("```\n$ ls\na…\n```\n"))), 1)

    def test_unittest_dots_are_not_flagged(self):
        self.assertEqual(cr.static_flags(cr.parse("```\n$ t\n..........\nOK\n```\n")), [])

    def test_unittest_verbose_lines_are_not_flagged(self):
        text = "```\n$ t -v\ntest_a (m.C.test_a) ... ok\ntest_b (m.C.test_b) ...\nOK\n```\n"
        self.assertEqual(cr.static_flags(cr.parse(text)), [])

    def test_ellipsis_after_verbose_marker_is_still_flagged(self):
        text = "```\n$ t -v\ntest_a (m.C.test_a) ... ok, then ...\n```\n"
        self.assertEqual(len(cr.static_flags(cr.parse(text))), 1)

    def test_block_without_command_is_flagged(self):
        flags = cr.static_flags(cr.parse("```\n-rw-r--r-- 1 u u 17496 f.py\n```\n"))
        self.assertIn("no '$ command'", flags[0])


class Rerun(unittest.TestCase):
    def test_all_match(self):
        code, out = run_main(REPORT, "--run")
        self.assertEqual(code, 0, out)
        self.assertEqual(out.count("MATCH"), 3)

    def test_edited_output_differs(self):
        code, out = run_main("```\n$ echo hello\nhola\n```\n", "--run")
        self.assertEqual(code, 1)
        self.assertIn("DIFFERS", out)
        self.assertIn("-hola", out)
        self.assertIn("+hello", out)

    def test_mask_hides_volatile_parts(self):
        text = "```\n$ echo 'Ran 3 tests in 1.234s'\nRan 3 tests in 9.999s\n```\n"
        self.assertEqual(run_main(text, "--run")[0], 1)
        self.assertEqual(run_main(text, "--run", "--mask", r"in [0-9.]+s")[0], 0)

    def test_only_runs_the_selected_command(self):
        code, out = run_main("```\n$ echo a\nWRONG\n$ echo b\nb\n```\n", "--run", "--only", "2")
        self.assertEqual(code, 0, out)
        self.assertNotIn("[1] DIFFERS", out)

    def test_without_run_nothing_is_executed(self):
        with tempfile.TemporaryDirectory() as d:
            marker = os.path.join(d, "ran")
            code, _ = run_main(f"```\n$ touch {marker}\n```\n")
            self.assertEqual(code, 0)
            self.assertFalse(os.path.exists(marker))


if __name__ == "__main__":
    unittest.main()
