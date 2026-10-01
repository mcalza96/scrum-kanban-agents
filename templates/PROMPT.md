TO: <agent name>

This is a new session. You have no memory of earlier work. Everything you need is in the files below.

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
- commits;
- overrides;
- changing the model, the effort, or the config;
- publishing.

If a gate seems to need a change: STOP and report.

**Report:** write `<path>/reports/<ID>.md`. Set your row in `STATUS.md` to `reported` with the
time from `date`. Paste every output literally, with its `$ command`.
