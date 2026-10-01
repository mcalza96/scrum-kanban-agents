# <ID> · PM review (<time from date>) · **<CLOSED | CLOSED (with notes) | RETURNED>**

## What is real (verified)

- The artifact was opened at `<path>`.
- Each pasted command was re-run, and it matches or it doesn't. Use `scripts/check_report.py`.
- The session log was checked: the session `<id>` ran `<command>` at `<time>`, and it edited
  `<file>` at `<time>`.
- The PM's own suite run: `<command>` → `<result>`.
- Nothing outside the lane changed (hashes and mtimes).

## Returned (if any)

**E1 · <short name> (rule)**
- The evidence: what the report says, and what reality says.
- What must be pasted instead.

## Notes (closed with notes: form problems, no false data)

1. ...

## Closing actions

- Updates to `EPIC.md` or to the board that this close triggers.

---

## Return 1 · PM review (<time>) · **<verdict>**

- What was checked again, and the result.
