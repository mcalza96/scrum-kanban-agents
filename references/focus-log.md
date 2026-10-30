# Focus: the objective survives the context

The PM's context is compacted or restarted many times over an epic. Each compaction keeps the
recent turns and summarizes the old ones, so the latest message weighs the most. A nuance the human
adds at the end of a turn («keep A, but adjust B») is still fresh when the summary is written, and
the original objective A is already a line in the summary. After the compaction the PM reads B as
the new directive. After three compactions the epic is building something the human never asked
for, and every step looked reasonable.

The fix is the same as for the board: what must survive goes in a file, not in the PM's memory.
The focus files (`focus/`, template in `templates/FOCUS.md`) hold the objectives and the directives
in force, and each PM re-reads them instead of trusting its summary.

## The anchor

Each focus file is an anchor: the objective of its level, the non-goals, and the directives in
force. It is short (it must fit in one screen) and the PM reads it whole at every re-anchor point.

- The objective is the human's words, quoted verbatim, with the date. The PM never paraphrases it,
  because a paraphrase written after a compaction carries the drift into the file.
- **Only the human changes the objective**, by saying so explicitly. The PM may propose a change;
  it writes it only after the human confirms.
- Writing «the current objective» from scratch at the end of every turn is the failure mode, not
  the fix: the PM would copy its drifted understanding into the file every turn. The anchor is
  carried forward unchanged unless the human changed it.
- The history of why a directive exists is the directive itself (the human's words and the date)
  plus git. There is no per-turn log: nothing re-reads it, and it grows without bound.

## Classify every steering message

When the human steers, the PM classifies the message before acting on it and writes it as a
directive (`D-<n>`) with the human's words quoted:

| Kind | Meaning | Effect on the anchor |
|---|---|---|
| `objective` | Replaces or changes what the project or line is for | New objective; the old one is marked `superseded by D-<n>` |
| `constraint` | Something that must or must not happen, under the same objective | Added to the directives in force |
| `nuance` | A refinement of how to do part of the work | Added, scoped to the part it refines (a ticket, a lane) |
| `decision` | The answer to a STOP point or a question | Added, linked to the ticket |

- **The default is `nuance`.** A message is an `objective` change only when the human says so, or
  confirms it when asked.
- **When a message contradicts the anchor, ask; don't pick.** One line: «D-4 says X; this says Y.
  Does Y replace D-4, or apply only to T-07?» Until the human answers, the anchor stays as it is.
- A later directive supersedes an earlier one only by naming it. Both stay in the file, and the old
  one is marked `superseded by D-<n>`.
- **Level: the default is the narrowest one, the PM's own line.** A message goes to the project
  file only when the PO says so, or confirms it when asked. A message that contradicts a wider
  level is a question to the PO, never a line directive that overrides it.

## When the PM writes and re-reads

- **Re-read the anchor whole:**
  - at the start of every session, before answering anything;
  - right after a compaction, before the next action (the summary is not the anchor);
  - before writing or splitting a ticket, before a verdict, and before a closing plan.
- **Write when the human steers**, in the same turn: the directive, in their words, with the date
  from `date`.
- **Lint after writing.** `scripts/focus.py check focus/` flags an anchor too long to read whole,
  directive ids that repeat or point nowhere, zero or several objectives in force, and active lines
  with no new directive in `--stale-days`. Exit 0 means nothing was flagged.
- **Check before acting.** If the next step doesn't serve the objective, or breaks a directive in
  force, the PM says so and asks instead of doing it.

## Keep the anchor small

When the directives in force no longer fit on one screen:

- move superseded directives and those of closed tickets to an «Archived» section at the end;
  everything from `## Archived` down is never printed;
- if what remains still doesn't fit, the work has too many open fronts: rule 7 applies.

## Two levels: project and line

```
focus/
  PROJECT.md      Level: project   the PO's objective, directives for every line
  lines/
    ui.md         Level: line      the UI PM's mandate and standing directives
    data.md       Level: line      the DATA PM's
```

- **Project.** The objective of the whole project and the directives every line obeys («never
  touch the billing schema», «tests stay under 60 s»). It changes rarely.
- **Line.** One PM's mandate across epics. It outlives the epics.
- **An epic's objective lives in its own `EPIC.md`** (`file-mode.md`), the one place it is edited.
  It is not repeated in `focus/`.
- **A line file has one writer: its PM.** `PROJECT.md` is shared: a PM appends a row only after the
  PO confirmed it belongs there, re-reads the file right before writing, and never edits rows
  another PM wrote.
- **States:** `active`, `paused`, `closed (YYYY-MM-DD)`. Only the PO pauses or closes a line; the
  lint asks about an active line that has gone quiet.
- **Devs and subagents never read or write the focus files.** What a dev needs from the objective
  is already in its ticket, written by the PM (`parent-focus.md`).

## Make the re-read mechanical where the runtime allows it

«Re-read after compaction» is a rule the PM forgets for the same reason it forgets the objective.
Where the runtime can inject text into the context, let it:

- **Claude Code:** `scripts/focus.py hook focus/` on `SessionStart`, which also fires after every
  compaction. It prints the project anchor and, when `PM_LINE` is set in the environment
  (`PM_LINE=ui claude`), the line's anchor. Any other event prints nothing. Missing files or a
  paused line come back as `[focus]` warnings, never as a failure. In the project's
  `.claude/settings.json` (adjust both paths):

  ```json
  {
    "hooks": {
      "SessionStart": [{ "hooks": [{ "type": "command",
        "command": "python3 ~/.claude/skills/scrum-kanban-agents/scripts/focus.py hook \"$CLAUDE_PROJECT_DIR/focus\"" }] }]
    }
  }
  ```

- **Other runtimes:** use their session-start or pre-compaction hook if they have one, running
  `focus.py anchor focus/ --line <line>`. If there is none, put in the PM's system prompt or
  project instructions the order to run that command first and to read what it prints. That
  instruction survives compaction because it is not part of the conversation.

## What was removed, and why

v0.5 to v0.7 added a session-to-line binding, an epic level and a per-turn log. One project used
them for a week:

- The PMs ran in a runtime without hooks; the only hooked chat was the PO's, which spans several
  lines. It ended up bound to two lines, and every unbound chat was told to bind on every prompt.
- `focus/epics/` held 2 files for 8 real epics, repeating what each `EPIC.md` already said.
- One line's log reached 283 entries (77 KB) that nothing re-read.

A mechanism comes back only when a second project shows the need.
