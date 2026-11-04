# Traceability: one-shot sessions, logbooks, and the runtime's own records

The biggest gain in the project this skill comes from was cutting **cycle time**: the time from a
ticket (or a PM observation) to a verified result. Fast test suites helped, and so did catching
waste in how the agents worked. Neither works unless you can see where the time goes. This file is
about seeing it.

## 1. One session, one task (strict)

A dev session gets **one** task, writes its report, and stops. The next task, even for the same
agent, starts a new session with a `TO:` prompt.

What this buys:
- **Clean measurement.** A session equals a task, so its duration, steps, and cost belong to one
  ticket.
- **Clean context.** The dev never carries the reasoning of a previous task into the next one,
  which matters most for returns.
- **Fewer misroutes.** A prompt pasted into an open session of the wrong agent gets worked by the
  wrong agent. With one-shot sessions, the only open session is the one just started.

Enforcement:
- The prompt says it (`templates/PROMPT.md`): «This session does only this task. When the report is
  written, stop. If you get another request, answer that it needs a new session.»
- The PM checks it in the runtime's records: a dev session with more than one user prompt is a
  process failure, and it is recorded in the review.
- Humans count too: a follow-up question to the dev after the report is a second task. Ask it in
  the next session's prompt instead.

## 2. The logbook (written by the dev)

The report ends with a section `## Logbook` of at most 10 lines, **without times**:

- the phases, in the dev's words: «read X, tried Y, Y failed because Z, switched to W»;
- what was repeated, and why;
- where it got stuck or waited;
- what would have saved time: missing context in the ticket, a command, a permission.

No times and no counts, because those come from the runtime's records. Agents estimate and invent times, and
in the original project an agent wrote 23:00 for work done at 18:26. The logbook is the editorial
part: why the agent took the path it took. The records are the verifiable part.

The PM compares them. If the logbook says «ran the suite once» and the records show six runs, that
is a finding, just like a mismatch in the evidence.

## 3. The numbers come from the runtime, not from a copy of it

The agent runtime already stores every session: its prompts, each tool call with its command and
output, and its timestamps. The PM queries that store directly, read-only, when it reviews a task.
There is no second database to ingest, build, and keep in sync.

Why not a trace database of our own: the skill shipped one (`tracer.py`, about 600 lines). In the
project this skill comes from, nobody ever ingested a session, so the Definition of Done asked for a
recorded trace that could not exist, and closes went on without it. A rule nobody can meet teaches
agents to skip rules. The queries below answer the same questions from the source.

What the PM looks at, per dev session:

| Question | Where it comes from |
|---|---|
| Was it one-shot? | count of user prompts in the session (1 is right) |
| How long did it take? | session start to its last idle time |
| How many test runs? | shell calls whose command matches the suite command |
| Re-reads, failures, hangs | tool calls per file, tool calls that ended in error, the slowest call |
| What did it really run? | the shell calls, with their output (rule 2) |
| Did it tell its PM? | an `inbox.py send --to <PM>` among its shell calls |

`adapters/opencode.md` («Measuring a session») has the queries for opencode. For another runtime,
write the same few queries against its store.

The cause of each return (evidence, code, or scope) is written where the verdict lives: in the
review and in the ticket's row. Counting returns by cause is reading those rows.

Decisions the numbers support:
- **Over 30 min or 10+ test runs on one task:** the ticket was probably too big. Split the next one.
- **A return that costs more than the work:** fix the report format or the prompt, not the code.
- **A single call that runs for minutes:** a hang or a missing timeout. Fix the tool, not the agent.
- **Many re-reads of the same files:** the ticket lacked context. Add it to the ticket.
- **One lane always slower:** its tickets need better context, or it needs faster tests.

## 4. Guardrails

- **Time informs; it is not a target.** Agents never see rankings. An agent pushed on speed skips
  verification.
- **Compare like with like.** A proposal task and a code task are not comparable. Group by kind
  and lane.
- **Small samples mislead.** One day is a baseline, not a trend. Wait for weeks before drawing
  conclusions about agents.
- **The records reveal rule breaks.** In the original project, a session's shell calls showed an
  agent printing an API key with `printenv` and `cat .env`. Look for that pattern in every review.
