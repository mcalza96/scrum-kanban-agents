// Ningún agente duerme en un loop, mata procesos que no lanzó ni pisa el árbol compartido.
// Plugin opencode v2: revisa cada shell antes de correr.
// Bloquea: sleep en loop (while/until/for con sleep) o sleep N>UMBRAL,
// kill/pkill/killall sobre PIDs no descendientes (pkill/killall siempre),
// y git que toca archivos o índice ajenos en el checkout principal (isGitBlocked).
// Cada rechazo deja línea en inbox/.bloqueos.log (fecha, sesión, comando recortado, regla).
// Errores del plugin dejan pasar el comando y lo anotan. Umbral configurable.
import { execSync, execFileSync } from "node:child_process";
import { appendFileSync, existsSync, readFileSync, readdirSync, realpathSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { raizGestion } from "../lib/gestion.js";

const SLEEP_UMBRAL = 5; // segundos, configurable por dueño
const MAX_CMD_LOG = 120;

// Mencionarlo (echo, heredoc, comillas) no es correrlo.
export function isLaunchctlBlocked(cmd) {
  const run = cmd.split("<<")[0]
    .replace(/"[^"\\]*(?:\\.[^"\\]*)*"/g, '""').replace(/'[^'\\]*(?:\\.[^'\\]*)*'/g, "''");
  return /(^|[;&|(]\s*|\bsudo\s+)launchctl\s+(bootout|unload|disable|remove|stop)\b/m.test(run);
}

function inboxRootFrom(ctx, project) {
  // Gestion-aware: inbox vive en raizGestion(repo)/inbox
  function gestionInbox(dir) {
    try { return join(raizGestion(dir), "inbox"); } catch { return join(dir, "inbox"); }
  }
  // Usa la ubicación de la sesión; la entrada instalada entrega el proyecto como respaldo.
  try {
    if (ctx && ctx.location && ctx.location.directory) {
      const cand = gestionInbox(ctx.location.directory);
      if (existsSync(cand)) return cand;
      // también prueba worktree
      if (ctx.location.worktree) {
        const cand2 = gestionInbox(ctx.location.worktree);
        if (existsSync(cand2)) return cand2;
      }
      return cand;
    }
  } catch {}
  // Respaldo explícito del proyecto, independiente de dónde se instala la skill.
  try {
    const proj = project;
    const r = gestionInbox(proj);
    if (existsSync(r)) return r;
    return r;
  } catch {
    return resolve("inbox");
  }
}

function logBloqueo(root, sessionID, cmd, regla, extra = "") {
  try {
    if (!existsSync(root)) return; // never create a mailbox folder
    const logPath = join(root, ".bloqueos.log");
    const fecha = new Date().toISOString();
    const recortado = cmd.replace(/\s+/g, " ").slice(0, MAX_CMD_LOG).replace(/\n/g, " ");
    const linea = `${fecha} ${sessionID} "${recortado}" ${regla}${extra ? " " + extra : ""}\n`;
    appendFileSync(logPath, linea, "utf8");
  } catch {}
}

function logError(root, sessionID, cmd, err) {
  try {
    if (!existsSync(root)) return; // never create a mailbox folder
    const logPath = join(root, ".bloqueos.log");
    const fecha = new Date().toISOString();
    const recortado = String(cmd).slice(0, MAX_CMD_LOG).replace(/\n/g, " ");
    const msg = String(err).slice(0, 80).replace(/\n/g, " ");
    appendFileSync(logPath, `${fecha} ${sessionID} "${recortado}" error-plugin ${msg}\n`, "utf8");
  } catch {}
}

function isSleepBlocked(cmd) {
  // ignora heredoc: lo que está después de << no se ejecuta, es contenido del archivo
  const effective = cmd.split("<<")[0];
  // quita strings entre comillas para no confundir `for`/`sleep` dentro de python/js con shell
  const stripped = effective.replace(/"[^"\\]*(?:\\.[^"\\]*)*"/g, '""').replace(/'[^'\\]*(?:\\.[^'\\]*)*'/g, "''");
  const lower = stripped;
  // si el comando ya es en segundo plano (contiene &), el sleep no bloquea el turno: se permite el umbral
  // pero el loop con sleep sigue bloqueado (aunque en segundo plano, sigue siendo 1,7h desperdiciada)
  const isBackground = lower.includes("&");
  // loop: detecta sleep dentro de un bloque do ... done
  const bodyRe = /\bdo\b([\s\S]*?)\bdone\b/g;
  let mBody;
  while ((mBody = bodyRe.exec(lower)) !== null) {
    if (/\bsleep\b/.test(mBody[1])) return { blocked: true, regla: "sleep-loop", detalle: "sleep dentro de do/done" };
  }
  if (/\b(while|until|for)\b/.test(lower) && /\bsleep\b/.test(lower) && !/\bdone\b/.test(lower)) {
    if (/\bdo\b/.test(lower)) return { blocked: true, regla: "sleep-loop", detalle: "loop con sleep sin done" };
  }
  // umbral: sleep N > SLEEP_UMBRAL (sobre stripped, sin heredoc ni strings)
  // si es background (&), no bloquea por umbral (alternativa del ticket)
  if (isBackground) return { blocked: false };
  const re = /sleep\s+(\d+(?:\.\d+)?)/g;
  let m;
  while ((m = re.exec(lower)) !== null) {
    const n = parseFloat(m[1]);
    if (n > SLEEP_UMBRAL) return { blocked: true, regla: "sleep-umbral", detalle: `sleep ${n} > ${SLEEP_UMBRAL}` };
  }
  return { blocked: false };
}

function parseKillPids(cmd) {
  // extrae PIDs numéricos de kill, ignora kill -0
  const pids = [];
  // evita kill -0 (solo chequeo)
  if (/\bkill\s+.*-0\b/.test(cmd)) return { pids: [], isCheck: true };
  const kills = cmd.split(/\bkill\b/);
  for (let i = 1; i < kills.length; i++) {
    let seg = kills[i].split(/[;\n&|]/)[0]; // hasta separador
    if (/-\s*0\b/.test(seg)) continue;
    // quita señal inicial "-9 " si existe, para no confundirla con pid
    seg = seg.replace(/^\s*-\d+\s+/, " ");
    const nums = seg.match(/\b\d+\b/g);
    if (nums) nums.forEach(n => pids.push(n));
  }
  return { pids, isCheck: false };
}

function getPsTree() {
  try {
    const out = execSync("ps -A -o pid,ppid,command 2>/dev/null || ps -o pid,ppid,command -x 2>/dev/null || ps -o pid,ppid,command 2>/dev/null", { encoding: "utf8", timeout: 2000 });
    const map = new Map(); // pid -> {ppid, cmd}
    for (const line of out.split("\n")) {
      const m = line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/);
      if (m) map.set(m[1], { ppid: m[2], cmd: m[3] });
    }
    return map;
  } catch {
    return new Map();
  }
}

function isProtectedCommand(cmd) {
  return /verificar-commit|tsx --test|next dev/.test(cmd);
}

function isDescendantDouble(pid, launchedSet, tree1, tree2) {
  // doble del árbol: verifica en dos snapshots que el pid existe y su cadena
  if (!tree1.has(pid) || !tree2.has(pid)) return false;
  // si el pid está en launchedSet, es descendiente (lanzado por la sesión)
  if (launchedSet && launchedSet.has(pid)) return true;
  // camina padres en tree1
  let cur = pid;
  const seen = new Set();
  while (cur && !seen.has(cur)) {
    seen.add(cur);
    const node = tree1.get(cur);
    if (!node) break;
    if (launchedSet && launchedSet.has(cur)) return true;
    // si algún ancestro es un pid lanzado, también es descendiente
    cur = node.ppid;
    if (cur === "1" || cur === "0") break;
  }
  return false;
}

// --- git que pisa trabajo ajeno en el checkout compartido ---
// En el checkout principal trabajan varios agentes a la vez sobre el mismo árbol e índice. Estos
// comandos tocan archivos o el índice de todos: 20 sesiones hicieron stash y 23 checkout/restore en
// 3 días (2026-11-01..04), y uno borró los buzones. En un worktree propio se permiten.
const GIT_RULES = [
  [/^stash\b(?!\s+(list|show)\b)/, "git-stash"],
  [/^(checkout|switch|restore)\b/, "git-checkout"],
  [/^reset\b.*--(hard|merge|keep)\b/, "git-reset-hard"],
  [/^clean\b.*(?:\s-[a-zA-Z]*f|\s--force\b)/, "git-clean"],
  [/^add\b.*(\s-A\b|\s--all\b|\s\.(\s|$)|\s-u\b|\s--update\b)/, "git-add-todo"],
  [/^commit\b.*(\s-[a-zA-Z]*a[a-zA-Z]*\b|\s--all\b|\s--amend\b)/, "git-commit-todo"],
];

function shellSegments(cmd) {
  const segments = [], words = [];
  let word = "", quote = null, started = false;
  const flush = () => { if (started) words.push(word); word = ""; started = false; };
  for (let i = 0; i < cmd.length; i++) {
    const c = cmd[i];
    if (c === "\\" && quote !== "'") {
      word += cmd[++i] || ""; started = true;
    } else if (quote) {
      if (c === quote) quote = null;
      else word += c;
    } else if (c === '"' || c === "'") {
      quote = c; started = true;
    } else if (/[;&|\n]/.test(c)) {
      flush(); segments.push(words.splice(0));
    } else if (/\s/.test(c)) flush();
    else { word += c; started = true; }
  }
  flush(); segments.push(words);
  return segments;
}

function gitPaths(dir) {
  return execFileSync("git", ["-C", dir, "rev-parse", "--path-format=absolute", "--show-toplevel", "--git-common-dir"],
    { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] }).trim().split("\n");
}

// El árbol compartido es el checkout principal del repo del proyecto: un worktree propio o un repo
// de prueba en /tmp no lo son. Devuelve una función dir -> bool, con caché por directorio.
export function sharedCheckout(projectDir) {
  let main; // se calcula solo cuando una regla de git calza: el resto de los comandos no paga git
  const memo = new Map();
  return (dir) => {
    if (main === undefined) {
      try { main = dirname(gitPaths(projectDir)[1]); } catch { main = null; }
    }
    if (!main) return false;
    if (!memo.has(dir)) {
      let top = null;
      try { top = gitPaths(dir)[0]; } catch {}
      memo.set(dir, top === main);
    }
    return memo.get(dir);
  };
}

// La metadata privada permanece aunque alguien haya borrado el directorio del worktree.
// No inferimos propiedad por el nombre codex/dev: las ramas ajenas siguen permitidas.
function managedWorktrees(repo) {
  let common;
  try { common = gitPaths(repo)[1]; } catch { return []; }
  const records = [];
  let dirs;
  try { dirs = readdirSync(join(common, "worktrees")); } catch { return []; }
  for (const dir of dirs) {
    try {
      const gd = join(common, "worktrees", dir);
      const data = JSON.parse(readFileSync(join(gd, "scrum-dev.json"), "utf8"));
      const linked = dirname(readFileSync(join(gd, "gitdir"), "utf8").trim());
      if (data.directory !== linked || !data.id || !data.gestion || !data.branch ||
          gitPaths(data.project)[1] !== common) continue;
      records.push(data);
    } catch {}
  }
  return records;
}

const canonicalPath = path => { try { return realpathSync(path); } catch { return resolve(path); } };
const shellQuote = value => "'" + value.replace(/'/g, "'\\''") + "'";

function managedCloseBlocked(words, target) {
  let paths = [], branches = [];
  if (words[0] === "worktree" && ["unlock", "remove"].includes(words[1])) {
    paths = words.slice(2).filter(word => !word.startsWith("-"));
  } else if (words[0] === "branch" && words.slice(1).some(word =>
      word === "--delete" || /^-[a-zA-Z]*[dD][a-zA-Z]*$/.test(word))) {
    // -r elimina referencias remotas de seguimiento; no la rama local administrada.
    if (words.slice(1).some(word => word === "--remotes" || /^-[a-zA-Z]*r[a-zA-Z]*$/.test(word))) return null;
    branches = words.slice(1).filter(word => !word.startsWith("-"));
  } else if (words[0] === "update-ref" && words.slice(1).some(word => ["-d", "--delete"].includes(word))) {
    branches = words.slice(1).filter(word => !word.startsWith("-"));
  } else return null;
  const data = managedWorktrees(target).find(data =>
    paths.some(path => canonicalPath(resolve(target, path)) === canonicalPath(data.directory) ||
      (!path.includes("/") && path === basename(data.directory))) ||
    branches.some(branch => branch === data.branch || branch === "refs/heads/" + data.branch));
  if (!data) return null;
  const helper = fileURLToPath(new URL("../../scripts/dev_worktree.py", import.meta.url));
  let sha = "<SHA-main>";
  for (const ref of ["refs/remotes/origin/main", "refs/heads/main"]) {
    try { sha = execFileSync("git", ["-C", data.project, "rev-parse", "--verify", ref],
      { encoding: "utf8", timeout: 2000, stdio: ["ignore", "pipe", "ignore"] }).trim(); break; } catch {}
  }
  return { blocked: true, regla: "git-cierre-administrado", detalle: data.id,
    command: `python3 ${shellQuote(helper)} cerrar ${shellQuote(join(data.gestion, "inbox"))} --pedido ${shellQuote(data.id)} --integrado ${sha} --sincronizar` };
}

// Devuelve { blocked, regla, detalle }. cwd es el directorio de la sesión; `cd X` y `git -C X`
// mueven el destino. isShared dice si un directorio es el árbol compartido (sharedCheckout).
export function isGitBlocked(cmd, cwd, isShared) {
  let dir = cwd;
  for (const words of shellSegments(cmd.split("<<")[0])) {
    if (words[0] === "cd" && words[1]) {
      const target = words[1].replace(/^~(?=\/|$)/, process.env.HOME || "~");
      dir = target.startsWith("/") ? target : join(dir, target);
      continue;
    }
    while (/^\w+=/.test(words[0] || "")) words.shift();
    if (words.shift() !== "git") continue;
    let target = dir;
    while (words[0]?.startsWith("-")) {
      const option = words.shift();
      if (option === "-C" || option === "-c") {
        const value = words.shift();
        if (value === undefined) break;
        if (option === "-C") target = value.startsWith("/") ? value : join(target, value);
      } else if (!["--no-pager", "--paginate", "--no-optional-locks"].includes(option)) break;
    }
    const managed = managedCloseBlocked(words, target);
    if (managed) return managed;
    if (words[0] === "commit") {
      for (let i = 1; i < words.length; i++) {
        if (["-m", "--message"].includes(words[i]) && i + 1 < words.length) words[++i] = "";
      }
    }
    const command = words.map(word => /\s/.test(word) ? '""' : word).join(" ");
    const rule = GIT_RULES.find(([re]) => re.test(command));
    if (rule && (rule[1] === "git-stash" || isShared(target))) return { blocked: true, regla: rule[1], detalle: `git ${command.slice(0, 40)}` };
  }
  return { blocked: false };
}

// memoria de PIDs lanzados por sesión (via tool.execute.after)
const launchedBySession = new Map(); // sessionID -> Set(pid)

export function createGuard(project) {
return {
  id: "scrum-kanban-agents.guard",
  setup: async (ctx) => {
    const inboxRoot = inboxRootFrom(ctx, project);
    const projectDir = (ctx.location && ctx.location.directory) || project;
    const isShared = sharedCheckout(projectDir);

    // hook antes de ejecutar herramienta
    await ctx.tool.hook("execute.before", async (event) => {
      try {
        if (["write", "edit"].includes(event.tool)) {
          const path = event.input?.path || event.input?.filePath;
          let parent = path ? dirname(resolve(projectDir, path)) : null;
          while (parent && !existsSync(parent) && parent !== dirname(parent)) parent = dirname(parent);
          if (path && !isShared(projectDir) && isShared(parent)) {
            logBloqueo(inboxRoot, event.sessionID || "unknown", path, "escritura-principal");
            throw new Error("bloqueado: escritura-principal. Este dev trabaja en su worktree; usa la ruta de su encargo.");
          }
          return;
        }
        if (event.tool !== "shell" && event.tool !== "bash") return;
        const cmd = (event.input && event.input.command) || "";
        if (!cmd) return;
        const sessionID = event.sessionID || "unknown";

        // --- sleep ---
        const sleepRes = isSleepBlocked(cmd);
        if (sleepRes.blocked) {
          logBloqueo(inboxRoot, sessionID, cmd, sleepRes.regla, sleepRes.detalle);
          throw new Error(
            `bloqueado: ${sleepRes.regla} (${sleepRes.detalle}). ` +
            `No se permite sleep en loop (while/until/for con sleep) ni sleep N con N>${SLEEP_UMBRAL} (umbral ${SLEEP_UMBRAL}s configurable). ` +
            `Alternativa: corre el comando en segundo plano (usa & , background: true o timeout acotado) y lee su salida al terminar, ` +
            `o usa \`bash scripts/suite.sh --resultado\` si ya existe. Motivo: sleep en loop bloquea el turno y desperdicia 1,7 h-agente.`
          );
        }

        // --- git en el checkout compartido ---
        const gitRes = isGitBlocked(cmd, projectDir, isShared);
        if (gitRes.blocked) {
          logBloqueo(inboxRoot, sessionID, cmd, gitRes.regla, gitRes.detalle);
          if (gitRes.regla === "git-cierre-administrado") {
            throw new Error(`bloqueado: ${gitRes.regla} (${gitRes.detalle}). El PO debe cerrar con preview apagado e integración verificada. Ejecuta: ${gitRes.command}. Si falla, conserva el worktree y reporta el error; no uses force ni borres la rama a mano.`);
          }
          throw new Error(
            `bloqueado: ${gitRes.regla} (${gitRes.detalle}) en el checkout compartido. Otros agentes tienen ` +
            `cambios sin commitear en este mismo árbol e índice; stash, checkout, restore, reset --hard, clean, ` +
            `add -A y commit -a/--amend los pisan. Qué hacer: commitea solo lo tuyo con ` +
            `\`git add <archivo nuevo>\` y \`git commit --only <tus archivos> -m ...\`; deja los cambios ajenos ` +
            `como están; para leer otra versión usa \`git show <sha>:<ruta>\`. Si necesitas un árbol limpio, ` +
            `trabaja en tu worktree. Los stashes son globales al repositorio y siguen bloqueados allí.`
          );
        }

        // --- el barrido no se apaga ---
        // 2026-11-05: el plan de pausa de ORDEN-5 hizo bootout del barrido y de codex-po; el dueño de la
        // pausa se detuvo y nadie lo notó en una hora. Pausar es escribir inbox/.pausa, no apagar launchd.
        if (isLaunchctlBlocked(cmd)) {
          logBloqueo(inboxRoot, sessionID, cmd, "launchctl-barrido");
          throw new Error(
            `bloqueado: launchctl bootout/unload/disable/remove/stop. El barrido y el Director vigilan que ` +
            `el trabajo avance, y más que nunca durante una pausa. ` +
            `Qué hacer: para pausar, escribe \`<PM dueño> <ticket>\` en la primera línea de inbox/.pausa; ` +
            `el barrido deja de empujar a los demás y de lanzar devs, y sigue vigilando al dueño. ` +
            `Si de verdad hay que bajar un servicio, lo hace el dueño a mano.`
          );
        }

        // --- kill / pkill / killall ---
        const effectiveCmd = cmd.split("<<")[0];
        const strippedKill = effectiveCmd.replace(/"[^"\\]*(?:\\.[^"\\]*)*"/g, '""').replace(/'[^'\\]*(?:\\.[^'\\]*)*'/g, "''");
        const hasKill = /\b(kill|pkill|killall)\b/.test(strippedKill);
        if (!hasKill) return;

        // pkill / killall por nombre siempre bloqueados
        if (/\b(pkill|killall)\b/.test(strippedKill)) {
          logBloqueo(inboxRoot, sessionID, cmd, "pkill-bloqueado");
          throw new Error(
            `bloqueado: pkill/killall por nombre siempre bloqueado. No se puede saber de quién es el proceso. ` +
            `Motivo: proteger verificar-commit.sh, sus tsx --test y los servidores next dev ajenos. El candado huérfano se recupera solo. ` +
            `Qué hacer: espera el turno de la cola (verificar-commit se encola) o avisa al PM. Alternativa para tu propio proceso: usa kill con PID descendiente de tu sesión.`
          );
        }

        // kill -0 es solo chequeo, se permite
        if (/\bkill\b[^;]*-0\b/.test(strippedKill)) return;

        // kill con PID
        const { pids, isCheck } = parseKillPids(strippedKill);
        if (isCheck) return;
        if (pids.length === 0) {
          // kill sin PID explícito (ej: kill %1, kill $$) -> es job control propio, se permite
          if (/\bkill\s+[%$]/.test(strippedKill) || /\bkill\s+-[0-9]+\s+[%$]/.test(strippedKill)) return;
          // lsof | xargs kill sin PID visible -> bloquea por precaución (no se sabe de quién es)
          if (/lsof/i.test(strippedKill) && /xargs\s+kill/.test(strippedKill)) {
            logBloqueo(inboxRoot, sessionID, cmd, "kill-lsof-bloqueado");
            throw new Error(
              `bloqueado: kill via lsof/xargs sin PID explícito. No se puede verificar descendencia. ` +
              `Qué hacer: espera el turno de la cola o avisa al PM. Si es tu propio next dev, usa kill con PID que hayas lanzado (echo $!).`
            );
          }
          // si no hay PID numérico, deja pasar (ej: kill %1)
          return;
        }

        // para cada PID, verifica descendencia y protección
        const launchedSet = launchedBySession.get(sessionID) || new Set();
        // doble snapshot del árbol
        const tree1 = getPsTree();
        // pequeña pausa para doble verificación (sin sleep en loop: usa timeout de ps)
        let tree2 = tree1;
        try {
          execSync("ps -o pid -p 1 >/dev/null 2>&1", { timeout: 50 });
          tree2 = getPsTree();
        } catch {}

        for (const pid of pids) {
          const node = tree1.get(pid) || tree2.get(pid);
          const targetCmd = node ? node.cmd : "";
          // protegido: verificar-commit, tsx --test, next dev (nunca se tocan)
          if (isProtectedCommand(targetCmd) || isProtectedCommand(strippedKill)) {
            logBloqueo(inboxRoot, sessionID, cmd, "kill-protegido", `pid ${pid} ${targetCmd.slice(0,40)}`);
            throw new Error(
              `bloqueado: kill sobre PID protegido (verificar-commit.sh, tsx --test o next dev ajeno) pid ${pid}. ` +
              `El candado huérfano de verificar-commit se recupera solo. Qué hacer: espera el turno de la cola o avisa al PM.`
            );
          }
          const descendant = isDescendantDouble(pid, launchedSet, tree1, tree2);
          if (!descendant) {
            // si el pid no existe en el árbol, puede ser que ya terminó; deja pasar (no hay proceso que matar)
            if (!tree1.has(pid) && !tree2.has(pid)) {
              // pid ya no existe, no bloquea (es inofensivo)
              continue;
            }
            logBloqueo(inboxRoot, sessionID, cmd, "kill-ajeno", `pid ${pid}`);
            throw new Error(
              `bloqueado: kill sobre PID no descendiente de la sesión (pid ${pid}). Solo se permite kill sobre PIDs que descienden de procesos lanzados por tu misma sesión. ` +
              `Motivo: una sesión mató la verificación de otra (kill 70649 sobre verificar-commit.sh ajeno). ` +
              `Qué hacer: espera el turno de la cola o avisa al PM.`
            );
          }
        }
        // si todos los pids son descendientes, deja pasar
      } catch (e) {
        // si el error es nuestro bloque (throw), se propaga para bloquear; si es error interno, deja pasar
        if (e && e.message && e.message.startsWith("bloqueado:")) throw e;
        // error interno del plugin: deja pasar y anota
        try {
          const cmd = (event.input && event.input.command) || "";
          const sessionID = event.sessionID || "unknown";
          logError(inboxRootFrom(ctx, project), sessionID, cmd, e);
        } catch {}
        // no bloquea
      }
    });

    // hook después de ejecutar para capturar PIDs lanzados en segundo plano
    await ctx.tool.hook("execute.after", async (event) => {
      try {
        if (event.tool !== "shell" && event.tool !== "bash") return;
        const cmd = (event.input && event.input.command) || "";
        const sessionID = event.sessionID || "unknown";
        if (!cmd.includes("&")) return;
        // busca PID en la salida
        const out = (event.result && event.result.output && event.result.output.output) || "";
        const content = (event.result && event.result.content && event.result.content[0] && event.result.content[0].text) || "";
        const combined = String(out) + "\n" + String(content);
        // si el comando fue `... & echo $!` el PID aparece en la salida
        if (/echo\s+\$\!/.test(cmd)) {
          const m = combined.match(/\b(\d{4,7})\b/);
          if (m) {
            let set = launchedBySession.get(sessionID);
            if (!set) { set = new Set(); launchedBySession.set(sessionID, set); }
            set.add(m[1]);
            // también guarda todos los números que parezcan PID
            for (const mm of combined.matchAll(/\b(\d{4,7})\b/g)) set.add(mm[1]);
          }
        } else if (cmd.trim().endsWith("&")) {
          // background sin echo, intenta capturar via ps? no fiable
        }
      } catch {}
    });
  },
};

}
export default createGuard(process.cwd());
