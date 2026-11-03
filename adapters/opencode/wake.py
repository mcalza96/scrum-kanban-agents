#!/usr/bin/env python3
"""Wake an idle opencode PM when its mailbox gets a message (see references/inbox.md).

    wake.py inbox/ PM-ui         run by the opencode plugin (inbox.js) when a mailbox changes
    wake.py --sweep inbox/       run every few minutes (launchd/cron): nudges idle PMs, and reaps
                                 the repo's stale worktrees and idle agent postgres

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

- A session whose last turn ended in an error (`outcome: failed` in opencode) is reported to the PO's
  mailbox, once per failed turn, and the sweep stops nudging it. Why: on 2026-11-02 two PMs had
  failed every wake for hours (the provider rejected their history with a 400) while the log said
  "woke" and "nudged", and 11 messages sat unread with nobody knowing.

- Every message has a kind, the first word of its text: `[bloqueo]`, `[decision]` or `[aviso]`; a
  message with none is an aviso. A bloqueo or a decision wakes at once. A mailbox with a HOLD keeps
  its avisos until the oldest has waited that long, then wakes once for all of them; the waker polls
  meanwhile, so a bloqueo that arrives during the hold still wakes at once. Why (D-282, 2026-11-03):
  the PO got 196 messages from PMs in 24 h and 25 wakes in half an hour, nearly all status echoes.

Every decision is one line in inbox/.wakes.log, which is also what the cap counts.

The sweep exists because a PM that ends its turn sleeps until a message arrives, even with free
dev slots and launchable tickets. For every PM-* mailbox it nudges the PM's session once per idle
stretch: idle for more than IDLE and less than STALE (older is a retired PM), and its last turn was
not already a nudge, so a PM that answered "nothing to do" is not nudged again until something else
happens in its session.

The sweep then runs `stale_worktrees.py <repo> --reap` on the repo that holds inbox/ (DoD 9):
worktrees with no git activity for a day go, saved first as `conserva/wt-<name>` when they hold
work, and agent postgres with no client go down. Each line it prints is logged. Last it runs
`scripts/archivar.py` on the repo (D-283): old read mail, old wake lines, closed STATUS rows and done
directives move to their archive files.
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
import unicodedata

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../scripts"))
import archivar  # noqa: E402
import inbox  # noqa: E402
import stale_worktrees  # noqa: E402

CAP = 40         # wakes per PM per rolling hour (owner decision, 2026-11-03)
WAIT = 30 * 60   # seconds to wait for a busy session before giving up
POLL = 20
DB = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
HOLD = {"PO": 15 * 60}  # seconds a mailbox keeps its avisos before waking (owner decision, D-282)
URGENT = re.compile(r"\s*\[(bloqueo|decisi[oó]n)\]", re.IGNORECASE)
IDLE = 20 * 60
STALE = 12 * 3600
NUDGE_MARK = "Barrido automático"
NUDGE = (NUDGE_MARK + ": {pm} lleva {minutes} min sin actividad. Relee tu STATUS: si tienes "
         "reportes por verificar, o cupo de devs libre y tickets pending sin dependencia abierta, "
         "avanza ahora. Si todo lo que queda espera al dueño o a otro PM, dilo en una línea: qué "
         "esperas y de quién.")
TEXT = ("Automatic wake: {pm} has unread messages ({ids}). Run "
        "`python3 {script} read {root} {pm}`, act on them as the skill says, and ack after acting. "
        "Do not reply to acknowledge or to report status: write only for a [bloqueo] or a "
        "[decision] (references/inbox.md). "
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


def held(msgs: list, hold: float, now: datetime.datetime) -> float:
    """Seconds left before msgs must wake: 0 when one is urgent or the oldest waited hold."""
    if not hold or any(URGENT.match(m.text) for m in msgs):
        return 0.0
    ages = [inbox.age_hours(m.sent, now) for m in msgs]
    return max(0.0, hold - max((a or 0.0) * 3600 for a in ages))


def addressee(db: sqlite3.Connection, sid: str) -> str | None:
    """The PM a session was started for: the `TO: <pm>` that opens its first user prompt."""
    row = db.execute("select data from session_message where session_id = ? and type = 'user'"
                     " order by time_created limit 1", (sid,)).fetchone()
    m = re.match(r"[\s\"'`]*TO:\s*([\w.-]+)", message_text(row))
    return plain(m.group(1)) if m else None


def plain(name: str) -> str:
    """`PM-auditoría` and `PM-auditoria` are the same PM: prompts carry the accent, mailboxes don't."""
    return "".join(c for c in unicodedata.normalize("NFD", name) if unicodedata.category(c) != "Mn")


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
        if addressee(db, sid) not in (None, plain(pm)):
            continue  # another PM read this mailbox to check on it; it is not pm's session
        for cmd in commands(data):
            for path in read.findall(cmd):
                path = os.path.join(cwd, os.path.expanduser(path.strip("'\"")))
                if os.path.realpath(path) == real:
                    return sid
    return None


def busy(db: sqlite3.Connection, sid: str) -> bool:
    b, i = db.execute("select time_busy, time_idle from session_v2 where id = ?", (sid,)).fetchone()
    return (b or 0) > (i or 0)


def failed_turn(db: sqlite3.Connection, sid: str) -> tuple[float, str] | None:
    """(when, error) if the session's last turn ended in a failure, else None."""
    row = db.execute("select time_created, data from session_message where session_id = ?"
                     " and type = 'idle' order by time_created desc limit 1", (sid,)).fetchone()
    try:
        data = json.loads(row[1]) if row else {}
    except ValueError:
        return None
    if not isinstance(data, dict) or data.get("outcome") != "failed":
        return None
    err = data.get("error") or {}
    return row[0], (err.get("message") if isinstance(err, dict) else str(err)) or "unknown error"


def alert_failed(db: sqlite3.Connection, root: str, pm: str, sid: str) -> str | None:
    """Tell PO, once per failed turn, that pm's session fails when woken: waking it again would fail
    the same way, and nobody reads its mailbox until someone relaunches it."""
    failed = failed_turn(db, sid)
    if not failed:
        return None
    mark = f"failed {sid} {failed[0]:.0f}"
    try:
        with open(log_path(root), encoding="utf-8") as f:
            if any(mark in line for line in f):
                return mark
    except FileNotFoundError:
        pass
    log(root, pm, f"{mark}: {failed[1]}")
    unread = [m.id for m in inbox.messages(root, pm) if m.unread]
    inbox.send(root, "PO", "barrido",
               f"{pm} no responde: su sesión {sid} terminó su último turno con error ({failed[1]}). "
               f"Tiene {len(unread)} sin leer. Despertarla de nuevo falla igual: relánzalo en una "
               f"sesión nueva que lea su buzón.")
    return mark


def opencode() -> str:
    return shutil.which("opencode") or os.path.expanduser("~/.opencode/bin/opencode")


def message_text(row) -> str:
    """The text a user typed, wherever opencode stored it: `text`, or the parts of `content`."""
    try:
        data = json.loads(row[0]) if row else {}
    except ValueError:
        return row[0]
    if not isinstance(data, dict):
        return ""
    parts = data.get("content") if isinstance(data.get("content"), list) else []
    return data.get("text") or "".join(c.get("text", "") for c in parts if isinstance(c, dict))


def prompt_text(row) -> str:
    """A stored user message as text, with its accents decoded: JSON escapes them."""
    try:
        return json.dumps(json.loads(row[0]), ensure_ascii=False) if row else ""
    except ValueError:
        return row[0]


def last_prompt(db: sqlite3.Connection, sid: str) -> str:
    return prompt_text(db.execute("select data from session_message where session_id = ?"
                                  " and type = 'user' order by time_created desc limit 1",
                                  (sid,)).fetchone())


def sweep(root: str, now: float | None = None, idle: float = IDLE, stale: float = STALE,
          send=None) -> list[str]:
    """Nudge every idle PM once per idle stretch. Returns one decision per PM with a session."""
    send = send or (lambda sid, text: subprocess.Popen(
        [opencode(), "run", "--session", sid, text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    now = time.time() if now is None else now
    done = []
    db = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        for pm in inbox.mailboxes(root):
            sid = pm.startswith("PM-") and find_session(db, root, pm)
            if not sid:
                continue
            idle_for = now - (db.execute("select time_idle from session_v2 where id = ?",
                                         (sid,)).fetchone()[0] or 0) / 1000
            if busy(db, sid) or not idle <= idle_for <= stale:
                continue
            if alert_failed(db, root, pm, sid):
                done.append(f"{pm} {sid}: its last turn failed, the PO was told")
                continue
            if NUDGE_MARK in last_prompt(db, sid):
                done.append(f"{pm} {sid}: already nudged, waiting for news")
                continue
            log(root, pm, f"nudged {sid} after {idle_for / 60:.0f} min idle")
            send(sid, NUDGE.format(pm=pm, minutes=round(idle_for / 60)))
            done.append(f"{pm} {sid}: nudged after {idle_for / 60:.0f} min idle")
    finally:
        db.close()
    return done


def reap(root: str, older: float = 24.0) -> list[str]:
    """Reap the stale worktrees and idle postgres of the repo that holds root; log what it did."""
    repo = os.path.dirname(root)
    lines = stale_worktrees.reap(repo, "main", stale_worktrees.in_use(), older)
    lines += stale_worktrees.stop_postgres(stale_worktrees.PG_ROOT, older)
    for line in lines:
        if not line.startswith("kept"):
            log(root, "reap", line)
    return lines or ["nothing to reap"]


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
    holding = False
    while True:
        msgs = [m for m in inbox.messages(root, pm) if m.unread]
        unread = [m.id for m in msgs]
        woken = woken_ids(root, pm)
        new = [m for m in msgs if m.id not in woken]
        if not new:
            what = "nothing new unread"
            break
        left = held(new, HOLD.get(pm, 0), datetime.datetime.now().astimezone())
        if left:
            if not holding:
                log(root, pm, f"held {' '.join(m.id for m in new)}: avisos, wake in {left / 60:.0f} min")
                holding = True
            time.sleep(min(poll, left))
            continue
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
            what = f"capped: {cap} wakes in the last hour, {' '.join(m.id for m in new)} wait for the human"
            log(root, pm, what)
            break
        what = f"woke {sid} for {' '.join(unread)}"
        log(root, pm, what)
        script = os.path.abspath(inbox.__file__)
        send(sid, TEXT.format(pm=pm, ids=", ".join(unread), script=script, root=root))
        if alert_failed(db, root, pm, sid):
            what = f"{what}; the turn failed, the PO was told"
            break
    return what


def main(argv: list[str]) -> int:
    if len(argv) == 2 and argv[0] == "--sweep":
        root = os.path.abspath(argv[1])
        print("\n".join(sweep(root)) or "nobody idle")
        print("\n".join(reap(root)))
        for line in archivar.archive(os.path.dirname(root)):
            log(root, "archivo", line)
            print(line)
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root, pm = os.path.abspath(argv[0]), argv[1]
    inbox.path_of(root, pm)  # validates the name
    print(run(root, pm))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
