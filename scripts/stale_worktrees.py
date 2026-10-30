#!/usr/bin/env python3
"""List the git worktrees a finished ticket left behind (Definition of Done, item 9).

    stale_worktrees.py [repo] [--base main]

For every worktree except the main one it prints one line:

    REMOVABLE  clean, and its HEAD is already in --base: `git worktree remove` loses nothing
    DIRTY      has uncommitted changes: save them (a patch, a commit) before removing it
    AHEAD      has commits outside --base: its branch survives the removal, but check them
    IN USE     a process runs with its cwd there (a dev server, an agent): leave it

Ignored files (node_modules, .next, .env) don't count as changes: they are regenerated, or are
copies of the main tree's. Nothing is removed here; the PM removes what the line says.

Exit code: 0 if no worktree is REMOVABLE, 1 if some is, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys


def git(repo: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)


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
    if git(path, "status", "--porcelain").stdout.strip():
        return "DIRTY"
    head = git(path, "rev-parse", "HEAD").stdout.strip()
    if git(repo, "merge-base", "--is-ancestor", head, base).returncode != 0:
        n = git(repo, "rev-list", "--count", f"{base}..{head}").stdout.strip()
        return f"AHEAD    ({n} commits outside {base})"
    return "REMOVABLE"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("repo", nargs="?", default=".")
    ap.add_argument("--base", default="main")
    args = ap.parse_args(argv)
    if git(args.repo, "rev-parse", "--verify", "-q", args.base).returncode != 0:
        print(f"error: {args.repo} is not a git repo with a branch {args.base!r}", file=sys.stderr)
        return 2
    busy = in_use()
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
