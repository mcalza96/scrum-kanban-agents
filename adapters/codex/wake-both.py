#!/usr/bin/env python3
"""Wrapper sin segundo vigía: un launchd observa PO.md y Director-entrega.md,
despierta ambos buzones con estado separado y misma agrupación pending/--complete."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import wake as wake_po  # type: ignore

# Configuración: reutiliza thread PO existente y crea uno para Director-entrega
PO_CFG = {
    "mailbox": "PO",
    "thread": "01a1a17b-5247-7700-b763-ee82f0caaaec",
    "state": "/Users/mcalzadilla/.local/state/codex-po-portafolio/wake.json",
    "codex": "/Applications/ChatGPT.app/Contents/Resources/codex-cli/CodexCLI.app/Contents/MacOS/codex",
}
DE_CFG = {
    "mailbox": "Director-entrega",
    "thread": PO_CFG["thread"],
    "state": "/Users/mcalzadilla/.local/state/codex-director-entrega/wake.json",
    "codex": PO_CFG["codex"],
}

def _wake_mailbox(root: str, cfg: dict, **kw):
    # monkey-patch inbox.messages mailbox for this call by temporarily replacing
    import inbox
    orig_messages = inbox.messages
    def patched(root_, pm_):
        if pm_ == cfg["mailbox"]:
            return orig_messages(root_, pm_)
        return orig_messages(root_, pm_)
    # call wake with mailbox-specific state/thread but same logic (pending grouping)
    # We reuse wake.wake but need to pass mailbox; instead call directly with custom logic
    # For now, duplicate wake logic for generic mailbox
    import fcntl, json, shlex, subprocess
    state = Path(cfg["state"])
    state.parent.mkdir(parents=True, exist_ok=True)
    with state.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        saved = json.loads(state.read_text()) if state.exists() else {"ids": []}
        if kw.get("complete"):
            saved["pending"] = False
            saved["ids"] = list(dict.fromkeys(saved["ids"] + list(kw.get("observed", []))))
        elif saved.get("pending"):
            return f"{cfg['mailbox']}: Aviso pendiente; mensajes agrupados"
        unread = [m.id for m in inbox.messages(str(root), cfg["mailbox"]) if m.unread]
        fresh = [mid for mid in unread if mid not in saved["ids"]]
        if not fresh:
            if kw.get("complete"):
                tmp = state.with_suffix(".tmp")
                tmp.write_text(json.dumps(saved) + "\n")
                tmp.replace(state)
            return f"{cfg['mailbox']}: Sin mensajes nuevos"
        completion = shlex.join([sys.executable, str(Path(__file__).resolve()),
                                 "--root", str(root), "--thread", cfg["thread"],
                                 "--state", str(state), "--codex", cfg["codex"],
                                 "--complete", "--observed"])
        prompt = ("Evento del buzón opencode: hay mensajes nuevos para " + cfg["mailbox"] +
                  " (" + ", ".join(fresh) + "). Lee " + str(root) + "/" + cfg["mailbox"] + ".md con inbox.py read, "
                  "verifica y actúa según la skill scrum-kanban-agents. Haz ack después de actuar. "
                  "Conserva el trabajo en curso. Este evento no autoriza push, deploy ni mutaciones de producción. "
                  "Antes de terminar, relee el buzón y ejecuta " + completion +
                  " seguido de todos los IDs observados en esta ronda, separados por espacios; "
                  "si no observaste ninguno, omite --observed. Esto libera el aviso agrupado "
                  "y vuelve a encolar solo mensajes que llegaron después de la lectura.")
        result = subprocess.run([cfg["codex"], "queue", "--thread", cfg["thread"], "--message", prompt, "--resume"],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            raise RuntimeError(f"{cfg['mailbox']}: No se confirmó el despertar: " + result.stderr.strip())
        saved["ids"] = list(dict.fromkeys(saved["ids"] + fresh))
        saved["pending"] = True
        tmp = state.with_suffix(".tmp")
        tmp.write_text(json.dumps(saved) + "\n")
        tmp.replace(state)
        return f"{cfg['mailbox']}: Cola y solicitud de despertar aceptadas: " + ", ".join(fresh)

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    ap.add_argument("--complete", action="store_true")
    ap.add_argument("--observed", nargs="*", default=[])
    # allow calling with old args for compatibility, but ignore thread/state/codex (use configs)
    ap.add_argument("--thread", required=False, default="")
    ap.add_argument("--state", required=False, default="")
    ap.add_argument("--codex", required=False, default=PO_CFG["codex"])
    a = ap.parse_args()
    if (Path(a.root) / ".git").exists():  # el repo: el buzón sale de .scrum.toml
        import gestion
        a.root = gestion.inbox_root(a.root)
    # When called with --complete, we need to know which mailbox; use state path to infer
    if a.complete and a.state:
        # reverse lookup
        if "director-entrega" in a.state.lower():
            cfg = DE_CFG
        else:
            cfg = PO_CFG
        print(_wake_mailbox(a.root, cfg, complete=True, observed=a.observed))
    else:
        # normal trigger: wake both
        out1 = _wake_mailbox(a.root, PO_CFG)
        out2 = _wake_mailbox(a.root, DE_CFG)
        print(out1 + " | " + out2)
