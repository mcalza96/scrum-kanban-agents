#!/usr/bin/env python3
"""Candados tocados: the assertions a set of commits removes from test files.

    candados.py <repo> <sha>...     one "sha path: line" per removed assertion; exit 1 if any

A gate changes only with the human's OK (rule 4). A dev that rewrites an assertion to make its own
change pass weakens the gate it is judged by, and a commit message that says «sin debilitar
candados» is a claim, not evidence. This lists every removed assertion line, so the PM sees it
before closing: a rewrite that keeps the gate is justified in the report, anything else needs the
human's OK. A modified assertion counts as removed (its old line is gone).

Why: on 2026-11-04, 13 of the 21 commits that touched tests in one project removed assertion
lines, several of them under a message that said the gates were intact, and nobody checked.
"""
from __future__ import annotations

import re
import subprocess
import sys

TEST_PATH = re.compile(r"(^|/)(tests?/|__tests__/)|(^|/)test_[^/]*$|[._-](test|spec)\.[^/.]+$|_test\.[^/.]+$")
ASSERTION = re.compile(r"\b(assert\w*|expect)\s*[.(]|\.should\b|\bassert\s")


def removed(repo: str, shas: list[str]) -> list[tuple[str, str, str]]:
    """(sha, path, line) for every assertion line the commits remove from a test file."""
    out = []
    for sha in shas:
        try:
            diff = subprocess.run(["git", "-C", repo, "show", "--format=", "--unified=0", sha],
                                  capture_output=True, text=True, timeout=30).stdout
        except (OSError, subprocess.SubprocessError):
            continue
        path = None
        for line in diff.splitlines():
            if line.startswith("diff --git "):
                path = line.split(" a/", 1)[-1].split(" b/", 1)[0]
            elif line.startswith("-") and not line.startswith("---") and path and \
                    TEST_PATH.search(path) and ASSERTION.search(line):
                out.append((sha, path, line[1:].strip()))
    return out


def summary(found: list[tuple[str, str, str]], files: int = 3) -> str:
    """One line for a message: how many, and in which files."""
    paths = sorted({p for _, p, _ in found})
    more = f" y {len(paths) - files} más" if len(paths) > files else ""
    return f"{len(found)} assert borrados en {', '.join(paths[:files])}{more}"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[2], file=sys.stderr)
        return 2
    found = removed(argv[0], argv[1:])
    for sha, path, line in found:
        print(f"{sha} {path}: {line}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
