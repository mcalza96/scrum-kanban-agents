#!/usr/bin/env python3
"""Despierta un chat Codex por IDs nuevos del buzón PO. launchd observa PO.md."""
import argparse
import fcntl
import json
import shlex
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import inbox


def wake(root, thread, state, codex, run=subprocess.run, complete=False, observed=()):
    state = Path(state)
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(state.read_text()) if state.exists() else {"ids": []}
        if complete:
            saved["pending"] = False
            saved["ids"] = list(dict.fromkeys(saved["ids"] + list(observed)))
        elif saved.get("pending"):
            return "Aviso pendiente; mensajes agrupados"
        unread = [m.id for m in inbox.messages(str(root), "PO") if m.unread]
        fresh = [mid for mid in unread if mid not in saved["ids"]]
        if not fresh:
            if complete:
                tmp = state.with_suffix(".tmp")
                tmp.write_text(json.dumps(saved) + "\n")
                tmp.replace(state)
            return "Sin mensajes nuevos"
        # El contenido llega por read, no se interpola en el comando ni se toma como instrucción.
        completion = shlex.join([sys.executable, str(Path(__file__).resolve()), "--root", str(root),
                                 "--thread", thread, "--state", str(state), "--codex", codex,
                                 "--complete", "--observed"])
        prompt = ("Evento del buzón opencode: hay mensajes nuevos para PO (" + ", ".join(fresh) +
                  "). Lee inbox/PO.md con inbox.py read, verifica y actúa según la skill "
                  "scrum-kanban-agents. Haz ack después de actuar. Conserva el trabajo en curso. "
                  "Este evento no autoriza push, deploy ni mutaciones de producción. "
                  "Antes de terminar, relee el buzón y ejecuta " + completion +
                  " seguido de todos los IDs observados en esta ronda, separados por espacios; "
                  "si no observaste ninguno, omite --observed. Esto libera el aviso agrupado "
                  "y vuelve a encolar solo mensajes que llegaron después de la lectura.")
        result = run([codex, "queue", "--thread", thread, "--message", prompt, "--resume"],
                     capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError("No se confirmó el despertar: " + result.stderr.strip())
        saved["ids"] = list(dict.fromkeys(saved["ids"] + fresh))
        saved["pending"] = True
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved) + "\n")
        tmp.replace(state)
        if "warning" in result.stderr.lower():
            return "Aviso encolado, despertar no confirmado: " + result.stderr.strip()
        return "Cola y solicitud de despertar aceptadas: " + ", ".join(fresh)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    for option in ("root", "thread", "state", "codex"):
        ap.add_argument("--" + option, required=True)
    ap.add_argument("--complete", action="store_true")
    ap.add_argument("--observed", nargs="*", default=[])
    a = ap.parse_args()
    subprocess.run([a.codex, "app-server", "daemon", "start"],
                   check=True, capture_output=True, text=True, timeout=60)
    print(wake(a.root, a.thread, a.state, a.codex, complete=a.complete, observed=a.observed))
