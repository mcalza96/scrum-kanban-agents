# scrum-kanban-agents

A skill for running software projects with an **AI project manager** and **AI dev agents**. It
grew out of a real multi-week project in which several coding agents worked in parallel on one
codebase, and it records what was needed to trust their work.

## What it gives you

- **A process.** Tickets, a status board, lanes so agents don't overwrite each other, STOP points
  for human decisions, and a closing plan for epics that keep growing.
- **Evidence rules.** Reports paste literal command output, and the PM re-runs every command and
  checks the agent's session log. Most returned tickets in the original project were caused by
  fabricated or hand-edited evidence, not by bad code.
- **Gates that don't overfit.** Changing a gate needs a human OK, tests that must keep failing, and
  a before/after measurement on a hidden evaluation set. In the original project, a detector tuned
  on one case caught 0 of 7 real cases. After the fix, it caught 7 of 7.
- **A checker.** `scripts/check_report.py` extracts every `$ command` block from a report, flags
  signs of hand editing, and with `--run` re-runs each command and diffs the output.
- **Traceability.** Dev sessions are strictly one-shot (one task each), and each report ends with a
  short logbook. The PM measures every task straight from the agent runtime's own store,
  read-only: prompts, time, test runs, re-reads, and failures (one SQL query for opencode).
- **Plan attack, for complex work only.** Before any card is assigned, four blind attackers attack
  the plan: figures, feasibility, logic and gates, and scope. The PM re-verifies each finding and
  attacks its own fixes in a second round. In the field, this found gates that could not fail and a
  goal that no card delivered, in about three minutes.
- **Focus that survives compaction.** Focus files at two levels (project and line of work, one
  PM per line) hold each objective in the PO's own words and the directives in force. Only the PO
  changes an objective, so a late nuance can't silently replace it when a PM's context is
  compacted. A session-start hook (`scripts/focus.py hook focus/`) re-injects the anchors after
  every compaction, and `scripts/focus.py check focus/` flags what lets an objective drift.
- **PMs talk to each other.** One mailbox per PM (`scripts/inbox.py`). A hook (an opencode plugin
  in `adapters/opencode/inbox.js`, or a Claude Code hook) puts unread messages in front of every PM
  turn, the PM acks after acting, and `inbox.py check` flags what was left unread. In opencode a
  message also wakes an idle PM (`adapters/opencode/wake.py`, capped at 20 wakes per PM per hour).
- **Templates.** `STATUS.md`, tickets, reviews, prompts, a closing plan, and the focus file.

It is agnostic: it works with any coding agent (Claude Code, opencode, Codex, ...) and any board,
from a kanban CLI to a markdown table. Tool-specific notes live in `adapters/`.

## Install

The skill is the folder. Put it wherever your agent loads skills from, for example:

```bash
# Claude Code
git clone https://github.com/mcalza96/scrum-kanban-agents ~/.claude/skills/scrum-kanban-agents

# Any agent: clone it anywhere and point the agent at SKILL.md
git clone https://github.com/mcalza96/scrum-kanban-agents
```

Then ask your agent to act as the PM «using the scrum-kanban-agents skill». Or start from
`templates/` and `SKILL.md` yourself.

## Check a report

```bash
python3 scripts/check_report.py reports/T-07.md                  # list commands + static flags
python3 scripts/check_report.py reports/T-07.md --run --cwd repo \
        --mask 'in [0-9.]+s'                                     # re-run and diff
```

`--run` executes commands that somebody else wrote. Read the listing first, and use `--only` to
run a subset. Both tools use only the Python standard library. Tests:
`python3 -m unittest discover -s scripts/tests`.

## Layout

```
SKILL.md          the skill: roles, lifecycle, the twelve rules, DoD, metrics
references/       one topic per file (evidence, gates, file mode, board mode, ...)
templates/        STATUS, TICKET, REVIEW, PROMPT, CLOSING-PLAN, FOCUS
scripts/          check_report.py, focus.py, inbox.py, archivar.py, stale_worktrees.py, tests
adapters/         opencode (inbox, wake, lanzar, guard), codex, Hermes kanban
```

## Contributing

Lessons are welcome when they come with evidence: what failed, the command, and the output. See
`references/lessons.md` for the format.

## License

MIT. See `LICENSE`.
