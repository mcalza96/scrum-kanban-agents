#!/usr/bin/env python3
"""Print and check the PM focus files (see references/focus-log.md).

    focus.py hook focus/ < event.json   for Claude Code's SessionStart hook (event JSON on stdin)
    focus.py anchor focus/ --line ui    the anchors the PM of line "ui" must hold
    focus.py check focus/               flag what lets an objective drift
    focus.py check focus/lines/ui.md    the same for one file
    focus.py claim SIM --by PM-ui       the next free SIM-<n>, never handed out twice
    focus.py claim MIG --by PM-x --files 'db/migrations/*.sql'   the next migration number

The focus folder holds one file per level, both from templates/FOCUS.md:

    focus/PROJECT.md        Level: project   the PO's objective and project-wide directives
    focus/lines/<line>.md   Level: line      one PM's mandate and standing directives

An epic's objective lives in its own EPIC.md (references/file-mode.md), not here.

`anchor` is what a session-start or post-compaction hook injects: the project anchor and, when the
line is known (--line or $PM_LINE), the line's anchor. Each anchor is everything above "## Archived"
(or above a legacy "## Log"). `hook` prints the anchors on SessionStart, which includes the start
after a compaction, and nothing on any other event. Problems come back as "[focus]" warnings with
exit 0, so the hook never blocks a session.

`check` flags, per file:
  * a missing or invalid "**Level:**" (project, line) or "**State:**" (active, paused,
    closed (YYYY-MM-DD));
  * an anchor longer than --max-anchor-lines or --max-anchor-bytes (it stops being read whole;
    one row can hold kilobytes, so lines alone don't measure it);
  * a directive row longer than MAX_ROW_CHARS: several decisions packed into one cell;
  * directive ids that repeat, kinds outside objective/constraint/nuance/decision, a status
    other than "in force", "superseded/suspended by D-<n>" or "executed (<report>)", "superseded by D-<n>"
    pointing at a directive that does not exist, and "executed" rows still in the anchor;
  * zero or several objectives in force.
On a folder it also flags active lines whose newest directive is older than --stale-days.

Exit code: 0 if nothing was flagged, 1 otherwise, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import sys
from dataclasses import dataclass

try:
    import gestion  # type: ignore
except ImportError:
    gestion = None  # type: ignore

KINDS = {"objective", "constraint", "nuance", "decision"}
END_OF_ANCHOR = re.compile(r"^##\s+(Archived|Log)\s*$")
SUPERSEDED = re.compile(r"superseded by (D-\d+)")
STATUS_OK = re.compile(r"^(in force\b|(superseded|suspended) by D-\d+|executed \(.+\))")
MAX_ROW_CHARS = 1500
STATE_OK = re.compile(r"^(active|paused|closed \(\d{4}-\d{2}-\d{2}\))$")
LEVELS = {"project", "line"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Directive:
    id: str
    kind: str
    date: str
    status: str
    line: int
    supersedes: str | None = None  # None: the table has no Supersedes column


def anchor_lines(text: str) -> list[str]:
    """The anchor: every line above "## Archived" (or a legacy "## Log")."""
    lines = text.splitlines()
    end = next((i for i, l in enumerate(lines) if END_OF_ANCHOR.match(l)), len(lines))
    return lines[:end]


def header(text: str, name: str) -> str | None:
    """Value of a "**Name:** value" line, or None."""
    m = re.search(rf"^\*\*{name}:\*\*\s*(.*?)\s*$", text, re.M)
    return m.group(1) if m else None


def state(text: str) -> str | None:
    return header(text, "State")


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def anchor_text(path: str, text: str) -> str:
    return f"<!-- {path} -->\n" + "\n".join(anchor_lines(text)).rstrip()


def anchor(root: str, line: str | None = None) -> str:
    """Anchors a PM of `line` must hold: the project, and the line if one is given.

    root is a folder with PROJECT.md and lines/*.md, or a single focus file. Never raises: problems
    come back as "[focus]" warnings, so a hook never blocks a session.
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
    if line:
        f = os.path.join(root, "lines", f"{line}.md")
        t = read(f)
        if t is None:
            warn.append(f"no {f}: line {line!r} has no focus yet. Copy templates/FOCUS.md there "
                        "and write the PO's words as D-1.")
        else:
            if state(t) != "active":
                warn.append(f"line {line!r} is {state(t) or 'without state'}, not active.")
            parts.append(anchor_text(f, t))
    return "\n\n".join([f"[focus] {w}" for w in warn] + parts)


def hook(root: str, event: dict) -> str:
    """What a Claude Code hook prints: the anchors on SessionStart, nothing otherwise."""
    if (event.get("hook_event_name") or "SessionStart") != "SessionStart":
        return ""
    return anchor(root, os.environ.get("PM_LINE") or None)


def directives(lines: list[str]) -> list[Directive]:
    """Rows of the directives table: | Id | Kind | Scope | Human's words | Date | Supersedes | Status |.

    Columns are read by the table's header, so a table without Supersedes (the older format)
    still parses; rows under no matching header are read as Date and Status in the last two."""
    found, cols = [], None
    for n, line in enumerate(lines, 1):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if cells and cells[0] == "Id":
            cols = cells
        elif len(cells) >= 6 and re.fullmatch(r"D-\d+", cells[0]):
            if cols and len(cells) == len(cols) and "Date" in cols and "Status" in cols:
                row = dict(zip(cols, cells))
                found.append(Directive(cells[0], cells[1], row["Date"], row["Status"], n,
                                       row.get("Supersedes")))
            else:
                found.append(Directive(cells[0], cells[1], cells[-2], cells[-1], n))
    return found


SUPERSEDES_NONE = {"—", "-"}


def supersedes_flags(ds: list[Directive], every: dict[str, Directive]) -> list[str]:
    """A new directive says which ones it makes obsolete, or "—" after checking there are none,
    and each one it names is marked superseded by it: the check runs when the row is written,
    not when someone remembers to look."""
    flags = []
    for d in ds:
        if d.supersedes is None:
            continue
        v = d.supersedes.strip()
        if not v:
            flags.append(f"line {d.line}: {d.id} has an empty Supersedes: write the ids it makes "
                         "obsolete, or — after checking the directives in force")
            continue
        if v in SUPERSEDES_NONE:
            continue
        for old in re.split(r"[,\s]+", v):
            if not re.fullmatch(r"D-\d+", old):
                flags.append(f"line {d.line}: {d.id} supersedes {old!r}: expected D-<n> ids or —")
            elif old not in every:
                flags.append(f"line {d.line}: {d.id} supersedes {old}, which does not exist")
            elif every[old].status != f"superseded by {d.id}":
                flags.append(f"line {d.line}: {d.id} supersedes {old}, which is still "
                             f"{every[old].status!r}: mark it superseded by {d.id} and move it "
                             "to ## Archived")
    return flags


def check(text: str, max_anchor_lines: int, max_anchor_bytes: int = 12000) -> list[str]:
    flags = []
    st = state(text)
    if st is None:
        flags.append('no "**State:**" line (active, paused, or closed (YYYY-MM-DD))')
    elif not STATE_OK.match(st):
        flags.append(f"state {st!r}: expected active, paused, or closed (YYYY-MM-DD)")
    level = header(text, "Level")
    if level == "epic":
        flags.append("level 'epic': the epic level is gone; its objective lives in the epic's "
                     "EPIC.md")
    elif level not in LEVELS:
        flags.append(f'level {level!r}: expected a "**Level:**" line with project or line')
    anchor = anchor_lines(text)
    if len(anchor) > max_anchor_lines:
        flags.append(f"anchor is {len(anchor)} lines (max {max_anchor_lines}): archive superseded "
                     "directives, or the work has too many open fronts (rule 7)")
    size = len("\n".join(anchor).encode())
    if size > max_anchor_bytes:
        flags.append(f"anchor is {size} bytes (max {max_anchor_bytes}): archive executed and "
                     "superseded directives; never summarize the human's words to fit")

    ds = directives(anchor)
    every = {d.id: d for d in directives(text.splitlines())}
    all_ids = set(every)
    if ds and all(d.supersedes is None for d in ds):
        flags.append("the directives table has no Supersedes column: add it before Status and "
                     "fill each row with the ids it makes obsolete, or — after checking")
    flags += supersedes_flags(ds, every)
    ids: dict[str, int] = {}
    for d in ds:
        if d.id in ids:
            flags.append(f"line {d.line}: {d.id} repeats (first at line {ids[d.id]})")
        ids.setdefault(d.id, d.line)
        if d.kind not in KINDS:
            flags.append(f"line {d.line}: {d.id} has kind {d.kind!r}, expected one of "
                         + ", ".join(sorted(KINDS)))
        if not STATUS_OK.match(d.status):
            flags.append(f"line {d.line}: {d.id} has status {d.status!r}, expected in force, "
                         "superseded or suspended by D-<n>, or executed (<report>)")
        if d.status.startswith(("executed", "superseded")):
            flags.append(f"line {d.line}: {d.id} is {d.status.split()[0]}: move it to ## Archived")
        if len(anchor[d.line - 1]) > MAX_ROW_CHARS:
            flags.append(f"line {d.line}: {d.id} row is {len(anchor[d.line - 1])} chars (max "
                         f"{MAX_ROW_CHARS}): one directive per row")
    for d in every.values():
        m = SUPERSEDED.search(d.status)
        if m and m.group(1) not in all_ids:
            flags.append(f"line {d.line}: {d.id} is superseded by {m.group(1)}, "
                         "which does not exist")
    in_force = [d for d in ds if d.kind == "objective" and d.status == "in force"]
    if len(in_force) != 1:
        names = ", ".join(d.id for d in in_force) or "none"
        flags.append(f"{len(in_force)} objectives in force ({names}): exactly one is expected")
    return flags


def newest_directive(text: str) -> str | None:
    dates = [d.date for d in directives(anchor_lines(text)) if DATE.match(d.date)]
    return max(dates) if dates else None


def check_dir(root: str, max_anchor_lines: int, stale_days: int = 14,
              today: datetime.date | None = None, max_anchor_bytes: int = 12000) -> list[str]:
    """check() on every focus file under root, plus stale active lines."""
    project = os.path.join(root, "PROJECT.md")
    files = ([project] if os.path.exists(project) else []) + sorted(
        glob.glob(os.path.join(root, "lines", "*.md")))
    flags = []
    today = today or datetime.date.today()
    for f in files:
        t = read(f) or ""
        flags += [f"{f}: {x}" for x in check(t, max_anchor_lines, max_anchor_bytes)]
        newest = newest_directive(t)
        if f != project and state(t) == "active" and newest:
            age = (today - datetime.date.fromisoformat(newest)).days
            if age > stale_days:
                flags.append(f"{f}: active, but its newest directive is {age} days old: ask the "
                             "PO whether it is still active, paused, or closed")
    flags += repeated_ids(root, files)
    return flags


# Project-local list of ids that already repeated across files before ids had to be unique.
# One id per line; `#` starts a comment. New ids never go here: renumber instead.
REPEATED_OK = ".d-repetidos"


def repeated_ids(root: str, files: list[str]) -> list[str]:
    """A D-<n> is the PO's words once: the same id in two files makes `superseded by` ambiguous."""
    where: dict[str, list[str]] = {}
    for f in files:
        for d in directives((read(f) or "").splitlines()):
            where.setdefault(d.id, []).append(f)
    allowed = {line.split("#")[0].strip() for line in
               (read(os.path.join(root, REPEATED_OK)) or "").splitlines()}
    return [f"{ident} is used in {', '.join(sorted(set(fs)))}: renumber the newer one to the "
            "next free id across the focus folder"
            for ident, fs in sorted(where.items()) if len(set(fs)) > 1 and ident not in allowed]


# Append-only ledger of claimed ids, one "PREFIX<TAB>n<TAB>by<TAB>date" per line, kept under the
# focus folder. Every id a PM hands out (D-<n>, SIM-<n>, a migration number) goes through claim().
CLAIMS = ".ids"
SKIP_DIRS = {".git", "node_modules", ".next"}


def used_numbers(repo: str, prefix: str, pattern: str | None) -> set[int]:
    """Numbers already taken: by files under repo, by focus directives (D), and by the ledger."""
    gr = gestion.raiz_gestion(repo) if gestion else repo
    nums: set[int] = set()
    if pattern:  # e.g. db/migrations/*.sql: the number is the basename's leading digits
        for path in glob.glob(os.path.join(repo, pattern)):
            m = re.match(r"\d+", os.path.basename(path))
            if m:
                nums.add(int(m.group()))
    else:
        name = re.compile(rf"(?<![A-Za-z]){re.escape(prefix)}-(\d+)")
        for _, dirs, files in os.walk(repo):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            nums.update(int(n) for f in files for n in name.findall(f))
        if prefix == "D":
            focus_dir = os.path.join(gr, "focus")
            for f in [os.path.join(focus_dir, "PROJECT.md")] + glob.glob(
                    os.path.join(focus_dir, "lines", "*.md")):
                for d in directives((read(f) or "").splitlines()):
                    m = re.match(r"D-(\d+)", d.id)
                    if m:
                        nums.add(int(m.group(1)))
    for line in (read(os.path.join(gr, "focus", CLAIMS)) or "").splitlines():
        cols = line.split("\t")
        if len(cols) >= 2 and cols[0] == prefix and cols[1].isdigit():
            nums.add(int(cols[1]))
    return nums


def claim(repo: str, prefix: str, by: str, pattern: str | None = None) -> str:
    """Hand out the next free number for prefix, atomically across concurrent PMs."""
    import fcntl
    gr = gestion.raiz_gestion(repo) if gestion else repo
    ledger = os.path.join(gr, "focus", CLAIMS)
    os.makedirs(os.path.dirname(ledger), exist_ok=True)
    with open(ledger, "a+") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)  # released when the file closes
        n = max(used_numbers(repo, prefix, pattern), default=0) + 1
        fh.write(f"{prefix}\t{n}\t{by}\t{datetime.date.today().isoformat()}\n")
    return f"{n:03d}" if pattern else f"{prefix}-{n}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchor", help="print the anchors a PM must hold")
    a.add_argument("path", help="the focus folder (PROJECT.md, lines/), or one file")
    a.add_argument("--line", default=os.environ.get("PM_LINE") or None,
                   help="the line of work this PM runs (default: $PM_LINE)")
    h = sub.add_parser("hook", help="for Claude Code's SessionStart hook: reads the event's JSON "
                                    "on stdin")
    h.add_argument("path", help="the focus folder")
    c = sub.add_parser("check", help="flag what lets the objective drift")
    c.add_argument("file", help="the focus folder, or one file")
    c.add_argument("--max-anchor-lines", type=int, default=60)
    c.add_argument("--max-anchor-bytes", type=int, default=12000)
    c.add_argument("--stale-days", type=int, default=14)
    k = sub.add_parser("claim", help="hand out the next free id (D, SIM, GRU, a migration number)")
    k.add_argument("prefix", help="D, SIM, GRU, ... or a name for numbered files (with --files)")
    k.add_argument("--by", required=True, help="who claims it (PM-x)")
    k.add_argument("--repo", default=".", help="the project root (holds focus/)")
    k.add_argument("--files", help="glob of numbered files, e.g. 'db/migrations/*.sql'")
    args = ap.parse_args(argv)

    if args.cmd == "claim":
        print(claim(args.repo, args.prefix, args.by, args.files))
        return 0
    if args.cmd == "anchor":
        print(anchor(args.path, args.line or None))
        return 0
    if args.cmd == "hook":
        try:
            event = json.load(sys.stdin)
            event = event if isinstance(event, dict) else {}
        except ValueError:
            event = {}
        try:
            out = hook(args.path, event)
        except Exception as e:  # a hook must never block the session
            out = f"[focus] hook error: {e}"
        if out:
            print(out)
        return 0
    if os.path.isdir(args.file):
        flags = check_dir(args.file, args.max_anchor_lines, args.stale_days,
                          max_anchor_bytes=args.max_anchor_bytes)
    else:
        text = read(args.file)
        if text is None:
            print(f"error: cannot read {args.file}", file=sys.stderr)
            return 2
        flags = check(text, args.max_anchor_lines, args.max_anchor_bytes)
    for flag in flags:
        print(f"FLAG {flag}")
    if not flags:
        print("ok")
    return 1 if flags else 0


if __name__ == "__main__":
    sys.exit(main())
