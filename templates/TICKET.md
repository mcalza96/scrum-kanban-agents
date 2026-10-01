# <ID> · <Title: what works when this closes>

Epic <NAME> · Lane **<LANE>** · Agent: **<agent>** · Depends on: <ID> closed ✓ · Decided by: <human, date>

## Why

Two to five lines. Give the measured problem, with a pointer to the report that measured it.
Say which files the dev must read first, and which ones it must **not** read (hidden sets).

## Files (lane <LANE>)

- `path/to/file.py`: only `<function>`, around lines ~120–180.
- `path/to/test_file.py`: only the class `<TestX>`.
- **New:** `path/to/test_new.py`.
- **Out of scope:** everything else, in particular `<files the dev might be tempted to touch>`.

## Tasks

### T1 · <what changes>

- What changes, precisely.
- **What does not change**: the other gates, thresholds, and prompts.

### T2 · Tests

- **Must keep failing:** <cases the gate must still block>.
- **Must now pass:** <cases>, using a **new synthetic example** with invented names.
- **Forbidden:** «the real output of case X passes», and items from hidden sets.

### T3 · Information only (don't change)

- A question the PM needs answered. Answer it with a file and a line, or with «not found».

## Parts and STOP

(Only when a human decision is needed in the middle.) Part A ends with **STOP**: report and wait.

## Rules

- Evidence: every claim has its `$ command` and literal output, with no `...` and no hand-written
  diffs or listings. Times come from `date`. See `references/evidence.md` of the skill.
- Gates: if a test shows that another gate needs to change, **STOP** and report.
- Speed: run each suite once to a file, then filter that file.
- Forbidden: commits, overrides, model or settings changes, publishing, and the lanes of others.

## Report

In `reports/<ID>.md`:
- the real diff (or the `sed -n` of the code for untracked files);
- the tests with `-v`;
- the changed tests, before and after;
- the answer to T3;
- the suites, with their command.

Then add, as its last section, `## Logbook`: at most 10 lines, **no times**. Cover the phases you
followed, what you repeated and why, where you got stuck, and what would have saved time.

Set the `STATUS.md` row to `reported`, with the time from `date`, and **end the session**: one task
per session.
