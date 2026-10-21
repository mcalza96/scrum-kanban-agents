# Focus of <project | epic | line> <NAME>

**Level:** line
**State:** active
**Lines:** <epic only: the lines of work it involves, e.g. ui, data, infra; delete this line otherwise>

Read the anchor whole at the start of every session, after every compaction, and before every
ticket, verdict, or closing plan. Every directive is the PO's words. Project and epic files are
shared by every PM; a line file is written only by that line's PM. See `references/focus-log.md`.

## Objective

> «<the PO's exact words>» (YYYY-MM-DD, D-1)

## Non-goals

- <what this project, epic, or line will not do, in the PO's words>

## Directives in force

| Id | Kind | Scope | Human's words | Date | Status |
|---|---|---|---|---|---|
| D-1 | objective | line | «...» | YYYY-MM-DD | in force |
| D-2 | constraint | line | «...» | YYYY-MM-DD | in force |
| D-3 | nuance | T-07 | «...» | YYYY-MM-DD | superseded by D-4 |
| D-4 | nuance | T-07 | «...» | YYYY-MM-DD | in force |

Kinds: `objective`, `constraint`, `nuance` (the default), `decision`.
Scopes: this file's level, or narrower (a ticket, a lane). Wider goes to the wider file.
States: `active`, `paused`, `closed (YYYY-MM-DD)`. A line works on one active epic at a time.

## Open questions to the human

- <contradiction or ambiguity the PM asked about; nothing changes until it is answered>

## Log

Append-only. One entry per turn that changed something. Never rewrite past entries.

### YYYY-MM-DD HH:MM · <epic> · <turn summary in one line>
- PO said: «exact words» → D-<n> (<kind>, scope <scope>), or → epics/<epic>.md D-<n>
- Done: <ticket ids and verdicts>
- Objective: unchanged (D-1)
- Next: <next step>

## Archived

<superseded directives and directives of closed tickets, moved here when the anchor stops fitting
on one screen>
