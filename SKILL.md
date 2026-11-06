---
name: scrum-kanban-agents
description: "Run a software project with an AI PM and AI dev agents: tickets, a status board, verified evidence, gates that don't overfit, and a plan to finish."
version: 0.10.0
license: MIT
tags: [scrum, kanban, pm, agents, verification, delegation, evaluation]
---

# Scrum/Kanban with AI agents as the dev team

One agent (or a human) is the **PM**. Other agent sessions are the **devs**. The PM writes tickets,
the devs implement and report, and the PM verifies the report against reality before closing
anything. The board is the record of truth: work that is not on the board does not exist, and work
is not done until the PM has verified it.

This skill is tool-agnostic. It works with any coding agent (Claude Code, opencode, Codex, Aider,
...) and with any board: a kanban CLI, an issue tracker, or a plain `STATUS.md` file.

## When to use

- The work spans several sessions, several files, or several agents.
- Some pieces can be built in parallel.
- The human wants to decide and review, not to drive every step.

Don't use it for a single change (just do it), for open research, or for work that needs a live
human decision at every step.

## Roles, never collapsed

| Role | Who | Does | Does NOT |
|---|---|---|---|
| Stakeholder | The human | Prioritizes, decides, accepts | Edit the board rows of others |
| Auditor | One agent session, optional, outside the flow and without a mailbox | Measures the runtime's records when the human asks (or every few hours) and reports to the human with evidence | Receive progress reports, prioritize, assign, launch devs, kill processes, edit code |
| PO | Agente responsable del producto, bajo las decisiones del dueño | Revisa alcance, prioridad y dependencias; presenta localhost al dueño, ejecuta merge y cierre de preview/worktree con gate integrado | Sustituir la verificación técnica del PM |
| PM | One agent session (or the human) | Escribe tickets, verifica commits y reportes, sube la rama revisada y abre el PR; actualiza closed con evidencia del cierre del PO | Implementar lo que revisa o hacer merge a main |
| Dev | A fresh agent session for **one** task (one-shot) | Implementa, prueba, hace commits en su worktree y entrega SHA, reporte y bitácora | Hacer push, abrir PR, hacer merge o cerrar su ticket |
| Gate | Tests, scripts, hidden evaluation sets | Mechanical verdict | Get changed by the dev it judges |

A PM that edits the artifact under review is reviewing its own work. The human decides; the PM
recommends with evidence and does not decide for the human.

## Two ways to run the board

- **Board mode**: a kanban tool spawns devs from cards. Read `references/board-mode.md`. The most
  important rule is that **assigning a card is starting it**.
- **File mode**: tickets are `.md` files, the board is `STATUS.md`, and each dev session starts
  from a `TO:` prompt. The human launches it, or the PM queues it for the runtime's launcher
  (`inbox.py lanzar`, opencode adapter: at most 10 devs per machine, no per-PM cap). Read
  `references/file-mode.md`. Use it when tests and evaluation sets matter more than automation.

Both modes share everything below.

## Ticket lifecycle

```
pending → in progress → reported → closed
                           ↓
                       returned → in progress (new session)
```

- Only the PM sets `closed` or `returned`. Para entregas de código, un PR abierto o aprobado
  sigue en `reported`: el PM marca closed después de recibir del PO evidencia del merge,
  gate integrado, localhost apagado y retiro verificado. El flujo Git y sus evidencias están en `references/file-mode.md`.
- Each dev edits only its own row on the board.
- A return goes to a **new dev session**, with a self-contained prompt that lists only what must be
  fixed (`references/prompts.md`).
- **Resumen.** Cada ticket (`epic-*/tickets/<ID>.md`) abre con `**Resumen:** …` justo después del título: una o dos frases en lenguaje llano que digan **qué pide**, sin rutas, hashes, IDs ni estado, máximo **240 caracteres**. Lo escribe el **PM al crear el ticket** y **no cambia con el avance**; el estado vive en `STATUS.md`. La columna `Ticket` de `STATUS.md` lleva solo `ID` + título corto; la bitácora va en `reports/<ID>.md`. Lector único `scripts/resumen.py get <ID>` / `from resumen import leer_resumen` (usa `gestion.raiz_gestion`, ORDEN-3) y `json` para el tablero (`id → resumen|null + ruta`); `check` lista abiertos sin resumen o que exceden el tope (avisa, no rompe). Hook propuesto donde el PM crea tickets; PM-calidad decide.
- **One session, one task.** A session ends when its task's report is written. The next task,
  even for the same agent, starts a new session (rule 8).

## The twelve rules

1. **Evidence is literal.** Every claim in a report comes with the exact `$ command` and its
   output, pasted as is: no `...`, no hand-written diffs, no hand-written `ls -l`, no estimated
   times. Read `references/evidence.md`.
2. **The PM re-runs and cross-checks.** The PM re-runs every pasted command and compares the
   output. It also checks the dev's session log to see what was really run. A report is a claim,
   not evidence.
3. **Verdict rule.** Any false or unverifiable data means **returned**, even if the code is right.
   If only the form is wrong and no data is false, the verdict is **closed with notes**.
4. **Gates don't overfit.** A change to a gate or threshold needs three things:
   - the human's OK, written in the ticket;
   - a test that must still fail;
   - a before/after measurement on a **hidden** evaluation set the dev never sees.

   Cases used to iterate and cases used to validate are kept separate. A test assertion is a gate:
   `scripts/candados.py` lists every assertion a ticket's commits removed, and the PM sees that
   list before closing. Read `references/gates.md`.
5. **Prompts have an addressee and stand alone.** Every prompt starts with `TO: <agent>` and
   assumes a brand-new session, with no memory of this one.
6. **Fast, single-run tests.** A suite is run once to a file, and then that file is filtered. The
   timeout is at least as long as the command. Read `references/test-speed.md`.
7. **Finish on purpose.** When tickets appear faster than they close, write a closing plan:
   - a result-based definition of done and a few steps;
   - everything else frozen;
   - no new ticket unless it comes from a failure seen in those steps.

   Read `references/closing.md`.
8. **One-shot devs, strictly.** Each dev session receives exactly one task: one ticket part or
   one return. It writes the report, sets its row to `reported`, and stops. It does not accept a
   second task, a follow-up question, or a «while you're at it».
   - Questions for the dev go into the next task's prompt, in a new session.
   - The PM checks this in the runtime's records: a session with more than one prompt is a process failure,
     and it is recorded.
   - Why: in the project this skill comes from, 7 of 18 sessions on one day took several tasks. The
     longest one took 7 prompts and ran for 3.5 hours with 58 test runs. A prompt meant for one
     agent landed in another agent's session. Old context made later tasks slower and harder to
     verify.
9. **Every task is traced.** The dev ends its report with a **logbook** of at most 10 lines,
   without times: the phases it followed, what it repeated and why, where it got stuck, and what
   would have saved time. The PM gets the numbers from the agent runtime's own store, read-only,
   never from the agent: prompts (one-shot), duration, test runs, re-reads, failures. There is no
   second database to keep in sync. Time informs the PM. It is never a target for the devs. Read
   `references/traceability.md`.
10. **The objective survives the context.** Focus files at two levels (`focus/PROJECT.md`, and
    `focus/lines/<line>.md` for each PM's line of work) keep an anchor: the objective in the PO's
    words, verbatim, the non-goals, and the directives in force. An epic's objective lives in its
    `EPIC.md`.
    - Only the PO changes an objective. Every steering message is classified by kind (`objective`,
      `constraint`, `nuance`, `decision`; default `nuance`) and by level (default: the PM's own
      line). A message that contradicts the anchor or a wider level is a question to the PO, not a
      new directive.
    - Each PM re-reads its anchors (project and its line) at the start of every session, right
      after every compaction, and before every ticket or verdict. Its own summary of the
      conversation is not the anchor. Where the runtime has a session-start hook,
      `scripts/focus.py hook` injects them.
    - A line file has one writer, its PM. Devs and subagents never read or write focus files.
    - The anchor grows by splitting, never by summarizing: one directive per row, each in the file
      of what it governs, and executed one-shot authorizations and superseded directives move,
      word for word, to `## Archived`, which is never loaded.
    - Why: a nuance given at the end of a turn survives a compaction better than the objective given
      at the start, so the PM takes it as the new direction, and the drift compounds with every
      compaction. Read `references/focus-log.md`.
11. **PMs talk through mailboxes.** Each PM has `inbox/<PM>.md`. A PM writes to another when it
    closes a ticket that unblocks that lane, before it touches that lane's files, and when it needs
    an answer to go on (`scripts/inbox.py send`). A hook puts the unread messages in front of every
    PM turn; the PM reads them first and acks them after acting, and `inbox.py check` flags the
    ones left unread. A message to an idle PM wakes it (at most 40 times per PM per hour; past that
    it waits for the human). The PM never waits on a dev: when a dev it launched finishes without
    writing to it, the runtime tells the PM within a minute. `inbox/` is live state, not history:
    it stays out of git. Read `references/inbox.md`.
12. **Un worktree temporal por encargo.** El lanzador crea cada dev desde `main` y registra
    repositorio, base, rama y directorio. El dev entrega un commit con `dev_worktree.py entregar`.
    El PM verifica, hace push de la rama y abre el PR; el PO autoriza y ejecuta el merge a main.
    El PO presenta una vista previa localhost del SHA del PR con los cambios a revisar
    (`references/pr-preview.md`). Tras el merge apaga su preview, sincroniza main y ejecuta
    `dev_worktree.py cerrar --integrado <SHA>`:
    exige sesión idle, entrega integrada y gate verde del SHA actual antes de eliminar worktree
    y rama. El barrido genérico conserva estas entregas. Los stashes son globales: no se mutan
    tampoco desde un worktree.

    **The shared checkout is nobody's to clean.** Several agents work in the same checkout, tree,
    and index. An agent commits only its own files (`git add <new file>`, `git commit --only
    <files>`) and never stashes, checks out, restores, resets, cleans, `add -A`s, `commit -a`s, or
    amends there; it needs a clean tree, it uses its own worktree. The opencode adapter enforces it
    (`guard.js`). Why: in the project this skill comes from, 20 sessions stashed in the shared
    checkout in three days, and one wiped every mailbox.

## Definition of Done

A ticket is not closed without all of these. The PM checks them one by one, not from memory:

1. The artifact exists at the declared path, and the PM opened it.
2. The tests pass with exit 0, or, on a trunk that is already red, exactly the listed known
   failures fail (`scripts/trinquete.py`, references/gates.md «A red trunk»). «The failures are
   someone else's» is never a close. The exact command is in the report, and the PM re-ran it.
3. Every pasted output matches the PM's re-run (rule 2).
4. The gates pass, and none of them changed without rule 4: `scripts/candados.py <repo> <shas>`
   over the ticket's commits lists no removed assertion, or each one is justified in the report.
5. No test of the form «the real output of case X passes».
6. The report is a pointer plus a short summary. The payload stays in the repo.
7. A verifier other than the dev approved the work. If the criterion is fully mechanical, a
   deterministic gate can replace the verifier.
8. The report ends with the logbook, the runtime's records show the session was one-shot, and the
   verdict (with the cause of any return) is recorded in the review and the ticket's row.
9. Whatever the ticket created outside the repo's tracked files is gone: worktrees, dev servers,
   ports, per-ticket build dirs (`.next-<ticket>`). Anything left alive on purpose is named in the
   report with its owner and when it goes. Throwaway worktrees remove themselves (`trap ... EXIT`). Los worktrees de dev administrados se retiran únicamente mediante el cierre verificado;
   el barrido genérico conserva sus entregas.
   Why: in the project this skill comes from, 14 worktrees of closed tickets held about 8 GB, and
   nothing said whose they were.
10. The full gate (build plus suite) passed on the **exact commit that closes the ticket**, in a
    clean checkout of that commit. A green run on an earlier commit, on a sibling commit, or on the
    working tree doesn't count. If the close needs one more commit (a missing helper, a re-pointed
    lock, a list entry), that commit gets its own green run before the row closes. A commit
    carries only files of its own ticket: an entry that points to another ticket's uncommitted
    file goes in with that file.
    Why: in the project this skill comes from, `main` went red three times in one day from closes
    verified on an earlier sha. A close removed a feature and left its lock pointing at it. A
    helper existed only in the working tree. A list named a migration whose file was never
    committed, and that stopped a deploy at its first step.

A `completed` status without an artifact is a **delivery failure**, not a success.

## Ceremonies

| Ceremony | What happens | When |
|---|---|---|
| Refinement | Split tickets into parts with `STOP` points where the human must decide | Before starting |
| Planning | Tickets, with lanes (`references/file-mode.md`) and dependencies | Start of a phase |
| Plan attack | Four blind attackers (figures, feasibility, logic and gates, scope) attack the plan; the PM re-verifies each finding, writes v2, and runs a second round if the design changed (`references/plan-attack.md`) | After planning, before any card is assigned, **only for complex work** |
| Daily | Board plus the dev's heartbeat or session log, `inbox.py check`, and `stale_worktrees.py` | When reporting to the human |
| Review | PM verification and verdict | Each report |
| Retro | Count returns by cause from the reviews, check one-shot and outliers in the runtime's records, and add or remove one concrete rule | End of a phase |

## Metrics

- **First-pass yield**: tickets closed without a return. Count returns by cause:
  - **evidence** (the report was wrong);
  - **code** (the work was wrong);
  - **scope** (the ticket was wrong).

  Mostly evidence means fix the report format, not the dev. Mostly scope means fix the tickets.
- **Timeout waste**: runs killed by a time limit. It is the most expensive way to fail.
- **One-shot rate**: sessions with exactly one prompt, out of all dev sessions. The target is 100%.
- **Cost of returns**: minutes spent on returns compared with minutes spent on the original work,
  per ticket, from the runtime's records. A return that costs more than the work points at the
  report format.
- **Gate drift**: for each gate, its precision and recall on the hidden set, recorded with the
  code hash. Every gate decision is compared against that baseline.

## Files

| Path | What it holds |
|---|---|
| `references/file-mode.md` | `STATUS.md`, tickets, lanes, claims |
| `references/board-mode.md` | Running with a kanban tool that spawns agents |
| `references/evidence.md` | Report format and the PM's verification procedure |
| `references/gates.md` | Changing gates without overfitting, hidden sets, evaluators |
| `references/prompts.md` | Prompts for dev sessions and for returns |
| `references/test-speed.md` | Fast suites and single-run output |
| `references/closing.md` | Closing plan for an epic that keeps growing |
| `references/parent-focus.md` | The PM's own todo, ticket cap, children never mutate the board |
| `references/focus-log.md` | Focus files per project and line: objectives and directives that survive compaction |
| `references/inbox.md` | One mailbox per PM: when to write, and how reading is enforced and proven |
| `references/traceability.md` | One-shot sessions, logbook, measuring from the runtime's records |
| `references/plan-attack.md` | Blind adversarial review of a complex plan before execution |
| `references/lessons.md` | Field lessons, grouped by topic |
| `templates/` | `STATUS.md`, `TICKET.md`, `REVIEW.md`, `CLOSING-PLAN.md`, `PROMPT.md`, `FOCUS.md` |
| `scripts/check_report.py` | Extracts the `$ command` blocks of a report, flags edits, re-runs them |
| `scripts/candados.py` | Lists the assertions a set of commits removed from test files (rule 4) |
| `scripts/trinquete.py` | Ratchet for a red trunk: only the known failures may fail, and their list only shrinks (DoD 2) |
| `scripts/hooks/pre-push` | Refuses a push whose commit does not build in a clean worktree |
| `adapters/opencode/control.sh` | The auditor's read-only check of the last N minutes (references/lessons.md, «Supervision») |
| `scripts/archivar.py` | Archiva mensajes leídos y logs en el barrido. STATUS/focus solo con `--tableros` manual, sin editores activos |
| `scripts/focus.py` | Prints a PM's anchors for a session-start hook, flags what lets an objective drift |
| `scripts/inbox.py` | Sends, reads, acks and checks the PM mailboxes; `pending` for a per-turn hook |
| `scripts/opencode_inbox_smoke.sh` | One real model call proving the opencode inbox plugin still works |
| `scripts/dev_worktree.py` | Entrega por SHA y retiro de worktree/rama tras integración y gate del commit exacto |
| `scripts/stale_worktrees.py` | Lists worktrees a closed ticket left behind (removable, dirty, ahead, in use); `--reap` removes the stale ones, saving their work as `conserva/wt-<name>`, and stops idle agent postgres |
| `adapters/opencode/inbox.js` | Plugin: unread mail in every PM turn; wakes idle PMs on new mail; runs the launcher and the dev-finished check every minute |
| `adapters/opencode/wake.py` | Wakes a PM for its mail as soon as its session stops, with everything unread in one turn; `--reap` tells a PM its dev finished; `--sweep <repo>` (launchd, 5 min) nudges idle PMs, reaps stale worktrees, runs `archivar.py`; `--activos <repo>` lists sessions with real activity, the only evidence that someone is working |
| `adapters/opencode/lanzar.py` | The launcher: starts queued dev sessions with `opencode run`, up to 10 per machine (no per-PM cap), and tells the PM when one fails |
| `adapters/opencode/guard.js` | Plugin: blocks sleep loops, `kill` of processes the session did not start, and git that touches others' work in the shared checkout (rule 12) |
| `adapters/codex/`, `adapters/codex.md` | Waking a Codex session from its mailbox |
| `adapters/hermes-kanban/` | Board mode with Hermes kanban |
