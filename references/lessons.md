# Field lessons, grouped by topic

Each lesson here comes from a real failure. Add a lesson only when it has evidence: a ticket, a
command, and an output. «It was slow» is not a lesson.

## Delivery and reporting

- **`completed` with zero files written is a delivery failure.** Subagents have reported success
  without writing anything. Check the artifact, not the status.
- **Pointer, not payload.** The dev writes the artifact to the repo and returns `{pointer,
  summary}`. Pasting the content into the card or the chat fills the context of everyone who comes
  after.
- **Siblings don't see each other.** A dev that closes a ticket other tickets depend on leaves its
  decisions as a note on each child: a frozen schema, naming conventions, reference rules.
  Otherwise the children re-derive those decisions and diverge.
- **Declare what was only tested with doubles.** An honest close lists the artifacts whose only
  evidence is a harness with mocks, and that list goes in the report to the human.

## Verification

- **Probe the obvious form.** A linter closed green with 48 tests while one of its 12 rules could
  never match. Its pattern needed a comma that the text normalizer removed. The dev's tests only
  covered the other branches.
- **Mass, uniform failure points at the verifier.** A new gate reported 69 failures on a correct
  artifact: it resolved references from the wrong root. Before you fix a pattern written over free
  text, print the real line it should match.
- **The ticket states the convention.** If the PM had to deduce a resolution rule by reading the
  artifact, the dev had to deduce it too. Write it in the ticket.
- **Plant the change the contract talks about.** If the contract says «artifact X changes», mutate
  a copy and confirm that the harness fails on exactly that case. Then restore the copy and run the
  harness again.

## Logs, counters, ledgers

- **A counter over a log that is never pruned counts the same row N times.** Separate what dedup
  guarantees once (that can accumulate) from what is re-read on every pass (report the level of the
  last run). Add an explicit invariant: `dropped + partial + new + duplicate == useful rows`.
- **A contract with an exact number over a live file can't be met.** Freeze the rows as a fixture
  and run the gate against that snapshot. Report the drift of the live file separately.
- **Attribute, don't compare raw counts.** A shared log grows because of other processes. Fail hard
  only on what this run wrote, and report the raw growth as observed.
- **Record the instrument in an append-only ledger.** Store a hash of the measuring script, and a
  `repeat_of` field on re-measurements. Without them, two rows with the same set id look like
  contradictory results.

## Git

- **A `git add` with stderr hidden can commit a fraction of the files.** A pathspec that contains a
  nested repo aborts the whole add. If you hide errors with `2>/dev/null`, the script reports
  success anyway.
  - Never silence the stderr of `git add`.
  - Compare the count of `git status --short` before and after.
  - Look for nested `.git` folders first.
- **Before you remove a nested `.git`, measure what is lost:** commits, refs, remotes. An
  accidental `git init` has nothing in it. A real clone has a remote, and its URL and commit must be
  recorded first.
- **The agent may not be able to commit.** For example, the `.git` may belong to another uid. In
  that case, deliver a script with the groups, pathspecs, and messages ready, the exact command,
  and the blocker.
- **Before you accept a commit, scan what went in.** Check the added lines for secret patterns and
  look for files that shouldn't be versioned. Also check that deleted files have a copy somewhere.

- **One checkout shared by many agents loses work to `stash`, `checkout`, `restore` and `reset`.**
  On 2026-11-03 a PM ran `git checkout HEAD -- inbox/` and `git stash push --include-untracked`
  then `drop`: the mailboxes of every PM went back twice, and other agents' uncommitted work went
  with them. Devs did the same to make `tsc` pass (`stash push --keep-index`, `pop`, `drop`,
  `reset HEAD`).
  - In a shared checkout an agent never runs `stash`, `checkout -- <path>`, `restore`, `reset`,
    `add -A` or `--amend`. It commits with `git commit --only <its paths>`.
  - A dropped stash is not gone: `git fsck --no-reflog --unreachable` lists it as a dangling
    commit. Recover file by file with `git show <sha>:<path> > <path>` (`<sha>^3` holds the
    untracked files), comparing with the current file first. Never `stash pop` it over a live tree.
- **«It builds» in a shared checkout means «it builds with everyone's uncommitted changes».** A
  commit used a prop whose change stayed uncommitted; another left out a new file that was never
  `git add`ed. Both were green in the tree, red in the commit, and the deploy broke while the
  devs' launcher queue stalled behind a trunk that did not compile. A `pre-push` hook
  (`scripts/hooks/pre-push`) builds each pushed commit in a throwaway worktree and refuses the
  push otherwise; it remembers trees that passed, so a re-push costs nothing.
- **Every worktree carries its own copy of the gate script.** An old copy skips the queue and the
  cache of the new one. The script's first act is to `exec` the copy of the main checkout
  (`git rev-parse --path-format=absolute --git-common-dir`).
- **A dev that commits its own work skips the gate.** Only the PM commits, after the verification
  of that exact sha (DoD 2).

## Supervision

- **When the PO drowns, add a delivery director, not a second PO.** (Superseded on 2026-11-04: the
  director drowned in progress reports too, and became an auditor without a mailbox, measuring on
  demand. What it measures stays the same.) On 2026-11-03 the PO took about
  one turn a minute, mostly status echoes from PMs. The human put one agent over the PO:
  - it reads the runtime's own records every 10 minutes (`adapters/opencode/control.sh`), never
    the agents' accounts: duplicate devs on one ticket, forbidden git or `kill` commands, ungated
    commits, reverted mailboxes, the PO's turns per window;
  - it alerts the PO with the evidence, typed `[bloqueo]` when work is at risk;
  - it records the human's decisions and turns them into tickets;
  - it does not prioritize, assign, launch devs, kill processes or edit code.
- **Typed messages and a hold cut the PO's wakes.** `[aviso]` waits up to 15 minutes and wakes the
  PO once for all of them; `[bloqueo]` and `[decision]` wake at once; nobody replies to acknowledge
  (references/inbox.md). In the first 10 minutes the PO went from 25 wakes per half hour to 6
  turns.
- **A launcher must make one request one launch.** A plugin that retried every 60 seconds with a
  claim that was not atomic started 6 devs on the same ticket, and they undid each other's files.
  Claim by an atomic rename, and test it with a double of the binary.
- **Files every agent reads each turn must be bounded.** Mailboxes, the wake log, `STATUS.md` and
  the focus tables grow forever, and their cost is paid on every read (`STATUS.md`: 555 reads in a
  day). `scripts/archivar.py` archiva buzones y logs en el barrido; STATUS y focus requieren
  `--tableros` manual, sin editores concurrentes (decisión del dueño, 2026-11-06). When ids are
  numbered from the highest row, the highest row never moves, or ids are handed out twice.

## Environment and permissions

- **Workers often have fewer permissions than the PM.** They may be sandboxed from writing to
  `/tmp`, blocked from running heredocs or interpreters through pipes, or running as a different
  uid. Write tickets that work under those limits.
- **A file the agent is forbidden to edit.** When a ticket needs a change to a guarded file, use an
  idempotent applier script. It makes a backup, does an exact replacement, runs a syntax check and
  then the harness, and has a dry-run mode. Test it on a copy, then run it through the authorized
  channel. Declare that channel in the summary.
- **Ownership problems can't be fixed from inside an unprivileged container.** Block the ticket
  with the exact command to run on the host. Use a targeted `find ... -user X -exec chown`, never a
  recursive chown of the root.
- **A binary on the PM's PATH may be missing from the worker's PATH.** Resolve it explicitly, pass
  `env`, and try N=1 before N=8.
- **Free or rate-limited models can hang instead of failing.** The worker stays alive and silent
  until a timeout kills it. When tasks always die at the same elapsed time, read the agent runtime's
  own log.
- **A model listed by the API is not proof that it works.** Make one real call before wiring it in.

## Planning

- **Attack the plan, not only the cards.** A knowledge-ingestion plan passed the author's own
  review. Four blind attackers then found two critical design defects in about three minutes:
  no card produced the stated goal, and several gates could not fail. They also found eight
  misquoted measurements. The author's fixes were not attacked again, and a later scope cut was
  not attacked either. That is why a second round exists (`plan-attack.md`).
