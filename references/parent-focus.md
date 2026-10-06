# Parent focus: the PM's own thread

The board keeps the work. Something has to keep the PM. When the PM's own
context fills with ticket bodies, it re-reads the board every turn, loses the
plot, and starts answering from stale context — the same failure mode as an
over-long dev session (rule 8), one level up.

## The PM keeps its own short todo

- The todo holds **pointers, not payload**: ticket ids and one line each.
  Detail lives on the card, never in the PM's working set.
- Small steps that need no delegation stay in the PM's todo. A ticket is
  opened only for work a child session will own.
- Reports back from children end in `{pointer, summary}` (max ~280 chars for
  the summary). The pointer is the card or file; the summary is all the PM
  carries forward.

## Cap the open tickets

Every open ticket costs the PM context on every turn. When tickets appear
faster than they close, rule 7 (closing plan) applies — but the early signal
is the PM re-reading cards instead of moving them. Practical cap: a handful
of open tickets per workstream. New ticket or closing plan, no middle ground.

## Children never mutate the board

Where the platform enforces it (Hermes kanban guard: `delegate_task` child
contexts cannot `create`/`comment`/`complete` via the CLI), this is automatic.
Where it doesn't, make it a rule anyway: children return `{pointer, summary}`
and stop. Only the PM comments, moves, and closes cards, so there is exactly
one writer and the board never diverges from what the PM believes.

## Human steering is an input, not an override of the process

When the human declares a task complex («this is complex», «go deep»), that
is a complexity signal the router must honor, not a request the PM may
downscope. Record the declaration in the ticket, route to the deep path
(delegation), and let the evidence rules judge the output as usual. Steering
changes the route, never the verdict bar.

## Calibrate from real misses

When production traffic shows a miss (work routed shallow that needed depth),
add the real case as a regression test and re-run the whole suite — the same
discipline as rule 4, applied to routing instead of gates. Tuning on the new
case alone is overfitting; the suite staying green is the proof.
