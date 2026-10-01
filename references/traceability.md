# Traceability: one-shot sessions, logbooks, and a trace database

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
- The PM checks it in the trace. `v_one_shot` counts sessions with more than one prompt, and each one
  is a process failure that gets recorded.
- Humans count too: a follow-up question to the dev after the report is a second task. Ask it in
  the next session's prompt instead.

## 2. The logbook (written by the dev)

The report ends with a section `## Logbook` of at most 10 lines, **without times**:

- the phases, in the dev's words: «read X, tried Y, Y failed because Z, switched to W»;
- what was repeated, and why;
- where it got stuck or waited;
- what would have saved time: missing context in the ticket, a command, a permission.

No times and no counts, because those come from the trace. Agents estimate and invent times, and
in the original project an agent wrote 23:00 for work done at 18:26. The logbook is the editorial
part: why the agent took the path it took. The trace is the verifiable part.

The PM compares them. If the logbook says «ran the suite once» and the trace shows six runs, that
is a finding, just like a mismatch in the evidence.

## 3. The trace (measured from the runtime)

`scripts/trace/tracer.py` reads the agent runtime's own session store (read-only; opencode is
supported, see `adapters/opencode.md`). It splits each session into **tasks**, one per user prompt,
and measures each task:

| Field | Meaning |
|---|---|
| `wall_min`, `tool_min`, `other_min` | Total time; time inside tool calls; the rest (model plus waiting) |
| `steps`, `calls` | Model turns; tool calls, split into reads, edits, and shell |
| `suite_runs`, `suite_min` | Test-command runs and their time (patterns are configurable) |
| `rereads` | Extra reads of a file already read in the same task |
| `failed` | Tool calls that ended in error |
| `longest_call_min`, `longest_gap_min` | The slowest single call (hangs) and the longest silence |
| `tokens_*`, `cost` | As the runtime reports them |

Each step is kept too, with its tool, a truncated command, its duration, and the gap before it, so
you can replay the path the agent followed. Secrets in commands are masked, and command outputs are
not stored.

## 4. The trace database

Store layout. Keep it in a **private** repo, because it names your tickets and commands:

```
data/<project>/sessions/<id>.json   measured, rewritten on each ingest (idempotent)
data/<project>/events.jsonl         PM facts, append-only: verdict, log, assign
data/trace.db                       derived; `tracer.py build` rebuilds it; not versioned
```

Why this layout:
- SQLite does not diff or merge in git, but the JSON files do.
- The database can always be rebuilt.
- Facts only the PM knows live in an append-only log that survives every rebuild: verdicts, the
  cause of each return, logbooks, and manual corrections of agent or ticket.

Typical loop after each review:

```bash
tracer.py --data DATA ingest --project P --since 2026-10-01
tracer.py --data DATA event verdict --project P --ticket T-7 --round 0 --verdict returned --cause evidence
tracer.py --data DATA event log --project P --session <id> --task 1 --file logbook.txt
tracer.py --data DATA build
tracer.py --data DATA report --view v_outliers
```

Ingest soon: some runtimes prune old tool outputs when they compact context.

Agent identity is read from the `TO: <agent>` line of each prompt. That rule exists to route prompts
correctly, and it also labels the data. Tickets are matched by regex on the prompt and the session
title. Fix the rest with `event assign`.

## 5. What to look at

| View | Question it answers |
|---|---|
| `v_outliers` | Which tasks need a look: a call over 5 min (hang?), 10+ test runs, 15+ re-reads, over 60 min, 5+ failures |
| `v_ticket` | What each ticket really cost, with work and returns separated |
| `v_return_cause` | Are returns about evidence, code, or scope? |
| `v_agent` | Per agent and task kind: average time, test runs, re-reads, failures, cost |
| `v_day` | The trend over time |
| `v_one_shot` | How often one-shot was broken |

Decisions the views support:
- **Over 30 min or 10+ test runs on one task:** the ticket was probably too big. Split the next one.
- **A return that costs more than the work:** fix the report format or the prompt, not the code.
- **A single call that runs for minutes:** a hang or a missing timeout. Fix the tool, not the agent.
- **Many re-reads of the same files:** the ticket lacked context. Add it to the ticket.
- **One lane always slower:** its tickets need better context, or it needs faster tests.

## 6. Guardrails

- **Time informs; it is not a target.** Agents never see rankings. An agent pushed on speed skips
  verification.
- **Compare like with like.** A proposal task and a code task are not comparable. Group by kind
  and lane.
- **Small samples mislead.** One day is a baseline, not a trend. Wait for weeks before drawing
  conclusions about agents.
- **The trace can reveal rule breaks.** In the original project, the first ingest showed an agent
  printing an API key with `printenv` and `cat .env`. Scan the steps for that pattern on every
  ingest.
