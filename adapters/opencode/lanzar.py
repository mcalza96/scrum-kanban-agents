#!/usr/bin/env python3
"""Lanzador de devs desde el servicio opencode.

Vive en el plugin (inbox.js lo arranca) porque un proceso desacoplado lanzado
desde el shell del PM no sobrevive al turno.

Tope (decisión del dueño, 2026-11-05): MAX_GLOBAL = 10 devs en la máquina (uno por núcleo). Sin tope por PM:
un PM puede tener varios devs a la vez mientras quepan en el global.
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
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "../../scripts"))
import inbox  # noqa: E402

MAX_GLOBAL = 10
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
    """Cuenta sesiones ocupadas y reservas todavía sin sesión, sin duplicarlas."""
    entries = inbox.launch_records(root)
    reservations = []
    logged_ids = {e.get("id") for e in entries}
    for path in glob.glob(os.path.join(qdir_of(root), "*.json.claim")):
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        if entry.get("id") not in logged_ids:
            reservations.append(entry)
    active = []
    if not os.path.exists(db_path):
        active = entries  # sin runtime observable, no inventar cupos libres
    else:
        db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            for e in entries:
                row = db.execute("select time_busy, time_idle from session_v2 where id = ?", (e.get("session"),)).fetchone()
                if (row and (row[0] or 0) > (row[1] or 0)) or (row is None and e.get("title")):
                    active.append(e)
        finally:
            db.close()
    active += reservations
    counts: dict[str, int] = {}
    for e in active:
        pm = e.get("pm", "")
        counts[pm] = counts.get(pm, 0) + 1
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


def _record_session(root: str, entry: dict, sid: str) -> str:
    # También se llama desde la reconciliación: el registro por pedido es idempotente.
    with inbox.launch_lock(root):
        _record_session_locked(root, entry, sid)
    return sid


def _record_session_locked(root: str, entry: dict, sid: str) -> None:
    prior = next((e for e in inbox.launch_records(root) if e.get("id") == entry["id"]), None)
    if prior and prior["session"] != sid:
        raise ValueError(f"pedido {entry['id']} asociado a dos sesiones; requiere revisión")
    if not prior:
        record = {k: entry[k] for k in ("pm", "ticket", "prompt", "from", "id", "expected", "retry", "title") if k in entry}
        record.update(ts=datetime.datetime.now().astimezone().isoformat(), session=sid)
        _append_log(root, record)
    try:
        os.remove(entry["_path"])
    except FileNotFoundError:
        pass


def _reconcile_claims(root: str, db_path: str) -> None:
    # Solo recuperar una sesión con la identidad exacta del pedido, nunca por ticket.
    records = {e.get("id"): e for e in inbox.launch_records(root)}
    for path in glob.glob(os.path.join(qdir_of(root), "*.json.claim")):
        with open(path, encoding="utf-8") as f:
            entry = json.load(f)
        entry["_path"] = path
        prior = records.get(entry.get("id"))
        sid = prior.get("session") if prior else _fallback_session_id(db_path, entry)
        if sid:
            _record_session_locked(root, entry, sid)
        elif entry.get("launcher_pid") and entry.get("status") == "launching":
            try:
                os.kill(entry["launcher_pid"], 0)
            except ProcessLookupError:
                entry.update(status="uncertain", error="el lanzador terminó sin registrar sesión")
                inbox.write_request(path, entry)
                _notify_launch_error(root, entry, "inicio incierto tras caída; reserva retenida, requiere reconciliación")
            except PermissionError:
                pass  # No demostrar la muerte del proceso no permite liberar su reserva.


def try_launch_one(root: str, opencode_bin: str | None = None, runner=None, db_path: str = DB) -> str | None:
    """Reserva bajo candado; ejecutar y esperar al CLI nunca retiene ese candado."""
    if not os.path.isdir(root):
        raise FileNotFoundError(f"no existe el buzón {root}")
    os.makedirs(qdir_of(root), exist_ok=True)
    with inbox.launch_lock(root):
        _reconcile_claims(root, db_path)
        gcount, _, _ = _active_counts(root, db_path)
        paused = inbox.pause(root)
        entry = next((e for e in _load_queue(root)
                      if not paused or e.get("pm") == paused["owner"]), None)
        if entry is None or gcount >= MAX_GLOBAL:
            return None
        claimed = entry["_path"] + ".claim"
        entry.update(status="launching", launcher_pid=os.getpid(),
                     title=f"dev {entry['ticket']} [{entry['id']}]",
                     directory=os.path.dirname(os.path.abspath(root)))
        inbox.write_request(entry["_path"], entry)
        os.rename(entry["_path"], claimed)
        entry["_path"] = claimed
    _reap_failed(root, db_path)
    return _launch_entry(root, entry, opencode_bin, runner, db_path)


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


def _session_id(line: str) -> str | None:
    try:
        event = json.loads(line)
        sid = event.get("sessionID") if isinstance(event, dict) else None
        return sid if isinstance(sid, str) and sid.startswith("ses_") else None
    except ValueError:
        return None


def _stream_run(cmd: list[str], text: str, repo: str, on_session) -> tuple[str, str, int]:
    # Leer stdout mientras corre: registrar el ID no debe esperar al fin del turno.
    # stderr va a un archivo temporal para no bloquearse si su pipe se llena.
    with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as errors:
        proc = subprocess.Popen(cmd + [text], cwd=repo, stdout=subprocess.PIPE,
                                stderr=errors, text=True)
        timer = threading.Timer(300, proc.kill)  # conserva el límite existente del CLI
        timer.start()
        try:
            with proc.stdout:
                for line in proc.stdout:
                    sid = _session_id(line)
                    if sid:
                        on_session(sid)
            code = proc.wait()
        finally:
            timer.cancel()
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        errors.seek(0)
        return "", errors.read(), code


def _launch_entry(root: str, entry: dict, opencode_bin: str | None, runner, db_path: str = DB) -> str | None:
    sid = None

    def observed(value: str) -> None:
        nonlocal sid
        if sid is None:
            sid = _record_session(root, entry, value)
        elif value != sid:
            raise ValueError("el CLI devolvió dos sesiones para el mismo pedido")

    try:
        with open(entry["prompt"], encoding="utf-8") as f:
            prompt_text = _ensure_close_block(f.read(), entry.get("pm", ""), root, entry["ticket"])
        opencode = opencode_bin or shutil.which("opencode") or os.path.expanduser("~/.opencode/bin/opencode")
        cmd = [opencode, "run", "--title", entry["title"], "--format", "json"]
        if runner is None:
            out, err, code = _stream_run(cmd, prompt_text, entry["directory"], observed)
        else:
            out, err, code = runner(cmd, prompt_text)
        for line in (out or "").splitlines():
            value = _session_id(line)
            if value:
                observed(value)
        if sid is None:
            recovered = _fallback_session_id(db_path, entry)
            if recovered:
                observed(recovered)
        if sid is None:
            raise RuntimeError(err or f"CLI terminó con código {code} sin sessionID")
        return sid
    except Exception as e:
        if sid is None:
            # No hay prueba de que no haya empezado: conservar reserva y bloquear duplicados.
            with inbox.launch_lock(root):
                # Otro lanzador pudo reconciliarlo mientras esperábamos al CLI.
                prior = next((r for r in inbox.launch_records(root) if r.get("id") == entry["id"]), None)
                if prior:
                    return prior["session"]
                entry.update(status="uncertain", error=str(e))
                inbox.write_request(entry["_path"], entry)
            _notify_launch_error(root, entry, f"inicio incierto, no reintentar automáticamente: {e}")
        return sid


def _fallback_session_id(db_path: str, entry: dict) -> str | None:
    if not entry.get("title") or not os.path.exists(db_path):
        return None
    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        rows = db.execute("select id from session_v2 where title = ?", (entry["title"],)).fetchall()
        if len(rows) > 1:
            raise ValueError(f"más de una sesión para {entry['id']}; requiere revisión")
        return rows[0][0] if rows else None
    finally:
        db.close()


def _notify_launch_error(root: str, entry: dict, msg: str) -> None:
    try:
        inbox.send(root, (entry.get("pm") or "PO"), "lanzador", f"[bloqueo] {entry.get('ticket')}: {msg}")
    except Exception:
        pass


def _reap_failed(root: str, db_path: str = DB) -> None:
    """Si una sesión activa terminó failed sin mandar buzón, avisa al PM."""
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
