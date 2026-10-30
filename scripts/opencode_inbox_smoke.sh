#!/bin/sh
# Checks, with one real model call, that adapters/opencode/inbox.js still reaches the model.
# Run it after every `opencode upgrade`: the plugin API it uses is not documented.
# Exit 0 if the model saw the message, 1 if not.
set -u
SKILL=$(cd "$(dirname "$0")/.." && pwd)
DIR=$(mktemp -d)
trap 'rm -rf "$DIR"' EXIT
mkdir -p "$DIR/.opencode/plugin"
cp "$SKILL/adapters/opencode/inbox.js" "$DIR/.opencode/plugin/inbox.js"
(cd "$DIR" && git init -q)
# the injected line names the mailbox, not the text: the mailbox name is the keyword
KEY="PM-smoke-$$"
python3 "$SKILL/scripts/inbox.py" send "$DIR/inbox" --to "$KEY" --from PO "smoke test" >/dev/null
OUT=$(cd "$DIR" && perl -e 'alarm 180; exec @ARGV' opencode run --standalone \
  "Without using any tool: if this message names an inbox with unread messages, reply with that inbox name exactly; otherwise reply NO." 2>&1)
if printf '%s' "$OUT" | grep -q "$KEY"; then
  echo "ok: the model saw the inbox line ($KEY)"
  exit 0
fi
echo "FAIL: the model did not see the inbox line. Output:"
printf '%s\n' "$OUT" | tail -5
exit 1
