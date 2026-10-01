# Adapter: Hermes Agent kanban

Notes for running board mode (`references/board-mode.md`) with the `hermes kanban` CLI.

## Setup

```bash
hermes kanban boards create <project>
hermes kanban boards switch <project>
hermes kanban boards set-default-workdir <project> /path/to/repo
hermes kanban ls && hermes kanban stats
```

## Command map

| Step | Command |
|---|---|
| Create, unassigned | `hermes kanban create "<title>" --body "<ticket>" --idempotency-key <slug> --workspace dir:<repo> --json` |
| Depend | `hermes kanban link <parent-id> <child-id>` (`--parent` only takes ids) |
| Refine | `hermes kanban specify <id>` / `decompose <id>` |
| Preview the kickoff | `hermes kanban dispatch --dry-run` |
| Start | `hermes kanban assign <id> <profile>` (this spawns the worker) |
| Daily | `hermes kanban ls`, `runs <id>`, `tail` |
| Review | `hermes kanban request-review <id> --summary "..."`, then a verdict of `complete` or `request-changes` |
| Metrics | `hermes kanban stats`, `diagnostics` |

## Gotchas

- **The dispatcher is on by default.** An assigned card in `ready` spawns on the next tick.
- **Never set `kanban.default_assignee`** while there is an unassigned backlog.
- **Set `kanban.review_dispatch: true`**, or `request-review` never spawns the reviewer.
- **`--completion-contract` only accepts `local-only`, `OWNER/REPO`, or a PR URL.** The acceptance
  criteria go in `--body`.
- **The default `--workspace` is `scratch`.** `set-default-workdir` does not change it, so pass
  `--workspace dir:<repo>` on every create.
- **Workers in dispatch context have a safe-write root.** They cannot write to `/tmp`, and they
  cannot run heredocs or `python3 -c`. Check board state with read-only `sqlite3` on
  `$HERMES_KANBAN_DB`.

## Helper

`create_backlog.py` creates a whole backlog from a JSON file. It never assigns a card, it is
idempotent, and it wires the dependencies after creation:

```bash
python3 adapters/hermes-kanban/create_backlog.py backlog.json --dry-run
python3 adapters/hermes-kanban/create_backlog.py backlog.json --board <project>
```

The input format is documented at the top of the script.
