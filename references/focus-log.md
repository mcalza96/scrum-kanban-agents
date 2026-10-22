# Focus log: the objective survives the context

The PM's context is compacted or restarted many times over an epic. Each compaction keeps the
recent turns and summarizes the old ones, so the latest message weighs the most. A nuance the human
adds at the end of a turn («keep A, but adjust B») is still fresh when the summary is written, and
the original objective A is already a line in the summary. After the compaction the PM reads B as
the new directive. After three compactions the epic is building something the human never asked
for, and every step looked reasonable.

The fix is the same as for the board: what must survive goes in a file, not in the PM's memory.
The focus files (`focus/`, template in `templates/FOCUS.md`) hold the objectives and the directives
in force at three levels (project, epic, line of work), and each PM re-reads its own set instead of
trusting its summary.

## Two parts, two owners

- **Anchor.** The objective of the file's level (project, epic, or line), its non-goals, and the
  directives in force. It is short (it must fit in one screen) and the PM reads it whole at every
  re-anchor point.
  - The objective is the human's words, quoted verbatim, with the date. The PM never paraphrases
    it, because a paraphrase written after a compaction carries the drift into the file.
  - **Only the human changes the objective**, by saying so explicitly. The PM may propose a change;
    it writes it only after the human confirms.
- **Log.** Append-only, one entry per PM turn that changed something. The PM never rewrites past
  entries. It is the history; the anchor is the state.

Writing «the current objective» from scratch at the end of every turn is the failure mode, not the
fix: the PM would copy its drifted understanding into the file every turn. The anchor is carried
forward unchanged unless the human changed it.

## Classify every steering message

When the human steers, the PM classifies the message before acting on it and writes it as a
directive (`D-<n>`) with the human's words quoted:

| Kind | Meaning | Effect on the anchor |
|---|---|---|
| `objective` | Replaces or changes what the project, epic, or line is for | New objective; the old one is marked `superseded by D-<n>` |
| `constraint` | Something that must or must not happen, under the same objective | Added to the directives in force |
| `nuance` | A refinement of how to do part of the work | Added, scoped to the part it refines (a ticket, a lane) |
| `decision` | The answer to a STOP point or a question | Added, linked to the ticket |

- **The default is `nuance`.** A message is an `objective` change only when the human says so, or
  confirms it when asked.
- **When a message contradicts the anchor, ask; don't pick.** One line: «D-4 says X; this says Y.
  Does Y replace D-4, or apply only to T-07?» Until the human answers, the anchor stays as it is.
- A later directive supersedes an earlier one only by naming it. Both stay in the file, and the old
  one is marked `superseded by D-<n>`.

## When the PM writes and re-reads

- **Re-read the anchor whole:**
  - at the start of every session, before answering anything;
  - right after a compaction, before the next action (the summary is not the anchor);
  - before writing or splitting a ticket, before a verdict, and before a closing plan.
- **Write at the end of every turn that changed something:** a new directive, a ticket opened or
  closed, a verdict, a STOP answered. A turn that only reported status adds nothing.
- **Lint after writing.** `scripts/focus.py check focus/` flags an anchor too long to read
  whole, directive ids that repeat or point nowhere, zero or several objectives in force, and log
  entries without an `Objective:` line or whose objective changed without naming an `objective`
  directive. Exit 0 means nothing was flagged.
- **Check before acting.** If the next step doesn't serve the objective, or breaks a directive in
  force, the PM says so and asks instead of doing it.

## The log entry

One entry per turn, at most five lines, no payload:

```
### YYYY-MM-DD HH:MM · turn summary in one line
- Human said: «exact words» → D-7 (nuance, scope T-07)
- Done: T-07 returned (E1, E2); T-09 opened
- Objective: unchanged (D-1)
- Next: wait for T-07 return; T-09 waits on T-07
```

- The time comes from `date`, never from memory.
- `Objective: unchanged` is written every time. When it says anything else, it must point at a
  directive the human confirmed.
- Pointers, not payload: ticket ids and directive ids. Detail lives on the card (`parent-focus.md`).

## Keep the anchor small

The log grows; the anchor must not. When the directives in force no longer fit on one screen:

- move superseded directives and those of closed tickets to an «Archived» section at the end;
- if what remains still doesn't fit, the epic has too many open fronts: rule 7 applies.

The PM reads the log only when it needs the history (why a directive exists, when it changed). The
anchor is what it reads every time.

## Three levels: project, epic, line

The PO owns the project. The project has several lines of work (UI, DATA, INFRA, ...), each run by
one PM, and epics that involve one or more lines. Each level has its own focus file, all from
`templates/FOCUS.md`, in one folder:

```
focus/
  PROJECT.md          Level: project   the PO's objective, directives for every line
  epics/
    payments.md       Level: epic      State: closed (2026-10-14)   never loaded again
    notifications.md  Level: epic      State: active   Lines: ui, data
  lines/
    ui.md             Level: line      the UI PM's thread: mandate, directives, turn log,
                                       Sessions: the chats that ran it
    data.md           Level: line      the DATA PM's thread
```

- **Project.** The objective of the whole project and the directives every line obeys («never
  touch the billing schema», «tests stay under 60 s»). It changes rarely.
- **Epic.** The objective of one module or feature, the lines it involves (`**Lines:**`), and the
  contracts between those lines («DATA exposes `/v2/notifications`, UI only reads it»). It is
  shared by every PM of those lines. It opens and closes.
- **Line.** One PM's thread across epics: the line's mandate, its standing directives, and the log
  of that PM's turns (the first field of each entry names the epic). It outlives the epics, so this
  is the thread that survives compactions, new sessions, and the end of an epic.

### Who writes where

- Every directive is the PO's words, wherever it lands.
- **A line file has one writer: its PM.** Other PMs read it, never edit it.
- **Project and epic files are shared.** A PM appends a row or a log entry there only after the PO
  confirmed the directive belongs at that level. It re-reads the file right before writing, appends
  in one edit, and never edits rows another PM wrote.
- The board stays as it is (`file-mode.md`, `board-mode.md`): focus files hold intent, the board
  holds work.

### Classify the level, too

A steering message gets a level besides its kind. **The default is the narrowest one: the PM's own
line.** A message goes higher only when the PO says so, or confirms it when asked:

- «make the buttons bigger», said to the UI PM → `lines/ui.md`, nuance;
- «notifications must also work offline» → it changes the epic for every line: the UI PM asks
  «does this apply to the whole notifications epic, DATA included?» and, on a yes, appends it to
  `epics/notifications.md`. The DATA PM sees it on its next re-anchor;
- a message that contradicts a wider level (a line nuance against an epic contract) is a question
  to the PO, never a line directive that overrides the wider one.

### Lifecycle

1. **Open an epic.** The PO states its objective and the lines it involves. One PM creates
   `epics/<name>.md` with `**State:** active`, `**Lines:**`, and the objective as D-1. Each
   involved PM writes an entry in its line log: «joins <epic>».
2. **One active epic per line.** A line in two active epics gets a warning on every re-anchor until
   the PO pauses one (`**State:** paused`, with a log entry saying why and who asked).
3. **Close an epic.** When its done criterion is met, or the PO drops it, each involved PM writes a
   last entry in its line log, and one PM writes the closing entry in the epic file comparing the
   result with D-1 and sets `**State:** closed (YYYY-MM-DD)`. The file stays as the record of why
   the module is the way it is; the hook never loads it again.
4. **Nothing carries over by accident.** Project directives live in `PROJECT.md` and line
   directives in the line file, so they persist on their own. Epic directives die with the epic. If
   the PO wants one to outlive it, it is moved, in the PO's words, to the line or project file.
5. **Close a line** only when the PO dissolves it: last log entry, `**State:** closed (date)`.

## One chat, one line

Lines of work are not a fixed list. The PO opens a chat whenever a new thread of work starts, and
can keep any number of chats open in parallel, each with its own line. Every chat inherits the
project and runs exactly one line; the line file is that thread's memory, so it outlives the chat.

- **The chat is bound to its line by its session id.** A line file lists the chats that ran it in
  `**Sessions:**`. The session-start hook reads the chat's session id and loads that line, so
  nothing has to be set by hand before opening a chat.
- **A new chat has no line yet.** The hook gives it the project anchor and an index of the active
  lines (name, date of the last entry, objective), and the PM's first act is to ask the PO: «a new
  line, or one of these?». Then it binds the chat:

  ```bash
  python3 scripts/focus.py bind focus/ notifications-ui --session <id>
  ```

  - A new name opens `lines/<name>.md` with this chat in `**Sessions:**` and an empty objective.
    The PM writes the PO's words as D-1 before anything else; until then every re-anchor warns.
  - An existing name adds the chat to `**Sessions:**` with a log entry, so a thread can continue in
    a fresh chat after the old one got too long. The old chat is closed: **a line has one writer**,
    so two open chats on one line is the failure to avoid, and `bind` names the earlier chats to
    make that visible.
  - A chat runs one line. Binding it to a second one is refused; work for another line goes to
    another chat.
- **The number of chats has no cap, the context does.** Each chat loads the project, its line's
  active epic, and its line: the same three anchors whether there are 2 lines or 40. Only the index
  a new chat sees grows, at one line per active line.
- **Lines go stale, so close them.** A line whose chat was abandoned stays `active` forever unless
  someone closes it. `focus.py check focus/` flags active lines whose last entry is older than
  `--stale-days` (14 by default), and the PM asks the PO whether to pause or close each one. A
  closed line drops out of the index; its file stays as history.

## Make the re-read mechanical where the runtime allows it

«Re-read after compaction» is a rule the PM forgets for the same reason it forgets the objective.
Where the runtime can inject text into the context, let it:

- **Claude Code:** a `SessionStart` hook runs on startup, resume, and after every compaction
  (`matcher` `compact`), so its output enters the context right after the summary. It gets the
  chat's `session_id` as JSON on stdin, and `scripts/focus.py hook focus/` reads it. For a chat
  bound to a line, it prints in this order the project anchor, the anchors of the active epics that
  list the line, and the line's own anchor (each anchor is everything above `## Log`). For a chat
  bound to no line, it prints the project anchor and the index of active lines, with the order to
  bind first. Missing files, a line without an objective, or a line in two active epics come back
  as `[focus]` warnings, never as a failure, so the hook never blocks a session. The hook never
  needs editing when a line or an epic opens or closes. In the project's `.claude/settings.json`
  (adjust both paths):

  ```json
  {
    "hooks": {
      "SessionStart": [{
        "matcher": "startup|resume|compact|clear",
        "hooks": [{ "type": "command",
                    "command": "python3 ~/.claude/skills/scrum-kanban-agents/scripts/focus.py hook \"$CLAUDE_PROJECT_DIR/focus\"" }]
      }]
    }
  }
  ```

  `PM_LINE=<line>` in the environment overrides the binding, for a PM launched by a script.
  `/clear` starts a new conversation, which comes back as a chat with no line: bind it again to
  the same line to continue.
- **Other runtimes:** use their pre-compaction or session-start hook if they have one, passing the
  chat's id to `focus.py anchor focus/ --session <id>`. If there is none, put in the PM's system
  prompt or project instructions the order to run `focus.py anchor focus/ --line <line>` first and
  to read what it prints. That instruction survives compaction because it is not part of the
  conversation.
- **Lint:** `scripts/focus.py check focus/` checks every file plus what only shows across files: an
  epic naming a line with no file, a line in two active epics, a chat bound to two lines, and stale
  active lines.
