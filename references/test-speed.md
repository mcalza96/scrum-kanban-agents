# Test speed and single-run output

Slow suites make agents run them less often, cut them short, or report from memory. Fast suites
are a process tool, not a nicety.

## Make the suite fast early

- A parallel test runner (for example `pytest -n auto`, or a small runner that splits the test
  modules across N processes) cut a suite of about 400 tests from roughly 4 minutes to about 30
  seconds.
- Do it as one of the first tickets of any project where agents will run the suite many times.
- Keep the sequential command too, and compare both once. The same number of tests and the same
  failures mean the parallel runner is trustworthy.
- Cache expensive loads that many tests repeat, such as dictionaries or vocabularies. Key the cache
  on the path plus the mtime, and never let callers mutate the cached object.

## Run once, then filter

```
$ <suite command> > /tmp/suite.txt 2>&1; echo "exit=$?"
$ tail -n 5 /tmp/suite.txt
$ grep -n "FAIL\|ERROR" /tmp/suite.txt
```

- Don't re-run the suite to see a different part of its output.
- Paste the filtering commands as evidence, as `evidence.md` requires.

## Timeouts

- The tool timeout must be at least as long as the command. A suite killed by the agent's own
  timeout looks like a hang, and agents then "report" from partial output.
- Put the expected duration in `EPIC.md` next to each suite command.

## Environment

- **Host versus container paths.** State in `EPIC.md` which path each command uses.
- **Secrets.** Pass them with an env file (`--env-file`). Never print, cat, or copy them into a
  report.
- **Independent checks in parallel.** If the agent's tool supports parallel calls, run the
  independent suites at the same time.
- **Test the expensive step with N=1 before N=8.** A missing binary in a background PATH failed
  8 of 8 jobs at once.
