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
import shlex

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


def close_command(root: str, request_id: str, sha: str = "<SHA-main>") -> str:
    return shlex.join(["python3", str(Path(__file__).resolve()), "cerrar", str(Path(root).resolve()),
                       "--pedido", request_id, "--integrado", sha, "--sincronizar"])


def register_preview(root: str, request_id: str, pr: int, pid: int | None, port: int | None,
                     directory: str | None, sha: str | None) -> dict:
    import inbox
    import preview_process
    if pr < 1:
        raise ValueError("PR inválido")
    with inbox.launch_lock(root):
        entry = _entry(root, request_id)
        path = receipt(root, request_id)
        data = json.loads(path.read_text())
        if data.get("status") != "delivered":
            raise ValueError("solo una entrega pendiente admite registrar preview")
        preview = None
        if pid is not None:
            if directory is None or port is None or sha is None:
                raise ValueError("preview requiere PID, puerto, directorio y SHA revisado")
            observed = preview_process.identity(pid)
            cwd = str(Path(directory).resolve())
            commit = _commit(entry["project"], sha)
            if not observed or observed["cwd"] != cwd or preview_process.listeners(port) != {pid}:
                raise ValueError("PID, cwd y listener no corresponden al preview")
            common = lambda repo: str((Path(repo) / git(repo, "rev-parse", "--git-common-dir")).resolve())
            if common(cwd) != common(entry["project"]) or git(cwd, "rev-parse", "HEAD") != commit:
                raise ValueError("preview no corresponde al repositorio y SHA revisado")
            if cwd != entry["directory"]:
                if not _gitdir(cwd) or metadata(cwd) or git(cwd, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
                    raise ValueError("preview adicional debe ser un worktree detached dedicado")
            _clean(cwd)
            preview = dict(observed, port=port, sha=commit)
        if "preview" in data and data["preview"] != preview:
            raise ValueError("ya existe un registro de preview distinto; no se sobrescribe")
        data.update(pr=pr, preview=preview)
        inbox.write_request(str(path), data)
        return data


def sync_main(project: str, expected: str) -> None:
    """Actualiza solo main, nunca el checkout/index compartidos de otra rama."""
    git(project, "fetch", "origin", "main")
    remote = git(project, "rev-parse", "refs/remotes/origin/main")
    if remote != expected:
        raise ValueError("origin/main cambió; usa el SHA remoto actual y repite el cierre")
    old = git(project, "rev-parse", "refs/heads/main")
    if old == remote:
        return
    git(project, "merge-base", "--is-ancestor", old, remote)
    rows = git(project, "worktree", "list", "--porcelain").splitlines()
    if "branch refs/heads/main" in rows:
        raise ValueError("main está en uso por un checkout; sincronízalo allí sin descartar cambios")
    git(project, "update-ref", "refs/heads/main", remote, old)


def close(root: str, request_id: str, integrated_sha: str, db_path: str, synchronize: bool = False) -> dict:
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
        if data.get("preview"):
            import preview_process
            preview_process.stop(data["preview"])
        return data
    _idle(entry, db_path)
    project = entry["project"]
    sha = _commit(project, integrated_sha)
    if synchronize:
        sync_main(project, sha)
    tip = _commit(project, data["commit"])
    if git(project, "rev-parse", "refs/heads/main") != sha:
        raise ValueError("--integrado debe ser el HEAD actual de main")
    _integrated(entry, sha, tip)
    if "preview" not in data:
        raise ValueError("falta registrar preview o --sin-preview; no se retira la entrega")
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
        preview = data["preview"]
        extra = preview and preview["cwd"] != directory
        if extra and Path(preview["cwd"]).exists():
            cwd = preview["cwd"]
            if (not _gitdir(cwd) or metadata(cwd)
                    or git(cwd, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD"
                    or git(cwd, "rev-parse", "HEAD") != preview["sha"]):
                raise ValueError("el worktree de preview cambió; se conserva")
            _integrated(entry, sha, preview["sha"])
            _clean(cwd)
        if preview:
            import preview_process
            preview_process.stop(preview)
        data.update(verified=sha, status="verified")
        inbox.write_request(str(path), data)
        if extra and Path(preview["cwd"]).exists():
            git(project, "worktree", "remove", preview["cwd"])
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
    for action in ("entregar", "cerrar", "registrar-preview"):
        parser = sub.add_parser(action)
        parser.add_argument("root", help="directorio inbox, no su carpeta padre")
        parser.add_argument("--pedido", required=True)
        if action == "registrar-preview":
            parser.add_argument("--pr", type=int, required=True)
            mode = parser.add_mutually_exclusive_group(required=True)
            mode.add_argument("--pid", type=int)
            mode.add_argument("--sin-preview", action="store_true")
            parser.add_argument("--puerto", type=int)
            parser.add_argument("--directorio")
            parser.add_argument("--sha")
        else:
            parser.add_argument("--commit" if action == "entregar" else "--integrado", required=True)
            if action == "cerrar":
                parser.add_argument("--sincronizar", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.action == "entregar":
            result = deliver(args.root, args.pedido, args.commit, os.getcwd())
        elif args.action == "registrar-preview":
            result = register_preview(args.root, args.pedido, args.pr, args.pid, args.puerto, args.directorio, args.sha)
        else:
            result = close(args.root, args.pedido, args.integrado,
                os.environ.get("OPENCODE_DB", os.path.expanduser("~/.local/share/opencode/opencode.db")), args.sincronizar)
    except (ValueError, OSError, KeyError) as exc:
        ap.exit(2, f"error: {exc}\nNo retires manualmente la entrega. "
                   f"Usa el inbox registrado y conserva el error completo (sin ocultarlo con tail).\n")
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
