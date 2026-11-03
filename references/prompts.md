# Prompts for dev sessions

The dev does not see the PM's conversation. If something is not in the prompt or in a file the
prompt points to, the dev does not know it.

## Rules

1. **First line: `TO: <agent>`.** With several agents and terminals open, a prompt without an
   addressee ends up with the wrong agent. This happened once, and the agent did work in another
   lane.
2. **Assume a brand-new session.** Don't write «as we discussed» or «continue». Give the ticket
   path and the files to read first.
3. **Use the paths the agent sees.** If the agent runs in a container, give container paths, or
   give both and say which is which.
4. **Include the shared rules, or point to them.** Evidence (`evidence.md`), speed
   (`test-speed.md`), and gates (`gates.md`). A pointer is fine only if the file is short and the
   prompt says «read it before starting».
5. **Say what is forbidden**:
   - lanes the agent must not touch;
   - hidden sets it must not read;
   - commits, overrides, and model or settings changes;
   - publishing.
6. **Say where to stop.** Give the ticket part and the STOP point. Say what to do if a gate change
   seems necessary: stop and report.
7. **Say how to report.** Give the report path, the row to update on the board, and the state to
   set. Ask for the logbook (`traceability.md`).
8. **Say that the session is one-shot.** One task, then stop. Any other request in that session
   gets the answer «new session». Don't send follow-up questions to a session that has already
   reported. Put them in the next task's prompt.

Template: `templates/PROMPT.md`.

## Lanzamiento vía servicio (CAL-25, D-278) — reemplaza `subagent` para devs

El PM no espera al dev en la herramienta `subagent` (33 % del tiempo de PM bloqueado, 2026-11-03).
En su lugar:

1. Escribe el prompt del dev en `epic-*/prompts/<ticket>-dev.md` (como antes).
2. Encola el pedido sin bloquearse: `python3 scripts/inbox.py lanzar inbox/ --pm PM-x --ticket CAL-n --prompt <ruta>`.
   Esto crea `inbox/.lanzar/<id>.json` y vuelve al instante con el id de la petición. Si el mismo ticket ya está en cola, tomado (`.claim`) o con dev vivo, `lanzar` rechaza el segundo pedido y dice `en cola, no reintentes, mira inbox/.lanzar/log` (candado D-285). No toques `inbox/.lanzar/` a mano (rm/mv/cp): deja que el lanzador lo gestione.
3. El plugin del servicio (`adapters/opencode/inbox.js` → `adapters/opencode/lanzar.py`) observa
   `inbox/.lanzar/` y arranca `opencode run --title "dev <ticket>"` con el contenido del prompt.
   Usa el mismo modelo y agente que hoy y el directorio del ticket. El PM no queda esperando.
4. El dev sigue siendo one-shot con `TO:` y una sola tarea; al terminar escribe su reporte, pone su
   fila en `reported` y avisa al PM por buzón: `inbox.py send --to PM-x` citando ticket y ruta del reporte.
   `wake.py` despierta al PM.

`subagent` para devs queda **reemplazado** por este flujo; no usarlo para lanzar devs (sigue disponible
para otras herramientas si hiciera falta).

## Return prompts

A return goes to a **new session**, never to the session that produced the false data. That
session's context still holds the reasoning that led to the problem.

The return prompt:

- starts with `TO: <agent>`;
- points to the ticket, the original report, and the review;
- lists **only** the findings to fix (E1, E2, ...), each with the expected evidence;
- says whether the code may change. Usually it must not: «the code is approved, only the evidence
  is returned»;
- asks for the corrected sections to be appended or replaced in the same report file.
