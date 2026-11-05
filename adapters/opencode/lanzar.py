#!/usr/bin/env python3
"""Lanzador de devs desde el servicio opencode.

Vive en el plugin (inbox.js lo arranca) porque un proceso desacoplado lanzado
desde el shell del PM no sobrevive al turno.

Topes (decisión del dueño):
  MAX_PER_PM = 1 dev por PM (igual que hoy, el PM gana capacidad mientras su dev trabaja)
  MAX_GLOBAL = 4 devs en la máquina
Subir el tope es decisión del dueño con medición en mano (epic-calidad/medicion/medir-agentes.py).

Cola: inbox/.lanzar/*.json (cada uno es un pedido de inbox.py lanzar).
Log de trazabilidad: inbox/.lanzar/log  (una línea JSON por lanzamiento: pm, ticket, session, prompt, ts, parent)
Si opencode run expone parent_id se usa; si no, el log es el registro.
Título del dev empieza con "dev " para que medir-agentes.py lo distinga.

Falla sin reporte: si la sesión termina con outcome failed y no mandó buzón, se deja aviso al PM.
No reintenta solo.

Este módulo es importable para tests unitarios; el demonio se arranca con `python3 lanzar.py <inbox_root>`.
No usa `sleep` en loop: el plugin lo despierta por watch + timer.
"""
from __future__ import annotations

import datetime
import glob
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../scripts"))
import inbox  # noqa: E402

MAX_PER_PM = 1
MAX_GLOBAL = 4
DB = os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))

def qdir_of(root: str) -> str:
    return os.path.join(root, ".lanzar")


def log_path(root: str) -> str:
    return os.path.join(qdir_of(root), "log")


def _load_queue(root: str) -> list[dict]:
    qdir = qdir_of(root)
    out = []
    for p in glob.glob(os.path.join(qdir, "*.json")):
        if os.path.basename(p) == "log":
            continue
        try:
            with open(p, encoding="utf-8") as f:
                d = json.load(f)
            d["_path"] = p
            # mtime for FIFO
            d["_mtime"] = os.path.getmtime(p)
            out.append(d)
        except Exception:
            continue
    out.sort(key=lambda d: d["_mtime"])
    return out


def _active_counts(root: str, db_path: str = DB) -> tuple[int, dict[str, int], list[dict]]:
    """Cuenta sesiones activas (busy) según log y DB. Retorna (global, por_pm, entries).
    Si no hay DB, cuenta por log sin verificar busy (para tests)."""
    lp = log_path(root)
    entries = []
    try:
        with open(lp, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except ValueError:
                    continue
    except FileNotFoundError:
        return 0, {}, []
    # keep only recent active? launch log is append-only; mark done when session idle.
    # For now, count those whose session still busy in DB.
    if not os.path.exists(db_path):
        # tests without DB: count all as active (caller mocks elsewhere)
        counts: dict[str, int] = {}
        for e in entries:
            counts[e.get("pm", "")] = counts.get(e.get("pm", ""), 0) + 1
        return len(entries), counts, entries
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except Exception:
        return 0, {}, entries
    active = []
    counts = {}
    for e in entries:
        sid = e.get("session")
        if not sid:
            continue
        try:
            row = db.execute("select time_busy, time_idle from session_v2 where id = ?", (sid,)).fetchone()
            if not row:
                continue
            busy = (row[0] or 0) > (row[1] or 0)
            if busy:
                active.append(e)
                pm = e.get("pm", "")
                counts[pm] = counts.get(pm, 0) + 1
        except Exception:
            continue
    db.close()
    return len(active), counts, active


def _is_session_failed(sid: str, db_path: str = DB) -> str | None:
    """Si la última vuelta terminó failed, retorna el mensaje de error, si no None."""
    if not os.path.exists(db_path):
        return None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = db.execute(
            "select data from session_message where session_id = ? and type = 'idle' order by time_created desc limit 1",
            (sid,),
        ).fetchone()
        db.close()
        if not row:
            return None
        data = json.loads(row[0])
        if not isinstance(data, dict) or data.get("outcome") != "failed":
            return None
        err = data.get("error") or {}
        return (err.get("message") if isinstance(err, dict) else str(err)) or "unknown error"
    except Exception:
        return None


def _append_log(root: str, entry: dict) -> None:
    if not os.path.isdir(root):  # a stale root: never recreate it
        raise FileNotFoundError(f"no existe el buzón {root}")
    os.makedirs(qdir_of(root), exist_ok=True)
    with open(log_path(root), "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def try_launch_one(
    root: str,
    opencode_bin: str | None = None,
    runner=None,
    db_path: str = DB,
) -> str | None:
    """Intenta lanzar el primer pedido en cola que cabe en los topes. Retorna session id o None."""
    q = _load_queue(root)
    if not q:
        return None
    gcount, pcounts, _ = _active_counts(root, db_path)
    # also reap failed sessions that died without report
    _reap_failed(root, db_path)
    # recount after reap
    gcount, pcounts, _ = _active_counts(root, db_path)
    paused = inbox.pause(root)
    for entry in q:
        pm = entry.get("pm", "")
        if paused and pm != paused["owner"]:
            continue  # queued, not lost: it goes when the pause is lifted
        if pcounts.get(pm, 0) >= MAX_PER_PM:
            continue
        if gcount >= MAX_GLOBAL:
            break  # global lleno: nada más cabe (FIFO estricto si cabeza bloquea global)
            # pero si permitimos saltar, usar continue; elegimos FIFO estricto para simplicidad
        # claim atómico: solo un lanzador puede tomar el archivo
        claimed = entry["_path"] + ".claim"
        try:
            os.rename(entry["_path"], claimed)
            entry["_path"] = claimed
        except FileNotFoundError:
            continue  # otro proceso ya lo tomó
        except OSError:
            continue
        # launch this one
        sid = _launch_entry(root, entry, opencode_bin, runner)
        # si falló sin session, devuelve el archivo a cola para reintento manual (no loop)
        if not sid and os.path.exists(claimed):
            try:
                orig = claimed[:-6]
                os.rename(claimed, orig)
                _notify_launch_error(root, entry, "re-encolado tras fallo sin session")
            except OSError:
                pass
        return sid
    return None


def _has_close_block(text: str, pm: str) -> bool:
    return "inbox.py send" in text and f"--to {pm}" in text


def _close_block(pm: str, root: str, ticket: str) -> str:
    # Ruta de inbox.py resuelta desde este archivo, con pm y root ya concretos
    inbox_py = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../scripts/inbox.py"))
    return (
        f'\n\n5. Tell the PM: `python3 {inbox_py} send --to {pm} --from dev-{ticket} {root} '
        f'"[aviso] {ticket} reported: <report path>"`.\n'
    )


def _ensure_close_block(text: str, pm: str, root: str, ticket: str) -> str:
    if _has_close_block(text, pm):
        return text
    return text.rstrip() + _close_block(pm, root, ticket)


def _launch_entry(root: str, entry: dict, opencode_bin: str | None, runner) -> str | None:
    prompt_path = entry.get("prompt", "")
    try:
        with open(prompt_path, encoding="utf-8") as f:
            prompt_text = f.read()
    except Exception as e:
        _notify_launch_error(root, entry, str(e))
        try:
            os.remove(entry["_path"])
        except FileNotFoundError:
            pass
        return None
    ticket = entry.get("ticket", "CAL-?")
    # M-88 arreglo 1: todo prompt lleva el bloque de cierre con PM y raíz resueltos; si falta lo agrega
    prompt_text = _ensure_close_block(prompt_text, entry.get("pm", ""), root, ticket)
    title = f"dev {ticket}"
    # directory: repo that holds inbox/
    repo = os.path.dirname(os.path.abspath(root))
    opencode = opencode_bin or shutil.which("opencode") or os.path.expanduser("~/.opencode/bin/opencode")
    cmd = [opencode, "run", "--title", title, "--format", "json"]
    # model/agent: use defaults
    if runner is None:
        def runner(cmd, text):
            proc = subprocess.Popen(cmd + [text], cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                out, err = proc.communicate(timeout=300)
            except subprocess.TimeoutExpired:
                # opencode run debería volver al instante con sessionID; si colgó 5 min,
                # matamos y buscamos id parcial antes de reintentar — evita duplicar pedido
                try:
                    proc.kill()
                    out, err = proc.communicate(timeout=5)
                except Exception:
                    out, err = "", "timeout 300s sin sessionID"
                # si hubo sessionID parcial, lo consideramos éxito abajo
            return out, err, proc.returncode

    try:
        out, err, code = runner(cmd, prompt_text)
        sid = None
        for line in (out or "").splitlines():
            try:
                j = json.loads(line)
                if j.get("sessionID"):
                    sid = j["sessionID"]
                    break
            except ValueError:
                continue
        if not sid:
            # fallback: try to parse session id from stderr or look at DB newest session
            sid = _fallback_session_id(db_path, ticket)
        if not sid:
            _notify_launch_error(root, entry, err or out or "no session id")
            return None
        # trazabilidad — registra entrega esperada sin bloquear
        expected = entry.get("expected") or inbox._infer_expected(ticket, prompt_path)
        log_entry = {
            "ts": datetime.datetime.now().astimezone().isoformat(),
            "pm": entry.get("pm"),
            "ticket": ticket,
            "session": sid,
            "prompt": prompt_path,
            "from": entry.get("from"),
            "id": entry.get("id"),
        }
        if expected:
            log_entry["expected"] = expected
        _append_log(root, log_entry)
        # el pedido ya estaba en .claim, lo borramos
        try:
            os.remove(entry["_path"])
        except FileNotFoundError:
            pass
        return sid
    except Exception as e:
        _notify_launch_error(root, entry, str(e))
        # deja el .claim para inspección; no re-encola automático para no duplicar
        return None


def _fallback_session_id(db_path: str, ticket: str) -> str | None:
    if not os.path.exists(db_path):
        return None
    try:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        row = db.execute("select id from session_v2 where title = ? order by time_created desc limit 1", (f"dev {ticket}",)).fetchone()
        db.close()
        return row[0] if row else None
    except Exception:
        return None


def _notify_launch_error(root: str, entry: dict, msg: str) -> None:
    try:
        inbox.send(root, (entry.get("pm") or "PO"), "lanzador", f"{entry.get('ticket')} no lanzó: {msg} (sesión sin id)")
    except Exception:
        pass


def _reap_failed(root: str, db_path: str = DB) -> None:
    """Si una sesión activa terminó failed sin mandar buzón, avisa al PM."""
    _, _, active = _active_counts(root, db_path)
    # But _active_counts already filters busy only; failed sessions are idle, so we need log entries not active?
    # Check all log entries that are idle+failed.
    lp = log_path(root)
    try:
        with open(lp, encoding="utf-8") as f:
            entries = [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return
    # dedup by checking inbox/.lanzar/.failed sentinel
    failed_mark = os.path.join(qdir_of(root), ".failed")
    seen = set()
    if os.path.exists(failed_mark):
        try:
            with open(failed_mark, encoding="utf-8") as f:
                seen = set(l.strip() for l in f)
        except Exception:
            seen = set()
    for e in entries:
        sid = e.get("session")
        if not sid or sid in seen:
            continue
        err = _is_session_failed(sid, db_path)
        if err:
            # check if dev already sent a mailbox message: look for any message that mentions ticket+session?
            # heuristic: if PM mailbox already has a message from dev about this ticket, don't duplicate.
            # Simple: always notify once.
            inbox.send(root, (e.get("pm") or "PO"), "lanzador", f"{e.get('ticket')} dev {sid} murió sin reportar: {err}")
            seen.add(sid)
    if seen:
        # persist marks
        try:
            with open(failed_mark, "w", encoding="utf-8") as f:
                for s in seen:
                    f.write(s + "\n")
        except Exception:
            pass


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        print(__doc__)
        return 2
    root = os.path.abspath(argv[0])
    # daemon: watch queue dir and launch
    # Sin sleep en loop: usa watch con polling interval del plugin; aquí solo hace un intento
    sid = try_launch_one(root)
    if sid:
        print(sid)
    else:
        print("nada para lanzar o topes llenos")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
