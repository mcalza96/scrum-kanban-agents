# Evidence: what a report must contain and how the PM verifies it

Agents fill gaps with plausible text. A report that says «tests pass» is a claim. What makes it
evidence is the exact command plus its output, and the PM's ability to reproduce it.

## Report format (the dev)

Every factual claim goes with a block like this:

````
```
$ <exact command>
<exact output>
```
````

Hard rules:

- **No `...` inside a block.** If you need to shorten the output, shorten it with a command and
  show that command (`tail -n 17`, `sed -n '40,60p'`, `grep -n`). Then the block is still a literal
  output.
- **No hand-written diffs.**
  - **Tracked files:** write the diff to a file once (`git diff -U3 -- <file> > /tmp/x.diff`), then
    paste filtered ranges of that file (`sed -n '12,40p' /tmp/x.diff`). Keep the `+` and `-`
    prefixes and the context lines.
  - **Untracked files:** git has no diff. Paste the current code with `sed -n '<range>p' <file>`
    and say that the file is untracked.
  - **Never claim an empty diff** without pasting the command that printed nothing.
- **No hand-written listings.** File sizes and times come from a real command (`ls -l
  --time-style=full-iso`, `stat`, `sha256sum`), pasted as is.
- **Times come from `date`.** Never estimate or invent a time. An invented time in a status row
  breaks the timeline that the PM uses to reason about what changed when.
- **Explain only what you know.** If you saw a test fail and don't know why, write «cause not
  investigated». A plausible explanation without evidence counts as false data.
- **Declare what you could not really run.** If something was only tested with mocks, say so in
  the summary.

## Verification procedure (the PM)

1. **Open the artifact** at the declared path. Never review only the description.
2. **Re-run every pasted command** and compare its output with the report. Differences are
   findings. `scripts/check_report.py` automates the extraction and the comparison.
3. **Check the session log.** Most agent runtimes keep a log or database of the tool calls each
   session made: commands, file edits, timestamps.
   - Did the command in the report actually run?
   - When did it run, and in which session?
   - Which files did the session edit, and at what time?

   This is how you catch a listing that was never produced, a diff claimed empty for a file that
   was edited, or a test run that came from an older version of the code. Open the log read-only.
   See `adapters/` for the queries for specific runtimes.
4. **Check the times.** Compare the file mtimes and the session-log timestamps with the times the
   report gives.
5. **Check that nothing outside the scope changed.** Compare the files of other lanes, the frozen
   inputs (use their hashes), and the gates.
6. **Re-run the suites yourself** with the canonical command from `EPIC.md`. If there are new red
   tests, check whether they come from another ticket in progress (look at mtimes) before blaming
   this one.

## Verdict

| Situation | Verdict |
|---|---|
| Any data is false or unverifiable: an invented listing, a hand-edited diff that changes content, an empty diff claimed for an edited file, an invented time, an explanation without support | **Returned**, even if the code is correct |
| All data is real, but the form breaks the rules: `+` prefixes stripped from a real diff, the top of a real output trimmed without showing the trimming command | **Closed with notes**. The notes go in the review so the next report avoids them |
| Everything matches | **Closed** |

Why this rule: the cost of a false report is not that one ticket. It is every later decision built
on it. A team where «the code was fine anyway» closes a ticket learns that evidence is optional.

## The PM is also checked

- The PM's own times come from `date` or from file mtimes.
- The PM must not leak hidden data (`references/gates.md`) into files the dev reads. If a review
  mentions a hidden item, remove the mention and record that you did.
