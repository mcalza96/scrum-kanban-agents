#!/usr/bin/env python3
"""Print and check the PM focus files (see references/focus-log.md).

    focus.py anchor focus/ --line ui    the anchors the PM of line "ui" must hold
    focus.py check focus/               flag what lets an objective drift
    focus.py check focus/lines/ui.md    the same for one file

The focus folder holds one file per level, all from templates/FOCUS.md:

    focus/PROJECT.md        Level: project   the PO's objective and project-wide directives
    focus/epics/<epic>.md   Level: epic      an epic's objective, its Lines, cross-line contracts
    focus/lines/<line>.md   Level: line      one PM's thread: its mandate, directives, turn log

`anchor` is what a session-start or post-compaction hook injects: the project anchor, the anchors of
the active epics that name the line in their "**Lines:**", and the line's own anchor (each anchor is
everything above "## Log"). The line comes from --line or $PM_LINE; with a single active line it is
picked by itself. Problems come back as "[focus]" warnings with exit 0, so the hook never blocks a
session.

`check` flags, per file:
  * a missing or invalid "**Level:**" (project, epic, line) or "**State:**" (active, paused,
    closed (YYYY-MM-DD)), and an epic without "**Lines:**";
  * an anchor longer than --max-anchor-lines (it stops being read whole);
  * directive ids that repeat, kinds outside objective/constraint/nuance/decision, and
    "superseded by D-<n>" pointing at a directive that does not exist;
  * zero or several objectives in force;
  * log entries without an "Objective:" line, and entries whose objective changed without naming an
    `objective` directive.
On a folder it also flags epics naming a line with no file, and lines in several active epics.

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
STATE_OK = re.compile(r"^(active|paused|closed \(\d{4}-\d{2}-\d{2}\))$")
LEVELS = {"project", "epic", "line"}


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


def header(text: str, name: str) -> str | None:
    """Value of a "**Name:** value" line, or None."""
    m = re.search(rf"^\*\*{name}:\*\*\s*(.*?)\s*$", text, re.M)
    return m.group(1) if m else None


def state(text: str) -> str | None:
    return header(text, "State")


def lines_of(text: str) -> list[str]:
    return [x.strip() for x in (header(text, "Lines") or "").split(",") if x.strip()]


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def anchor_text(path: str, text: str) -> str:
    return f"<!-- {path} -->\n" + "\n".join(split(text)[0]).rstrip()


def anchor(root: str, line: str | None = None) -> str:
    """Anchors a PM of `line` must hold: project, the line's active epics, the line.

    root is a folder with PROJECT.md, epics/*.md and lines/*.md, or a single focus file. Never
    raises and never fails: problems come back as "[focus]" warnings, so a hook never blocks a
    session.
    """
    if not os.path.isdir(root):
        text = read(root)
        if text is None:
            return f"[focus] {root} not found. Ask the PO for the objective before any ticket."
        return anchor_text(root, text)

    warn, parts = [], []
    project = os.path.join(root, "PROJECT.md")
    text = read(project)
    if text is None:
        warn.append(f"no {project}: the project objective is not written down. Ask the PO for it.")
    else:
        parts.append(anchor_text(project, text))

    lines = {os.path.splitext(os.path.basename(f))[0]: f
             for f in sorted(glob.glob(os.path.join(root, "lines", "*.md")))}
    active_lines = [n for n, f in lines.items() if state(read(f) or "") == "active"]
    if not line and len(active_lines) == 1:
        line = active_lines[0]

    epics = []
    for f in sorted(glob.glob(os.path.join(root, "epics", "*.md"))):
        t = read(f) or ""
        if state(t) == "active" and (not line or line in lines_of(t)):
            epics.append((f, t))
    if not line:
        warn.append(f"{len(active_lines)} active lines ({', '.join(active_lines) or 'none'}) and "
                    "this session does not say which one it runs: set PM_LINE (or --line). "
                    "Showing every active epic.")
    elif not epics:
        warn.append(f"no active epic involves line {line!r}. Ask the PO what this line works on "
                    "before any ticket.")
    elif len(epics) > 1:
        warn.append(f"{len(epics)} active epics involve line {line!r}: "
                    + ", ".join(f for f, _ in epics) + ". Ask the PO which one comes first.")
    parts += [anchor_text(f, t) for f, t in epics]

    if line:
        f = lines.get(line, os.path.join(root, "lines", f"{line}.md"))
        t = read(f)
        if t is None:
            warn.append(f"no {f}: line {line!r} has no focus yet. Create it from the template.")
        else:
            if state(t) != "active":
                warn.append(f"line {line!r} is {state(t) or 'without state'}, not active.")
            parts.append(anchor_text(f, t))
    return "\n\n".join([f"[focus] {w}" for w in warn] + parts)


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
    level = header(text, "Level")
    if level not in LEVELS:
        flags.append(f'level {level!r}: expected a "**Level:**" line with project, epic, or line')
    if level == "epic" and not lines_of(text):
        flags.append('epic without a "**Lines:**" line naming the lines of work it involves')
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
            flags.append(f"line {d.line}: {d.id} is superseded by {m.group(1)}, "
                         "which does not exist")
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


def check_dir(root: str, max_anchor_lines: int) -> list[str]:
    """check() on every focus file under root, plus what only shows across files."""
    files = ([os.path.join(root, "PROJECT.md")] if os.path.exists(os.path.join(root, "PROJECT.md"))
             else []) + sorted(glob.glob(os.path.join(root, "epics", "*.md"))
                               + glob.glob(os.path.join(root, "lines", "*.md")))
    flags = []
    texts = {f: read(f) or "" for f in files}
    for f, t in texts.items():
        flags += [f"{f}: {x}" for x in check(t, max_anchor_lines)]
    known = {os.path.splitext(os.path.basename(f))[0]
             for f in glob.glob(os.path.join(root, "lines", "*.md"))}
    for f, t in texts.items():
        if header(t, "Level") == "epic":
            for name in lines_of(t):
                if name not in known:
                    flags.append(f"{f}: line {name!r} has no lines/{name}.md")
    for name in sorted(known):
        f = os.path.join(root, "lines", f"{name}.md")
        busy = [e for e, t in texts.items() if header(t, "Level") == "epic"
                and state(t) == "active" and name in lines_of(t)]
        if len(busy) > 1:
            flags.append(f"{f}: {len(busy)} active epics involve this line: " + ", ".join(busy))
    return flags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchor", help="print the anchors a PM must hold")
    a.add_argument("path", help="the focus folder (PROJECT.md, epics/, lines/), or one file")
    a.add_argument("--line", default=os.environ.get("PM_LINE") or None,
                   help="the line of work this PM runs (default: $PM_LINE)")
    c = sub.add_parser("check", help="flag what lets the objective drift")
    c.add_argument("file", help="the focus folder, or one file")
    c.add_argument("--max-anchor-lines", type=int, default=60)
    args = ap.parse_args(argv)

    if args.cmd == "anchor":
        print(anchor(args.path, args.line or None))
        return 0
    if os.path.isdir(args.file):
        flags = check_dir(args.file, args.max_anchor_lines)
    else:
        text = read(args.file)
        if text is None:
            print(f"error: cannot read {args.file}", file=sys.stderr)
            return 2
        flags = check(text, args.max_anchor_lines)
    for flag in flags:
        print(f"FLAG {flag}")
    if not flags:
        print("ok")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
