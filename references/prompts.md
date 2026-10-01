# Prompts for dev sessions

The dev does not see the PM's conversation. If something is not in the prompt or in a file the
prompt points to, the dev does not know it.

## Rules

1. **First line: `TO: <agent>`.** With several agents and terminals open, a prompt without an
   addressee ends up with the wrong agent. This happened once, and the agent did work in another
   lane.
2. **Assume a brand-new session.** Don't write «as we discussed» or «continue». Give the ticket
   path and the files to read first.
3. **Use the paths the agent sees.** If the agent runs in a container, give container paths, or
   give both and say which is which.
4. **Include the shared rules, or point to them.** Evidence (`evidence.md`), speed
   (`test-speed.md`), and gates (`gates.md`). A pointer is fine only if the file is short and the
   prompt says «read it before starting».
5. **Say what is forbidden**:
   - lanes the agent must not touch;
   - hidden sets it must not read;
   - commits, overrides, and model or settings changes;
   - publishing.
6. **Say where to stop.** Give the ticket part and the STOP point. Say what to do if a gate change
   seems necessary: stop and report.
7. **Say how to report.** Give the report path, the row to update on the board, and the state to
   set.

Template: `templates/PROMPT.md`.

## Return prompts

A return goes to a **new session**, never to the session that produced the false data. That
session's context still holds the reasoning that led to the problem.

The return prompt:

- starts with `TO: <agent>`;
- points to the ticket, the original report, and the review;
- lists **only** the findings to fix (E1, E2, ...), each with the expected evidence;
- says whether the code may change. Usually it must not: «the code is approved, only the evidence
  is returned»;
- asks for the corrected sections to be appended or replaced in the same report file.
