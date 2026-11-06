#!/usr/bin/env python3
"""List the git worktrees a finished ticket left behind (Definition of Done, item 9), or reap them.

    stale_worktrees.py [repo] [--base main]
    stale_worktrees.py [repo] --reap [--older-than 24] [--pg-root DIR] [--dry-run]

For every worktree except the main one it prints one line:

    REMOVABLE  clean, and its HEAD is already in --base: `git worktree remove` loses nothing
    DIRTY      has uncommitted changes: save them (a patch, a commit) before removing it
    AHEAD      has commits outside --base: its branch survives the removal, but check them
    IN USE     a process runs with its cwd there (a dev server, an agent): leave it

Ignored files (node_modules, .next, .env) don't count as changes: they are regenerated, or are
copies of the main tree's. Without --reap nothing is removed; the PM removes what the line says.

--reap does it, and is what the opencode sweep runs (`wake.py --sweep`):

- A worktree goes when nothing runs in it and git saw no activity in it (commit, add, checkout)
  for --older-than hours. A younger one stays, because its dev may come back to it between runs.
- Before a DIRTY or AHEAD one goes, its whole state (commits, tracked changes and untracked files,
  never ignored ones) is saved as branch `conserva/wt-<name>`, without touching any index.
  `git worktree add <dir> conserva/wt-<name>` brings it back. MISSING ones are pruned.
- A postgres an agent started under --pg-root (opencode's temp dir) with no client connected and
  no WAL written for --older-than hours is stopped with `pg_ctl stop -m fast`. Its data stays.

Why: in the project this comes from, measurement tickets kept a worktree per ticket for their
rebases and nobody removed it at close: 30 worktrees and 8 postgres held about 15 GB, one postgres
ran for two days after its worktree was gone, and DoD 9 alone depended on every agent remembering.

Exit code: 0 if no worktree is REMOVABLE (always 0 with --reap), 1 if some is, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import tempfile
import time

import dev_worktree

CLIENT = re.compile(r"\(\d+\)|\[local\]")
PG_ROOT = os.path.join(tempfile.gettempdir(), "opencode")  # where opencode agents start postgres


def git(repo: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          env={**os.environ, **env} if env else None)


def checked_git(repo: str, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
    result = git(repo, *args, env=env)
    if result.returncode:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result


def worktrees(repo: str) -> list[str]:
    out = git(repo, "worktree", "list", "--porcelain").stdout
    paths = [l[len("worktree "):] for l in out.splitlines() if l.startswith("worktree ")]
    return paths[1:]  # the first one is the main worktree


def in_use() -> set[str]:
    """Directories some process has as its cwd (lsof; empty if it is missing)."""
    try:
        out = subprocess.run(["lsof", "-a", "-d", "cwd", "-Fn"], capture_output=True,
                             text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    return {l[1:] for l in out.splitlines() if l.startswith("n")}


def classify(repo: str, path: str, base: str, busy: set[str]) -> str:
    real = os.path.realpath(path)
    if any(d == real or d.startswith(real + os.sep) for d in busy):
        return "IN USE"
    if not os.path.isdir(path):
        return "MISSING  (run `git worktree prune`)"
    # --no-optional-locks: looking must not refresh the index, or it would count as activity
    if checked_git(path, "--no-optional-locks", "status", "--porcelain").stdout.strip():
        return "DIRTY"
    head = checked_git(path, "rev-parse", "HEAD").stdout.strip()
    if git(repo, "merge-base", "--is-ancestor", head, base).returncode != 0:
        n = git(repo, "rev-list", "--count", f"{base}..{head}").stdout.strip()
        return f"AHEAD    ({n} commits outside {base})"
    return "REMOVABLE"


def last_activity(path: str) -> float:
    """Newest mtime of the worktree's HEAD, index and reflog: its last commit, add or checkout."""
    gitdir = checked_git(path, "rev-parse", "--absolute-git-dir").stdout.strip()
    files = [os.path.join(gitdir, f) for f in ("HEAD", "index", os.path.join("logs", "HEAD"))]
    return max((os.path.getmtime(f) for f in files if os.path.exists(f)), default=0.0)


def conserve(repo: str, path: str) -> str:
    """Save commits, tracked changes and untracked files as conserva/wt-<name>."""
    fd, index = tempfile.mkstemp()
    os.close(fd)
    try:
        env = {"GIT_INDEX_FILE": index}
        checked_git(path, "read-tree", "HEAD", env=env)
        checked_git(path, "add", "-A", env=env)
        tree = checked_git(path, "write-tree", env=env).stdout.strip()
    finally:
        os.unlink(index)
    commit = checked_git(path, "rev-parse", "HEAD").stdout.strip()
    if tree and tree != checked_git(path, "rev-parse", "HEAD^{tree}").stdout.strip():
        commit = checked_git(path, "commit-tree", tree, "-p", commit, "-m",
                     f"conserva: {os.path.basename(path)} sin commitear, antes de quitar el "
                     "worktree (stale_worktrees.py --reap)").stdout.strip()
    branch = f"conserva/wt-{os.path.basename(path)}"
    old = git(repo, "rev-parse", "-q", "--verify", f"refs/heads/{branch}").stdout.strip()
    if old and old != commit:  # an earlier save of another worktree by that name: keep both
        branch += f"-{commit[:8]}"
    if not tree or not commit or git(repo, "branch", "-f", branch, commit).returncode != 0:
        raise RuntimeError(f"could not save {path} as {branch}")
    return f"{branch} {commit[:8]}"


def reap(repo: str, base: str, busy: set[str], older: float, now: float | None = None,
         dry: bool = False) -> list[str]:
    now = time.time() if now is None else now
    done = []
    for path in worktrees(repo):
        if dev_worktree.metadata(path):
            done.append(f"kept {path}: entrega aislada pendiente de cierre verificado")
            continue
        try:
            active = last_activity(path) if os.path.isdir(path) else 0.0
            state = classify(repo, path, base, busy).split()[0]
        except RuntimeError as e:
            done.append(f"FAILED {path}: {e}")
            continue
        if state == "IN":
            done.append(f"kept {path}: in use")
            continue
        if state == "MISSING":
            done.append(f"pruned {path}")
            continue
        hours = (now - active) / 3600
        if hours < older:
            done.append(f"kept {path}: {state.lower()}, git activity {hours:.0f} h ago")
            continue
        saved = ""
        if state != "REMOVABLE":
            try:
                saved = " saved as " + (f"conserva/wt-{os.path.basename(path)}" if dry
                                        else conserve(repo, path))
            except RuntimeError as e:
                done.append(f"FAILED {path}: {e}")
                continue
        r = None if dry else git(repo, "worktree", "remove", "--force", path)
        if r is not None and r.returncode != 0:
            done.append(f"FAILED {path}: {r.stderr.strip()}")
            continue
        done.append(f"{'would remove' if dry else 'removed'} {path} ({state.lower()}){saved}")
    if not dry:
        git(repo, "worktree", "prune")
    return done


def ps() -> list[tuple[int, int, str]]:
    out = subprocess.run(["ps", "-axo", "pid=,ppid=,command="], capture_output=True,
                         text=True).stdout
    rows = []
    for line in out.splitlines():
        parts = line.split(None, 2)
        if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
            rows.append((int(parts[0]), int(parts[1]), parts[2]))
    return rows


def idle_postgres(pg_root: str, older: float, now: float | None = None,
                  procs: list[tuple[int, int, str]] | None = None) -> list[tuple[str, str]]:
    """(pg_ctl, datadir) of every postmaster under pg_root with no client and no recent WAL."""
    now = time.time() if now is None else now
    procs = ps() if procs is None else procs
    root = os.path.realpath(pg_root) + os.sep
    found = []
    for pid, _, cmd in procs:
        words = cmd.split()
        if os.path.basename(words[0]) != "postgres" or "-D" not in words[:-1]:
            continue
        datadir = words[words.index("-D") + 1]
        if not os.path.realpath(datadir).startswith(root):
            continue
        # a client backend is "postgres: <user> <db> 127.0.0.1(51234) idle" or "... [local] idle";
        # helpers ("checkpointer", "logical replication launcher") carry no host
        clients = [c for _, ppid, c in procs if ppid == pid and c.startswith("postgres: ")
                   and CLIENT.search(c)]
        wal = os.path.join(datadir, "pg_wal")
        written = max((os.path.getmtime(os.path.join(wal, f)) for f in os.listdir(wal)),
                      default=0.0) if os.path.isdir(wal) else 0.0
        if not clients and (now - written) / 3600 >= older:
            found.append((os.path.join(os.path.dirname(words[0]), "pg_ctl"), datadir))
    return found


def stop_postgres(pg_root: str, older: float, dry: bool = False) -> list[str]:
    done = []
    for pg_ctl, datadir in idle_postgres(pg_root, older):
        if dry:
            done.append(f"would stop postgres {datadir}")
            continue
        r = subprocess.run([pg_ctl, "-D", datadir, "stop", "-m", "fast", "-w", "-t", "60"],
                           capture_output=True, text=True)
        done.append(f"{'stopped' if r.returncode == 0 else 'FAILED to stop'} postgres {datadir}")
    return done


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--base", default="main")
    ap.add_argument("--reap", action="store_true")
    ap.add_argument("--older-than", type=float, default=24.0, metavar="HOURS")
    ap.add_argument("--pg-root", default=PG_ROOT)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    if git(args.repo, "rev-parse", "--verify", "-q", args.base).returncode != 0:
        print(f"error: {args.repo} is not a git repo with a branch {args.base!r}", file=sys.stderr)
        return 2
    busy = in_use()
    if args.reap:
        lines = reap(args.repo, args.base, busy, args.older_than, dry=args.dry_run)
        lines += stop_postgres(args.pg_root, args.older_than, dry=args.dry_run)
        print("\n".join(lines) or "nothing to reap")
        return 0
    removable = 0
    for path in worktrees(args.repo):
        state = classify(args.repo, path, args.base, busy)
        removable += state == "REMOVABLE"
        print(f"{state:<10} {path}")
    if removable:
        print(f"{removable} removable: git -C {args.repo} worktree remove <path> (branches stay)")
    return 1 if removable else 0


if __name__ == "__main__":
    sys.exit(main())
