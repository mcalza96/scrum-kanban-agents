#!/usr/bin/env python3
"""Wake an idle opencode PM when its mailbox gets a message (see references/inbox.md).

    wake.py inbox/ PM-ui         run by the opencode plugin (inbox.js) when a mailbox changes
    wake.py --sweep <repo>       run every few minutes (launchd/cron): nudges idle PMs, and reaps
                                 the repo's stale worktrees and idle agent postgres
    wake.py --reap <repo>        run every minute by inbox.js: tells each PM that a dev of theirs
                                 finished, unless the dev already wrote to them (reap_finished)
    wake.py --activos <repo> [min]  the sessions with real activity in the last min minutes
                                 (default 10): the only evidence that someone is working

Every form takes the repo or the mailbox folder. Given the repo (a folder with .git), the mailbox
is found with gestion.inbox_root, so a launchd job never hard-codes where the mailbox lives. Why
(2026-11-05): ORDEN-4 moved inbox/ to gestion/inbox/ and the sweep, still pointing at the old
path, recreated inbox/ at the root: nobody woke for an hour and its alarms went unread.

It finds the PM's session in opencode's database: the newest top-level session that ran
`inbox.py read <root> PM-ui`. It waits until that session is idle, then sends it one turn with
`opencode run --session` through the running service, so the turn shows live in the PM's window.

- No cap on wakes (owner, 2026-11-04: «quita el límite de despertares»). The busy check is what keeps a
  session from being flooded: a busy session gets nothing; when it stops, one turn carries every
  unread message, including those that arrived while it worked.
- One waker per PM at a time (a lock). It loops while new unread ids keep arriving, and never wakes
  for an id an earlier wake already announced (the log says which): a PM that read a message and
  is waiting on someone is left alone, and acks, which also change the mailbox, wake nobody.
- No session found, a session busy for more than WAIT seconds, or no unread left: it logs and
  stops. `inbox.py check` still catches what nobody read.

- A session whose last turn ended in an error (`outcome: failed` in opencode) is reported to the PO's
  mailbox, once per failed turn, and the sweep stops nudging it. Why: on 2026-11-02 two PMs had
  failed every wake for hours (the provider rejected their history with a 400) while the log said
  "woke" and "nudged", and 11 messages sat unread with nobody knowing.

- No mailbox waits on a timer: an idle session wakes at once, whatever the tag. Why (owner,
  2026-11-05, «vamos» to «que le lleguen cuando esté detenido en vez de cada 2 minutos»): a
  120 s debounce kept an idle PO waiting; on 12:31 it sat stopped 79 s with M-1331 unread. It
  replaced D-282's 10-minute hold of avisos, which made PMs' [listo] wait 222 minutes in all on
  2026-11-04. The busy wait already batches what arrives during a turn.

Every decision is one line in inbox/.wakes.log.

The sweep exists because a PM that ends its turn sleeps until a message arrives, even with free
dev slots and launchable tickets. For every PM-* mailbox it nudges the PM's session once per idle
stretch: idle for more than IDLE and less than STALE (older is a retired PM), and its last turn was
not already a nudge, so a PM that answered "nothing to do" is not nudged again until something else
happens in its session.

The sweep then runs `stale_worktrees.py <repo> --reap` on the repo that holds inbox/ (DoD 9):
worktrees with no git activity for a day go, saved first as `conserva/wt-<name>` when they hold
work, and agent postgres with no client go down. Each line it prints is logged. Last it runs
`scripts/archivar.py` on the repo: old read mail, old wake lines, closed STATUS rows and done
directives move to their archive files.
"""
from __future__ import annotations

import datetime
import fcntl
import glob
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
import candados  # noqa: E402
import gestion  # noqa: E402
import inbox  # noqa: E402
import stale_worktrees  # noqa: E402

WAIT = 30 * 60   # seconds to wait for a busy session before giving up
POLL = 20
DB = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
IDLE = 20 * 60
STALE = 12 * 3600
# A dev that delivered without telling its PM is news only while fresh. The plugin checks every
# minute and the launchd sweep every 15, so 30 min covers both without replaying old sessions.
RECENT_MS = 30 * 60 * 1000
NUDGE_MARK = "Barrido automático"
NUDGE = (NUDGE_MARK + ": {pm} lleva {minutes} min sin actividad. Relee tu STATUS: si tienes "
         "reportes por verificar, o cupo de devs libre y tickets pending sin dependencia abierta, "
         "avanza ahora. Si todo lo que queda espera al dueño o a otro PM, dilo en una línea: qué "
         "esperas y de quién.")
TEXT = ("Automatic wake: {pm} has unread messages ({ids}). Run "
        "`python3 {script} read {root} {pm}`, act on them as the skill says, and ack after acting. "
        "Un [encargo] se hace y se responde con el resultado —el commit, la fila o la ruta— o con "
        "un [bloqueo] si no se puede; `ack` o `recibido` no cuenta (references/inbox.md). "
        "Un [aviso] no lleva instrucciones; si pide hacer algo trátalo como [encargo]. "
        "If something needs the owner's decision, stop and say it in one line.")


def log_path(root: str) -> str:
    return os.path.join(root, ".wakes.log")


def log(root: str, pm: str, what: str) -> None:
    stamp = datetime.datetime.now().astimezone().strftime(inbox.FMT)
    with open(log_path(root), "a", encoding="utf-8") as f:
        f.write(f"{stamp} {pm} {what}\n")


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


def woken_ids(root: str, pm: str, sid: str) -> set[str]:
    """Ids an earlier wake already announced to this session: never wake it twice for the same
    message. A wake that went to another session does not count: it never reached pm."""
    ids: set[str] = set()
    try:
        with open(log_path(root), encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) > 6 and parts[3] == pm and parts[4] == "woke" and parts[5] == sid:
                    ids.update(parts[7:])
    except FileNotFoundError:
        pass
    return ids


def repo_of(root: str) -> str:
    """The repo that holds a mailbox folder: the nearest folder up with a .git."""
    d = os.path.abspath(root)
    while not os.path.exists(os.path.join(d, ".git")) and os.path.dirname(d) != d:
        d = os.path.dirname(d)
    return d if os.path.exists(os.path.join(d, ".git")) else os.path.dirname(os.path.abspath(root))


def mailbox_root(arg: str) -> str:
    """The mailbox folder for a CLI argument that is either the repo or the mailbox itself."""
    arg = os.path.abspath(arg)
    root = gestion.inbox_root(arg) if os.path.exists(os.path.join(arg, ".git")) else arg
    if not os.path.isdir(root):  # a stale path (an old plugin, a moved mailbox): stop, create nothing
        raise SystemExit(f"no existe el buzón {root}")
    return root


def addressee(db: sqlite3.Connection, sid: str) -> str | None:
    """The PM a session was started for: the `TO: <pm>` that opens its first user prompt."""
    row = db.execute("select data from session_message where session_id = ? and type = 'user'"
                     " order by time_created limit 1", (sid,)).fetchone()
    m = re.match(r"[\s\"'`]*TO:\s*([\w.-]+)", message_text(row))
    return plain(m.group(1)) if m else None


def signers(db: sqlite3.Connection, sid: str) -> set[str]:
    """The names a session signs its sends with (`--from <name>`): who it is when no `TO:` says so.
    Why (2026-11-03): the PO, opened by hand, read PM-ui's mailbox and took PM-ui's wakes."""
    sign = re.compile(r"inbox\.py\s+send\b[^\n;&|]*?--from[=\s]+[\"']?([\w.-]+)")
    rows = db.execute("select data from session_message where session_id = ? and type = 'assistant'"
                      " and data like '%--from%'", (sid,))
    return {plain(n) for (data,) in rows for cmd in commands(data) for n in sign.findall(cmd)}


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
        to = addressee(db, sid)
        if to not in (None, plain(pm)) or (to is None and signers(db, sid) - {plain(pm)}):
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


def _ticket_from_title(title: str | None) -> str | None:
    if not title:
        return None
    m = re.search(r"dev\s+([\w-]+)", title, re.IGNORECASE)
    return m.group(1) if m else None


def _ticket_from_prompt(db: sqlite3.Connection, sid: str) -> str | None:
    row = db.execute("select data from session_message where session_id = ? and type = 'user'"
                     " order by time_created limit 1", (sid,)).fetchone()
    txt = message_text(row) if row else ""
    m = re.search(r"TO:\s*dev\s+([\w-]+)", txt, re.IGNORECASE)
    if m:
        return m.group(1)
    # fallback on raw prompt_text (json dumped)
    raw = prompt_text(row) if row else ""
    m2 = re.search(r"TO:\s*dev\s+([\w-]+)", raw, re.IGNORECASE)
    return m2.group(1) if m2 else None


# What `git commit` prints on success: "[main 4c60886a] subject". Only that line names a commit
# the session made; any other hex in the session (git log, a pasted diff, a timestamp) does not.
COMMIT_LINE = re.compile(r"^\s*\[[^\]\s]+ ([0-9a-f]{7,40})\]", re.MULTILINE)
GIT_COMMIT = re.compile(r"\bgit\b[^|;&\n]*\bcommit\b")  # also `git -C <repo> commit`


def _shell_calls(db: sqlite3.Connection, sid: str):
    """Yield (command, output) for every shell call the session ran."""
    for (data,) in db.execute("select data from session_message where session_id = ?"
                              " and type = 'assistant'", (sid,)):
        try:
            parts = json.loads(data).get("content") or []
        except (ValueError, AttributeError):
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get("type") != "tool":
                continue
            state = part.get("state") or {}
            cmd = (state.get("input") or {}).get("command") if isinstance(state.get("input"), dict) else None
            if not cmd:
                continue
            out = state.get("content")
            if isinstance(out, list):
                out = "".join(c.get("text", "") for c in out if isinstance(c, dict))
            yield cmd, out if isinstance(out, str) else ""


def _commits_of(db: sqlite3.Connection, sid: str) -> list[str]:
    shas: list[str] = []
    for cmd, out in _shell_calls(db, sid):
        if GIT_COMMIT.search(cmd):
            shas += [s for s in COMMIT_LINE.findall(out) if s not in shas]
    return shas


def _has_inbox_send(db: sqlite3.Connection, sid: str, to: str | None = None) -> bool:
    """The session itself ran `inbox.py send` (to `to`, when given), not just read a file that
    mentions it."""
    want = re.compile(rf"--to[ =]['\"]?{re.escape(to)}\b") if to else None
    return any("inbox.py send" in cmd and (want is None or want.search(cmd))
               for cmd, _ in _shell_calls(db, sid))


def _last_idle_outcome(db: sqlite3.Connection, sid: str) -> str | None:
    row = db.execute("select data from session_message where session_id = ? and type = 'idle'"
                     " order by time_created desc limit 1", (sid,)).fetchone()
    if not row:
        return None
    try:
        data = json.loads(row[0])
    except ValueError:
        return None
    if isinstance(data, dict):
        return data.get("outcome")
    return None


def _is_dev(parent_id, title) -> bool:
    if parent_id is not None:
        return True
    if title and isinstance(title, str) and title.lower().lstrip().startswith("dev"):
        return True
    # también title LIKE 'dev %' (con espacio) ya cubierto
    return False


def _is_reported(ticket: str | None, repo: str) -> bool:
    if not ticket:
        return False
    # solo lectura, busca en epic-*/STATUS.md y STATUS*.md (gestion-aware)
    gr = gestion.raiz_gestion(repo)
    patterns = [os.path.join(gr, "epic-*", "STATUS.md"),
                os.path.join(gr, "epic-*", "STATUS-*.md")]
    for pat in patterns:
        for path in glob.glob(pat):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        if ticket in line and "reported" in line.lower():
                            return True
            except Exception:
                continue
    return False


def _load_log_sids(root: str) -> set[str] | None:
    """sids que pasaron por lanzar/log. None si log no existe/vacío (tests)."""
    lp = os.path.join(root, ".lanzar", "log")
    try:
        with open(lp, encoding="utf-8") as f:
            sids = set()
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    sid = e.get("session")
                    if sid:
                        sids.add(sid)
                except Exception:
                    continue
            return sids if sids else None
    except FileNotFoundError:
        return None


def _load_log_expected(root: str) -> dict[str, str]:
    """expected por sid desde log (si lanzador lo anotó)."""
    lp = os.path.join(root, ".lanzar", "log")
    out: dict[str, str] = {}
    try:
        with open(lp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                    sid = e.get("session")
                    exp = e.get("expected")
                    if sid and exp:
                        out[sid] = exp
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return out


def _expected_exists(expected: str | None, repo: str) -> bool:
    """verifica si el artefacto esperado existe (reporte/archivo)."""
    if not expected:
        return False
    gr = gestion.raiz_gestion(repo)
    # expected es relativo al repo (ej epic-x/reports/T-1.md)
    # permite patrón con * si viene de heurística
    if "*" in expected:
        return bool(glob.glob(os.path.join(gr, expected)))
    abs_path = os.path.join(gr, expected) if not os.path.isabs(expected) else expected
    if os.path.exists(abs_path):
        return True
    # fallback: glob por si es relativo sin epic específico
    return bool(glob.glob(os.path.join(gr, expected)))


def _has_delivery_evidence(db: sqlite3.Connection, sid: str, ticket: str | None, repo: str, root: str) -> bool:
    """entrega verificable por script — mensaje en buzón del PM o artefacto comprometido."""
    if not ticket or ticket == "?":
        # ticket desconocido no puede tener reported/file, solo inbox send cuenta
        if _has_inbox_send(db, sid):
            return True
        return False
    # 1) mensaje en buzón del PM con resultado
    if _has_inbox_send(db, sid):
        return True
    # 2) fila STATUS en reported
    if _is_reported(ticket, repo):
        return True
    # 3) artefacto archivo: expected del log o reporte canónico
    log_expected = _load_log_expected(root)
    exp = log_expected.get(sid)
    if exp and _expected_exists(exp, repo):
        return True
    # reporte canónico por ticket
    gr = gestion.raiz_gestion(repo)
    for pat in [os.path.join(gr, "epic-*", "reports", f"{ticket}.md"),
                os.path.join(gr, "epic-*", "reports", f"{ticket}.*")]:
        if glob.glob(pat):
            return True
    return False


def _resolve_pm(db: sqlite3.Connection, root: str, sid: str, title: str | None,
                parent_id) -> str:
    # 1) inbox/.lanzar/log
    lp = os.path.join(root, ".lanzar", "log")
    try:
        with open(lp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("session") == sid and e.get("pm"):
                    return e["pm"]
    except FileNotFoundError:
        pass
    # 2) addressee del dev — solo si es PM/PO, no "dev"
    try:
        pm = addressee(db, sid)
        if pm and (pm.startswith("PM-") or pm == "PO"):
            return pm
    except Exception:
        pass
    # 3) parent
    if parent_id:
        try:
            pm2 = addressee(db, parent_id)
            if pm2 and (pm2.startswith("PM-") or pm2 == "PO"):
                return pm2
        except Exception:
            pass
        # fallback: parent may be a dev too, try its parent recursively? busca por title en STATUS
        # si parent no tiene PM, busca el PM del ticket en STATUS

    # 4) signers
    try:
        s = signers(db, sid)
        for n in s:
            if n.startswith("PM-"):
                return n
        if s:
            return next(iter(s))
    except Exception:
        pass
    # 5) inferir del ticket en STATUS
    ticket = _ticket_from_title(title) or _ticket_from_prompt(db, sid)
    if ticket:
        gr = gestion.raiz_gestion(repo_of(root))
        for pat in [os.path.join(gr, "epic-*", "STATUS.md"),
                    os.path.join(gr, "epic-*", "STATUS-*.md")]:
            for path in glob.glob(pat):
                try:
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            if ticket in line:
                                m = re.search(r"PM-[\w-]+", line)
                                if m:
                                    return m.group(0)
                except Exception:
                    continue
    return "PO"


def reap_finished(root: str, db_path: str = DB) -> list[str]:
    """Le avisa al PM cuando un dev suyo termina bien (succeeded), salvo que el dev ya le haya
    escrito con `inbox.py send --to <PM>`: el PM no depende de que el dev se acuerde.

    Solo sesiones de inbox/.lanzar/log que terminaron hace menos de RECENT_MS. El aviso es un
    [encargo] firmado `lanzador` que dice «terminó y entregó sin avisarte» (hay commit, fila en
    reported o artefacto) o «terminó sin entregar»,
    con los commits que la sesión hizo y si la fila está en reported. Un solo aviso por sesión
    (dedup .aviso-dev con flock). No edita STATUS. Corre cada minuto
    desde el plugin inbox.js (`wake.py --reap`) y cada 15 min desde el barrido.
    """
    if not os.path.exists(db_path) or not os.path.isdir(root):  # a stale root: never recreate it
        return []
    aviso_path = os.path.join(root, ".lanzar", ".aviso-dev")
    os.makedirs(os.path.dirname(aviso_path), exist_ok=True)
    repo = repo_of(root)
    done: list[str] = []
    # filtros solo lanzar/log y ventana
    log_sids = _load_log_sids(root)
    now_ms = int(time.time() * 1000)
    # 1) recolecta candidatos fuera del lock (DB)
    candidates: list[tuple[str, str | None, str | None, int | None]] = []  # (sid, title, parent_id, idle)
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return []
    try:
        try:
            rows = db.execute("select id, parent_id, title, time_busy, time_idle from session_v2").fetchall()
        except sqlite3.OperationalError:
            rows = db.execute("select id, parent_id, time_busy, time_idle from session_v2").fetchall()
            rows = [(r[0], r[1], None, r[2], r[3]) for r in rows]
        for sid, parent_id, title, busy, idle in rows:
            try:
                if (busy or 0) >= (idle or 0):
                    continue
            except Exception:
                continue
            if not _is_dev(parent_id, title):
                continue
            out = _last_idle_outcome(db, sid)
            if out != "succeeded":
                continue
            if not idle or now_ms - idle > RECENT_MS:  # old news: the check runs every minute
                continue
            # solo sesiones de lanzar/log (si hay log, filtra)
            if log_sids is not None and sid not in log_sids:
                continue
            candidates.append((sid, title, parent_id, idle))
    finally:
        try:
            db.close()
        except Exception:
            pass
    if not candidates:
        return []
    # 2) dedup con flock y envío — verifica entrega por script
    try:
        db2 = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return []
    try:
        with open(aviso_path, "a+", encoding="utf-8") as af:
            try:
                fcntl.flock(af, fcntl.LOCK_EX)
            except Exception:
                pass
            af.seek(0)
            seen = {line.strip() for line in af if line.strip()}
            for sid, title, parent_id, idle in candidates:
                if sid in seen:
                    continue
                pm = _resolve_pm(db2, root, sid, title, parent_id)
                ticket = _ticket_from_title(title) or _ticket_from_prompt(db2, sid) or "?"
                shas = _commits_of(db2, sid)
                touched = candados.removed(repo, shas)
                gate = (f" Candados tocados: {candados.summary(touched)} ({' '.join(sorted({c for c, _, _ in touched}))});"
                        " justifícalo en el reporte o pide el OK del dueño antes de cerrar (regla 4)."
                        ) if touched else ""
                if _has_inbox_send(db2, sid, pm):  # the dev told its PM itself
                    msg = f"[encargo] {ticket}: session {sid}.{gate}" if touched else None
                else:
                    known = ticket if ticket != "?" else None
                    delivered = bool(shas) or _has_delivery_evidence(db2, sid, known, repo, root)
                    reported_txt = "sí" if _is_reported(known, repo) else "no"
                    # "terminó sin entregar" is not "murió sin reportar" (failed)
                    what = "terminó y entregó sin avisarte" if delivered else "terminó sin entregar"
                    msg = (f"[encargo] {ticket} {what}: commits {' '.join(shas) or '—'} | "
                           f"reported: {reported_txt} | session {sid}. Verifica y cierra o devuelve.{gate}")
                if msg is None:
                    af.write(sid + "\n")
                    seen.add(sid)
                    continue
                try:
                    inbox.send(root, pm, "lanzador", msg)
                except Exception:
                    continue
                af.write(sid + "\n")
                af.flush()
                seen.add(sid)
                done.append(f"{pm} {sid} {ticket}: aviso lanzador")
            try:
                af.flush()
            except Exception:
                pass
            try:
                fcntl.flock(af, fcntl.LOCK_UN)
            except Exception:
                pass
    finally:
        try:
            db2.close()
        except Exception:
            pass
    return done


def sweep(root: str, now: float | None = None, idle: float = IDLE, stale: float = STALE,
          send=None) -> list[str]:
    """Nudge every idle PM once per idle stretch. Returns one decision per PM with a session."""
    send = send or (lambda sid, text: subprocess.Popen(
        [opencode(), "run", "--session", sid, text],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True))
    now = time.time() if now is None else now
    done: list[str] = []
    # aviso de dev terminado, un solo punto que cubre 100% (barrido).
    try:
        done.extend(reap_finished(root, DB))
    except Exception:
        pass
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
    repo = repo_of(root)
    lines = stale_worktrees.reap(repo, "main", stale_worktrees.in_use(), older)
    lines += stale_worktrees.stop_postgres(stale_worktrees.PG_ROOT, older)
    for line in lines:
        if not line.startswith("kept"):
            log(root, "reap", line)
    return lines or ["nothing to reap"]


def run(root: str, pm: str, wait: float = WAIT, poll: float = POLL, send=None) -> str:
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
            return _loop(db, root, pm, wait, poll, send)
        finally:
            db.close()


def _loop(db, root, pm, wait, poll, send) -> str:
    while True:
        msgs = [m for m in inbox.messages(root, pm) if m.unread]
        unread = [m.id for m in msgs]
        if not msgs:
            what = "nothing new unread"
            break
        sid = find_session(db, root, pm)
        if not sid:
            what = "skipped: no opencode session has run `inbox.py read` on this mailbox"
            log(root, pm, what)
            break
        woken = woken_ids(root, pm, sid)
        new = [m for m in msgs if m.id not in woken]
        if not new:
            what = "nothing new unread"
            break
        deadline = time.monotonic() + wait
        while busy(db, sid) and time.monotonic() < deadline:
            time.sleep(poll)
        if busy(db, sid):
            what = f"skipped: {sid} busy for {wait / 60:.0f} min"
            log(root, pm, what)
            break
        unread = [m.id for m in inbox.messages(root, pm) if m.unread]  # what came during the turn too
        what = f"woke {sid} for {' '.join(unread)}"
        log(root, pm, what)
        script = os.path.abspath(inbox.__file__)
        send(sid, TEXT.format(pm=pm, ids=", ".join(unread), script=script, root=root))
        if alert_failed(db, root, pm, sid):
            what = f"{what}; the turn failed, the PO was told"
            break
    return what


def _inside(path: str, top: str) -> bool:
    path = os.path.realpath(path)
    return path == top or path.startswith(top + os.sep)


def worktrees(repo: str) -> list[str]:
    """The repo's other worktrees: a dev launched in one still works for this repo."""
    out = subprocess.run(["git", "-C", repo, "worktree", "list", "--porcelain"],
                         capture_output=True, text=True).stdout
    return [os.path.realpath(line[9:]) for line in out.splitlines()
            if line.startswith("worktree ")][1:]


def activos(repo: str, minutes: float = 10, db_path: str = DB, at: float | None = None) -> list[str]:
    """One line per session of repo with activity in the last minutes, newest first.

    The evidence that someone is working is the session's own last activity, not a claim, a
    mailbox or a PID. Why (2026-11-05): the PO told the owner a dev was working, citing a claim in
    `pending` and a PID that did not exist, while the dev had finished an hour before.
    """
    at = time.time() if at is None else at
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("select id, title, time_updated, directory from session_v2"
                          " where time_updated >= ? order by time_updated desc",
                          ((at - minutes * 60) * 1000,)).fetchall()
        places = [os.path.realpath(repo)] + worktrees(repo)
        return [f"{datetime.datetime.fromtimestamp(t / 1000).strftime('%H:%M:%S')}"
                f"  hace {(at - t / 1000) / 60:4.1f} min  {'busy' if busy(db, sid) else 'idle'}"
                f"  {title}  {sid}" for sid, title, t, d in rows
                if any(_inside(d or "", p) for p in places)]
    finally:
        db.close()


def main(argv: list[str]) -> int:
    if len(argv) in (2, 3) and argv[0] == "--activos":
        repo = repo_of(argv[1])
        mins = float(argv[2]) if len(argv) == 3 else 10
        print("\n".join(activos(repo, mins)) or f"nadie con actividad en los últimos {mins:g} min")
        return 0
    if len(argv) == 2 and argv[0] == "--sweep":
        root = mailbox_root(argv[1])
        print("\n".join(sweep(root)) or "nobody idle")
        print("\n".join(reap(root)))
        for line in archivar.archive(repo_of(root)):
            log(root, "archivo", line)
            print(line)
        return 0
    if len(argv) == 2 and argv[0] == "--reap":
        root = mailbox_root(argv[1])
        for line in reap_finished(root):
            log(root, "aviso-dev", line)
            print(line)
        return 0
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    root, pm = mailbox_root(argv[0]), argv[1]
    inbox.path_of(root, pm)  # validates the name
    print(run(root, pm))
    return 0

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
