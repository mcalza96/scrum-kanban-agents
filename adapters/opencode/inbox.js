// opencode (v2) plugin: every PM turn starts with the unread messages of the project's inbox/.
// Install: copy to <project>/.opencode/plugin/inbox.js. See references/inbox.md.
//
// It appends `inbox.py pending` to the user's message right before the model call. The line is
// not stored in the session, so it neither grows the history nor shows up after it was acked.
// Dev sessions (subagents, with a parentID) never get it. Any failure is ignored: a plugin must
// never block a turn. After every `opencode upgrade`, run scripts/opencode_inbox_smoke.sh.
//
// It also wakes idle PMs: it watches inbox/ and, on any change to a mailbox, starts wake.py for it.
// It runs here because the service outlives every turn; a waker started from a PM's shell command
// left no trace in a real run. wake.py decides whether there is anything new to wake for.
//
// Lanzador: el PM deja `inbox.py lanzar --pm PM-x --ticket CAL-n --prompt <ruta>`
// que escribe en `inbox/.lanzar/`. El plugin observa esa carpeta y arranca la sesión del dev con
// `opencode run`. Vive en el servicio (igual que wake.py) porque un hijo del shell del PM no
// sobrevive al turno. Tope: 4 devs en la máquina, sin tope por PM (documentado en lanzar.py).
import { execFileSync, spawn } from "node:child_process";
import { watch } from "node:fs";
import { existsSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { raizGestion } from "./gestion.js";

const PROJECT = resolve(dirname(fileURLToPath(import.meta.url)), "../..");
// The mailbox is read from .scrum.toml on every use, never once at load: the service outlives a
// move of the mailbox, and on 2026-11-05 a path fixed at 11:55 kept writing to the old folder and
// recreated it every minute after .scrum.toml changed at 12:54, while nobody watched the new one.
const root = () => join(raizGestion(PROJECT), "inbox");
const SCRIPT = process.env.INBOX_PY ||
  join(homedir(), ".claude/skills/scrum-kanban-agents/scripts/inbox.py");
const WAKE = join(dirname(SCRIPT), "../adapters/opencode/wake.py");
const LANZAR = join(dirname(SCRIPT), "../adapters/opencode/lanzar.py");
const timers = new Map();
let lanzarTimer = null;

function wake(file) {
  const m = /^([A-Za-z0-9][A-Za-z0-9._-]*)\.md$/.exec(file ?? "");
  if (!m) return;
  clearTimeout(timers.get(m[1])); // one write fires several events
  timers.set(m[1], setTimeout(() => {
    timers.delete(m[1]);
    try {
      spawn("python3", [WAKE, root(), m[1]], { detached: true, stdio: "ignore" }).unref();
    } catch {}
  }, 2000));
}

function run(script, ...args) {
  if (!existsSync(root())) return; // a stale mailbox: never recreate it
  try { spawn("python3", [script, ...args], { detached: true, stdio: "ignore" }).unref(); } catch {}
}

function lanzarKick() {
  if (lanzarTimer) clearTimeout(lanzarTimer);
  lanzarTimer = setTimeout(() => {
    lanzarTimer = null;
    run(LANZAR, root());
  }, 800);
}

function pending() {
  try {
    return execFileSync("python3", [SCRIPT, "pending", root()], { encoding: "utf8", timeout: 3000 }).trim();
  } catch {
    return "";
  }
}

export default {
  id: "scrum-kanban-agents.inbox",
  setup: async (api) => {
    // Watch the mailbox and its launch queue; every minute re-read .scrum.toml and re-arm the
    // watchers if the mailbox moved. A folder that does not exist is not watched or written.
    let armed = null, watchers = [];
    const arm = () => {
      const r = root();
      if (r === armed) return;
      for (const w of watchers) try { w.close(); } catch {}
      watchers = [];
      armed = existsSync(r) ? r : null;
      if (!armed) return;
      try { watchers.push(watch(r, (_, file) => { wake(file); if (file === ".lanzar") lanzarKick(); })); } catch {}
      const qdir = join(r, ".lanzar");
      try { if (existsSync(qdir)) watchers.push(watch(qdir, () => lanzarKick())); } catch {}
      lanzarKick(); // a queue left from before the restart or the move
    };
    arm();
    setInterval(() => {
      arm();
      run(LANZAR, root());   // the retry re-evaluates freed caps
      run(WAKE, "--reap", PROJECT); // the app repo, never the mailbox: its commits carry the candados
    }, 60_000);
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
