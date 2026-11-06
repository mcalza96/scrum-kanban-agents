// El servicio mantiene imports en memoria. La URL cambia solo cuando cambia el código.
import { createHash } from "node:crypto";
import { readFileSync, readdirSync } from "node:fs";
import { join } from "node:path";
import { fileURLToPath } from "node:url";

const library = fileURLToPath(new URL(".", import.meta.url));
export function fresh(url) {
  const hash = createHash("sha256").update(readFileSync(fileURLToPath(url)));
  for (const name of readdirSync(library).filter(name => name.endsWith(".js")).sort())
    hash.update(name).update(readFileSync(join(library, name)));
  const versioned = new URL(url);
  versioned.searchParams.set("scrum", hash.digest("hex"));
  return import(versioned.href);
}
