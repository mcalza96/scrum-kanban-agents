#!/usr/bin/env python3
"""El barrido avisa al PO los PR abiertos/listos, una vez por repositorio, PR y SHA."""
from __future__ import annotations

import fcntl
import json
from pathlib import Path
import re
import shlex
import subprocess
from urllib.parse import quote, urlsplit

import gestion
import inbox

REPOSITORY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*")
SHA = re.compile(r"[0-9a-f]{40}")


def _gh(repo: str, *args: str) -> str:
    result = subprocess.run(['gh', *args], cwd=repo, capture_output=True, text=True)
    if result.returncode:
        raise ValueError(f"gh {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def _origin_name(url: str) -> str:
    if '://' in url:
        path = urlsplit(url).path
    else:
        match = re.fullmatch(r'(?:[^@:/]+@)?[^:/]+:(.+)', url)
        path = match.group(1) if match else ''
    name = path.strip('/').removesuffix('.git')
    if not REPOSITORY.fullmatch(name):
        raise ValueError('origin no identifica un repositorio owner/name')
    return name


def _pulls(repo: str, expected: str) -> tuple[str, list[dict]]:
    name = _gh(repo, 'repo', 'view', '--json', 'nameWithOwner', '--jq', '.nameWithOwner').strip()
    if not REPOSITORY.fullmatch(name) or name.lower() != expected.lower():
        raise ValueError('GitHub devolvió un repositorio distinto de origin o sin identidad válida')
    pages = json.loads(_gh(repo, 'api', '--paginate', '--slurp',
                          f'repos/{name}/pulls?state=open&base=main&per_page=100'))
    if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
        raise ValueError('respuesta de PRs paginada inválida')
    found = []
    for page in pages:
        for pr in page:
            if not isinstance(pr, dict):
                raise ValueError('PR sin objeto de datos')
            if pr.get('state') == 'closed' or pr.get('draft') is True:
                continue
            base = pr.get('base') or {}
            if base.get('ref') != 'main':
                continue
            number, head, url = pr.get('number'), pr.get('head') or {}, pr.get('html_url')
            if (pr.get('state') != 'open' or pr.get('draft') is not False
                    or not isinstance(number, int) or isinstance(number, bool) or number < 1
                    or not isinstance(pr.get('title'), str) or not isinstance(url, str)
                    or not isinstance(head.get('sha'), str) or not SHA.fullmatch(head['sha'])
                    or (base.get('repo') or {}).get('full_name') != name):
                raise ValueError('PR sin identidad, estado o repositorio verificable')
            parsed = urlsplit(url)
            if parsed.scheme != 'https' or not parsed.netloc or parsed.path.rstrip('/') != f'/{name}/pull/{number}':
                raise ValueError('URL del PR no corresponde al repositorio y número')
            found.append(pr)
    return name, found


def _already_delivered(root: Path, token: str) -> bool:
    if any(token in message.text for message in inbox.messages(str(root), 'PO')):
        return True
    for path in (root/'archivo').glob('*.md'):
        match = inbox.ARCHIVED.fullmatch(path.name)
        if match and match.group('pm') == 'PO':
            if any(token in message.text for message in inbox.parse(path.read_text())):
                return True
    return False


def _merged_pull(repo: str, name: str, delivery: dict) -> dict | None:
    """Consulta solo el PR registrado o la rama de la entrega histórica."""
    number = delivery.get('pr')
    branch = delivery.get('branch')
    if not isinstance(branch, str) or not branch.startswith('codex/dev/'):
        raise ValueError('entrega sin rama administrada')
    if number is not None:
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise ValueError('número de PR inválido en entrega')
        candidates = [json.loads(_gh(repo, 'api', f'repos/{name}/pulls/{number}'))]
    else:
        head = quote(f"{name.split('/')[0]}:{branch}", safe='')
        pages = json.loads(_gh(repo, 'api', '--paginate', '--slurp',
                              f'repos/{name}/pulls?state=closed&head={head}&base=main&per_page=100'))
        if not isinstance(pages, list) or any(not isinstance(page, list) for page in pages):
            raise ValueError('respuesta de cierres paginada inválida')
        candidates = [pr for page in pages for pr in page]
    merged = []
    for pr in candidates:
        if not isinstance(pr, dict):
            raise ValueError('cierre sin objeto PR')
        base, head = pr.get('base') or {}, pr.get('head') or {}
        num = pr.get('number')
        if (not isinstance(num, int) or isinstance(num, bool) or num < 1
                or (number is not None and num != number)
                or base.get('ref') != 'main' or (base.get('repo') or {}).get('full_name') != name
                or head.get('ref') != branch):
            raise ValueError('PR de cierre no corresponde a la entrega/repositorio')
        url = urlsplit(pr.get('html_url') or '')
        if url.scheme != 'https' or not url.netloc or url.path.rstrip('/') != f'/{name}/pull/{num}':
            raise ValueError('URL inválida del PR de cierre')
        if pr.get('state') == 'closed' and pr.get('merged_at'):
            merged.append(pr)
    if len(merged) > 1:
        raise ValueError('más de un PR integrado para la misma entrega; requiere revisión')
    return merged[0] if merged else None


def _pending_closures(repo: str, root: Path, name: str):
    """Produce avisos o errores por entrega; nunca muta procesos, ramas ni worktrees."""
    for path in sorted((root/'.lanzar').glob('*.delivery')):
        try:
            data = json.loads(path.read_text())
            request = path.stem
            if (not isinstance(data, dict) or data.get('id') != request
                    or Path(data.get('project', '')).resolve() != Path(repo).resolve()
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', request)):
                raise ValueError('identidad de entrega inválida')
            if data.get('status') == 'closed':
                preview = data.get('preview')
                if not preview:
                    continue
                import preview_process
                if not preview_process.alive(preview):
                    continue
            pr = _merged_pull(repo, name, data)
            if pr is None:
                continue
            main = subprocess.run(['git', '-C', repo, 'rev-parse', '--verify', 'refs/remotes/origin/main'],
                                  capture_output=True, text=True)
            sha = main.stdout.strip() if main.returncode == 0 else '<SHA-origin-main-actual>'
            command = shlex.join(['python3', str(Path(__file__).resolve().with_name('dev_worktree.py')),
                                 'cerrar', str(root), '--pedido', request,
                                 '--integrado', sha, '--sincronizar'])
            token = f"[cierre:{name}#{pr['number']}@{request}]"
            text = (f"[encargo] Cierre pendiente tras merge: {pr['html_url']}; pedido {request}, "
                    f"recibo {data.get('status')}. El PO debe verificar y cerrar preview/worktree "
                    f"por el flujo único: {command}. Si falla, conserva la entrega y registra el bloqueo; "
                    f"no uses unlock/remove/branch manuales. {token}")
            yield token, text, None
        except (OSError, ValueError, KeyError, TypeError, AttributeError, ImportError) as exc:
            yield None, None, f'Cierre {path.name}: no verificable; se conserva: {exc}'


def scan(repo: str) -> list[str]:
    """No crea buzones. Un fallo se informa y queda reintentable en el próximo barrido."""
    root = Path(gestion.inbox_root(repo))
    if not root.is_dir():
        return [f'PRs: buzón ausente: {root}']
    try:
        origin = subprocess.run(['git', '-C', repo, 'config', '--get', 'remote.origin.url'],
                                capture_output=True, text=True)
        if origin.returncode == 1:  # repos locales, incluidos los fixtures del barrido
            return []
        if origin.returncode:
            raise ValueError(f'no se pudo consultar origin: {origin.stderr.strip()}')
        if not origin.stdout.strip():
            return []
        with (root/'.pr-watch.lock').open('a+') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            name, pulls = _pulls(repo, _origin_name(origin.stdout.strip()))
            path = root/'.pr-watch.json'
            state = json.loads(path.read_text()) if path.exists() else {'notified': []}
            keys = state.get('notified') if isinstance(state, dict) else None
            if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
                raise ValueError('estado de notificaciones de PR inválido; se conserva para revisión')
            seen = set(keys)
            done = []
            for pr in pulls:
                sha = pr['head']['sha']
                token = f"[pr:{name}#{pr['number']}@{sha}]"
                if token in seen:
                    continue
                # Recupera el envío si el proceso cayó antes de persistir su marcador.
                if not _already_delivered(root, token):
                    mid = inbox.send(str(root), 'PO', 'pr-watch',
                        f"[encargo] PR listo para revisión: {pr['html_url']} — {pr['title']}. "
                        f"HEAD {sha}. Revisa checks y evidencia, prepara localhost y explica los cambios "
                        f"si es revisable en navegador. Este aviso no autoriza el merge. {token}")
                    done.append(f"PR #{pr['number']} {sha}: PO avisado ({mid})")
                else:
                    done.append(f"PR #{pr['number']} {sha}: aviso previo recuperado")
                seen.add(token)
                inbox.write_request(str(path), {'notified': sorted(seen)})
            for token, message, error in _pending_closures(repo, root, name):
                if error:
                    done.append(error)
                    continue
                if token in seen:
                    continue
                if not _already_delivered(root, token):
                    mid = inbox.send(str(root), 'PO', 'pr-watch', message)
                    done.append(f'Cierre pendiente: PO avisado ({mid}) {token}')
                else:
                    done.append(f'Cierre pendiente: aviso previo recuperado {token}')
                seen.add(token)
                inbox.write_request(str(path), {'notified': sorted(seen)})
            return done
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return [f'PRs: consulta/aviso falló; se reintentará: {exc}']
