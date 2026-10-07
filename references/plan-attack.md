# Plan attack: blind adversarial review before execution

A plan written by one agent carries that agent's blind spots into every card. The cheapest time
to find a design defect is before any card is assigned: a defect found in the plan costs an edit.
Found after execution, it costs redoing phases.

## When to run it (and when not)

Run it only when **at least one** of these holds:

1. The work is planned with a board and several agents (several phases, several hands).
2. The result becomes a **source of truth** that others will query many times (a knowledge base,
   a procedure, a glossary, a dataset).
3. An error has a cost that **cannot be undone**: a published figure, a spent budget, a physical
   action taken from the procedure.

Don't run it for small changes, notes, drafts or one-off answers. Four attackers cost money, and
their noise distracts. When in doubt, run a single front (logic).

## Process

1. **Plan v1** is written, and its cards are created **unassigned**. The author does not mark the
   plan «approved»: the attackers read the file, and the author does not approve its own plan.
2. **Four blind attackers in parallel**, one front each, each with a bounded portion of the plan.
   They get the plan file, the real use context (who uses the result, what happens if it is
   wrong), and the order to attack, not to validate. They never get the author's conversation,
   conclusions or list of fixes. Use a different model from the author's if you can.
3. **Consolidate**:
   - drop findings without reproducible evidence;
   - dedupe across fronts, keeping the highest severity;
   - **re-verify every accepted finding yourself** before applying it;
   - apply the fixes to the plan source.
4. **Record** the attack in the plan: a «what changed in v2» section that pairs each finding with
   how it was verified.
5. **Second round** only on what changed, if the fixes touched design (phases, gates, scope, a new
   deliverable), or if a later version reshapes the plan. Run one or two fronts, again blind: the
   attacker is not told the sections are fixes. Fixes to a single measurement need only the
   author's own re-check against the source. Cap: **two rounds**; anything still open is declared
   in the plan, not attacked in a loop.
6. **Human approval**, then cards are assigned.

When the plan produces a source of truth, add a final attack card on the finished set as well.

## Fronts for a plan

| Front | What it attacks | Portion |
|---|---|---|
| Figures and measurements | Does every measured number re-run to the same value? Does each gate's instrument detect what the gate promises? | measurement section + evidence files |
| Feasibility and citations | Does every tool, flag and capability exist **in this environment**? Try it, don't read about it. | architecture, tools, sources |
| Logic, phases and gates | Gates that cannot fail; forward dependencies (a phase consumes what a later phase produces); branches with no exit or no abort criterion | phases, dependencies, gates |
| Scope | From the end user's role: is any deliverable **the** stated goal, or only raw material? Which risk is not warned about? | the whole plan |

## Defects this catches that card review does not

- **A gate that cannot fail.** «Passes with 100% of pages in error» is not a gate. A gate is
  written so that it can fail.
- **A goal with no deliverable.** The stated goal was a step-by-step procedure, but no card
  produced one: fragments plus an index are raw material, not a procedure.
- **An input nobody produces.** A gate consumed a mutation bank that no phase generated.
- **A blind instrument.** A figure check compared multisets, so a range with min and max swapped
  passed with recall 1.0.

## Severity

Anchor severity to the consequence for the user of the result, not to academic seriousness:

- **critical**: someone acts on it and causes harm or waste;
- **major**: it leads to a wrong diagnosis, or a citation is misattributed;
- **minor**: an imprecision that does not change the action.

A finding without a literal citation of the source, or without a walk-through of the logic, is
dropped. Ask each attacker to say what is right and what it could not verify.
