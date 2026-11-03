import contextlib
import io
import os
import subprocess
import sys
import tempfile
import time
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


class ReapTest(StaleWorktreesTest):
    """--reap: nothing that holds work is lost, and nothing recent or in use goes."""

    def age(self, path, hours):
        gitdir = subprocess.run(["git", "-C", path, "rev-parse", "--absolute-git-dir"],
                                capture_output=True, text=True).stdout.strip()
        old = time.time() - hours * 3600
        for f in ("HEAD", "index", os.path.join("logs", "HEAD")):
            if os.path.exists(os.path.join(gitdir, f)):
                os.utime(os.path.join(gitdir, f), (old, old))

    def branches(self):
        return subprocess.run(["git", "-C", self.repo, "branch", "--list", "conserva/*",
                               "--format=%(refname:short)"],
                              capture_output=True, text=True).stdout.split()

    def test_stale_ones_go_and_their_work_survives_as_a_branch(self):
        clean, dirty, recent, busy = (self.add(n) for n in ("clean", "dirty", "recent", "busy"))
        with open(os.path.join(dirty, "nuevo.txt"), "w") as f:
            f.write("trabajo sin commitear")
        os.makedirs(os.path.join(dirty, "node_modules"))  # ignored: never saved
        with open(os.path.join(dirty, "node_modules", "x"), "w") as f:
            f.write("x")
        for p in (clean, dirty, busy):
            self.age(p, 48)
        lines = sw.reap(self.repo, "main", {os.path.realpath(busy)}, older=24)

        self.assertFalse(os.path.exists(clean), lines)
        self.assertFalse(os.path.exists(dirty), lines)
        self.assertTrue(os.path.exists(recent), lines)
        self.assertTrue(os.path.exists(busy), lines)
        self.assertEqual(self.branches(), ["conserva/wt-dirty"])
        files = subprocess.run(["git", "-C", self.repo, "ls-tree", "-r", "--name-only",
                                "conserva/wt-dirty"], capture_output=True, text=True).stdout
        self.assertIn("nuevo.txt", files)
        self.assertNotIn("node_modules", files)
        self.assertEqual(sorted(os.path.realpath(p) for p in sw.worktrees(self.repo)),
                         sorted(os.path.realpath(p) for p in (recent, busy)))

    def test_ahead_detached_commits_stay_reachable_and_saves_never_overwrite(self):
        ahead = self.add("again")
        sh(ahead, "git", "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q",
           "--allow-empty", "-m", "solo aqui")
        sha = subprocess.run(["git", "-C", ahead, "rev-parse", "HEAD"], capture_output=True,
                             text=True).stdout.strip()
        sh(self.repo, "git", "branch", "conserva/wt-again", "main")  # an earlier save
        self.age(ahead, 48)
        lines = sw.reap(self.repo, "main", set(), older=24)
        self.assertFalse(os.path.exists(ahead), lines)
        self.assertIn(f"conserva/wt-again-{sha[:8]}", self.branches())
        self.assertIn("conserva/wt-again", self.branches())

    def test_dry_run_touches_nothing(self):
        dirty = self.add("dirty")
        with open(os.path.join(dirty, "x.txt"), "w") as f:
            f.write("x")
        self.age(dirty, 48)
        lines = sw.reap(self.repo, "main", set(), older=24, dry=True)
        self.assertTrue(os.path.exists(os.path.join(dirty, "x.txt")))
        self.assertEqual(self.branches(), [])
        self.assertTrue(lines[0].startswith("would remove"), lines)

    def test_idle_postgres_only_under_the_agents_root_without_clients(self):
        root = os.path.join(self.tmp.name, "opencode")
        dirs = {n: os.path.join(root, n, "pgdata") for n in ("idle", "client", "fresh")}
        for d in dirs.values():
            os.makedirs(os.path.join(d, "pg_wal"))
            open(os.path.join(d, "pg_wal", "000001"), "w").close()
        for n in ("idle", "client"):
            wal = os.path.join(dirs[n], "pg_wal", "000001")
            os.utime(wal, (time.time() - 48 * 3600,) * 2)
        outside = os.path.join(self.tmp.name, "mine", "pgdata")
        pg = "/opt/pg/bin/postgres"
        procs = [(10, 1, f"{pg} -D {dirs['idle']} -p 1"),
                 (11, 10, "postgres: checkpointer"),
                 (12, 10, "postgres: logical replication launcher"),
                 (20, 1, f"{pg} -D {dirs['client']} -p 2"),
                 (21, 20, "postgres: app_user portafolio [local] idle"),
                 (30, 1, f"{pg} -D {dirs['fresh']} -p 3"),
                 (40, 1, f"{pg} -D {outside} -p 4")]
        found = sw.idle_postgres(root, older=24, procs=procs)
        self.assertEqual(found, [("/opt/pg/bin/pg_ctl", dirs["idle"])])


if __name__ == "__main__":
    unittest.main()
