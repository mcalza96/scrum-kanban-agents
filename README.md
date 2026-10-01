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
- **Templates.** `STATUS.md`, tickets, reviews, prompts, and a closing plan.

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
run a subset. The script uses only the standard library. Tests:
`python3 -m unittest discover -s scripts/tests`.

## Layout

```
SKILL.md          the skill: roles, lifecycle, the seven rules, DoD, metrics
references/       one topic per file (evidence, gates, file mode, board mode, ...)
templates/        STATUS, TICKET, REVIEW, PROMPT, CLOSING-PLAN
scripts/          check_report.py + tests
adapters/         opencode, Hermes kanban
```

## Contributing

Lessons are welcome when they come with evidence: what failed, the command, and the output. See
`references/lessons.md` for the format.

## License

MIT. See `LICENSE`.
