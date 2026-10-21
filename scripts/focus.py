#!/usr/bin/env python3
"""Print and check a PM focus file (FOCUS.md, see references/focus-log.md).

    focus.py anchor FOCUS.md     print the anchor: everything above "## Log"
    focus.py check FOCUS.md      flag what lets the objective drift

`anchor` is what a session-start or post-compaction hook should inject. If the file is missing it
prints a warning instead of failing, so the hook never blocks a session.

`check` flags:
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
    a = sub.add_parser("anchor", help="print the anchor (everything above '## Log')")
    a.add_argument("file")
    c = sub.add_parser("check", help="flag what lets the objective drift")
    c.add_argument("file")
    c.add_argument("--max-anchor-lines", type=int, default=60)
    args = ap.parse_args(argv)

    try:
        with open(args.file, encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        if args.cmd == "anchor":
            print(f"[focus] {args.file} not found: there is no anchor for this epic yet. "
                  "Ask the human for the objective before the next ticket.")
            return 0
        print(f"error: {e}", file=sys.stderr)
        return 2

    if args.cmd == "anchor":
        print("\n".join(split(text)[0]).rstrip())
        return 0
    flags = check(text, args.max_anchor_lines)
    for flag in flags:
        print(f"FLAG {flag}")
    if not flags:
        print("ok")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
