# <ID> · PM review (<time from date>) · **<CLOSED | CLOSED (with notes) | RETURNED>**

## What is real (verified)

- The artifact was opened at `<path>`.
- Each pasted command was re-run, and it matches or it doesn't. Use `scripts/check_report.py`.
- The session log was checked: the session `<id>` ran `<command>` at `<time>`, and it edited
  `<file>` at `<time>`.
- The PM's own suite run: `<command>` → `<result>`.
- Nothing outside the lane changed (hashes and mtimes).
- Runtime records: the session was one-shot (`prompts=1`), and its logbook agrees with them
  (test runs, duration; `adapters/opencode.md`, «Measuring a session»). The verdict and the cause
  of any return are recorded here and in the ticket's row.

## Returned (if any)

**E1 · <short name> (rule)**
- The evidence: what the report says, and what reality says.
- What must be pasted instead.

## Notes (closed with notes: form problems, no false data)

1. ...

## Closing actions

- SHA entregado y verificado por el PM: `<SHA>`; evidencia y resultado: `<ruta/comando>`.
- Rama subida por el PM y PR a main: `<rama>` / `<enlace>`; HEAD revisado: `<SHA>`.
- Preview oficial del PO: `<URL/rutas>`; SHA del PR: `<SHA>`; worktree: `<ruta>`.
- Servidor: `<comando, PID/job, identidad al inicio, puerto, log, responsable>`; stack de datos verificado sin secretos: `<local/remoto>`.
- Cambios explicados y pasos de revisión: `<antes/después y rutas>`; OK visual del dueño: `<referencia>`.
- Autorización y merge del PO: `<referencia>`; SHA integrado: `<SHA>`.
- Gate del SHA integrado desde principal: `<comando>` → `<resultado>`.
- Preview apagado por el PO: `<resultado>`; proceso propio detenido y puerto sin listener: `<evidencia>`.
- Cierre del PO `dev_worktree.py cerrar`: `<resultado>`; worktree y rama local retirados: `<evidencia>`.
- Solo entonces: actualizaciones de `EPIC.md` o del tablero que este cierre requiere.

---

## Return 1 · PM review (<time>) · **<verdict>**

- What was checked again, and the result.
