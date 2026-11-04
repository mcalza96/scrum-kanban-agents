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

- Every message has a kind, the first word of its text: `[bloqueo]`, `[encargo]` (`[decision]`
  es alias) or `[aviso]`; a message with none is an aviso. Un `[bloqueo]` o un `[encargo]`
  despierta al tiro; `[decision]` se acepta como alias de `[encargo]`. A mailbox with a HOLD keeps
  its avisos until the oldest has waited that long, then wakes once for all of them; the waker polls
  meanwhile, so a bloqueo or an encargo that arrives during the hold still wakes at once. Why
  (D-282, 2026-11-03): the PO got 196 messages from PMs in 24 h and 25 wakes in half an hour, nearly
  all status echoes.

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
import inbox  # noqa: E402
import stale_worktrees  # noqa: E402

CAP = 40         # wakes per PM per rolling hour (owner decision, 2026-11-03)
WAIT = 30 * 60   # seconds to wait for a busy session before giving up
POLL = 20
DB = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))
HOLD = {"PO": 10 * 60}  # seconds a mailbox keeps its avisos before waking (owner decision, D-282 → M-85 10 min)
URGENT = re.compile(r"\s*\[(bloqueo|encargo|decisi[oó]n)\]", re.IGNORECASE)
# M-83: si un [aviso] cita M-número de un [encargo] pendiente del PO, despierta al tiro aunque sea aviso
M_CITE_RE = re.compile(r"\bM-(\d+)\b")
IDLE = 20 * 60
STALE = 12 * 3600
# CAL-29: ventana para "terminó sin entregar" (evita inundación M-260–M-617)
VENTANA_HORAS = 24
VENTANA_MS = VENTANA_HORAS * 3600 * 1000
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


def held(msgs: list, hold: float, now: datetime.datetime) -> float:
    """Seconds left before msgs must wake: 0 when one is urgent or the oldest waited hold."""
    if not hold or any(URGENT.match(m.text) for m in msgs):
        return 0.0
    # M-83: si un [aviso] cita M-número de un [encargo] pendiente del PO, despierta al tiro
    # aunque el tag sea [aviso]; no cambia cadencia de avisos rutinarios sin cita
    # Caso M-723: [aviso] M-723 (respuesta a mi encargo M-82) quedó held 15 min por tag [aviso]
    # Detecta cita con "encargo" + M-número, aunque el encargo citado no esté en esta misma lista
    # (está en otro buzón del PO), y también el caso intra-buzón.
    pending_encargo_ids = {m.id for m in msgs if URGENT.match(m.text)}
    for m in msgs:
        if URGENT.match(m.text):
            continue
        # intra-buzón: cita a un [encargo] presente en este lote
        for cited in M_CITE_RE.findall(m.text):
            if f"M-{cited}" in pending_encargo_ids:
                return 0.0
        # cross-buzón: [aviso] que menciona encargo + M-número (ej. "respuesta a mi encargo M-82")
        if M_CITE_RE.search(m.text) and re.search(r"encargo", m.text, re.IGNORECASE):
            return 0.0
    ages = [inbox.age_hours(m.sent, now) for m in msgs]
    return max(0.0, hold - max((a or 0.0) * 3600 for a in ages))


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


# CAL-29 M-70: regex explícita que excluye timestamps decimales de 13 dígitos
# 1793725787505 es solo dígitos y 13 largo -> no es sha hex; shas válidos 7-40 hex con al
# menos una letra o longitud !=13 si son solo dígitos. Evita confundir timestamp con sha.
_TIMESTAMP_13_RE = re.compile(r"^\d{13}$")


def _commits_of(db: sqlite3.Connection, sid: str) -> list[str]:
    rows = db.execute("select data from session_message where session_id = ? and type = 'assistant'"
                      " and data like '%git commit%'", (sid,)).fetchall()
    shas: list[str] = []
    seen: set[str] = set()
    for (data,) in rows:
        # \b[0-9a-f]{7,40}\b captura shas hex; el filtro posterior excluye \d{13} puros
        for sha in re.findall(r"\b[0-9a-f]{7,40}\b", data, re.IGNORECASE):
            if _TIMESTAMP_13_RE.match(sha):
                continue
            low = sha.lower()
            if low not in seen:
                # evita falsos positivos muy cortos que sean palabras comunes
                if len(sha) >= 7:
                    seen.add(low)
                    shas.append(sha)
    return shas


def _has_inbox_send(db: sqlite3.Connection, sid: str) -> bool:
    row = db.execute("select 1 from session_message where session_id = ? and type = 'assistant'"
                     " and data like '%inbox.py send%' limit 1", (sid,)).fetchone()
    return row is not None


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
    # solo lectura, busca en epic-*/STATUS.md y STATUS*.md
    patterns = [os.path.join(repo, "epic-*", "STATUS.md"),
                os.path.join(repo, "epic-*", "STATUS-*.md")]
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
    """CAL-29: sids que pasaron por lanzar/log. None si log no existe/vacío (tests)."""
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
    """CAL-29: expected por sid desde log (si lanzador lo anotó)."""
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


def _is_within_window(idle_ms: int | None, now_ms: int | None = None) -> bool:
    """CAL-29: solo sesiones dentro de la ventana (24h). Valores legacy pequeños se consideran dentro."""
    if idle_ms is None:
        return False
    # tests usan 1/2 como busy/idle legacy
    if idle_ms < 1000000000000:
        return True
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    return (now_ms - idle_ms) < VENTANA_MS


def _expected_exists(expected: str | None, repo: str) -> bool:
    """CAL-29: verifica si el artefacto esperado existe (reporte/archivo)."""
    if not expected:
        return False
    # expected es relativo al repo (ej epic-calidad/reports/CAL-29.md)
    # permite patrón con * si viene de heurística
    if "*" in expected:
        return bool(glob.glob(os.path.join(repo, expected)))
    abs_path = os.path.join(repo, expected) if not os.path.isabs(expected) else expected
    if os.path.exists(abs_path):
        return True
    # fallback: glob por si es relativo sin epic específico
    return bool(glob.glob(os.path.join(repo, expected)))


def _has_delivery_evidence(db: sqlite3.Connection, sid: str, ticket: str | None, repo: str, root: str) -> bool:
    """CAL-29: entrega verificable por script — mensaje en buzón del PM o artefacto comprometido."""
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
    for pat in [os.path.join(repo, "epic-*", "reports", f"{ticket}.md"),
                os.path.join(repo, "epic-*", "reports", f"{ticket}.*")]:
        if glob.glob(pat):
            return True
    # expected inferido como en lanzar.py
    inferred = os.path.join("epic-calidad", "reports", f"{ticket}.md")
    if _expected_exists(inferred, repo):
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
        repo = os.path.dirname(os.path.abspath(root))
        for pat in [os.path.join(repo, "epic-*", "STATUS.md"),
                    os.path.join(repo, "epic-*", "STATUS-*.md")]:
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


def reap_finished(root: str, db_path: str = DB, window_ms: int | None = None) -> list[str]:
    """Avisa al PM cuando un dev termina bien (succeeded) sin entrega verificable (CAL-29).

    Escanea session_v2 donde time_idle > time_busy y outcome=='succeeded',
    filtra devs, y solo considera sesiones dentro de la ventana (24h) y que
    estén en inbox/.lanzar/log (corrige M-260–M-617 sin ventana que barrió historial
    y marcó `? dev`). Un dev cuenta como éxito solo si tiene evidencia por script:
    mensaje en buzón del PM con resultado o artefacto comprometido (reporte/archivo/fila
    STATUS). Sin eso se avisa con "terminó sin entregar" (distingue de "murió sin
    reportar" para failed). Un solo aviso por sesión (dedup .aviso-dev con flock),
    con ventana, solo lanzar/log, no edita STATUS, no cuenta para CAP.
    """
    if window_ms is None:
        window_ms = VENTANA_MS
    if not os.path.exists(db_path):
        return []
    aviso_path = os.path.join(root, ".lanzar", ".aviso-dev")
    os.makedirs(os.path.dirname(aviso_path), exist_ok=True)
    repo = os.path.dirname(os.path.abspath(root))
    done: list[str] = []
    # filtros CAL-29: solo lanzar/log y ventana
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
            # CAL-29: ventana temporal
            if not _is_within_window(idle, now_ms):
                continue
            # CAL-29: solo sesiones de lanzar/log (si hay log, filtra)
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
    # 2) dedup con flock y envío — CAL-29: verifica entrega por script
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
            for sid, title, parent_id, _idle in candidates:
                if sid in seen:
                    continue
                ticket = _ticket_from_title(title) or _ticket_from_prompt(db2, sid) or "?"
                # CAL-29: done verificable — evidencia por script
                # si tiene entrega (buzón o artefacto), se considera éxito y no se avisa
                if _has_delivery_evidence(db2, sid, ticket if ticket != "?" else None, repo, root):
                    # marca como visto para no re-evaluar en próximos barridos
                    # (no avisamos, pero evitamos re-chequear indefinidamente si ya tiene entrega)
                    # No escribimos en .aviso-dev para los entregados: solo dedup de no-entregados
                    continue
                # re-verifica por carrera (si llegó un send entre colecta y lock)
                if _has_inbox_send(db2, sid):
                    seen.add(sid)
                    continue
                pm = _resolve_pm(db2, root, sid, title, parent_id)
                shas = _commits_of(db2, sid)
                reported = _is_reported(ticket if ticket != "?" else None, repo)
                commits_txt = " ".join(shas) if shas else "—"
                reported_txt = "sí" if reported else "no"
                # CAL-29: distingue "terminó sin entregar" de "murió sin reportar"
                msg = (f"{ticket} dev {sid} terminó sin entregar: commits {commits_txt} | "
                       f"reported: {reported_txt} | session {sid}")
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
    # CAL-27: aviso de dev terminado, un solo punto que cubre 100% (barrido). No cuenta para CAP.
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
        left = held(new, HOLD.get(pm, 0), datetime.datetime.now().astimezone())
        if left:
            if not holding:
                log(root, pm, f"held {' '.join(m.id for m in new)}: avisos, wake in {left / 60:.0f} min")
                holding = True
            time.sleep(min(poll, left))
            continue
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
