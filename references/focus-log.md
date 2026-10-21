# Focus log: the objective survives the context

The PM's context is compacted or restarted many times over an epic. Each compaction keeps the
recent turns and summarizes the old ones, so the latest message weighs the most. A nuance the human
adds at the end of a turn («keep A, but adjust B») is still fresh when the summary is written, and
the original objective A is already a line in the summary. After the compaction the PM reads B as
the new directive. After three compactions the epic is building something the human never asked
for, and every step looked reasonable.

The fix is the same as for the board: what must survive goes in a file, not in the PM's memory.
`FOCUS.md` (template in `templates/FOCUS.md`) holds the objective and the directives in force, and
the PM re-reads it instead of trusting its summary.

## Two parts, two owners

- **Anchor.** The objective of the epic, its non-goals, and the directives in force. It is short
  (it must fit in one screen) and the PM reads it whole at every re-anchor point.
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
| `objective` | Replaces or changes what the epic is for | New objective; the old one is marked `superseded by D-<n>` |
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

## Make the re-read mechanical where the runtime allows it

«Re-read after compaction» is a rule the PM forgets for the same reason it forgets the objective.
Where the runtime can inject a file into the context, let it:

- **Claude Code:** a `SessionStart` hook prints the anchor; it runs on startup, resume, and after
  every compaction (`matcher` `compact`), so the anchor enters the context right after the summary.
  For example, with the anchor above a `## Log` heading:

  ```json
  {
    "hooks": {
      "SessionStart": [{
        "matcher": "startup|resume|compact",
        "hooks": [{ "type": "command",
                    "command": "sed '/^## Log/,$d' \"$CLAUDE_PROJECT_DIR/epic/FOCUS.md\"" }]
      }]
    }
  }
  ```
- **Other runtimes:** use their pre-compaction or session-start hook if they have one. If not, put
  the path of `FOCUS.md` in the PM's system prompt or project instructions with the order to read
  it first. That instruction survives compaction because it is not part of the conversation.

## Where it lives

- **File mode:** `FOCUS.md` next to `EPIC.md` and `STATUS.md`. `EPIC.md` keeps the plan (lanes,
  suites, DoD); `FOCUS.md` keeps what the human asked for and how that changed.
- **Board mode:** the same file in the repo, or a pinned card that only the PM edits. Children
  never edit it (`parent-focus.md`).
- One `FOCUS.md` per epic. If a session covers several epics, each has its own, and the PM names
  which one it is working on in the first line of every log entry.
