#!/usr/bin/env python3
"""One mailbox per PM, so PMs can tell each other what they need to know (see references/inbox.md).

    inbox.py send inbox/ --to PM-ui --from PM-ui-2 "SIM-26 closed in 53577e73: SIM-29 is unblocked"
    inbox.py read inbox/ PM-ui           the unread messages of PM-ui, in full
    inbox.py ack inbox/ PM-ui M-3 M-4    mark them read, after acting on them
    inbox.py pending inbox/              one line per mailbox with unread messages, or nothing
    inbox.py check inbox/ --max-hours 4  flag messages unread for longer than that
    inbox.py hook inbox/ < event.json    for a Claude Code hook: `pending`, never fails

Each mailbox is inbox/<PM>.md, named like the `TO:` of that PM's prompts. A message is one row:

    | M-3 | PM-ui-2 | 2026-10-30 00:40 -0300 | SIM-26 closed in 53577e73 | unread |

The last cell is `unread` until the addressee runs `ack`, which writes the time it was read. A
message is a pointer plus one or two sentences: anything longer goes in a file the message names.

`pending` is what a runtime hook injects into every PM turn, so an unread message can't go
unnoticed; `ack` is the proof it was read, and `check` catches the mailbox nobody opens.

Exit code: 0 if nothing was flagged, 1 if `check` flagged something, 2 on usage errors.
"""
from __future__ import annotations

import argparse
import datetime
import fcntl
import glob
import json
import os
import re
import sys
from dataclasses import dataclass

HEADER = """# Inbox of {pm}

Messages to {pm}. Read the unread ones, act, then `inbox.py ack`. Never edit other rows.

| Id | From | Sent | Message | Read |
|---|---|---|---|---|
"""
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROW = re.compile(r"^\|\s*(M-\d+)\s*\|(.*)\|\s*$")
CELL = re.compile(r"(?<!\\)\|")  # a message may hold escaped pipes
FMT = "%Y-%m-%d %H:%M %z"


@dataclass
class Message:
    id: str
    sender: str
    sent: str
    text: str
    read: str

    @property
    def unread(self) -> bool:
        return self.read == "unread"

    def row(self) -> str:
        return f"| {self.id} | {self.sender} | {self.sent} | {self.text} | {self.read} |"


def now() -> str:
    return datetime.datetime.now().astimezone().strftime(FMT)


def path_of(root: str, pm: str) -> str:
    if not NAME.match(pm):
        raise ValueError(f"bad mailbox name {pm!r}: use the PM's name, e.g. PM-ui")
    return os.path.join(root, f"{pm}.md")


def parse(text: str) -> list[Message]:
    found = []
    for line in text.splitlines():
        m = ROW.match(line)
        if m:
            cells = [c.strip() for c in CELL.split(m.group(2))]
            if len(cells) == 4:
                found.append(Message(m.group(1), *cells))
    return found


def messages(root: str, pm: str) -> list[Message]:
    try:
        with open(path_of(root, pm), encoding="utf-8") as f:
            return parse(f.read())
    except FileNotFoundError:
        return []


def mailboxes(root: str) -> list[str]:
    return sorted(os.path.basename(p)[:-3] for p in glob.glob(os.path.join(root, "*.md")))


def _locked_update(path: str, change) -> str:
    """Apply change(text) -> (new_text, result) under an exclusive lock: PMs write concurrently."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        new, result = change(f.read())
        f.seek(0)
        f.truncate()
        f.write(new)
        return result


def send(root: str, to: str, sender: str, text: str, when: str | None = None) -> str:
    text = " ".join(text.split()).replace("|", "\\|")
    if not text:
        raise ValueError("empty message")
    if not NAME.match(sender):
        raise ValueError(f"bad sender {sender!r}")

    def change(old: str):
        old = old or HEADER.format(pm=to)
        ids = [int(m.id[2:]) for m in parse(old)]
        mid = f"M-{max(ids, default=0) + 1}"
        row = Message(mid, sender, when or now(), text, "unread").row()
        return old.rstrip("\n") + "\n" + row + "\n", mid

    return _locked_update(path_of(root, to), change)


def ack(root: str, pm: str, ids: list[str], when: str | None = None) -> list[str]:
    """Mark ids read; returns the ids that were not found or already read."""
    stamp = when or now()

    def change(old: str):
        wanted, out = set(ids), []
        for line in old.splitlines():
            m = ROW.match(line)
            if m and m.group(1) in wanted:
                msg = parse(line)[0]
                if msg.unread:
                    msg.read = stamp
                    line = msg.row()
                    wanted.discard(msg.id)
            out.append(line)
        return "\n".join(out) + "\n", sorted(wanted)

    path = path_of(root, pm)
    if not os.path.exists(path):
        return sorted(ids)
    return _locked_update(path, change)


def read(root: str, pm: str) -> str:
    unread = [m for m in messages(root, pm) if m.unread]
    if not unread:
        return f"{pm}: no unread messages."
    lines = [f"{m.id} · from {m.sender} · {m.sent}\n  {m.text.replace(chr(92) + '|', '|')}"
             for m in unread]
    ids = " ".join(m.id for m in unread)
    return "\n".join(lines) + f"\nAfter acting on them: inbox.py ack {root} {pm} {ids}"


def pending(root: str) -> str:
    out = []
    for pm in mailboxes(root):
        unread = [m for m in messages(root, pm) if m.unread]
        if unread:
            ids = ", ".join(m.id for m in unread)
            out.append(f"[inbox] {pm}: {len(unread)} unread ({ids}), oldest {unread[0].sent}. "
                       f"If you are {pm}, read them before anything else: "
                       f"inbox.py read {root} {pm}")
    return "\n".join(out)


def age_hours(sent: str, today: datetime.datetime) -> float | None:
    try:
        return (today - datetime.datetime.strptime(sent, FMT)).total_seconds() / 3600
    except ValueError:
        return None


def check(root: str, max_hours: float, today: datetime.datetime | None = None) -> list[str]:
    today = today or datetime.datetime.now().astimezone()
    flags = []
    for pm in mailboxes(root):
        seen = set()
        for m in messages(root, pm):
            if m.id in seen:
                flags.append(f"{pm}: {m.id} repeats")
            seen.add(m.id)
            if not m.unread:
                continue
            age = age_hours(m.sent, today)
            if age is None:
                flags.append(f"{pm}: {m.id} has an unreadable date {m.sent!r}")
            elif age > max_hours:
                flags.append(f"{pm}: {m.id} from {m.sender} unread for {age:.0f} h")
    return flags


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("send", help="write a message to another PM's mailbox")
    s.add_argument("root")
    s.add_argument("--to", required=True)
    s.add_argument("--from", dest="sender", required=True)
    s.add_argument("text")
    r = sub.add_parser("read", help="print a PM's unread messages")
    r.add_argument("root")
    r.add_argument("pm")
    a = sub.add_parser("ack", help="mark messages read, after acting on them")
    a.add_argument("root")
    a.add_argument("pm")
    a.add_argument("ids", nargs="+")
    p = sub.add_parser("pending", help="one line per mailbox with unread messages")
    p.add_argument("root")
    c = sub.add_parser("check", help="flag messages unread for too long")
    c.add_argument("root")
    c.add_argument("--max-hours", type=float, default=4)
    h = sub.add_parser("hook", help="for a Claude Code hook: prints `pending`, never fails")
    h.add_argument("root")
    args = ap.parse_args(argv)

    try:
        if args.cmd == "send":
            print(send(args.root, args.to, args.sender, args.text))
        elif args.cmd == "read":
            print(read(args.root, args.pm))
        elif args.cmd == "ack":
            missing = ack(args.root, args.pm, args.ids)
            if missing:
                print(f"not found or already read: {' '.join(missing)}", file=sys.stderr)
                return 2
            print("ok")
        elif args.cmd == "check":
            flags = check(args.root, args.max_hours)
            for f in flags:
                print(f"FLAG {f}")
            if not flags:
                print("ok")
            return 1 if flags else 0
        elif args.cmd == "pending":
            out = pending(args.root)
            if out:
                print(out)
        else:
            try:
                json.load(sys.stdin)
            except ValueError:
                pass
            try:
                out = pending(args.root)
            except Exception as e:  # a hook must never block the session
                out = f"[inbox] error: {e}"
            if out:
                print(out)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
