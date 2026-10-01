#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Crea un backlog completo en un tablero Kanban, sin asignar y de forma idempotente.

Por que existe: el paso que mas se equivoca a mano en el setup de un sprint es crear N tarjetas y
luego cablear las dependencias. Este script hace las dos cosas desde un solo archivo, y con
--idempotency-key derivada de la clave de la tarjeta, de modo que re-ejecutarlo no duplica nada.

Entrada (JSON):
{
  "board": "mi-proyecto",
  "workdir": "/path/to/repo",             # opcional; default del tablero
  "tasks": [
    {"key": "s1-token",
     "title": "Definir el archivo de tokens",
     "body": "...spec en markdown...",
     "contract": "...criterios de aceptacion...",
     "priority": 1,                        # opcional, desempate
     "parent": "s0-setup",                # opcional, clave de otra tarjeta
     "parents": ["s0-setup", "s0-otro"],  # opcional, varias dependencias
     "skills": ["mi-skill"],               # opcional, --skill repetible
     "workspace": "dir:/path/to/repo",    # opcional, ver nota del CLI mas abajo
     "max_runtime": "45m"}                # opcional
  ]
}

Uso:
  python3 crear_backlog.py backlog.json              # crea
  python3 crear_backlog.py backlog.json --dry-run    # muestra que haria

Nunca asigna: la asignacion es el arranque del sprint y se hace aparte, a proposito.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys


def hermes_bin() -> str:
    for c in (os.environ.get("HERMES_BIN"), shutil.which("hermes")):
        if c and os.path.exists(c):
            return c
    sys.exit("no encuentro el binario 'hermes' (ponelo en el PATH o en HERMES_BIN)")


def run(args: list[str], dry: bool) -> tuple[int, str]:
    if dry:
        print("    [dry] " + " ".join(args[:6]) + ("..." if len(args) > 6 else ""))
        return 0, ""
    p = subprocess.run(args, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("backlog")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--board", help="override del board del JSON")
    a = ap.parse_args()

    spec = json.load(open(a.backlog, encoding="utf-8"))
    board = a.board or spec.get("board")
    if not board:
        sys.exit("el JSON necesita 'board' (o pasá --board)")
    tasks = spec.get("tasks") or []
    if not tasks:
        sys.exit("el JSON no tiene 'tasks'")

    H = hermes_bin()
    base = [H, "kanban", "--board", board]

    # 1) tablero. `boards list` imprime una fila por tablero con el slug en la primera columna;
    # el tablero activo lleva un marcador '●' delante, que hay que sacar antes de comparar.
    rc, out = run([H, "kanban", "boards", "list"], a.dry_run)
    slugs = set()
    for line in out.splitlines():
        toks = line.replace("●", " ").split()
        if toks:
            slugs.add(toks[0])
    existe = board in slugs
    if not existe:
        print(f"tablero '{board}': creando")
        rc, out = run([H, "kanban", "boards", "create", board], a.dry_run)
        if rc:
            print("  fallo al crear el tablero:", out[:400])
            return 1
    else:
        print(f"tablero '{board}': ya existe")

    if spec.get("workdir"):
        run([H, "kanban", "boards", "set-default-workdir", board, spec["workdir"]], a.dry_run)

    # 2) tarjetas, en orden, resolviendo el padre por clave
    #
    # OJO: `--completion-contract` NO es texto libre. Es donde se publica el trabajo: `local-only`
    # (default), `OWNER/REPO`, o una URL exacta de PR, y exige los gates de CI hechos. Los criterios
    # de aceptacion van al CUERPO, no ahi: pasarlos por ese flag rebota con
    # "completion_contract must be local-only, OWNER/REPO, or an exact GitHub PR URL".
    ids: dict[str, str] = {}
    for t in tasks:
        key = t["key"]
        cuerpo = t.get("body", "")
        if t.get("contract"):
            cuerpo = cuerpo.rstrip() + "\n\n## Contrato de aceptacion\n\n" + t["contract"] + "\n"
        args = base + ["create", t["title"],
                       "--body", cuerpo,
                       "--completion-contract", t.get("completion_contract", "local-only"),
                       "--idempotency-key", f"{board}:{key}",
                       "--created-by", "pm",
                       "--json"]
        if t.get("priority") is not None:
            args += ["--priority", str(t["priority"])]
        # El default del CLI es `scratch`: un directorio desechable. Si no se pasa `dir:<ruta>`, el
        # worker escribe ahi y el trabajo NUNCA aterriza en el repo — la tarjeta "pasa" y el artefacto
        # no existe. `boards set-default-workdir` NO cubre esto (el default del CLI gana).
        if t.get("workspace"):
            args += ["--workspace", t["workspace"]]
        if t.get("max_runtime"):
            args += ["--max-runtime", str(t["max_runtime"])]
        # `--skill` fuerza la carga en el worker. Para tarjetas cuyo criterio de calidad vive en una
        # skill (instrumentos de medicion, cron, reviews) es mas fuerte que pedirlo en el body.
        for sk in t.get("skills") or []:
            args += ["--skill", sk]
        # el padre se pasa por id; si aun no existe, se cablea despues con link
        rc, out = run(args, a.dry_run)
        if rc:
            print(f"  FALLO {key}: {out[:400]}")
            return 1
        tid = ""
        if not a.dry_run:
            # `create --json` imprime JSON INDENTADO en varias lineas. Parsear la ultima linea devuelve
            # '}' y los links salen con un id basura ("a task cannot depend on itself"). Hay que
            # parsear el stdout completo, y si no es JSON, caer a regex sobre el campo "id".
            try:
                tid = json.loads(out).get("id", "")
            except Exception:
                m = re.search(r'"id"\s*:\s*"([^"]+)"', out)
                tid = m.group(1) if m else ""
            if not tid:
                print(f"  FALLO {key}: no pude leer el id de la salida: {out[:200]}")
                return 1
        ids[key] = tid or f"<{key}>"
        print(f"  {key:24s} -> {ids[key]}")

    # 3) dependencias
    print("dependencias:")
    for t in tasks:
        pars = list(t.get("parents") or [])
        if t.get("parent"):
            pars.append(t["parent"])
        for par in pars:
            if par not in ids:
                print(f"  AVISO: '{t['key']}' declara padre '{par}' que no está en el JSON")
                continue
            rc, out = run(base + ["link", ids[par], ids[t["key"]]], a.dry_run)
            flag = "ok" if rc == 0 else f"FALLO {out[:200]}"
            print(f"  {par} -> {t['key']}: {flag}")

    print(f"\n{len(tasks)} tarjetas. NINGUNA asignada: asignar = arrancar el sprint.")
    if not a.dry_run:
        print("verificar antes de asignar:  hermes kanban --board %s dispatch --dry-run" % board)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
