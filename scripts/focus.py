#!/usr/bin/env python3
"""Print and check a PM focus file (FOCUS.md, see references/focus-log.md).

    focus.py anchor epics/       print the anchor of the active epic: everything above "## Log"
    focus.py anchor FOCUS.md     the same for one file
    focus.py check FOCUS.md      flag what lets the objective drift

`anchor` is what a session-start or post-compaction hook should inject. Given a directory, it reads
<dir>/FOCUS.md and <dir>/*/FOCUS.md and prints the ones whose "**State:**" line says active; closed
and paused epics are skipped. With no active epic, two active epics, or a missing file, it prints a
warning and still exits 0, so the hook never blocks a session.

`check` flags:
  * a missing "**State:**" line, or a state other than active, paused, or closed (YYYY-MM-DD);
  * an anchor longer than --max-anchor-lines (it stops being read whole);
  * directive ids that repeat, kinds outside objective/constraint/nuance/decision, and
    "superseded by D-<n>" pointing at a directive that does not exist;
  * zero or several objectives in force;
  * log entries without an "Objective:" line, and entries whose objective changed without naming an
    `objective` directive.

Exit code: 0 if nothing was flagged, 1 otherwise, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from dataclasses import dataclass

KINDS = {"objective", "constraint", "nuance", "decision"}
LOG_HEADING = re.compile(r"^##\s+Log\s*$")
SECTION = re.compile(r"^##\s+")
ENTRY = re.compile(r"^###\s+")
DIRECTIVE_ID = re.compile(r"\bD-\d+\b")
SUPERSEDED = re.compile(r"superseded by (D-\d+)")
OBJECTIVE_LINE = re.compile(r"^\s*-\s*Objective:\s*(.*)$")
STATE_LINE = re.compile(r"^\*\*State:\*\*\s*(.*?)\s*$")
STATE_OK = re.compile(r"^(active|paused|closed \(\d{4}-\d{2}-\d{2}\))$")


@dataclass
class Directive:
    id: str
    kind: str
    status: str
    line: int


def split(text: str) -> tuple[list[str], list[str]]:
    """Return (anchor lines, log lines). The log ends at the next "## " section."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if LOG_HEADING.match(line):
            log = []
            for rest in lines[i + 1:]:
                if SECTION.match(rest):
                    break
                log.append(rest)
            return lines[:i], log
    return lines, []


def state(text: str) -> str | None:
    for line in text.splitlines():
        m = STATE_LINE.match(line)
        if m:
            return m.group(1)
    return None


def anchor(path: str) -> str:
    """The anchor of the active epic(s) under path, or a warning. Never raises for missing files."""
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "FOCUS.md"))
                       + glob.glob(os.path.join(path, "*", "FOCUS.md")))
    else:
        files = [path]
    texts = {}
    for f in files:
        try:
            with open(f, encoding="utf-8") as fh:
                texts[f] = fh.read()
        except OSError:
            pass
    active = {f: t for f, t in texts.items() if state(t) == "active"}
    if not active:
        seen = ", ".join(f"{f} ({state(t) or 'no state'})" for f, t in texts.items()) or "none"
        return (f"[focus] no active epic under {path} (found: {seen}). Ask the human for the "
                "objective of the next epic before opening any ticket.")
    parts = []
    if len(active) > 1:
        parts.append(f"[focus] {len(active)} epics are active: " + ", ".join(active)
                     + ". Ask the human which one to pause before the next ticket.")
    for f, t in active.items():
        if len(active) > 1:
            parts.append(f"<!-- {f} -->")
        parts.append("\n".join(split(t)[0]).rstrip())
    return "\n\n".join(parts)


def directives(lines: list[str]) -> list[Directive]:
    """Rows of the directives table: | Id | Kind | Scope | Human's words | Date | Status |."""
    found = []
    for n, line in enumerate(lines, 1):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) >= 6 and re.fullmatch(r"D-\d+", cells[0]):
            found.append(Directive(cells[0], cells[1], cells[-1], n))
    return found


def entries(lines: list[str]) -> list[tuple[str, list[str]]]:
    out: list[tuple[str, list[str]]] = []
    for line in lines:
        if ENTRY.match(line):
            out.append((line.lstrip("#").strip(), []))
        elif out:
            out[-1][1].append(line)
    return out


def check(text: str, max_anchor_lines: int) -> list[str]:
    flags = []
    st = state(text)
    if st is None:
        flags.append('no "**State:**" line (active, paused, or closed (YYYY-MM-DD))')
    elif not STATE_OK.match(st):
        flags.append(f"state {st!r}: expected active, paused, or closed (YYYY-MM-DD)")
    anchor, log = split(text)
    if not log and not any(LOG_HEADING.match(l) for l in text.splitlines()):
        flags.append('no "## Log" section')
    if len(anchor) > max_anchor_lines:
        flags.append(f"anchor is {len(anchor)} lines (max {max_anchor_lines}): archive superseded "
                     "directives, or the epic has too many open fronts (rule 7)")

    ds = directives(anchor)
    all_ids = {d.id for d in ds}
    ids: dict[str, int] = {}
    for d in ds:
        if d.id in ids:
            flags.append(f"line {d.line}: {d.id} repeats (first at line {ids[d.id]})")
        ids.setdefault(d.id, d.line)
        if d.kind not in KINDS:
            flags.append(f"line {d.line}: {d.id} has kind {d.kind!r}, expected one of "
                         + ", ".join(sorted(KINDS)))
        m = SUPERSEDED.search(d.status)
        if m and m.group(1) not in all_ids:
            flags.append(f"line {d.line}: {d.id} is superseded by {m.group(1)}, which does not exist")
    in_force = [d for d in ds if d.kind == "objective" and d.status == "in force"]
    if len(in_force) != 1:
        names = ", ".join(d.id for d in in_force) or "none"
        flags.append(f"{len(in_force)} objectives in force ({names}): exactly one is expected")

    objective_ids = {d.id for d in ds if d.kind == "objective"}
    for title, body in entries(log):
        lines = [OBJECTIVE_LINE.match(l) for l in body]
        lines = [m.group(1).strip() for m in lines if m]
        if not lines:
            flags.append(f"log entry {title!r}: no 'Objective:' line")
            continue
        value = lines[0]
        if value.lower().startswith("unchanged"):
            continue
        named = set(DIRECTIVE_ID.findall(value)) & objective_ids
        if not named:
            flags.append(f"log entry {title!r}: objective changed ({value!r}) without naming an "
                         "objective directive the human confirmed")
    return flags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchor", help="print the anchor of the active epic")
    a.add_argument("path", help="a folder of epics, or one FOCUS.md")
    c = sub.add_parser("check", help="flag what lets the objective drift")
    c.add_argument("file")
    c.add_argument("--max-anchor-lines", type=int, default=60)
    args = ap.parse_args(argv)

    if args.cmd == "anchor":
        print(anchor(args.path))
        return 0
    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    flags = check(text, args.max_anchor_lines)
    for flag in flags:
        print(f"FLAG {flag}")
    if not flags:
        print("ok")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
