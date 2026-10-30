#!/usr/bin/env python3
"""Wake an idle opencode PM when its mailbox gets a message (see references/inbox.md).

    wake.py inbox/ PM-ui         run by the opencode plugin (inbox.js) when a mailbox changes

It finds the PM's session in opencode's database: the newest top-level session that ran
`inbox.py read <root> PM-ui`. It waits until that session is idle, then sends it one turn with
`opencode run --session` through the running service, so the turn shows live in the PM's window.

- At most CAP wakes per PM per hour. Past that the message waits for the human, and the log
  says so.
- One waker per PM at a time (a lock). It loops while new unread ids keep arriving, and never wakes
  for an id an earlier wake already announced (the log says which): a PM that read a message and
  is waiting on someone is left alone, and acks, which also change the mailbox, wake nobody.
- No session found, a session busy for more than WAIT seconds, or no unread left: it logs and
  stops. `inbox.py check` still catches what nobody read.

Every decision is one line in inbox/.wakes.log, which is also what the cap counts.
"""
from __future__ import annotations

import datetime
import fcntl
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../scripts"))
import inbox  # noqa: E402

CAP = 20         # wakes per PM per rolling hour (the owner raised it from 3, 2026-10-30)
WAIT = 30 * 60   # seconds to wait for a busy session before giving up
POLL = 20
DB = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
TEXT = ("Automatic wake: {pm} has unread messages ({ids}). Run "
        "`python3 {script} read {root} {pm}`, act on them as the skill says, and ack after acting. "
        "If something needs the owner's decision, stop and say it in one line.")


def log_path(root: str) -> str:
    return os.path.join(root, ".wakes.log")


def log(root: str, pm: str, what: str) -> None:
    stamp = datetime.datetime.now().astimezone().strftime(inbox.FMT)
    with open(log_path(root), "a", encoding="utf-8") as f:
        f.write(f"{stamp} {pm} {what}\n")


def wakes_last_hour(root: str, pm: str, now: datetime.datetime) -> int:
    n = 0
    try:
        with open(log_path(root), encoding="utf-8") as f:
            for line in f:
                parts = line.split(" ", 5)
                if len(parts) > 4 and parts[3] == pm and parts[4] == "woke":
                    age = inbox.age_hours(" ".join(parts[:3]), now)
                    n += age is not None and age < 1
    except FileNotFoundError:
        pass
    return n


def commands(data: str) -> list[str]:
    """The shell commands a stored message ran: tool inputs only, never their output, since
    `inbox.py pending` prints a read command for every mailbox."""
    try:
        content = json.loads(data).get("content") or []
    except (ValueError, AttributeError):
        return []
    found = []
    for c in content if isinstance(content, list) else []:
        cmd = ((c.get("state") or {}).get("input") or {}).get("command") if isinstance(c, dict) else None
        if isinstance(cmd, str):
            found.append(cmd)
    return found


def woken_ids(root: str, pm: str) -> set[str]:
    """Ids some earlier wake of pm already announced: never wake twice for the same message."""
    ids: set[str] = set()
    try:
        with open(log_path(root), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 6 and parts[3] == pm and parts[4] == "woke":
                    ids.update(parts[7:])
    except FileNotFoundError:
        pass
    return ids


def find_session(db: sqlite3.Connection, root: str, pm: str) -> str | None:
    """Newest top-level, unarchived session that ran `inbox.py read` on this mailbox."""
    read = re.compile(r"inbox\.py\s+read\s+(\S+)\s+" + re.escape(pm) + r"(?![\w.-])")
    real = os.path.realpath(root)
    rows = db.execute(
        "select m.session_id, s.directory, m.data from session_message m"
        " join session_v2 s on s.id = m.session_id"
        " where m.type = 'assistant' and s.parent_id is null and s.time_archived is null"
        " and m.data like ? order by m.time_created desc limit 500", (f"%inbox.py read %{pm}%",))
    for sid, cwd, data in rows:
        for cmd in commands(data):
            for path in read.findall(cmd):
                path = os.path.join(cwd, os.path.expanduser(path.strip("'\"")))
                if os.path.realpath(path) == real:
                    return sid
    return None


def busy(db: sqlite3.Connection, sid: str) -> bool:
    b, i = db.execute("select time_busy, time_idle from session_v2 where id = ?", (sid,)).fetchone()
    return (b or 0) > (i or 0)


def opencode() -> str:
    return shutil.which("opencode") or os.path.expanduser("~/.opencode/bin/opencode")


def run(root: str, pm: str, wait: float = WAIT, poll: float = POLL, cap: int = CAP,
        send=None) -> str:
    """Wake pm's session while new unread ids arrive. Returns the last decision logged."""
    send = send or (lambda sid, text: subprocess.run([opencode(), "run", "--session", sid, text],
                                                     stdout=subprocess.DEVNULL,
                                                     stderr=subprocess.DEVNULL))
    with open(os.path.join(root, f".wake-{pm}.lock"), "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return "another waker is on it"
        db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        try:
            return _loop(db, root, pm, wait, poll, cap, send)
        finally:
            db.close()


def _loop(db, root, pm, wait, poll, cap, send) -> str:
    while True:
        unread = [m.id for m in inbox.messages(root, pm) if m.unread]
        new = [i for i in unread if i not in woken_ids(root, pm)]
        if not new:
            what = "nothing new unread"
            break
        sid = find_session(db, root, pm)
        if not sid:
            what = "skipped: no opencode session has run `inbox.py read` on this mailbox"
            log(root, pm, what)
            break
        deadline = time.monotonic() + wait
        while busy(db, sid) and time.monotonic() < deadline:
            time.sleep(poll)
        if busy(db, sid):
            what = f"skipped: {sid} busy for {wait / 60:.0f} min"
            log(root, pm, what)
            break
        if wakes_last_hour(root, pm, datetime.datetime.now().astimezone()) >= cap:
            what = f"capped: {cap} wakes in the last hour, {' '.join(new)} wait for the human"
            log(root, pm, what)
            break
        what = f"woke {sid} for {' '.join(unread)}"
        log(root, pm, what)
        script = os.path.abspath(inbox.__file__)
        send(sid, TEXT.format(pm=pm, ids=", ".join(unread), script=script, root=root))
    return what


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root, pm = os.path.abspath(argv[0]), argv[1]
    inbox.path_of(root, pm)  # validates the name
    print(run(root, pm))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
