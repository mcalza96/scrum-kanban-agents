#!/usr/bin/env python3
"""Un worktree por pedido; una entrega por commit; retiro solo tras gate del integrado."""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
MARKER = "scrum-dev.json"


def git(repo: str, *args: str) -> str:
    result = subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout.strip()


def _id(value: str) -> str:
    if not NAME.fullmatch(value):
        raise ValueError("id de pedido inválido")
    return value


def _gitdir(path: str) -> Path | None:
    dot = Path(path) / ".git"
    if not dot.is_file():
        return None
    text = dot.read_text().strip()
    if not text.startswith("gitdir: "):
        return None
    return (Path(path) / text[8:]).resolve()


def metadata(path: str) -> dict | None:
    """Solo worktrees creados por este ciclo; no consulta Git ni infiere la app del buzón."""
    gd = _gitdir(path)
    marker = gd / MARKER if gd else None
    if marker is None or not marker.is_file():
        return None
    data = json.loads(marker.read_text())
    if not isinstance(data, dict) or data.get("directory") != str(Path(path).resolve()):
        raise ValueError("metadata de worktree inconsistente")
    return data


def _location(project: str, request_id: str) -> tuple[str, str]:
    parent = Path(project).parent / ".scrum-worktrees" / Path(project).name
    return str(parent / _id(request_id)), f"codex/dev/{request_id}"


def _binding(root: str, project: str) -> str:
    import gestion
    project = str(Path(project).resolve())
    top = git(project, "rev-parse", "--show-toplevel")
    first = next((line[9:] for line in git(project, "worktree", "list", "--porcelain").splitlines()
                  if line.startswith("worktree ")), "")
    if str(Path(top).resolve()) != project or str(Path(first).resolve()) != project:
        raise ValueError("project debe ser el checkout principal de la aplicación")
    if Path(gestion.inbox_root(project)).resolve() != Path(root).resolve():
        raise ValueError("el buzón no corresponde al proyecto explícito")
    return project


def prepare(root: str, entry: dict) -> dict:
    """Crea o recupera únicamente el worktree de este pedido, sin tocar el principal."""
    import install
    project = _binding(root, entry.get("project", "")) if entry.get("project") else None
    if project is None:
        raise ValueError("pedido sin project; encola con --repo del proyecto de aplicación")
    errors = install.install(Path(project), check=True)
    if errors:
        raise ValueError("instala las entradas canónicas antes de lanzar: " + ", ".join(errors))
    for relative, expected in install.entries().items():
        if git(project, "show", f"refs/heads/main:{relative}").strip() != expected.strip():
            raise ValueError(f"la entrada canónica debe estar commiteada en main: {relative}")
    directory, branch = _location(project, entry["id"])
    current = metadata(directory) if Path(directory).exists() else None
    if current:
        expected = {"id": entry["id"], "project": project, "branch": branch,
                    "gestion": str(Path(root).resolve().parent), "directory": directory}
        if any(current.get(key) != value for key, value in expected.items()):
            raise ValueError("el directorio existente pertenece a otro pedido")
        return current
    if Path(directory).exists():
        raise ValueError("el directorio del pedido ya existe sin metadata válida")
    base = git(project, "rev-parse", "refs/heads/main^{commit}")
    Path(directory).parent.mkdir(parents=True, exist_ok=True)
    git(project, "worktree", "add", "-b", branch, directory, base)
    # El lock de Git añade protección aun frente a limpiadores que no conocen la metadata.
    git(project, "worktree", "lock", "--reason", "pedido pendiente de integración y gate", directory)
    data = {"id": entry["id"], "project": project, "branch": branch, "base": base,
            "gestion": str(Path(root).resolve().parent), "directory": directory}
    gd = _gitdir(directory)
    assert gd is not None
    (gd / MARKER).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    modules = Path(project) / "node_modules"
    if modules.is_dir() and not (Path(directory) / "node_modules").exists():
        ignored = subprocess.run(["git", "-C", directory, "check-ignore", "-q", "node_modules/"],
                                 capture_output=True)
        if ignored.returncode == 0:
            (Path(directory) / "node_modules").symlink_to(modules, target_is_directory=True)
    return data


def receipt(root: str, request_id: str) -> Path:
    return Path(root) / ".lanzar" / f"{_id(request_id)}.delivery"


def _entry(root: str, request_id: str) -> dict:
    import inbox
    request_id = _id(request_id)
    found = [entry for entry in inbox.launch_records(root) if entry.get("id") == request_id]
    if len(found) != 1:
        raise ValueError("pedido sin lanzamiento único registrado")
    entry = found[0]
    expected, branch = _location(entry.get("project", ""), request_id)
    if entry.get("directory") != expected or entry.get("branch") != branch or not entry.get("base"):
        raise ValueError("pedido sin worktree administrado verificable")
    _binding(root, entry["project"])
    return entry


def _clean(directory: str) -> None:
    if git(directory, "status", "--porcelain", "--untracked-files=all"):
        raise ValueError("el worktree tiene cambios sin commit o archivos sin seguimiento")


def _commit(project: str, value: str) -> str:
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", value):
        raise ValueError("se requiere un SHA de commit")
    return git(project, "rev-parse", f"{value}^{{commit}}")


def deliver(root: str, request_id: str, commit: str, cwd: str) -> dict:
    import inbox
    with inbox.launch_lock(root):
        entry = _entry(root, request_id)
        meta = metadata(entry["directory"])
        if not meta or any(meta.get(key) != entry.get(key) for key in meta):
            raise ValueError("metadata no coincide con el lanzamiento")
        if Path(cwd).resolve() != Path(entry["directory"]).resolve():
            raise ValueError("la entrega debe ejecutarse desde el worktree del pedido")
        sha = _commit(entry["project"], commit)
        if git(entry["directory"], "rev-parse", "HEAD") != sha or sha == entry["base"]:
            raise ValueError("el commit entregado debe ser el HEAD nuevo del worktree")
        git(entry["project"], "merge-base", "--is-ancestor", entry["base"], sha)
        _clean(entry["directory"])
        path = receipt(root, request_id)
        if path.exists():
            old = json.loads(path.read_text())
            if old.get("commit") != sha:
                raise ValueError("el pedido ya tiene otro commit entregado")
            return old
        data = {key: entry[key] for key in ("id", "project", "branch", "base", "gestion", "directory", "session")}
        data.update(commit=sha, status="delivered")
        inbox.write_request(str(path), data)
        return data


def _idle(entry: dict, db_path: str) -> None:
    try:
        with closing(sqlite3.connect(f"file:{Path(db_path).resolve()}?mode=ro", uri=True)) as db:
            row = db.execute("select time_busy,time_idle from session_v2 where id=?", (entry["session"],)).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(f"runtime no observable: {exc}") from exc
    if not row or not row[1] or (row[0] or 0) >= row[1]:
        raise ValueError("la sesión no está observada como terminada/idle")


def _integrated(entry: dict, sha: str, tip: str) -> None:
    result = subprocess.run(["git", "-C", entry["project"], "merge-base", "--is-ancestor", tip, sha],
                            capture_output=True)
    if result.returncode == 0:
        return
    if result.returncode != 1:
        raise ValueError("no se pudo comprobar la integración")
    lines = git(entry["project"], "cherry", sha, tip, entry["base"]).splitlines()
    if not lines or any(not line.startswith("- ") for line in lines):
        raise ValueError("el commit entregado todavía no está integrado en main")


def close(root: str, request_id: str, integrated_sha: str, db_path: str) -> dict:
    import inbox
    # No se retiene el candado de admisión mientras compila el gate.
    entry = _entry(root, request_id)
    path = receipt(root, request_id)
    if not path.exists():
        raise ValueError("falta una entrega con commit")
    data = json.loads(path.read_text())
    if any(data.get(key) != entry.get(key) for key in ("id", "project", "branch", "base", "gestion", "directory", "session")):
        raise ValueError("la entrega no corresponde al lanzamiento")
    if data.get("status") == "closed":
        return data
    _idle(entry, db_path)
    project = entry["project"]
    sha = _commit(project, integrated_sha)
    tip = _commit(project, data["commit"])
    if git(project, "rev-parse", "refs/heads/main") != sha:
        raise ValueError("--integrado debe ser el HEAD actual de main")
    _integrated(entry, sha, tip)
    directory = entry["directory"]
    exists = Path(directory).exists()
    if exists:
        if metadata(directory) != {key: entry[key] for key in ("id", "project", "branch", "base", "gestion", "directory")}:
            raise ValueError("metadata no coincide")
        if git(directory, "rev-parse", "HEAD") != tip:
            raise ValueError("el worktree avanzó después de entregar")
        _clean(directory)
    elif data.get("verified") != sha:
        raise ValueError("el worktree desapareció antes del gate; requiere revisión")
    verifier = Path(project) / "scripts/verificar-commit.sh"
    if not verifier.is_file():
        raise ValueError("falta scripts/verificar-commit.sh en el checkout principal")
    result = subprocess.run(["bash", str(verifier), sha], cwd=project)
    if result.returncode:
        raise ValueError(f"gate rechazado ({result.returncode}); se conserva el worktree")
    with inbox.launch_lock(root):
        _idle(entry, db_path)
        if git(project, "rev-parse", "refs/heads/main") != sha:
            raise ValueError("main cambió durante el gate; verifica el nuevo commit")
        branch_ref = f"refs/heads/{entry['branch']}"
        present = subprocess.run(["git", "-C", project, "rev-parse", "--verify", branch_ref], capture_output=True, text=True)
        if present.returncode == 0 and present.stdout.strip() != tip:
            raise ValueError("la rama avanzó durante el gate")
        if Path(directory).exists():
            if git(directory, "rev-parse", "HEAD") != tip:
                raise ValueError("el worktree avanzó durante el gate")
            _clean(directory)
        data.update(verified=sha, status="verified")
        inbox.write_request(str(path), data)
        if Path(directory).exists():
            git(project, "worktree", "unlock", directory)
            try:
                git(project, "worktree", "remove", directory)
            except ValueError:
                git(project, "worktree", "lock", "--reason", "cierre incompleto", directory)
                raise
        if present.returncode == 0:
            git(project, "update-ref", "-d", branch_ref, tip)
        data.update(status="closed")
        inbox.write_request(str(path), data)
        return data


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="action", required=True)
    for action in ("entregar", "cerrar"):
        parser = sub.add_parser(action)
        parser.add_argument("root")
        parser.add_argument("--pedido", required=True)
        parser.add_argument("--commit" if action == "entregar" else "--integrado", required=True)
    args = ap.parse_args(argv)
    try:
        result = (deliver(args.root, args.pedido, args.commit, os.getcwd()) if args.action == "entregar"
                  else close(args.root, args.pedido, args.integrado, os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db"))))
    except (ValueError, OSError) as exc:
        ap.exit(2, f"error: {exc}\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
