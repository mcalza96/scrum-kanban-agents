#!/usr/bin/env python3
"""Instala entradas mínimas; nunca copia la implementación. --check detecta réplicas."""
import argparse
from pathlib import Path

from gestion import raiz_gestion

JS_LOADER = '''// Generado por scrum-kanban-agents/scripts/install.py. Lógica en la skill.
import { homedir } from "node:os";
import { join } from "node:path";
import { pathToFileURL } from "node:url";
const skill = process.env.SCRUM_SKILL || join(homedir(), ".agents/skills/scrum-kanban-agents");
const { fresh } = await import(pathToFileURL(join(skill, "adapters/lib/load.js")).href);
export const load = (file) => fresh(pathToFileURL(join(skill, file)));
'''
PY_ENTRY = '''#!/usr/bin/env python3
# Generado por scrum-kanban-agents/scripts/install.py. Lógica en la skill.
import os as _os
import runpy as _runpy
import sys as _sys
_skill = _os.environ.get("SCRUM_SKILL", _os.path.expanduser("~/.agents/skills/scrum-kanban-agents"))
_sys.path.insert(0, _os.path.join(_skill, "scripts"))
globals().update(_runpy.run_path(_os.path.join(_skill, "scripts", "{name}.py"), run_name=__name__))
'''


def entries():
    files = {".opencode/lib/scrum.js": JS_LOADER}
    for name in ("inbox", "guard"):
        factory = "create" + name.title()
        files[f".opencode/plugin/{name}.js"] = f'''// Generado por scrum-kanban-agents/scripts/install.py. Lógica en la skill.
import {{ fileURLToPath }} from "node:url";
import {{ load }} from "../lib/scrum.js";
const {{ {factory} }} = await load("adapters/opencode/{name}.js");
export default {factory}(fileURLToPath(new URL("../../", import.meta.url)));
'''
    for name in ("inbox", "focus", "archivar", "gestion"):
        files[f"scripts/{name}.py"] = PY_ENTRY.format(name=name)
    return files


def install(project, check=False, replace=False):
    errors = []
    for relative, content in entries().items():
        path = Path(project) / relative
        old = path.read_text() if path.exists() else None
        if old == content:
            continue
        if check or (old is not None and "Generado por scrum-kanban-agents/scripts/install.py" not in old and not replace):
            errors.append(relative)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    # El antiguo helper solo servía a las copias de los plugins.
    legacy = Path(project) / ".opencode/lib/gestion.js"
    if legacy.exists():
        if not check and replace:
            legacy.unlink()
        else:
            errors.append(str(legacy.relative_to(project)))
    replicas = Path(raiz_gestion(str(project))) / "adapters/opencode"
    if replicas.exists():
        errors.extend(str(p) for p in replicas.rglob("*") if p.is_file())
    return errors


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("project", type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--replace", action="store_true", help="reemplaza implementaciones antiguas ya revisadas")
    args = ap.parse_args()
    errors = install(args.project.resolve(), args.check, args.replace)
    if errors:
        ap.exit(1, "Entradas ausentes o replicadas: " + ", ".join(errors) + "\n")
    print("OK: entradas canónicas" + (" verificadas" if args.check else " instaladas"))
