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
import subprocess
from contextlib import closing, contextmanager
from dataclasses import dataclass

try:
    import gestion  # type: ignore
except ImportError:
    gestion = None  # type: ignore

HEADER = """# Inbox of {pm}

Messages to {pm}. Read the unread ones, act, then `inbox.py ack`. Never edit other rows.

| Id | From | Sent | Message | Read |
|---|---|---|---|---|
"""
NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
ROW = re.compile(r"^\|\s*(M-\d+)\s*\|(.*)\|\s*$")
CELL = re.compile(r"(?<!\\)\|")  # a message may hold escaped pipes
FMT = "%Y-%m-%d %H:%M %z"
AVISO_IMPERATIVOS = ("haz", "avanza", "pide", "corrige", "lanza", "revisa", "cierra")


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


def _aviso_imperativo(text: str) -> str | None:
    """Si text es [aviso] y trae imperativo en las primeras palabras, retorna el verbo."""
    m = re.match(r"\s*\[aviso\]\s*(.*)", text, re.IGNORECASE)
    if not m:
        return None
    resto = m.group(1).strip()
    if not resto:
        return None
    palabras = re.split(r"\s+", resto)[:4]
    for p in palabras:
        limpio = re.sub(r"[^a-záéíóúñ]", "", p.lower())
        if limpio in AVISO_IMPERATIVOS:
            return limpio
    return None


ARCHIVED = re.compile(r"^(?P<pm>.+)-(?:\d{4}-\d{2}|rescate-\d{4}-\d{2}-\d{2})\.md$")


def _archived_max(root: str, pm: str) -> int:
    """Highest id this mailbox ever had in inbox/archivo/. Archiving moves rows out of the live
    file, so numbering from the live file alone gave out the same id twice (M-192 on 2026-11-04)."""
    top = 0
    for path in glob.glob(os.path.join(root, "archivo", "*.md")):
        m = ARCHIVED.match(os.path.basename(path))
        if m and m.group("pm") == pm:
            with open(path, encoding="utf-8") as f:
                top = max([top] + [int(x.id[2:]) for x in parse(f.read())])
    return top


def mailbox_arg(arg: str, create: bool = False) -> str:
    """The mailbox for a CLI argument: the repo (its .scrum.toml names the mailbox) or the folder.

    A wrong path must fail, never read as an empty mailbox: on 2026-11-05 `read <repo> PO` answered
    «no unread messages» while the PO had mail in gestion/inbox.
    """
    arg = os.path.abspath(arg)
    if gestion and os.path.exists(os.path.join(arg, ".git")):
        arg = os.path.abspath(gestion.inbox_root(arg))
    if not create and not os.path.isdir(arg):
        raise FileNotFoundError(f"no existe el buzón {arg}")
    return arg


def ensure_mailbox(folder: str) -> None:
    """Create a missing mailbox folder only if it is the one its repo's .scrum.toml names.

    A stale path (a long-running service, a plist, a prompt from before a move) would otherwise
    recreate the old folder and leave an orphan mailbox nobody reads: on 2026-11-05 it happened
    twice, at the repo root in the morning and in portafolio/gestion/ after the move at 12:54.
    """
    folder = os.path.abspath(folder)
    if os.path.isdir(folder):
        return
    named = named_mailbox(folder)
    if named and named != folder:
        raise FileNotFoundError(f"no existe el buzón {folder}; el de su repo es {named}")
    os.makedirs(folder, exist_ok=True)


def pause(root: str) -> dict | None:
    """The pause in force, or None. `inbox/.pausa` holds `<owner PM> <ticket> [why]` on its first
    line. While it exists nobody launches devs or nudges PMs except the owner's, and the sweep keeps
    running: it is what notices the owner stopped. An empty file is a pause without owner, which the
    sweep reports to the PO. 2026-11-05: ORDEN-5 paused by bootout of the sweep; PM-calidad stopped
    with the pause on and nobody noticed for an hour."""
    try:
        with open(os.path.join(root, ".pausa"), encoding="utf-8") as f:
            words = (f.readline().split() + [None, None])[:2]
    except FileNotFoundError:
        return None
    owner = words[0] if words[0] and words[0].startswith("PM") else None
    return {"owner": owner, "ticket": words[1] if owner else None}


def named_mailbox(path: str) -> str | None:
    """The mailbox that the .scrum.toml of the repo holding path names, even if path is gone.

    A path from before a move (portafolio/gestion/inbox) still names today's mailbox through its
    repo; None outside a repo or without gestion.py.
    """
    repo = os.path.abspath(path)
    while not os.path.isdir(repo):
        repo = os.path.dirname(repo)
    while repo != os.path.dirname(repo) and not os.path.exists(os.path.join(repo, ".git")):
        repo = os.path.dirname(repo)
    if not gestion or not os.path.exists(os.path.join(repo, ".git")):
        return None
    return os.path.abspath(gestion.inbox_root(repo))


def _locked_update(path: str, change) -> str:
    """Apply change(text) -> (new_text, result) under an exclusive lock: PMs write concurrently."""
    ensure_mailbox(os.path.dirname(path) or ".")
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
    adv = _aviso_imperativo(text)
    if adv:
        print(f"advertencia: [aviso] con imperativo '{adv}' — si pide hacer algo usa [encargo] (references/inbox.md)", file=sys.stderr)

    def change(old: str):
        old = old or HEADER.format(pm=to)
        ids = [int(m.id[2:]) for m in parse(old)] + [_archived_max(root, to)]
        mid = f"M-{max(ids) + 1}"
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


def _infer_expected(ticket: str, prompt: str) -> str | None:
    """artefacto esperado (reporte/ruta) inferido del ticket/prompt, sin bloquear."""
    if not ticket or ticket == "CAL-?":
        return None
    try:
        pp = os.path.abspath(prompt) if prompt else ""
        m = re.search(r"/(epic-[^/]+)/", pp)
        return os.path.join(m.group(1), "reports", f"{ticket}.md") if m else None
    except Exception:
        return None


@contextmanager
def launch_lock(root: str):
    """El mismo candado protege encolado, reserva de cupo y registro de sesión."""
    with open(os.path.join(root, ".lanzar", ".lanzar.lock"), "a+") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def write_request(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in data.items() if not k.startswith("_")}, f, ensure_ascii=False)
    os.replace(tmp, path)


def launch_records(root: str) -> list[dict]:
    try:
        with open(os.path.join(root, ".lanzar", "log"), encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def _review_pending(root: str, entry: dict, db_path: str) -> bool:
    """Una entrega o un turno exitoso requiere devolución explícita del PM."""
    expected = entry.get("expected")
    if expected:
        report = os.path.join(os.path.dirname(os.path.abspath(root)), expected)
        since = datetime.datetime.fromisoformat(entry["ts"]).timestamp()
        if os.path.isfile(report) and os.path.getmtime(report) >= since:
            return True
    if os.path.isfile(db_path) and entry.get("session"):
        import sqlite3
        with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
            row = db.execute(
                "select time_busy, time_idle from session_v2 where id = ?", (entry["session"],)
            ).fetchone()
            if row and (row[1] or 0) >= (row[0] or 0):
                outcome = db.execute(
                    "select data from session_message where session_id = ? and type = 'idle' "
                    "order by time_created desc limit 1", (entry["session"],)
                ).fetchone()
                return bool(outcome and json.loads(outcome[0]).get("outcome") == "succeeded")
    return False


def lanzar(root: str, pm: str, ticket: str, prompt: str, sender: str | None = None, *, retry: bool = False, project: str | None = None) -> str:
    """Encola un pedido en inbox/.lanzar/. Vuelve al instante con el id del pedido."""
    sender = sender or pm
    if not NAME.match(pm):
        raise ValueError(f"bad pm {pm!r}")
    if not re.match(r"^[A-Za-z0-9._-]+$", ticket):
        raise ValueError(f"bad ticket {ticket!r}")
    prompt = os.path.abspath(os.path.expanduser(prompt))
    if not os.path.isfile(prompt):
        raise ValueError(f"prompt not found: {prompt}")
    # El lanzador solo observa <buzón>/.lanzar/: un pedido encolado en otra carpeta queda varado
    # sin error (2026-11-04: 6 pedidos en <proyecto>/.lanzar/ porque el PM pasó la raíz del
    # proyecto). Una raíz que no existe o que contiene un inbox/ no es el buzón.
    # Gestion-aware: detecta tanto inbox/ como gestion/inbox cuando gestion está configurado.
    def _is_repo_root(p: str) -> bool:
        if os.path.isdir(os.path.join(p, "inbox")):
            return True
        if gestion is not None:
            try:
                gr = gestion.raiz_gestion(p)
                if os.path.abspath(gr) != os.path.abspath(p) and os.path.isdir(os.path.join(gr, "inbox")):
                    return True
            except Exception:
                pass
        return False

    if _is_repo_root(root):
        hint = os.path.join(gestion.raiz_gestion(root), "inbox") if gestion else os.path.join(root, "inbox")
        raise ValueError(f"{root} es el proyecto, no el buzón; ¿quisiste decir {hint}?")
    if not os.path.isdir(root):
        raise ValueError(f"no existe el buzón {root}")
    if project is None:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
        project = result.stdout.strip() if result.returncode == 0 else ""
    project = os.path.realpath(project) if project else ""
    if not project or not gestion or os.path.realpath(gestion.inbox_root(project)) != os.path.realpath(root):
        raise ValueError("indica --repo con el repositorio de aplicación cuyo .scrum.toml apunta a este buzón")
    qdir = os.path.join(root, ".lanzar")
    os.makedirs(qdir, exist_ok=True)
    # un ticket no se encola dos veces mientras haya uno en cola,
    # ya tomado (.claim) o con dev vivo (log + DB busy). Segundo intento se rechaza.
    with launch_lock(root):
        # 1) en cola o reclamado (.json y .json.claim)
        for pat in (os.path.join(qdir, "*.json"), os.path.join(qdir, "*.json.claim")):
            for p in glob.glob(pat):
                if os.path.basename(p) == "log":
                    continue
                try:
                    with open(p, encoding="utf-8") as f:
                        d = json.load(f)
                    if d.get("ticket") == ticket:
                        raise ValueError(f"en cola, no reintentes, mira inbox/.lanzar/log (ticket {ticket} ya en {os.path.basename(p)})")
                except ValueError:
                    raise
                except Exception:
                    continue
        # 2) La ausencia de una sesión nueva en el runtime no demuestra que terminó.
        records = launch_records(root)
        related = [e for e in records if e.get("ticket") == ticket]
        db_path = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
        if related:
            import sqlite3
            if not os.path.exists(db_path):
                if any(e.get("title") for e in related):
                    raise ValueError(f"{ticket}: runtime no observable; reconcilia el pedido antes de reintentar")
            else:
                try:
                    with closing(sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)) as db:
                        for e in related:
                            row = db.execute("select time_busy, time_idle from session_v2 where id = ?", (e.get("session"),)).fetchone()
                            if row and (row[0] or 0) > (row[1] or 0):
                                raise ValueError(f"en cola, no reintentes, mira inbox/.lanzar/log (ticket {ticket} con dev vivo {e['session']})")
                            if row is None and e.get("title"):
                                raise ValueError(f"{ticket}: sesión {e['session']} no observable; requiere reconciliación")
                except sqlite3.Error as e:
                    raise ValueError(f"{ticket}: no se pudo verificar el runtime; no se reintenta: {e}") from e
        previous = [e for e in records if e.get("ticket") == ticket]
        if previous and not retry:
            db_path = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
            if _review_pending(root, previous[-1], db_path):
                raise ValueError(f"{ticket} tiene una entrega pendiente de revisión; solo una devolución explícita del PM permite --retry")
        stamp = datetime.datetime.now().astimezone().strftime("%Y%m%d%H%M%S")
        # unique id: ticket-pm-stamp-rand
        uniq = f"{ticket}--{pm}--{stamp}-{os.getpid()}"
        path = os.path.join(qdir, f"{uniq}.json")
        # ensure not overwriting
        counter = 0
        known_ids = {e.get("id") for e in records}
        while os.path.exists(path) or os.path.basename(path)[:-5] in known_ids:
            counter += 1
            path = os.path.join(qdir, f"{uniq}-{counter}.json")
        uniq = os.path.basename(path)[:-5]
        expected = _infer_expected(ticket, prompt)
        data = {"id": uniq, "pm": pm, "ticket": ticket, "prompt": prompt, "from": sender,
                "created": now(), "status": "pending", "retry": retry, "project": project}
        if expected:
            data["expected"] = expected
        write_request(path, data)
        return uniq


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
    lz = sub.add_parser("lanzar", help="encola un dev para el lanzador del servicio")
    lz.add_argument("root")
    lz.add_argument("--pm", required=True, help="PM dueño, ej. PM-ui")
    lz.add_argument("--ticket", required=True, help="id del ticket, ej. CAL-25")
    lz.add_argument("--prompt", required=True, help="ruta al prompt del dev")
    lz.add_argument("--from", dest="sender", default=None, help="quien pide el lanzamiento (por defecto, el PM)")
    lz.add_argument("--repo", dest="project", help="repositorio de aplicación (por defecto, repo del directorio actual)")
    lz.add_argument("--retry", action="store_true", help="devolución explícita del PM tras revisar una entrega; no salta pedidos activos o inciertos")
    args = ap.parse_args(argv)

    try:
        if args.cmd not in ("lanzar", "hook"):  # lanzar has its own check; a hook never fails
            args.root = mailbox_arg(args.root, create=args.cmd == "send")
        if args.cmd == "lanzar":
            print(lanzar(args.root, args.pm, args.ticket, args.prompt, args.sender, retry=args.retry, project=args.project))
            return 0
        elif args.cmd == "send":
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
                out = pending(mailbox_arg(args.root))  # a stale path shows up as an error line
            except Exception as e:  # a hook must never block the session
                out = f"[inbox] error: {e}"
            if out:
                print(out)
    except (ValueError, FileNotFoundError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
