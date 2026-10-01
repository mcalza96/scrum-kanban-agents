# File mode: tickets as files, `STATUS.md` as the board

Use file mode when the human launches each dev session by hand, or when no kanban tool exists.
In this mode the board is a markdown table that every agent can read and edit.

## Layout

```
<epic>/
  EPIC.md            # goal, lanes, rules that never break, suite commands, DoD
  STATUS.md          # the board (template in templates/STATUS.md)
  PROMPT-AGENT.md    # shared rules every dev prompt points to
  CLOSING-PLAN.md    # only once the epic needs one (references/closing.md)
  <TICKET-ID>.md     # one file per ticket (templates/TICKET.md)
  reports/
    <TICKET-ID>.md            # the dev's report
    <TICKET-ID>-review.md     # the PM's review, with each return appended
```

## `STATUS.md` rules

- One row per ticket, with these columns: ticket, lane, depends on, state, agent, report, updated.
- Each agent edits **only its own row**.
- States are `pending → in progress → reported → (returned → in progress →) closed`.
- Only the PM writes `closed`, `closed (with notes)`, `returned`, or `deferred`.
- The `updated` time comes from `date`, never from memory.
- Deferred tickets stay visible in the table, marked `deferred (<why>)`, so nobody picks them up.

## Lanes

A lane is a set of files that only one agent may touch at a time. Two agents writing the same file
overwrite each other, and the board never notices.

- Declare the lanes in `EPIC.md` with their exact paths, for example:
  - `CORE`: the pipeline scripts;
  - `SOURCES`: the source registry;
  - `TESTS-SPEED`: the test runner.
- Each ticket names its lane and lists the files it may touch, with approximate line ranges.
- Tickets in the same lane run one after another. Tickets in different lanes can run in parallel.
- When a ticket must touch a file from another lane, it says so explicitly, and the PM sequences it.

## Ticket parts and STOP points

When a ticket needs a human decision in the middle, split it into parts (A, B, C).

- Each part ends with **STOP**: the dev reports, the PM verifies, and the human decides.
- Example: A proposes three candidates and stops. The human picks one. B prepares the input and
  freezes it. C runs the pipeline.
- Without STOP points, the dev makes the decision that belonged to the human.

## The review file

The PM writes `reports/<ID>-review.md`:

- a header with the verdict and the real time;
- a section «what is real», verified one item at a time;
- for a return: numbered findings (E1, E2, ...), each with the evidence and what must change.

Each return round is appended as «Return N · PM review (time) · verdict», so the history stays
in one place.

## Sync

If agents write to a different copy of the tree (another machine, a desktop folder), sync it in
both directions after every edit, without deleting anything. For example, run
`rsync -au a/ b/ && rsync -au b/ a/`.
