"""Candado 2026-11-04: el trinquete acepta solo los fallos conocidos, exige sacar los arreglados, no
deja crecer la lista y rechaza una suite que no terminó. Y el pre-push rechaza un commit que solo
compila con cambios sin commit del árbol compartido."""
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
import trinquete  # noqa: E402

HOOKS = os.path.join(HERE, "..", "hooks")


def node_log(fails):
    body = ["✔ always passes (1.2ms)"] + [f"✖ {f} (3.4ms)" for f in fails]
    summary = ["✖ failing tests:", ""] + [f"✖ {f} (3.4ms)" for f in fails] if fails else []
    return "\n".join(body + [f"ℹ tests {len(fails) + 1}", f"ℹ fail {len(fails)}"] + summary) + "\n"


class TrinqueteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lista = self.file("# comment\n\nA (4, static): old\nB: also old\n")

    def tearDown(self):
        self.tmp.cleanup()

    def file(self, text):
        fd, path = tempfile.mkstemp(dir=self.tmp.name)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def suite(self, log, lista=None, *extra):
        return trinquete.main(["suite", self.file(log), lista or self.lista, *extra])

    def test_exactly_the_known_failures_pass(self):
        self.assertEqual(self.suite(node_log(["A (4, static): old", "B: also old"])), 0)

    def test_a_new_failure_is_rejected_among_known_ones(self):
        self.assertEqual(self.suite(node_log(["A (4, static): old", "B: also old", "C: new"])), 1)

    def test_a_fixed_test_must_leave_the_list(self):
        self.assertEqual(self.suite(node_log(["A (4, static): old"])), 1)

    def test_a_suite_without_summary_is_rejected(self):
        self.assertEqual(self.suite("Error: cannot load x.test.ts\n", self.file("")), 1)

    def test_the_list_can_shrink_but_not_grow(self):
        parent = self.file("A (4, static): old\nB: also old\n")
        self.assertEqual(trinquete.main(["list", self.file("A (4, static): old\n"), parent]), 0)
        self.assertEqual(trinquete.main(["list", self.file("A (4, static): old\nC: new\n"), parent]), 1)
        self.assertEqual(trinquete.main(["list", self.file("C: new\n"), os.path.join(self.tmp.name, "absent")]), 1)

    def test_other_runners_through_regexes(self):
        log = "FAILED tests/test_x.py::test_a\n==== 1 failed, 3 passed in 0.12s ====\n"
        args = ["--fail-re", r"^FAILED (\S+)", "--summary-re", r"^=+ .* in [0-9.]+s"]
        self.assertEqual(self.suite(log, self.file("tests/test_x.py::test_a\n"), *args), 0)
        self.assertEqual(self.suite(log, self.file(""), *args), 1)


class PrePushTest(unittest.TestCase):
    def git(self, cwd, *args):
        r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=cwd,
                           capture_output=True, text=True)
        return r.returncode, r.stdout + r.stderr

    def test_a_commit_that_builds_only_with_uncommitted_changes_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            remote, local = os.path.join(tmp, "r"), os.path.join(tmp, "l")
            self.git(tmp, "init", "-q", "--bare", remote)
            self.git(tmp, "init", "-q", "-b", "main", local)
            self.git(local, "config", "core.hooksPath", os.path.abspath(HOOKS))
            # The «build»: every name a.txt asks for must be defined in b.txt.
            self.git(local, "config", "prepush.cmd", "grep -qxFf a.txt b.txt")
            self.git(local, "remote", "add", "origin", remote)
            for name, text in {"a.txt": "f\n", "b.txt": "f\n"}.items():
                with open(os.path.join(local, name), "w") as fh:
                    fh.write(text)
            self.git(local, "add", ".")
            self.git(local, "commit", "-qm", "base")
            with open(os.path.join(local, "b.txt"), "w") as fh:
                fh.write("f\ng\n")
            with open(os.path.join(local, "a.txt"), "w") as fh:
                fh.write("g\n")
            self.git(local, "commit", "-q", "--only", "a.txt", "-m", "uses g")
            code, out = self.git(local, "push", "-q", "origin", "main")
            self.assertNotEqual(code, 0, out)
            self.assertIn("does not build in a clean worktree", out)
            self.assertNotEqual(self.git(remote, "rev-parse", "--verify", "-q", "main")[0], 0)
            self.git(local, "commit", "-q", "--only", "b.txt", "-m", "defines g")
            code, out = self.git(local, "push", "-q", "origin", "main")
            self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
