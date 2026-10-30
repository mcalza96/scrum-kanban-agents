import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import stale_worktrees as sw  # noqa: E402


def sh(cwd, *args):
    subprocess.run(args, cwd=cwd, check=True, capture_output=True)


class StaleWorktreesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.repo)
        sh(self.repo, "git", "init", "-q", "-b", "main")
        sh(self.repo, "git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
           "--allow-empty", "-m", "base")
        with open(os.path.join(self.repo, ".gitignore"), "w") as f:
            f.write("node_modules/\n")
        sh(self.repo, "git", "add", ".gitignore")
        sh(self.repo, "git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "-m", "ig")

    def tearDown(self):
        self.tmp.cleanup()

    def add(self, name, branch=None):
        path = os.path.join(self.tmp.name, name)
        args = ["git", "worktree", "add", "-q"] + (["-b", branch] if branch else ["--detach"])
        sh(self.repo, *args, path, "main")
        return path

    def state(self, path, busy=frozenset()):
        return sw.classify(self.repo, path, "main", set(busy))

    def test_each_state(self):
        clean = self.add("clean")
        os.makedirs(os.path.join(clean, "node_modules"))  # ignored: still removable
        self.assertEqual(self.state(clean), "REMOVABLE")

        dirty = self.add("dirty")
        with open(os.path.join(dirty, "x.txt"), "w") as f:
            f.write("x")
        self.assertEqual(self.state(dirty), "DIRTY")

        ahead = self.add("ahead", branch="feat")
        sh(ahead, "git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
           "--allow-empty", "-m", "new")
        self.assertTrue(self.state(ahead).startswith("AHEAD    (1 commits"))

        busy = {os.path.join(os.path.realpath(clean), "sub")}
        self.assertEqual(self.state(clean, busy), "IN USE")

    def test_main_worktree_is_never_listed_and_exit_code(self):
        self.assertEqual(sw.worktrees(self.repo), [])
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(sw.main([self.repo]), 0)
            self.add("clean")
            self.assertEqual(sw.main([self.repo]), 1)
            self.assertEqual(sw.main([self.repo, "--base", "nope"]), 2)


if __name__ == "__main__":
    unittest.main()
