# Inbox: PMs tell each other, and the reading is proven

With several PMs, most of what one needs from another is short and dated: «SIM-26 closed in
53577e73, SIM-29 is unblocked», «I'm starting on `_feed/Tarjetas.tsx`, don't touch it until
SIM-27 closes». When that message goes through the human, it waits until the human copies it, and
nothing shows whether the other PM read it.

## The mailbox

- One file per PM: `inbox/<PM>.md`, named like the `TO:` of that PM's prompts (`PM-ui`, `PM-ui-2`).
- **`inbox/` is live state, not history: put it in `.gitignore`.** Versioned, the mailboxes were
  always dirty, so devs saw «someone else's changes» and stashed them, and a `reset` or `checkout`
  wiped them (2026-11-04). The archive below is the history.
- `scripts/inbox.py send inbox/ --to PM-ui --from PM-ui-2 "<message>"` appends a row with an id
  (`M-<n>`), the sender, the time, and `unread`. Writes are locked: PMs send at the same time.
- A message is a pointer plus one or two sentences: the hash, the ticket, the file. Anything longer
  goes in a file the message names.
- Only `send` and `ack` write the file. Nobody edits a row by hand.
- **Every message starts with its kind:** `[bloqueo]`, `[encargo]` or `[aviso]`. A message without
  one is an aviso. `[decision]` se acepta como alias de `[encargo]` (mensajes viejos y agentes que
  aún no se enteran).
  - `[bloqueo]`: trabajo detenido hasta que el destinatario responda. Despierta al tiro.
  - `[encargo]` (`[decision]` es alias): el destinatario debe hacer y responder con el resultado
    —el commit, la fila o la ruta—; si no puede, responde con un `[bloqueo]` que diga por qué.
    `ack` o `recibido` no cuenta como respuesta. Despierta al tiro.
  - `[aviso]`: para dejar constancia (cerrado, lanzado, corregido). No lleva instrucciones; si pide
    hacer algo es un `[encargo]` aunque diga `[aviso]`, y el destinatario lo trata como encargo.
    No despierta al PO de inmediato: los avisos al PO esperan hasta que el más antiguo tenga 10
    minutos, y entonces el PO despierta una vez por todos ellos.

## The mailbox stays short

The sweep runs `scripts/archivar.py` (D-283). Read rows older than 48 hours move to
`inbox/archivo/<mailbox>-AAAA-MM.md`; unread rows never move. `send` numbers past the highest id
of the live file and of the mailbox's archive files, so an id is never handed out twice, even after
a restore. Lines of `.wakes.log` older than 7 days move to `inbox/archivo/wakes-AAAA-MM.log`.
The same pass moves `closed` rows of every `STATUS.md` to `STATUS-archivo.md`, except the 5 most
recently updated, and directives `executed` or `superseded` below `## Archived` in the focus files.
It moves and never deletes; whoever commits a `STATUS.md` or focus file commits its archive too.

## When a PM writes

- **On closing a ticket that unblocks another lane:** to that lane's PM, with the hash.
- **Before touching files of another lane:** first check the board for that lane's work in
  progress; if it is free, tell its PM which files and why, before starting.
- **When it needs an answer from another PM to go on:** the question, and what waits on it.
- A decision that belongs to the PO goes to the PO, not to another PM.

## When a PM does not write

Every message to the PO costs a PO turn, and one turn of the PO reads the context of the whole
project. On 2026-11-03 the PO got 196 messages from PMs in 24 hours and 25 wakes in half an hour,
and nearly all of them were status echoes (D-282).

- **No acknowledgements.** «Recibido», «M-65 ya aplicado», «ya estaba cerrada»: the `ack` already
  records that the message was read. Acting on a message needs no reply unless the act blocks or
  needs a decision.
- **No status in the mailbox.** A ticket that closed, launched or went back is a row in `STATUS.md`,
  which the PO reads. The mailbox says only what the board cannot: a block, a question, a hand-off
  to another lane.
- **No corrections of your own message.** Send the right one once; if you must correct, the new
  message says so in its first words and is an aviso.
- **One message per turn to the same addressee.** Several things go in one message.

## Reading is enforced, not hoped for

1. **Every PM turn starts with the pending line.** A hook prints `inbox.py pending inbox/`, one
   line per mailbox with unread messages and nothing when there are none:
   - **opencode:** copy `adapters/opencode/inbox.js` to `<project>/.opencode/plugin/inbox.js`.
     It appends the line to the user's message right before the model call. The line is not
     stored in the session, and sessions with a parent (one-shot devs) never get it. After every
     `opencode upgrade`, run `scripts/opencode_inbox_smoke.sh`: the plugin API it relies on is not
     documented, and the script makes one real model call to prove the line still arrives.
   - **Claude Code:** `python3 <skill>/scripts/inbox.py hook "$CLAUDE_PROJECT_DIR/inbox"` on
     `UserPromptSubmit`.
   - **Any other runtime:** the PM's system prompt orders running `inbox.py pending` at the start
     of every turn.
2. **A PM that sees its name reads before anything else:** `inbox.py read inbox/ <PM>`.
3. **It acks after acting, not after reading:** `inbox.py ack inbox/ <PM> M-3`. The ack is the
   proof. A sender that depends on a message checks its row before assuming it was received.
4. **`inbox.py check inbox/ --max-hours 4` flags messages left unread.** The PO or any PM runs it
   at the daily; a flag means a PM that is not running or not looking.

## A message wakes an idle PM

The hook only works while the PM has a turn. A PM that finished its turn sees nothing until the
human writes to it, so a message that unblocks it would wait for the human anyway.

- **opencode:** the same plugin, `inbox.js`, watches `inbox/` and starts
  `adapters/opencode/wake.py` for every mailbox that changes. It runs in the plugin because the
  opencode service outlives every turn, while a waker started from a PM's shell command left no
  trace in a real run. `wake.py` finds the PM's session (the newest top-level session that ran `inbox.py read inbox/ <PM>`, so a PM is
  findable after its first read), waits up to 30 minutes for it to be idle, and sends it one turn
  that says to read the mailbox. The turn shows live in the PM's window. It does not pass
  `--auto`: permission prompts still go to the human.
- **No timer: a session gets its mail when it stops.** Idle, it wakes at once, whatever the tag.
  Busy, it gets nothing; when the turn ends, one turn carries every unread message, including what
  arrived during the work (owner, 2026-11-05, replacing a 120 s debounce for the PO).
- **No cap on wakes** (owner, 2026-11-04). No id wakes a PM twice: a PM that read a message and is waiting on someone else is left alone, and an
  ack, which also changes the mailbox, wakes nobody.
- A PM that never ran `inbox.py read` (it opened the file with `cat`) is not findable: its line in
  the log says `skipped: no opencode session`. Its next turn shows the pending line; once it reads
  with `inbox.py read`, it is.
- Every decision (woke, capped, skipped and why) is a line in `inbox/.wakes.log`, which is also
  what the cap counts. Read it at the daily next to `check`.

## The PM never waits on its dev

A PM launches a dev and goes on with other work. It does not poll. Every minute `inbox.js` runs
`wake.py --reap`: for each dev session in `inbox/.lanzar/log` that finished in the last 30 minutes
and did not run `inbox.py send --to <its PM>`, the PM gets one `[encargo]` from `lanzador`:
«<ticket> terminó y entregó sin avisarte» (a commit, a `reported` row, or the expected report) or
«terminó sin entregar», with the commits and the session id. That wakes it. A dev that did write
to its PM gets no second message. Commits are read from the output of `git commit` only, and
«wrote to its PM» means a shell call that ran `inbox.py send --to <PM>`, not a file that mentions
it.
