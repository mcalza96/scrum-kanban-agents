# Card templates (hermes kanban)

Each template is pasted into the `--body` of `hermes kanban create`. The acceptance criteria live
in the body: `--completion-contract` only takes `local-only`, `OWNER/REPO` or a PR URL. For a full
ticket outside the board, use `templates/TICKET.md`.

---

## 1. Story (most cards)

```markdown
## Goal
One sentence. What works when this card closes.

## Context the dev cannot guess
Exact paths, decisions already made and why, the input format, what already exists and must not be
rebuilt. The dev does not see this conversation: if it is not here, they do not know it.

## Scope
- Files to create (exact path).
- Files to modify (exact path).
- Out of scope: what must NOT be touched, said explicitly.

## Acceptance contract
1. `<path>` exists and contains `<verifiable shape>`.
2. `<test command>` exits 0 and `<N>` tests pass.
3. `<gate command>` exits 0.

## Report
Comment on this card: `{pointer: <absolute path>, summary: <=280 chars}`. No inline artifacts. If
something is half done, say so in the summary instead of dressing it up.

## Known trap
The specific failure mode of this work, if there is one.
```

## 2. Review

```markdown
## What is reviewed
Card <id> closed with pointer `<path>`. Review THAT artifact, not its description.

## Required verdict
One line per point, with evidence:
1. Does the artifact at the declared path meet the contract of <id>? (read it, do not infer it)
2. Do the tests run and pass? (run them)
3. Does the dev's summary match what the artifact says? (differences are findings)

## Rule
The reviewer does not fix. If something is missing: `request-changes` with the concrete list.
Approving without opening the artifact is worse than not reviewing, because it authorises.
```

## 3. Retro (per sprint)

```markdown
## What was done
Share of the sprint backlog closed, and what was left out.

## Observed failures
Only those with evidence: card, command, output. "It was slow" is not a failure.

## Process change
One concrete rule added to or removed from the skill. Without it the retro is theatre.

## Backlog change
Cards to split, rewrite or delete.
```

## 4. Spike (research needed before a card can be specified)

```markdown
## Question
Only one, answerable with evidence.

## Why it blocks
Which backlog card cannot be specified without this answer.

## Deliverable
A file with the findings and, for each claim, the source opened and checked against the cited
topic. `no source` is a valid answer; inventing one is not.

## Acceptance contract
Every claim has an opened URL, and the URL is about what the claim says.
```

---

## Spec mistakes that already cost a lot

- **A spec that describes instead of specifying.** "Implement the token system" names no file, so
  the dev picks one, and picks differently from what the PM had in mind.
- **No *Out of scope* section.** The dev refactors too much and the diff stops being reviewable.
- **A contract with no command.** "Check that it works" is not a contract. A command with an exit
  code is.
- **Asking for the payload.** If the body says "paste the result here", the dev pastes it and the
  context fills up. The report contract is always pointer + summary.
