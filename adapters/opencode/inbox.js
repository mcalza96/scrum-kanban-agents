// opencode (v2) plugin: every PM turn starts with the unread messages of the project's inbox/.
// Install: copy to <project>/.opencode/plugin/inbox.js. See references/inbox.md.
//
// It appends `inbox.py pending` to the user's message right before the model call. The line is
// not stored in the session, so it neither grows the history nor shows up after it was acked.
// Dev sessions (subagents, with a parentID) never get it. Any failure is ignored: a plugin must
// never block a turn. After every `opencode upgrade`, run scripts/opencode_inbox_smoke.sh.
import { execFileSync } from "node:child_process";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../inbox");
const SCRIPT = process.env.INBOX_PY ||
  join(homedir(), ".claude/skills/scrum-kanban-agents/scripts/inbox.py");

function pending() {
  try {
    return execFileSync("python3", [SCRIPT, "pending", ROOT], { encoding: "utf8", timeout: 3000 }).trim();
  } catch {
    return "";
  }
}

export default {
  id: "scrum-kanban-agents.inbox",
  setup: async (api) => {
    await api.session.hook("context", async (req) => {
      try {
        const last = req.messages?.at(-1);
        if (last?.role !== "user" || !Array.isArray(last.content)) return; // once per turn
        const session = await api.session.get({ sessionID: req.sessionID });
        if (session?.parentID) return; // a one-shot dev
        const text = pending();
        if (text) last.content.push({ type: "text", text });
      } catch {}
    });
  },
};
