#!/usr/bin/env python3
"""Check the evidence blocks of an agent report.

A report pastes evidence as fenced code blocks where each command line starts with "$ " and the
lines after it are that command's output. A "$ " line right before the fence also counts:

    ```
    $ git diff --stat
     app.py | 4 ++--
    ```

This script:
  * lists every command it finds (default);
  * flags signs of hand editing: an ellipsis inside a block ("..." or "…"), and blocks with output
    but no "$ command" line;
  * with --run, re-runs each command and compares its output with the pasted one.

--run executes commands written by someone else. Read them first (run without --run), and use
--only to run a subset.

Exit code: 0 if nothing was flagged and every re-run matched, 1 otherwise, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import difflib
import re
import subprocess
import sys
from dataclasses import dataclass, field

FENCE = re.compile(r"^\s*(```|~~~)")
# "..." not part of a longer run of dots (unittest prints "......"), or the single-char ellipsis.
ELLIPSIS = re.compile(r"(?<!\.)\.\.\.(?!\.)|…")
# unittest -v prints "test_x (module.Class.test_x) ... ok"; that "..." is real output.
UNITTEST_VERBOSE = re.compile(r"\) \.\.\.( |$)")


@dataclass
class Command:
    line: int                      # 1-based line of the "$ " line in the report
    cmd: str
    expected: list[str] = field(default_factory=list)


@dataclass
class Block:
    start: int                     # 1-based line of the opening fence
    lines: list[tuple[int, str]]   # (line number, text) inside the fences
    commands: list[Command] = field(default_factory=list)


def parse(text: str) -> list[Block]:
    blocks: list[Block] = []
    cur: Block | None = None
    before: tuple[int, str] | None = None   # last non-blank line outside a block
    for n, raw in enumerate(text.splitlines(), 1):
        if FENCE.match(raw):
            if cur is None:
                cur = Block(start=n, lines=[])
                # Also accepted: "$ command" on the line just before the fence.
                if before and before[1].startswith("$ "):
                    cur.lines.append(before)
                before = None
            else:
                blocks.append(cur)
                cur = None
            continue
        if cur is not None:
            cur.lines.append((n, raw))
        elif raw.strip():
            before = (n, raw)
    if cur is not None:            # unclosed fence: keep it, it is still evidence
        blocks.append(cur)

    for b in blocks:
        pending: Command | None = None
        continuing = False
        for n, raw in b.lines:
            if continuing and pending is not None:
                pending.cmd += "\n" + raw
                continuing = raw.rstrip().endswith("\\")
                continue
            if raw.startswith("$ "):
                pending = Command(line=n, cmd=raw[2:])
                b.commands.append(pending)
                continuing = raw.rstrip().endswith("\\")
            elif pending is not None:
                pending.expected.append(raw)
    return blocks


def static_flags(blocks: list[Block]) -> list[str]:
    flags = []
    for b in blocks:
        if not b.commands and any(t.strip() for _, t in b.lines):
            flags.append(f"line {b.start}: block has output but no '$ command' line")
        for n, t in b.lines:
            if ELLIPSIS.search(UNITTEST_VERBOSE.sub(") ", t)):
                flags.append(f"line {n}: ellipsis inside a block: {t.strip()[:100]}")
    return flags


def normalize(lines: list[str], masks: list[re.Pattern]) -> list[str]:
    out = []
    for ln in lines:
        ln = ln.rstrip()
        for m in masks:
            ln = m.sub("<MASK>", ln)
        out.append(ln)
    while out and not out[-1]:
        out.pop()
    return out


def rerun(c: Command, cwd: str | None, timeout: int, masks: list[re.Pattern]) -> tuple[bool, str]:
    try:
        p = subprocess.run(["bash", "-c", c.cmd], cwd=cwd, capture_output=True, text=True,
                           timeout=timeout)
        got = (p.stdout + p.stderr).splitlines()
    except subprocess.TimeoutExpired:
        return False, f"timeout after {timeout}s"
    exp, act = normalize(c.expected, masks), normalize(got, masks)
    if exp == act:
        return True, ""
    diff = difflib.unified_diff(exp, act, "report", "re-run", lineterm="", n=1)
    return False, "\n".join(diff)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("report")
    ap.add_argument("--run", action="store_true", help="re-run the commands and compare outputs")
    ap.add_argument("--only", type=int, nargs="+", metavar="N",
                    help="with --run, only these commands (numbers from the listing)")
    ap.add_argument("--cwd", help="working directory for --run")
    ap.add_argument("--timeout", type=int, default=600, help="seconds per command (default 600)")
    ap.add_argument("--mask", action="append", default=[], metavar="REGEX",
                    help="replace matches with <MASK> on both sides before comparing, e.g. "
                         r"'in [0-9.]+s' for test timings; repeatable")
    a = ap.parse_args(argv)

    try:
        with open(a.report, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"cannot read {a.report}: {e}", file=sys.stderr)
        return 2
    blocks = parse(text)
    commands = [c for b in blocks for c in b.commands]
    masks = [re.compile(m) for m in a.mask]

    print(f"{len(blocks)} blocks, {len(commands)} commands")
    for i, c in enumerate(commands, 1):
        lines = c.cmd.splitlines()
        more = " (continues)" if len(lines) > 1 else ""
        print(f"  [{i}] line {c.line}: $ {lines[0]}{more}  ({len(c.expected)} output lines)")

    bad = 0
    flags = static_flags(blocks)
    if flags:
        print("\nflags:")
        for f in flags:
            print("  " + f)
        bad += len(flags)

    if a.run:
        print("\nre-run:")
        for i, c in enumerate(commands, 1):
            if a.only and i not in a.only:
                continue
            ok, detail = rerun(c, a.cwd, a.timeout, masks)
            print(f"  [{i}] {'MATCH' if ok else 'DIFFERS'}")
            if not ok:
                bad += 1
                print("    " + detail.replace("\n", "\n    "))

    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
