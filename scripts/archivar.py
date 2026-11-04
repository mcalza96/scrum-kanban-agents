#!/usr/bin/env python3
"""Move what is done out of the files agents read every turn. It moves, never deletes.

    archivar.py <repo>              archive, one line per move
    archivar.py <repo> --dry-run    say what it would move, touch nothing

- Mailboxes `inbox/<name>.md`: rows already read whose read stamp (else sent) is older than
  INBOX_HOURS go to `inbox/archivo/<name>-AAAA-MM.md`, by the month they were sent. An unread row
  never moves, and neither does the newest row: `send` numbers from the highest id in the file, so
  keeping it keeps ids from being handed out twice.
- `inbox/.wakes.log`: lines older than WAKES_DAYS go to `inbox/archivo/wakes-AAAA-MM.log`.
- `epic-*/STATUS.md`: `closed` rows go to `STATUS-archivo.md` next to it, except the KEEP_CLOSED
  most recently updated, which stay as context.
- `focus/**/*.md`: directives whose status starts with `executed` or `superseded` move from the
  table above `## Archived` to the table below it, which focus.py never loads.

The archive is written before the live file, and a row already in the archive is not written
again, so a crash or a second run leaves a duplicate at worst, never a loss. Nothing is committed:
whoever commits a file commits its archive too. The sweep (adapters/opencode/wake.py --sweep) runs
it. Why (D-283, 2026-11-03): inbox/PO.md held 107 KB with no unread row, and agents read STATUS.md
555 times in 24 hours, most of it closed rows.
"""
from __future__ import annotations

import datetime
import glob
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import inbox  # noqa: E402

INBOX_HOURS = 48     # owner decision
WAKES_DAYS = 7
KEEP_CLOSED = 5
DONE = re.compile(r"^(executed|superseded)", re.IGNORECASE)
ROW = re.compile(r"^\|.*\|\s*$")
STAMP = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def cells(line: str) -> list[str]:
    return [c.strip() for c in inbox.CELL.split(line.strip()[1:-1])]


def append_new(path: str, lines: list[str], header: str, dry: bool) -> int:
    """Append the lines not already in path, creating it with header. Returns how many."""
    try:
        with open(path, encoding="utf-8") as f:
            have = set(f.read().splitlines())
    except FileNotFoundError:
        have = None
    new = [l for l in lines if l not in (have or set())]
    if new and not dry:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(("" if have is not None else header) + "\n".join(new) + "\n")
    return len(new)


def mailboxes(root: str, now: datetime.datetime, dry: bool) -> list[str]:
    done = []
    for name in inbox.mailboxes(root):
        def change(text: str, name=name):
            msgs = inbox.parse(text)
            top = max((int(m.id[2:]) for m in msgs), default=0)
            old = [m for m in msgs if not m.unread and int(m.id[2:]) != top
                   and (inbox.age_hours(m.read if STAMP.match(m.read) else m.sent, now) or 0)
                   > INBOX_HOURS]
            by_month: dict[str, list[str]] = {}
            for m in old:
                by_month.setdefault(m.sent[:7], []).append(m.row())
            for month, rows in by_month.items():
                path = os.path.join(root, "archivo", f"{name}-{month}.md")
                append_new(path, rows, inbox.HEADER.format(pm=f"{name} (archivo {month})"), dry)
            gone = {m.id for m in old}
            keep = [l for l in text.splitlines()
                    if not ((r := inbox.ROW.match(l)) and r.group(1) in gone)]
            return (text if dry else "\n".join(keep) + "\n"), len(old)

        if dry:
            with open(inbox.path_of(root, name), encoding="utf-8") as f:
                n = change(f.read())[1]
        else:
            n = inbox._locked_update(inbox.path_of(root, name), change)
        if n:
            done.append(f"{name}: {n} filas leídas a inbox/archivo/")
    return done


def wakes(root: str, now: datetime.datetime, dry: bool) -> list[str]:
    path = os.path.join(root, ".wakes.log")
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        return []
    def old(line: str) -> bool:
        age = inbox.age_hours(" ".join(line.split(" ")[:3]), now)
        return age is not None and age > WAKES_DAYS * 24
    moving = [l for l in lines if old(l)]
    if not moving:
        return []
    by_month: dict[str, list[str]] = {}
    for l in moving:
        by_month.setdefault(l[:7], []).append(l)
    for month, rows in by_month.items():
        append_new(os.path.join(root, "archivo", f"wakes-{month}.log"), rows, "", dry)
    if not dry:
        with open(path, encoding="utf-8") as f:
            late = f.read().splitlines()[len(lines):]  # a wake that logged meanwhile
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join([l for l in lines if not old(l)] + late) + "\n")
    return [f".wakes.log: {len(moving)} líneas a inbox/archivo/"]


def statuses(repo: str, dry: bool) -> list[str]:
    done = []
    for path in sorted(glob.glob(os.path.join(repo, "epic-*", "STATUS*.md"))):
        if path.endswith("-archivo.md"):
            continue
        with open(path, encoding="utf-8") as f:
            text = f.read()
        lines = text.splitlines()
        head = next((i for i, l in enumerate(lines)
                     if ROW.match(l) and ("State" in cells(l) or "Estado" in cells(l)) and i + 1 < len(lines)
                     and lines[i + 1].startswith("|---")), None)
        if head is None:
            continue
        cols = cells(lines[head])
        # admite State y Estado
        state_name = "State" if "State" in cols else ("Estado" if "Estado" in cols else None)
        if state_name is None:
            continue
        state, upd = cols.index(state_name), (cols.index("Updated") if "Updated" in cols else (cols.index("Actualizado") if "Actualizado" in cols else None))
        rows = [(i, cells(l)) for i, l in enumerate(lines) if i > head + 1 and ROW.match(l)]
        # avisa filas mal formadas sin moverlas (longitud distinta)
        for i, c in rows:
            if len(c) != len(cols):
                print(f"warn: {os.path.relpath(path, repo)}:{i+1} fila mal formada len {len(c)} != {len(cols)}: {lines[i][:80]}", file=sys.stderr)
        closed = [(i, c) for i, c in rows if len(c) == len(cols) and c[state].startswith("closed")]
        closed.sort(key=lambda r: (r[1][upd] if upd is not None else "", r[0]))
        moving = sorted(i for i, _ in closed[:max(0, len(closed) - KEEP_CLOSED)])
        if not moving:
            continue
        archive = os.path.join(os.path.dirname(path), "STATUS-archivo.md")
        header = (f"# Archivo de {os.path.basename(os.path.dirname(path))}\n\nFilas closed que "
                  f"salieron de STATUS.md. Nunca se editan.\n\n{lines[head]}\n{lines[head + 1]}\n")
        append_new(archive, [lines[i] for i in moving], header, dry)
        if not dry:
            gone = set(moving)
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(l for i, l in enumerate(lines) if i not in gone) + "\n")
        done.append(f"{os.path.relpath(path, repo)}: {len(moving)} filas closed a STATUS-archivo.md")
    return done


def directives(repo: str, dry: bool) -> list[str]:
    done = []
    for path in sorted(glob.glob(os.path.join(repo, "focus", "**", "*.md"), recursive=True)):
        if path.endswith("-archivo.md"):
            continue
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        end = next((i for i, l in enumerate(lines) if re.match(r"^##\s+Archived\s*$", l)), None)
        top = len(lines) if end is None else end
        moving = [i for i, l in enumerate(lines[:top])
                  if l.startswith("| D-") and DONE.match(cells(l)[-1])]
        if not moving:
            continue
        table = next((l for l in lines if l.startswith("| Id |")), "| Id | Kind | Scope | Human's words | Date | Status |")
        gone = set(moving)
        kept = [l for i, l in enumerate(lines[:top]) if i not in gone]
        rest = lines[top:] if end is not None else ["", "## Archived", "", table, "|" + "---|" * len(cells(table))]
        archived = set(rest)
        rest += [lines[i] for i in moving if lines[i] not in archived]
        if not dry:
            with open(path, "w", encoding="utf-8") as f:
                f.write("\n".join(kept + rest) + "\n")
        done.append(f"{os.path.relpath(path, repo)}: {len(moving)} directivas a ## Archived")
    return done


def archive(repo: str, now: datetime.datetime | None = None, dry: bool = False) -> list[str]:
    now = now or datetime.datetime.now().astimezone()
    root = os.path.join(repo, "inbox")
    done = []
    if os.path.isdir(root):
        done += mailboxes(root, now, dry) + wakes(root, now, dry)
    return done + statuses(repo, dry) + directives(repo, dry)


def main(argv: list[str]) -> int:
    if not argv or argv[0].startswith("-"):
        print(__doc__, file=sys.stderr)
        return 2
    print("\n".join(archive(os.path.abspath(argv[0]), dry="--dry-run" in argv)) or "nada que archivar")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
