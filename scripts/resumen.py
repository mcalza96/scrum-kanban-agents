#!/usr/bin/env python3
"""Lector único y candado del resumen del ticket (SKILL-E).

- Cada ticket `epic-*/tickets/<ID>.md` abre con `**Resumen:** …` justo después
  del título (SKILL-E). Esta es la única forma de leerlo.
- `resumen.py get <ID> [<repo>]`  -> imprime el resumen o "sin resumen"
- `resumen.py check [<repo>]`     -> lista abiertos sin resumen o con >240c, exit 1 si hay
- `resumen.py json [<repo>]`      -> JSON id -> {resumen, ruta} para el tablero
- Sin repo, opera sobre "." (raiz_gestion por defecto, igual que el resto de la skill).

Reglas SKILL-E:
- Si no existe archivo o no trae línea **Resumen:**, devuelve "sin resumen" (nunca primera línea del cuerpo).
- Tope 240 caracteres. La columna Ticket de STATUS.md lleva solo ID+título corto; la bitácora va en el reporte.
- El chequeo solo mira tickets abiertos (pending/in progress/reported/returned en STATUS*.md); cerrados no cuentan.
- Aditivo: un proyecto sin resúmenes sigue funcionando, el chequeo solo avisa (exit 1) sin romper nada.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

try:
    import gestion  # type: ignore
except ImportError:
    gestion = None  # type: ignore

MAX_RESUMEN = 240
SIN_RESUMEN = "sin resumen"
RESUMEN_RE = re.compile(r"^\*\*Resumen:\*\*\s*(.+?)\s*$", re.MULTILINE)
# Ticket ID en la columna Ticket de STATUS.md: primer token
TICKET_ID_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")
ROW_RE = re.compile(r"^\|.*\|\s*$")
OPEN_STATES = ("pending", "in progress", "reported", "returned")


def _raiz(repo: str) -> str:
    if gestion is not None:
        return gestion.raiz_gestion(repo)
    return os.path.abspath(repo)


def _ruta_ticket(repo: str, tid: str) -> str | None:
    """Busca epic-*/tickets/<ID>.md bajo la raíz de gestión. Retorna ruta absoluta o None."""
    gr = _raiz(repo)
    # glob con gestión raíz
    pat = os.path.join(gr, "epic-*", "tickets", f"{tid}.md")
    hits = glob.glob(pat)
    if hits:
        return os.path.normpath(hits[0])
    # también busca sin prefijo epic- por si el repo es raro (compat)
    pat2 = os.path.join(gr, "tickets", f"{tid}.md")
    hits2 = glob.glob(pat2)
    return os.path.normpath(hits2[0]) if hits2 else None


def _leer_resumen_de_archivo(path: str) -> str | None:
    """Lee el resumen de un archivo ya encontrado. None si no trae línea."""
    try:
        with open(path, encoding="utf-8") as f:
            txt = f.read()
    except OSError:
        return None
    m = RESUMEN_RE.search(txt)
    if not m:
        return None
    resumen = m.group(1).strip()
    # Si la línea es vacía después de **Resumen:**, trátalo como sin resumen
    if not resumen:
        return None
    return resumen


def leer_resumen(tid: str, repo: str = ".") -> tuple[str, str | None]:
    """Lector único SKILL-E.

    Returns (resumen_o_sin_resumen, ruta_absoluta_o_None).
    Nunca devuelve otra línea del cuerpo.
    """
    tid = tid.strip()
    if not tid:
        return SIN_RESUMEN, None
    path = _ruta_ticket(repo, tid)
    if path is None:
        return SIN_RESUMEN, None
    resumen = _leer_resumen_de_archivo(path)
    if resumen is None:
        return SIN_RESUMEN, path
    return resumen, path


def _parse_status_header(lines: list[str]) -> tuple[int, list[str], int | None, int | None] | None:
    for i, line in enumerate(lines):
        if not ROW_RE.match(line):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        # debe tener State o Estado y ser header (siguiente línea es separador)
        has_state = "State" in cells or "Estado" in cells
        has_ticket = "Ticket" in cells
        if not (has_state and has_ticket):
            continue
        if i + 1 < len(lines) and lines[i + 1].startswith("|---"):
            cols = cells
            if "State" in cols:
                s_idx = cols.index("State")
            else:
                s_idx = cols.index("Estado")
            t_idx = cols.index("Ticket")
            # Updated/Actualizado índice opcional
            if "Updated" in cols:
                u_idx = cols.index("Updated")
            elif "Actualizado" in cols:
                u_idx = cols.index("Actualizado")
            else:
                u_idx = None
            return i, cols, t_idx, s_idx
    return None


def _is_open_state(state: str) -> bool:
    s = state.strip().lower()
    if not s:
        return False
    # cerrados y diferidos no cuentan, aunque contengan palabras de abierto
    if s.startswith("closed") or s.startswith("deferred") or s.startswith("superseded"):
        return False
    # "closed with notes" ya cubierto por startswith closed
    for st in OPEN_STATES:
        if s.startswith(st):
            return True
    # fallback: contiene la palabra pero no empieza con closed (por si formato raro)
    # solo si es el primer token la consideramos abierta
    # ya está cubierto, resto es no-abierto
    return False


def _lista_tickets_abiertos(repo: str) -> list[tuple[str, str, str]]:
    """Retorna [(tid, state_raw, status_path), ...] para tickets abiertos."""
    gr = _raiz(repo)
    out: list[tuple[str, str, str]] = []
    pats = [
        os.path.join(gr, "epic-*", "STATUS.md"),
        os.path.join(gr, "epic-*", "STATUS-*.md"),
    ]
    files: set[str] = set()
    for pat in pats:
        for p in glob.glob(pat):
            if p.endswith("-archivo.md"):
                continue
            files.add(os.path.normpath(p))
    for path in sorted(files):
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            continue
        lines = text.splitlines()
        hdr = _parse_status_header(lines)
        if hdr is None:
            continue
        head, cols, t_idx, s_idx = hdr
        for i in range(head + 2, len(lines)):
            line = lines[i]
            if not ROW_RE.match(line):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) != len(cols):
                continue
            ticket_cell = cells[t_idx] if t_idx < len(cells) else ""
            state_cell = cells[s_idx] if s_idx < len(cells) else ""
            if not _is_open_state(state_cell):
                continue
            m = TICKET_ID_RE.match(ticket_cell.strip())
            if not m:
                continue
            tid = m.group(1)
            out.append((tid, state_cell, path))
    return out


def check(repo: str = ".") -> list[dict]:
    """Candado SKILL-E: abiertos sin resumen o con >MAX. Retorna lista de dicts con detalles."""
    abiertos = _lista_tickets_abiertos(repo)
    faltas: list[dict] = []
    for tid, state, status_path in abiertos:
        resumen, ruta = leer_resumen(tid, repo)
        if resumen == SIN_RESUMEN:
            faltas.append(
                {
                    "id": tid,
                    "estado": state,
                    "ruta": ruta,
                    "status_path": status_path,
                    "motivo": "sin resumen",
                    "resumen": None,
                }
            )
        elif len(resumen) > MAX_RESUMEN:
            faltas.append(
                {
                    "id": tid,
                    "estado": state,
                    "ruta": ruta,
                    "status_path": status_path,
                    "motivo": f"excede {len(resumen)} > {MAX_RESUMEN}",
                    "resumen": resumen,
                    "largo": len(resumen),
                }
            )
    return faltas


def json_map(repo: str = ".") -> dict:
    """Mapa JSON id -> {resumen: str|null, ruta: str|null, largo, excede} para el tablero."""
    abiertos = _lista_tickets_abiertos(repo)
    out: dict[str, dict] = {}
    for tid, _state, _path in abiertos:
        resumen, ruta = leer_resumen(tid, repo)
        # ruta relativa a la raíz de gestión para el tablero
        gr = _raiz(repo)
        ruta_rel = os.path.relpath(ruta, gr) if ruta else None
        if resumen == SIN_RESUMEN:
            out[tid] = {"resumen": None, "ruta": ruta_rel, "largo": None, "excede": False}
        else:
            out[tid] = {
                "resumen": resumen,
                "ruta": ruta_rel,
                "largo": len(resumen),
                "excede": len(resumen) > MAX_RESUMEN,
            }
    # Incluye también cerrados? No, solo abiertos para tablero; pero si se quiere todo, el tablero pedirá solo abiertos.
    # Para compat, si no hay STATUS, igual permite lectura directa vía get.
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lector y candado de **Resumen:** (SKILL-E)")
    sub = ap.add_subparsers(dest="cmd")

    # get <ID> [repo]
    g = sub.add_parser("get", help="imprime el resumen de un ticket o 'sin resumen'")
    g.add_argument("id", help="ID del ticket, ej. SKILL-E")
    g.add_argument("repo", nargs="?", default=".", help="raíz del repo (por defecto .)")

    # check [repo]
    c = sub.add_parser("check", help="lista abiertos sin resumen o que exceden 240c; exit 1 si hay")
    c.add_argument("repo", nargs="?", default=".", help="raíz del repo (por defecto .)")

    # json [repo]
    j = sub.add_parser("json", help="mapa JSON id -> {resumen|null, ruta}")
    j.add_argument("repo", nargs="?", default=".", help="raíz del repo (por defecto .)")

    # Compat: llamada directa sin subcomando: resumen.py <ID> [<repo>]  o  resumen.py check <repo>
    # Si no hay subcomando pero hay args, los interpretamos manualmente en el fallback.
    args, unknown = ap.parse_known_args(argv)

    # Fallback para `resumen.py <ID>` sin subcomando
    if args.cmd is None:
        raw = argv if argv is not None else sys.argv[1:]
        if not raw:
            ap.print_help()
            return 2
        # `check <repo>` sin subparser por compat con spec "resumen.py check <raíz>"
        if raw[0] == "check":
            repo = raw[1] if len(raw) > 1 else "."
            faltas = check(repo)
            if not faltas:
                print("ok: todos los tickets abiertos tienen resumen <= 240c")
                return 0
            for f in faltas:
                print(f"FLAG {f['id']}: {f['motivo']} (estado: {f['estado']}, ruta: {f.get('ruta') or 'no encontrada'})")
            return 1
        if raw[0] == "json":
            repo = raw[1] if len(raw) > 1 else "."
            print(json.dumps(json_map(repo), ensure_ascii=False, indent=2))
            return 0
        # asume get <ID> [<repo>]
        tid = raw[0]
        repo = raw[1] if len(raw) > 1 else "."
        # evita confundir "json"/"check" ya manejados
        if tid in ("get",):
            # `get` solo con ID siguiente
            if len(raw) < 2:
                print("error: get requiere <ID>", file=sys.stderr)
                return 2
            tid = raw[1]
            repo = raw[2] if len(raw) > 2 else "."
        resumen, _ruta = leer_resumen(tid, repo)
        print(resumen)
        return 0

    if args.cmd == "get":
        resumen, _ruta = leer_resumen(args.id, args.repo)
        print(resumen)
        return 0
    if args.cmd == "check":
        faltas = check(args.repo)
        if not faltas:
            print("ok: todos los tickets abiertos tienen resumen <= 240c")
            return 0
        for f in faltas:
            print(f"FLAG {f['id']}: {f['motivo']} (estado: {f['estado']}, ruta: {f.get('ruta') or 'no encontrada'})")
        return 1
    if args.cmd == "json":
        print(json.dumps(json_map(args.repo), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
