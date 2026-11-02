# Focus of <project | line> <NAME>

**Level:** line
**State:** active

Read the anchor whole at the start of every session, after every compaction, and before every
ticket, verdict, or closing plan. Every directive is the PO's words. The project file is
shared by every PM; a line file is written only by that line's PM. See `references/focus-log.md`.

## Objective

> «<the PO's exact words>» (YYYY-MM-DD, D-1)

## Non-goals

- <what this project or line will not do, in the PO's words>

## Directives in force

| Id | Kind | Scope | Human's words | Date | Status |
|---|---|---|---|---|---|
| D-1 | objective | line | «...» | YYYY-MM-DD | in force |
| D-2 | constraint | line | «...» | YYYY-MM-DD | in force |
| D-3 | nuance | T-07 | «...» | YYYY-MM-DD | superseded by D-4 |
| D-4 | nuance | T-07 | «...» | YYYY-MM-DD | in force |

One directive per row; never append a decision inside another row's cell.
Kinds: `objective`, `constraint`, `nuance` (the default), `decision`.
Statuses: `in force`, `superseded by D-<n>`, `suspended by D-<n>`, `executed (<report>)`.
Scopes: this file's level, or narrower (a ticket, a lane). Wider goes to the wider file.
States: `active`, `paused`, `closed (YYYY-MM-DD)`. An epic's objective lives in its EPIC.md.

## Open questions to the human

- <contradiction or ambiguity the PM asked about; nothing changes until it is answered>

## Archived

<superseded directives, directives of closed tickets, and executed one-shot authorizations
(`executed (<report>)`), moved here unchanged; never summarized>
