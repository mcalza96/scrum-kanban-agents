# Closing plan of epic <NAME> (<date>, approved by <human>)

## What "done" means

One sentence, by result. For example: «good output on new inputs, with no accommodation of the
evidence, operated by the production system».

## Steps (one at a time)

1. **New case.** The human picks it, and its input is frozen (hash plus a read-only copy). At most N
   expensive runs. Each failure is classified as content, false positive, or generation failure.
2. **Hidden validation.** Run the cases the dev has never seen.
3. **Fixes.** Only for what failed in steps 1–2. Conditional tickets: `<ID> only if <condition>`.
4. **Handover.** The production system operates the work. Pending operational tickets.

## Frozen (deferred, not worked on)

- `<ID>`: why it can wait.

## Rule

No new ticket unless it comes from a failure observed in steps 1–2. Ideas go to «Later».

## Later

- ...
