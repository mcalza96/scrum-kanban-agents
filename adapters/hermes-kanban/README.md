# Adapter: Hermes Agent kanban

Notes for running board mode (`references/board-mode.md`) with the `hermes kanban` CLI.

## Setup

```bash
hermes kanban boards create <project>
hermes kanban boards switch <project>
hermes kanban boards set-default-workdir <project> /path/to/repo
hermes kanban ls && hermes kanban stats
```

Done when `hermes kanban boards show` prints the new slug as the active board.

## Command map

| Step | Command |
|---|---|
| Create, unassigned | `hermes kanban create "<title>" --body "<ticket>" --idempotency-key <slug> --workspace dir:<repo> --json` |
| Depend | `hermes kanban link <parent-id> <child-id>` (`--parent` only takes ids) |
| Refine | create with `--triage`, then `hermes kanban specify <id>` / `decompose <id>` |
| Preview the kickoff | `hermes kanban dispatch --dry-run` |
| Start | `hermes kanban assign <id> <profile>` (this spawns the worker) |
| Daily | `hermes kanban ls`, `runs <id>`, `tail` |
| Review | `hermes kanban request-review <id> --summary "..."`, then a verdict of `complete` or `request-changes`; `reopen-review` if it comes back |
| Metrics | `hermes kanban stats`, `diagnostics` |

## Gotchas

- **The dispatcher is on by default.** An assigned card in `ready` spawns on the next tick.
- **Never set `kanban.default_assignee`** while there is an unassigned backlog.
- **Set `kanban.review_dispatch: true`**, or `request-review` never spawns the reviewer.
- **`--completion-contract` only accepts `local-only`, `OWNER/REPO`, or a PR URL.** The acceptance
  criteria go in `--body`.
- **The default `--workspace` is `scratch`.** `set-default-workdir` does not change it, so pass
  `--workspace dir:<repo>` on every create.
- **Delegated children cannot mutate the board.** A `delegate_task` child context
  cannot `create`/`comment`/`block`/`complete` via the CLI (guarded). Children
  return `{pointer, summary}` and stop; only the parent comments, moves, and
  closes cards. See `references/parent-focus.md` (one writer, PM keeps its own
  short todo, cap open tickets per workstream).
- **Workers in dispatch context have a safe-write root.** They cannot write to `/tmp`, and they
  cannot run heredocs or `python3 -c`. Check board state with read-only `sqlite3` on
  `$HERMES_KANBAN_DB`.

- **Dispatch runs inside the gateway by default** (`dispatch_in_gateway: true`). That is why
  assignment is execution: nothing else has to be started.
- **`--parent` is enforced.** A card whose parent is still open stays in `todo`, and `claim_task`
  refuses to promote it. Use it for real dependencies only.
- **Cap parallelism with `kanban.max_in_progress_per_profile`.** Together with "create
  unassigned" and `dispatch --dry-run`, it is the only control that holds when you assign too much.
- **Parallel devs in the same directory overwrite each other** and the board does not notice.
  Give each card its own workdir (`--workspace worktree:<path>` or `--project`), or serialise the
  cards that share files with `link`.
- **One profile only?** Then every card goes to it and the role split lives in the card body, not
  the profile. The reviewer must be a different agent or a script, never the same flow.
- **A worker with no heartbeat past `--max-runtime` is a timeout, not a success.** Watch it with
  `tail` and `runs <id>`.
- **A worker plugin may pin its model.** If the dev runs through a plugin, changing the model can
  mean editing the plugin and the `__init__.py` that imports the constant. Renaming one without the
  other breaks the whole plugin load, and the tool disappears. Run the plugin tests afterwards.
- **Only `kanban_attach` survives a scratch workspace.** Attach anything the PM must verify later
  (scripts, inventories) to the card.
- **Dispatch context changes the environment.** Workers see `HERMES_SESSION_SOURCE=kanban` and a
  `HERMES_WRITE_SAFE_ROOT`. Fixtures and logs go under that root, never `/tmp`, and verification
  runs from script files. A contract that asks for a fixture in `/tmp` cannot be met as written.
- **Make tests declare their root with an environment variable** (falling back to a path derived
  from `__file__`). Then the PM can run the real test against copied case trees without touching
  guarded paths. Fallback when a test has no such variable: load it with
  `importlib.util.spec_from_file_location`, reassign its path constants to a temp tree, and run it.
- **The board is the work queue, not the report.** The user reads chat. Report there, short, with
  the pointer.
- **Sprints are defined by a result with a verifiable gate**, not by the calendar. With no human
  team, a weekly sprint measures the calendar, not progress.

## Sprint metrics

Close each sprint with `hermes kanban stats` and `diagnostics`, plus first-pass yield (cards closed
without `request-changes`). Above 70% means the specs are well written; below 50% means the problem
is the specification, not the dev.

## Card templates

`card-templates.md` has bodies for story, review, retro and spike cards, ready to paste into
`--body`.

## Helper

`create_backlog.py` creates a whole backlog from a JSON file. It never assigns a card, it is
idempotent, and it wires the dependencies after creation:

```bash
python3 adapters/hermes-kanban/create_backlog.py backlog.json --dry-run
python3 adapters/hermes-kanban/create_backlog.py backlog.json --board <project>
```

The input format is documented at the top of the script.
