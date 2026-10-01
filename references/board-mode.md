# Board mode: a kanban tool that spawns dev agents

Some kanban tools run a dispatcher: a card that has an assignee and is in `ready` gets an agent
spawned on it. That is powerful and dangerous.

## Irreversible rule: assignment is execution

If the dispatcher spawns any card that is assigned and ready, then **assigning means "start
now"**, with no further confirmation.

1. **Create the backlog unassigned.** Unassigned cards stay parked.
2. **Assigning is the sprint kickoff.** Do it in priority order.
3. **Never set a default assignee** while unassigned cards exist. That key auto-assigns every ready
   card and starts everything at once.
4. **Use a dry-run first.** If the tool has a dispatch dry-run, run it before assigning in bulk.
5. **Cap parallelism.** Use the tool's limit on tasks in progress per worker, not discipline.

## Card lifecycle

```
triage → todo → ready → running → review → done
                  ↑                  ↓
               blocked        request-changes
```

- **Create.** Give the card a title, a body (the ticket from `templates/TICKET.md`), and an
  idempotency key. Don't give it an assignee.
- **Depend.** Express dependencies with the tool's link or parent feature, so they are enforced in
  code and not by convention.
- **Run.** The worker inherits the card body as its only spec, and it does not see the PM's
  conversation.
- **Report.** The worker posts a comment with `{pointer, summary}`. The artifact goes in the repo,
  never inline.
- **Review.** Apply `references/evidence.md` before approving.
- **Close.** Only the PM closes, and only when the DoD is green.

## Pitfalls seen with dispatching boards

- **The default workspace may be a throwaway directory.** The worker writes there, the card
  "passes", and the artifact is missing from the repo. Always pass an explicit workspace on every
  card, and check it in the card's stored fields.
- **CLI flags that look like free text often aren't.** A field named like a "completion contract"
  may only accept a publication target. Acceptance criteria go in the body.
- **Some tools print `create --json` output indented over several lines.** If you parse only the
  last line, you get `}`, and the dependency links then fail with errors that look like dependency
  bugs. Parse the whole stdout.
- **Workers run with fewer permissions than the PM.** They often cannot write to `/tmp`, run
  heredocs, pipe into an interpreter, or use the board CLI. Write tickets that assume this: script
  files instead of inline code, temp files under the repo, and the board's database opened
  read-only.
- **Only the heartbeat says whether a worker is alive.** A worker with no heartbeat past its
  runtime limit is a timeout, not a success.

Tool-specific notes and a backlog helper are in `adapters/`.
