// Un solo punto de resolución para JS (par de gestion.py).
// Lee SCRUM_GESTION / GESTION del entorno y .scrum.toml (gestion = "gestion").
// Por defecto es la raíz del repo. Uso: raizGestion(repo) -> /abs/path
import { readFileSync, existsSync } from "node:fs";
import { join, resolve, isAbsolute, normalize } from "node:path";

const ENV_VARS = ["SCRUM_GESTION", "GESTION"];
const CONFIG_NAMES = [".scrum.toml", "scrum.toml"];

function fromEnv() {
  for (const k of ENV_VARS) {
    const v = process.env[k];
    if (v !== undefined && v !== null && String(v).trim() !== "") return String(v).trim();
  }
  return null;
}

function fromFile(repo) {
  for (const name of CONFIG_NAMES) {
    const p = join(repo, name);
    if (!existsSync(p)) continue;
    try {
      const txt = readFileSync(p, "utf8");
      for (const line of txt.split("\n")) {
        let m = line.match(/^\s*gestion\s*=\s*["']([^"']+)["']\s*(?:#.*)?$/);
        if (m) return m[1].trim();
        m = line.match(/^\s*gestion\s*=\s*([A-Za-z0-9._\/-]+)\s*(?:#.*)?$/);
        if (m) return m[1].trim();
      }
    } catch {}
  }
  return null;
}

function managedGestion(base) {
  try {
    const pointer = readFileSync(join(base, ".git"), "utf8").trim();
    if (!pointer.startsWith("gitdir: ")) return null;
    const gitdir = resolve(base, pointer.slice(8));
    return JSON.parse(readFileSync(join(gitdir, "scrum-dev.json"), "utf8")).gestion || null;
  } catch { return null; }
}

export function raizGestion(repo) {
  const base = resolve(repo);
  let raw = fromEnv();
  if (raw == null) raw = managedGestion(base);
  if (raw == null) raw = fromFile(base);
  if (raw == null || raw === "." || raw === "./" || raw === "") return base;
  if (isAbsolute(raw)) return normalize(raw);
  return normalize(join(base, raw));
}

export function inboxRoot(repo) { return join(raizGestion(repo), "inbox"); }
export function focusRoot(repo) { return join(raizGestion(repo), "focus"); }
