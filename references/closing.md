# Closing an epic that keeps growing

Agent teams produce findings fast. Every finding looks like it deserves a ticket, so the backlog
grows faster than it closes and the epic never ends.

## Warning signs

- New tickets are opened faster than tickets are closed, over several days.
- Tickets open «while we're at it».
- The original goal is no longer what is being measured.
- The human asks «what are the lines of work?» and nobody can answer in five lines.

## The closing plan

Write `CLOSING-PLAN.md` (template in `templates/`) and get the human's approval:

1. **Define "done" by result, not by tickets.** For example: «good output on new inputs, with no
   accommodation of the evidence, operated by the production system».
2. **Write a few steps, usually 3 to 5**, each with a verifiable gate:
   - test on a new case;
   - test on hidden validation cases;
   - fix only what failed in the first two steps;
   - hand over to operation.
3. **Freeze the rest.** List each frozen ticket by name and mark it `deferred (closing plan)` on the
   board.
4. **The rule: no new ticket unless it comes from a failure seen in the closing steps.** Interesting
   ideas go to a «later» list, not to the board.
5. **One phase at a time.** The next step starts only after the previous one is closed.

## Conditional tickets

Some fixes may or may not be needed. Keep them in the plan as conditional: «ticket T only if step 2
shows X». If the condition doesn't trigger, the ticket stays frozen.

## Testing on a genuinely new case

- The human picks the case. The dev proposes candidates in a part that ends with STOP.
- The input is prepared through the normal tools, with no manual edits and no overrides, and then
  frozen: hash it, copy it to a read-only folder, and compare the hash after every run.
- Limit the expensive runs, for example to at most two LLM runs, with no code changes between them.
- For each failure, the report says which gate blocked and classifies the cause:
  - **content**: the input really lacks something;
  - **false positive**: the gate is wrong;
  - **generation failure**: the model produced bad output.

  Only the first two can lead to tickets.
