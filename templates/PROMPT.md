TO: <agent name>

This is a new session. You have no memory of earlier work. Everything you need is in the files below.

**One-shot:** this session does only this task. When the report is written and your `STATUS.md` row
says `reported`, stop. If you get any other request in this session, answer that it needs a new
session, and do not do it.

**Task:** ticket `<absolute path>/<ID>.md`, part <A>. Stop at the STOP of that part.

**Read first, in this order:**
1. `<path>/EPIC.md`: lanes, suite commands, and the rules that never break.
2. `<path>/PROMPT-AGENT.md`: the evidence, speed, and gate rules. They are mandatory.
3. The ticket.

**Never read:** `<hidden evaluation folder>/`.

**Paths:** the repository is at `<path>` on the host and at `<path>` inside the container. Commands
that run in the container use `<exec prefix>`.

**Forbidden:**
- touching files outside lane `<LANE>`;
- commits, unless the epic allows them (see Close, step 2);
- overrides;
- changing the model, the effort, or the config;
- publishing.

If a gate seems to need a change: STOP and report.

**Close, in this order. It is the same for every task, also when the deliverable is a plan, a
ticket, or a measurement and not code:**
1. The deliverable is at `<exact path>`. Say that path in the report.
2. Commits: <the epic's rule. If the epic allows dev commits: `git commit --only <your paths>`, and
   put the sha in the report. If it forbids them: no commit, and the report lists every path you
   wrote, so the PM can find and commit them>.
3. Write `<path>/reports/<ID>.md`. Paste every output literally, with its `$ command`. End the
   report with `## Logbook`: at most 10 lines, no times. Cover the phases you followed, what you
   repeated and why, where you got stuck, and what would have saved you time.
4. Set your row in `STATUS.md` to `reported`, with the time from `date` and the report path.
5. Tell the PM: `inbox.py send --to <PM> --from dev-<ID> <inbox root> "[aviso] <ID> reported: <report path>"`.

A task that ends without steps 4 and 5 counts as not delivered, even if the file exists.
