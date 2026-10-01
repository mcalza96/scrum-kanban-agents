# Gates without overfitting

A gate is code that blocks or accepts work: a test, a linter, a validator, a detector, a
threshold. Gates are what let the human trust work they didn't watch. They also overfit easily. A
gate that is adjusted until one known case passes stops measuring anything else.

## A real failure this rule comes from

A detector of contradictory figures was tuned on one flagship case plus synthetic examples. On the
synthetic set it scored 23 of 25. A hidden set of real inputs from 7 different domains was then
measured:

- the detector blocked **all 7** genuine contradictions, scoring 0 of 7;
- its heuristics extracted single words from free text, and on real text they picked the wrong
  word every time;
- retries then taught the generator to stop declaring contradictions at all, which was the most
  valuable part of the output.

The fix kept as a hard failure only what code can verify well, such as an id that doesn't exist or
two values that don't differ. Everything heuristic became a **warning** for a human or a critic.
Results on the hidden real set went from 0/7 to 7/7.

## Rule: changing a gate

A change to a gate or a threshold, in either direction, needs all three:

1. **The human's OK, written in the ticket**, with the options considered and the measurement that
   motivates the change.
2. **Tests that must still fail.** The ticket lists the cases that must keep being blocked. A
   change that also makes those pass is a loosened gate, not a fixed one.
3. **A before/after measurement** on a hidden evaluation set, run by the PM. Record it with the
   code hashes as a baseline (`templates/` has no baseline template, because a table in the review
   is enough).

If a dev finds out mid-ticket that another gate would need to change, the dev **stops** and
reports. The dev does not change it.

## Iteration cases versus validation cases

- **Iteration cases** are the examples the dev works against. The dev may see them and debug them.
- **Validation cases** are separate inputs used only to judge whether the work generalizes. They
  are chosen and run by the PM.
- A case used to iterate is spent for validation. Once a dev has tuned against it, it no longer
  proves anything.
- **Freeze the evidence.** Validation inputs are copied, hashed (sha256), and made read-only before
  a run, and the hash is compared after the run. A run that "passes" because its input was edited
  proves nothing.

## Hidden evaluation sets

- The hidden sets live in a folder the devs are told **never to read**. Every ticket prompt says
  so.
- The PM runs the measurements and publishes only aggregate numbers in files the devs read:
  precision, recall, and counts. Never publish item ids or content.
- Label the sets honestly: a synthetic set and a real set give very different numbers. Report both.

## Forbidden tests

- **«The real output of case X passes.»** That test encodes the overfit: it turns the flagship
  case into a constant.
- **Production code that names a known case.** Add a guard test that scans the production code for
  the names of the iteration and validation cases, and fails if any appears.
- **Tests built from items of the hidden set.** Use a new synthetic example, with invented names,
  that reproduces the shape of the problem.

## The evaluator must match the gate

When the gate evolves and the evaluation script does not, the measurement silently becomes wrong.
Two real examples:

- the gate had moved a condition from *failures* to *warnings*, and the evaluator still read only
  failures, so recall showed 0 everywhere;
- the evaluator matched ids by substring, so `C1` also matched `C16`, and hits were inflated.

Before you trust a measurement:

1. Check that the evaluator reads the same output channel the gate writes to.
2. Compare ids exactly, never by substring.
3. Run the evaluator against a case you know is good and a case you know is bad.

If the original evaluator belongs to a closed ticket, measure with an adapted **copy** and record
the difference. Don't edit the evaluator that produced the earlier numbers.

## Other gate lessons

- **The contract of a gate includes what it must NOT flag.** Include the false positives in the
  ticket. Without them, the "fix" lowers the threshold until something fires, and a noisy gate gets
  ignored.
- **A new gate that fails everything is probably wrong itself.** Before trusting it, run it on a
  case you know is good.
- **A green that comes from an emptied exception list is ambiguous.** Plant the violation the gate
  must catch, and confirm it fails again.
- **The gate's mtime must be older than the sprint.** If the verifier was edited while the
  verified thing was being "fixed", the evidence doesn't count.
- **A warning that fires on almost everything does not discriminate.** If it fires on 55 of 56
  items, report that, and don't present it as a safety net.
- **Probe the most obvious form of a rule, not the form the dev's test uses.** A rule meant to catch
  the literal phrase «No es X, es Y» must be tested with that exact phrase.
- **If the expected verdict equals the verdict of an empty input, assert the size too.** Count the
  items that were really measured.
- **Fixtures must not contain the token the gate forbids.** Watch comments and docstrings.
