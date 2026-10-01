---
name: scrum-kanban-agents
description: "Run a software project with an AI PM and AI dev agents: tickets, a status board, verified evidence, gates that don't overfit, and a plan to finish."
version: 0.3.0
license: MIT
tags: [scrum, kanban, pm, agents, verification, delegation, evaluation]
---

# Scrum/Kanban with AI agents as the dev team

One agent (or a human) is the **PM**. Other agent sessions are the **devs**. The PM writes tickets,
the devs implement and report, and the PM verifies the report against reality before closing
anything. The board is the record of truth: work that is not on the board does not exist, and work
is not done until the PM has verified it.

This skill is tool-agnostic. It works with any coding agent (Claude Code, opencode, Codex, Aider,
...) and with any board: a kanban CLI, an issue tracker, or a plain `STATUS.md` file.

## When to use

- The work spans several sessions, several files, or several agents.
- Some pieces can be built in parallel.
- The human wants to decide and review, not to drive every step.

Don't use it for a single change (just do it), for open research, or for work that needs a live
human decision at every step.

## Roles, never collapsed

| Role | Who | Does | Does NOT |
|---|---|---|---|
| Stakeholder | The human | Prioritizes, decides, accepts | Edit the board rows of others |
| PM | One agent session (or the human) | Writes tickets, verifies reports, closes or returns | Implement what it reviews |
| Dev | A fresh agent session for **one** task (one-shot) | Implements, runs tests, writes the report and its logbook | Take a second task, decide scope, close its own ticket |
| Gate | Tests, scripts, hidden evaluation sets | Mechanical verdict | Get changed by the dev it judges |

A PM that edits the artifact under review is reviewing its own work. The human decides; the PM
recommends with evidence and does not decide for the human.

## Two ways to run the board

- **Board mode**: a kanban tool spawns devs from cards. Read `references/board-mode.md`. The most
  important rule is that **assigning a card is starting it**.
- **File mode**: tickets are `.md` files, the board is `STATUS.md`, and the human launches each dev
  session with a prompt. Read `references/file-mode.md`. Use it when the human launches agents by
  hand, or when tests and evaluation sets matter more than automation.

Both modes share everything below.

## Ticket lifecycle

```
pending → in progress → reported → closed
                           ↓
                       returned → in progress (new session)
```

- Only the PM sets `closed` or `returned`.
- Each dev edits only its own row on the board.
- A return goes to a **new dev session**, with a self-contained prompt that lists only what must be
  fixed (`references/prompts.md`).
- **One session, one task.** A session ends when its task's report is written. The next task,
  even for the same agent, starts a new session (rule 8).

## The nine rules

1. **Evidence is literal.** Every claim in a report comes with the exact `$ command` and its
   output, pasted as is: no `...`, no hand-written diffs, no hand-written `ls -l`, no estimated
   times. Read `references/evidence.md`.
2. **The PM re-runs and cross-checks.** The PM re-runs every pasted command and compares the
   output. It also checks the dev's session log to see what was really run. A report is a claim,
   not evidence.
3. **Verdict rule.** Any false or unverifiable data means **returned**, even if the code is right.
   If only the form is wrong and no data is false, the verdict is **closed with notes**.
4. **Gates don't overfit.** A change to a gate or threshold needs three things:
   - the human's OK, written in the ticket;
   - a test that must still fail;
   - a before/after measurement on a **hidden** evaluation set the dev never sees.

   Cases used to iterate and cases used to validate are kept separate. Read `references/gates.md`.
5. **Prompts have an addressee and stand alone.** Every prompt starts with `TO: <agent>` and
   assumes a brand-new session, with no memory of this one.
6. **Fast, single-run tests.** A suite is run once to a file, and then that file is filtered. The
   timeout is at least as long as the command. Read `references/test-speed.md`.
7. **Finish on purpose.** When tickets appear faster than they close, write a closing plan:
   - a result-based definition of done and a few steps;
   - everything else frozen;
   - no new ticket unless it comes from a failure seen in those steps.

   Read `references/closing.md`.
8. **One-shot devs, strictly.** Each dev session receives exactly one task: one ticket part or
   one return. It writes the report, sets its row to `reported`, and stops. It does not accept a
   second task, a follow-up question, or a «while you're at it».
   - Questions for the dev go into the next task's prompt, in a new session.
   - The PM checks this in the trace: a session with more than one prompt is a process failure,
     and it is recorded.
   - Why: in the project this skill comes from, 7 of 18 sessions on one day took several tasks. The
     longest one took 7 prompts and ran for 3.5 hours with 58 test runs. A prompt meant for one
     agent landed in another agent's session. Old context made later tasks slower and harder to
     verify.
9. **Every task is traced.** The dev ends its report with a **logbook** of at most 10 lines,
   without times: the phases it followed, what it repeated and why, where it got stuck, and what
   would have saved time. The PM gets the numbers from the agent runtime's own records, never from
   the agent: duration, steps, test runs, re-reads, failures, tokens, and cost. The PM keeps them in
   a trace database with each verdict. Time informs the PM. It is never a target for the devs. Read
   `references/traceability.md`.

## Definition of Done

A ticket is not closed without all of these. The PM checks them one by one, not from memory:

1. The artifact exists at the declared path, and the PM opened it.
2. The tests pass with exit 0. The exact command is in the report, and the PM re-ran it.
3. Every pasted output matches the PM's re-run (rule 2).
4. The gates pass, and none of them changed without rule 4.
5. No test of the form «the real output of case X passes».
6. The report is a pointer plus a short summary. The payload stays in the repo.
7. A verifier other than the dev approved the work. If the criterion is fully mechanical, a
   deterministic gate can replace the verifier.
8. The report ends with the logbook, the session was one-shot, and the PM ingested its trace and
   recorded the verdict.

A `completed` status without an artifact is a **delivery failure**, not a success.

## Ceremonies

| Ceremony | What happens | When |
|---|---|---|
| Refinement | Split tickets into parts with `STOP` points where the human must decide | Before starting |
| Planning | Tickets, with lanes (`references/file-mode.md`) and dependencies | Start of a phase |
| Daily | Board plus the dev's heartbeat or session log | When reporting to the human |
| Review | PM verification and verdict | Each report |
| Retro | Read the trace views (`v_outliers`, `v_return_cause`, `v_one_shot`) and add or remove one concrete rule | End of a phase |

## Metrics

- **First-pass yield**: tickets closed without a return. Count returns by cause:
  - **evidence** (the report was wrong);
  - **code** (the work was wrong);
  - **scope** (the ticket was wrong).

  Mostly evidence means fix the report format, not the dev. Mostly scope means fix the tickets.
- **Timeout waste**: runs killed by a time limit. It is the most expensive way to fail.
- **One-shot rate**: sessions with exactly one prompt, out of all dev sessions. The target is 100%.
- **Cost of returns**: minutes spent on returns compared with minutes spent on the original work,
  per ticket (`v_ticket`). A return that costs more than the work points at the report format.
- **Gate drift**: for each gate, its precision and recall on the hidden set, recorded with the
  code hash. Every gate decision is compared against that baseline.

## Files

| Path | What it holds |
|---|---|
| `references/file-mode.md` | `STATUS.md`, tickets, lanes, claims |
| `references/board-mode.md` | Running with a kanban tool that spawns agents |
| `references/evidence.md` | Report format and the PM's verification procedure |
| `references/gates.md` | Changing gates without overfitting, hidden sets, evaluators |
| `references/prompts.md` | Prompts for dev sessions and for returns |
| `references/test-speed.md` | Fast suites and single-run output |
| `references/closing.md` | Closing plan for an epic that keeps growing |
| `references/traceability.md` | One-shot sessions, logbook, trace database, what to look at |
| `references/lessons.md` | Field lessons, grouped by topic |
| `templates/` | `STATUS.md`, `TICKET.md`, `REVIEW.md`, `CLOSING-PLAN.md`, `PROMPT.md` |
| `scripts/check_report.py` | Extracts the `$ command` blocks of a report, flags edits, re-runs them |
| `scripts/trace/` | `tracer.py`: ingests agent sessions into a trace database (SQLite) with views |
| `adapters/` | Tool-specific notes and helpers: `opencode.md`, `hermes-kanban/` |
