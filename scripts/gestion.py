#!/usr/bin/env python3
"""Un solo punto de resolución para la carpeta que contiene epic-*, focus/ e inbox/.

Lee de:

  1) variable de entorno SCRUM_GESTION (override para CI / tests temporales);
  2) archivo .scrum.toml en la raíz del repo con ``gestion = "gestion"``;
  3) por defecto la raíz del repo (``.``), igual que hoy.

Justificación: el archivo es persistente por repo (ORDEN-4 lo creará con
``gestion = "gestion"``) y no requiere dependencia externa; la env var
permite a scripts y tests apuntar a un repo temporal sin tocar el
filesystem. SCRUM_GESTION gana al archivo para que un CI pueda forzar
una ubicación sin editar el repo. Por defecto ``.`` mantiene compatibilidad
total: sin config los 5 scripts operan sobre la raíz y los tests existentes
pasan sin tocarlos.

Uso:
    from gestion import raiz_gestion
    gr = raiz_gestion(repo)          # -> /abs/path al root de gestión
    inbox_root = os.path.join(gr, "inbox")
    focus_root = os.path.join(gr, "focus")
"""
from __future__ import annotations

import os
import re

ENV_VAR = "SCRUM_GESTION"
CONFIG_NAMES = (".scrum.toml", "scrum.toml")  # .scrum.toml es el canónico
_KEY_RE = re.compile(r'^\s*gestion\s*=\s*["\']([^"\']+)["\']\s*(?:#.*)?$')


def _from_env() -> str | None:
    v = os.environ.get(ENV_VAR)
    if v is None:
        # compat: GESTION también aceptado (documentado en reporte)
        v = os.environ.get("GESTION")
    if v is not None:
        v = v.strip()
        if v == "":
            return None
        return v
    return None


def _from_file(repo: str) -> str | None:
    for name in CONFIG_NAMES:
        path = os.path.join(repo, name)
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    m = _KEY_RE.match(line)
                    if m:
                        return m.group(1).strip()
                    # también soporta gestion = gestion sin comillas (tolerante)
                    m2 = re.match(r'^\s*gestion\s*=\s*([A-Za-z0-9._/-]+)\s*(?:#.*)?$', line)
                    if m2:
                        return m2.group(1).strip()
        except FileNotFoundError:
            continue
        except OSError:
            continue
    return None


def raiz_gestion(repo: str) -> str:
    """Carpeta que contiene epic-*, focus/ e inbox/. Por defecto es repo."""
    # repo debe ser directorio; si se pasa un archivo, usa su dirname
    base = os.path.abspath(repo)
    # si repo apunta a un archivo .scrum.toml dentro, normaliza a su dir?
    # no: repo es siempre el root del proyecto
    raw = _from_env()
    if raw is None:
        raw = _from_file(base)
    if raw is None or raw in (".", "./", ""):
        return base
    # normaliza: quita ./ prefijo
    raw = raw.lstrip("./") if raw.startswith("./") else raw
    if raw in ("", "."):
        return base
    if os.path.isabs(raw):
        return os.path.normpath(raw)
    return os.path.normpath(os.path.join(base, raw))


def inbox_root(repo: str) -> str:
    return os.path.join(raiz_gestion(repo), "inbox")


def focus_root(repo: str) -> str:
    return os.path.join(raiz_gestion(repo), "focus")
