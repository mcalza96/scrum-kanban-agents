#!/usr/bin/env python3
"""Print and check the PM focus files (see references/focus-log.md).

    focus.py hook focus/ < event.json   for Claude Code hooks: SessionStart, UserPromptSubmit,
                                        SessionEnd (the event's JSON on stdin)
    focus.py bind focus/ ui --session S open line "ui" for chat S, or add S to it
    focus.py anchor focus/ --line ui    the anchors the PM of line "ui" must hold
    focus.py check focus/               flag what lets an objective drift
    focus.py check focus/lines/ui.md    the same for one file

The focus folder holds one file per level, all from templates/FOCUS.md:

    focus/PROJECT.md        Level: project   the PO's objective and project-wide directives
    focus/epics/<epic>.md   Level: epic      an epic's objective, its Lines, cross-line contracts
    focus/lines/<line>.md   Level: line      one PM's thread: its mandate, directives, turn log,
                                             and the chats (Sessions) that ran it

`anchor` is what a session-start or post-compaction hook injects: the project anchor, the anchors of
the active epics that name the line in their "**Lines:**", and the line's own anchor (each anchor is
everything above "## Log"). The line comes from --line or $PM_LINE, else from the line whose
"**Sessions:**" lists the chat's session id. A chat on no line gets the project anchor and the index
of active lines, and the PM binds it with `bind` before anything else. `hook` drives this from
Claude Code's hooks: it loads a bound chat's anchors, keeps the line across /clear, and keeps
one-shot dev sessions (first prompt "TO: ...", or FOCUS_ROLE=dev) away from the focus files. See
hook() for each event. Problems come back as "[focus]" warnings with exit 0, so the hook never
blocks a session.

`check` flags, per file:
  * a missing or invalid "**Level:**" (project, epic, line) or "**State:**" (active, paused,
    closed (YYYY-MM-DD)), and an epic without "**Lines:**";
  * an anchor longer than --max-anchor-lines (it stops being read whole);
  * directive ids that repeat, kinds outside objective/constraint/nuance/decision, and
    "superseded by D-<n>" pointing at a directive that does not exist;
  * zero or several objectives in force;
  * log entries without an "Objective:" line, and entries whose objective changed without naming an
    `objective` directive.
On a folder it also flags epics naming a line with no file, lines in several active epics, a
session bound to two lines, and active lines whose last entry is older than --stale-days.

Exit code: 0 if nothing was flagged, 1 otherwise, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
import time
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
LINE_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
QUOTE = re.compile(r"«([^»]*)»")
ENTRY_DATE = re.compile(r"^###\s+(\d{4}-\d{2}-\d{2})")
PLACEHOLDER = "<the PO's exact words>"
DEV_PROMPT = re.compile(r"^\s*TO:", re.I)
SHELLS = {"sh", "bash", "zsh", "dash", "fish", "env", "python", "python3", "timeout"}
HANDOFF_TTL = 600  # seconds between the end of a chat on /clear and the start of its successor
DEV_NOTICE = """[focus] This session is a one-shot dev (its prompt starts with TO:). The ticket is your only
spec. Never read, bind, or write anything under {root}: lines of work and their focus files belong
to the PMs. Write your report, set your row to reported, and stop."""
LINE_SKELETON = """# Focus of line {name}

**Level:** line
**State:** active
**Sessions:** {session}

Read the anchor whole at the start of every session, after every compaction, and before every
ticket, verdict, or closing plan. Every directive is the PO's words. Only this line's PM writes
this file. See `references/focus-log.md`.

## Objective

> «{placeholder}» ({date}, D-1)

## Non-goals

## Directives in force

| Id | Kind | Scope | Human's words | Date | Status |
|---|---|---|---|---|---|
| D-1 | objective | line | «{placeholder}» | {date} | in force |

## Open questions to the human

## Log

Append-only. One entry per turn that changed something. Never rewrite past entries.

### {now} · — · line opened by chat {session}
- Objective: unchanged (D-1), pending: ask the PO and write their words as D-1

## Archived
"""


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


def sessions_of(text: str) -> list[str]:
    return [x.strip() for x in (header(text, "Sessions") or "").split(",") if x.strip()]


def objective_quote(text: str) -> str:
    anchor_lines, _ = split(text)
    body = "\n".join(anchor_lines)
    i = body.find("## Objective")
    m = QUOTE.search(body, i) if i >= 0 else None
    return m.group(1) if m else ""


def line_files(root: str) -> dict[str, str]:
    return {os.path.splitext(os.path.basename(f))[0]: f
            for f in sorted(glob.glob(os.path.join(root, "lines", "*.md")))}


def line_for_session(root: str, session: str) -> str | None:
    for name, f in line_files(root).items():
        if session in sessions_of(read(f) or ""):
            return name
    return None


def last_entry_date(text: str) -> str | None:
    dates = [m.group(1) for m in map(ENTRY_DATE.match, split(text)[1]) if m]
    return max(dates) if dates else None


def read(path: str) -> str | None:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError:
        return None


def anchor_text(path: str, text: str) -> str:
    return f"<!-- {path} -->\n" + "\n".join(split(text)[0]).rstrip()


def anchor(root: str, line: str | None = None, session: str | None = None) -> str:
    """Anchors a PM of `line` must hold: project, the line's active epics, the line.

    Without `line`, the line is the one whose "**Sessions:**" lists `session` (the chat). A chat
    bound to no line gets the project anchor and the index of active lines, to pick or open one.

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

    lines = line_files(root)
    active_lines = [n for n, f in lines.items() if state(read(f) or "") == "active"]
    if not line and session:
        line = line_for_session(root, session)
        if not line:
            warn.append(f"this chat (session {session}) has no line of work yet. Before anything "
                        "else, ask the PO which line it runs: a new one, or one below. Then run: "
                        f"focus.py bind {root} <line> --session {session}")
            index = ["Active lines:" if active_lines else "No active lines yet."]
            for n in active_lines:
                t = read(lines[n]) or ""
                goal = objective_quote(t)
                goal = f"«{goal}»" if goal and goal != PLACEHOLDER else "no objective yet"
                index.append(f"- {n} · last entry {last_entry_date(t) or '—'} · {goal}")
            return "\n\n".join([f"[focus] {w}" for w in warn] + parts + ["\n".join(index)])
    if not line and not session and len(active_lines) == 1:
        line = active_lines[0]

    epics = []
    for f in sorted(glob.glob(os.path.join(root, "epics", "*.md"))):
        t = read(f) or ""
        if state(t) == "active" and (not line or line in lines_of(t)):
            epics.append((f, t))
    if not line:
        warn.append(f"{len(active_lines)} active lines ({', '.join(active_lines) or 'none'}) and "
                    "this session does not say which one it runs: bind it (focus.py bind), or "
                    "set PM_LINE. Showing every active epic.")
    elif len(epics) > 1:
        warn.append(f"{len(epics)} active epics involve line {line!r}: "
                    + ", ".join(f for f, _ in epics) + ". Ask the PO which one comes first.")
    parts += [anchor_text(f, t) for f, t in epics]

    if line:
        f = lines.get(line, os.path.join(root, "lines", f"{line}.md"))
        t = read(f)
        if t is None:
            warn.append(f"no {f}: line {line!r} has no focus yet. Open it with focus.py bind.")
        else:
            if state(t) != "active":
                warn.append(f"line {line!r} is {state(t) or 'without state'}, not active.")
            if objective_quote(t) in ("", PLACEHOLDER):
                warn.append(f"line {line!r} has no objective yet: ask the PO and write their "
                            "words as D-1 before any ticket.")
            parts.append(anchor_text(f, t))
    return "\n\n".join([f"[focus] {w}" for w in warn] + parts)


def bind(root: str, line: str, session: str, now: datetime.datetime | None = None,
         after: str | None = None) -> str:
    """Bind a chat to a line: create lines/<line>.md, or add the chat to an existing line.

    `after` is the session this one replaces after /clear: the same chat, so no warning about
    earlier chats.
    """
    if not LINE_NAME.match(line):
        raise ValueError(f"line name {line!r}: use lowercase letters, digits and dashes")
    if os.environ.get("FOCUS_ROLE") == "dev" or os.path.exists(state_path(root, f"dev-{session}")):
        raise ValueError(f"session {session} is a one-shot dev: devs never open or join a line")
    now = now or datetime.datetime.now()
    stamp = now.strftime("%Y-%m-%d %H:%M")
    bound = line_for_session(root, session)
    if bound and bound != line:
        raise ValueError(f"session {session} is already bound to line {bound!r}")
    path = os.path.join(root, "lines", f"{line}.md")
    text = read(path)
    note = ""
    if text is None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = LINE_SKELETON.format(name=line, session=session, placeholder=PLACEHOLDER,
                                    date=now.strftime("%Y-%m-%d"), now=stamp)
        note = f"[focus] opened line {line!r} for this chat."
    elif session not in sessions_of(text):
        before = sessions_of(text)
        value = ", ".join(before + [session])
        if header(text, "Sessions") is None:
            text = re.sub(r"^(\*\*State:\*\*.*)$", rf"\1\n**Sessions:** {value}", text,
                          count=1, flags=re.M)
        else:
            text = re.sub(r"^\*\*Sessions:\*\*.*$", f"**Sessions:** {value}", text, count=1,
                          flags=re.M)
        what = f"continues {after} after /clear" if after else "joined"
        entry = (f"### {stamp} · — · chat {session} {what}\n"
                 "- Objective: unchanged (D-1)\n")
        text = add_entry(text, entry)
        note = (f"[focus] this chat continues line {line!r} after /clear (was {after})."
                if after else f"[focus] this chat joined line {line!r}.")
        if before and not after:
            note += (f" Earlier chats on it: {', '.join(before)}. If one is still open, close it:"
                     " a line has one writer.")
    else:
        note = f"[focus] this chat is already on line {line!r}."
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return note + "\n\n" + anchor(root, line)


def state_path(root: str, name: str) -> str:
    """A file under <root>/.state/: machine state, never committed."""
    return os.path.join(root, ".state", name)


def write_state(root: str, name: str, data: dict) -> None:
    os.makedirs(os.path.join(root, ".state"), exist_ok=True)
    ignore = state_path(root, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w", encoding="utf-8") as f:
            f.write("*\n")
    with open(state_path(root, name), "w", encoding="utf-8") as f:
        json.dump(data, f)


def parent(pid: int) -> tuple[int, str]:
    """(parent pid, command name of pid). Linux /proc, else ps (macOS)."""
    try:
        with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
            stat = f.read()
        name = stat[stat.index("(") + 1:stat.rindex(")")]
        return int(stat[stat.rindex(")") + 2:].split()[1]), name
    except (OSError, ValueError, IndexError):
        out = subprocess.run(["ps", "-o", "ppid=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, check=False).stdout.split(None, 1)
        if len(out) < 2:
            return 0, ""
        return int(out[0]), os.path.basename(out[1].strip())


def agent_pid() -> int:
    """The pid of the agent process that ran this hook: the first ancestor that is not a shell.

    The same chat keeps its process across /clear while its session id changes, so this pairs
    the SessionEnd of the old session with the SessionStart of the new one.
    """
    pid = os.getppid()
    for _ in range(8):
        ppid, name = parent(pid)
        if name and name not in SHELLS:
            return pid
        if ppid <= 1:
            break
        pid = ppid
    return os.getppid()


def hook(root: str, event: dict, now: float | None = None, agent: int | None = None) -> str:
    """What a Claude Code hook prints, by event. Never raises: a hook must not block a session.

    SessionStart       a bound chat gets its anchors; after /clear the new session takes over the
                       line of the old one; an unbound chat gets nothing yet (its first prompt
                       says whether it is a PM or a dev).
    UserPromptSubmit   an unbound chat: a prompt starting with TO: is a one-shot dev, which is
                       recorded and told to keep away from the focus files; any other prompt is a
                       PM, which gets the project and the index of lines until it binds.
    SessionEnd         on /clear, leave a handoff so the next session of the same chat keeps the
                       line.
    """
    now = time.time() if now is None else now
    name = event.get("hook_event_name") or "SessionStart"
    session = event.get("session_id") or None
    forced = os.environ.get("PM_LINE") or None
    try:
        agent = agent or agent_pid()
    except (OSError, ValueError):
        agent = 0
    if os.environ.get("FOCUS_ROLE") == "dev" and session:
        write_state(root, f"dev-{session}", {"since": now})
    if session and os.path.exists(state_path(root, f"dev-{session}")):
        return DEV_NOTICE.format(root=root) if name != "SessionEnd" else ""

    if name == "SessionEnd":
        line = session and line_for_session(root, session)
        if event.get("reason") == "clear" and line:
            write_state(root, f"clear-{agent}", {"line": line, "session": session, "time": now})
        return ""

    bound = forced or (session and line_for_session(root, session))
    if name == "SessionStart":
        if bound:
            return anchor(root, bound, session)
        if event.get("source") == "clear" and session:
            handoff = state_path(root, f"clear-{agent}")
            data = None
            try:
                with open(handoff, encoding="utf-8") as f:
                    data = json.load(f)
                os.remove(handoff)
            except (OSError, ValueError):
                pass
            if data and now - data.get("time", 0) <= HANDOFF_TTL:
                try:
                    return bind(root, data["line"], session, after=data.get("session"))
                except (ValueError, KeyError, OSError) as e:
                    return f"[focus] could not keep the line after /clear: {e}"
        if not session:
            return anchor(root, None, None)
        return ""

    if name == "UserPromptSubmit":
        if bound:
            return ""
        if DEV_PROMPT.match(event.get("prompt") or ""):
            write_state(root, f"dev-{session}", {"since": now})
            return DEV_NOTICE.format(root=root)
        return anchor(root, None, session)
    return ""


def add_entry(text: str, entry: str) -> str:
    """Append a log entry at the end of the "## Log" section."""
    lines = text.splitlines(keepends=True)
    start = next((i for i, l in enumerate(lines) if LOG_HEADING.match(l)), None)
    if start is None:
        return text.rstrip("\n") + "\n\n## Log\n\n" + entry
    end = next((i for i in range(start + 1, len(lines)) if SECTION.match(lines[i])), len(lines))
    while end > start + 1 and not lines[end - 1].strip():
        end -= 1
    return "".join(lines[:end]) + "\n" + entry + "\n" + "".join(lines[end:]).lstrip("\n")


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


def check_dir(root: str, max_anchor_lines: int, stale_days: int = 14,
              today: datetime.date | None = None) -> list[str]:
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
    today = today or datetime.date.today()
    owner: dict[str, str] = {}
    for name, f in line_files(root).items():
        t = texts.get(f, "")
        for sid in sessions_of(t):
            if sid in owner:
                flags.append(f"{f}: session {sid} is also bound to line {owner[sid]!r}")
            owner.setdefault(sid, name)
        last = last_entry_date(t)
        if state(t) == "active" and last:
            age = (today - datetime.date.fromisoformat(last)).days
            if age > stale_days:
                flags.append(f"{f}: active, but its last entry is {age} days old: ask the PO "
                             "whether to pause or close it")
    return flags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("anchor", help="print the anchors a PM must hold")
    a.add_argument("path", help="the focus folder (PROJECT.md, epics/, lines/), or one file")
    a.add_argument("--line", default=os.environ.get("PM_LINE") or None,
                   help="the line of work this PM runs (default: $PM_LINE)")
    a.add_argument("--session", help="the chat's session id: picks the line bound to it")
    h = sub.add_parser("hook", help="for Claude Code hooks (SessionStart, UserPromptSubmit, "
                                    "SessionEnd): reads the event's JSON on stdin")
    h.add_argument("path", help="the focus folder")
    b = sub.add_parser("bind", help="bind a chat to a line: open the line, or join it")
    b.add_argument("path", help="the focus folder")
    b.add_argument("line", help="the line's name: lowercase letters, digits and dashes")
    b.add_argument("--session", required=True, help="the chat's session id")
    c = sub.add_parser("check", help="flag what lets the objective drift")
    c.add_argument("file", help="the focus folder, or one file")
    c.add_argument("--max-anchor-lines", type=int, default=60)
    c.add_argument("--stale-days", type=int, default=14)
    args = ap.parse_args(argv)

    if args.cmd == "anchor":
        print(anchor(args.path, args.line or None, args.session))
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
    if args.cmd == "bind":
        try:
            print(bind(args.path, args.line, args.session))
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return 0
    if os.path.isdir(args.file):
        flags = check_dir(args.file, args.max_anchor_lines, args.stale_days)
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
