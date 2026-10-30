# Inbox: PMs tell each other, and the reading is proven

With several PMs, most of what one needs from another is short and dated: «SIM-26 closed in
53577e73, SIM-29 is unblocked», «I'm starting on `_feed/Tarjetas.tsx`, don't touch it until
SIM-27 closes». When that message goes through the human, it waits until the human copies it, and
nothing shows whether the other PM read it.

## The mailbox

- One file per PM: `inbox/<PM>.md`, named like the `TO:` of that PM's prompts (`PM-ui`, `PM-ui-2`).
- `scripts/inbox.py send inbox/ --to PM-ui --from PM-ui-2 "<message>"` appends a row with an id
  (`M-<n>`), the sender, the time, and `unread`. Writes are locked: PMs send at the same time.
- A message is a pointer plus one or two sentences: the hash, the ticket, the file. Anything longer
  goes in a file the message names.
- Only `send` and `ack` write the file. Nobody edits a row by hand.

## When a PM writes

- **On closing a ticket that unblocks another lane:** to that lane's PM, with the hash.
- **Before touching files of another lane:** first check the board for that lane's work in
  progress; if it is free, tell its PM which files and why, before starting.
- **When it needs an answer from another PM to go on:** the question, and what waits on it.
- A decision that belongs to the PO goes to the PO, not to another PM.

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
