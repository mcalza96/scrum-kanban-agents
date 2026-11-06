"""Identidad de un preview local; nunca mata por nombre ni por puerto solamente."""
import os
from pathlib import Path
import signal
import subprocess


def _run(*args):
    r = subprocess.run(args, capture_output=True, text=True)
    if r.returncode not in (0, 1):
        raise ValueError(f'no se pudo observar {args[0]}: {r.stderr.strip()}')
    return r.stdout.strip()


def identity(pid):
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        raise ValueError('PID inválido')
    started = _run('ps', '-p', str(pid), '-o', 'lstart=')
    if not started:
        return None
    rows = _run('lsof', '-a', '-p', str(pid), '-d', 'cwd', '-Fn').splitlines()
    paths = [row[1:] for row in rows if row.startswith('n')]
    if len(paths) != 1:
        state = _run('ps', '-p', str(pid), '-o', 'stat=')
        if not state or state.startswith('Z'):
            return None
        raise ValueError('cwd del proceso no observable')
    return {'pid': pid, 'started': started, 'cwd': str(Path(paths[0]).resolve())}


def listeners(port):
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError('puerto inválido')
    return {int(p) for p in _run('lsof', '-nP', '-tiTCP:' + str(port), '-sTCP:LISTEN').splitlines()}


def alive(preview):
    observed = identity(preview['pid'])
    return observed == {k: preview[k] for k in ('pid', 'started', 'cwd')}


def stop(preview):
    observed = identity(preview['pid'])
    expected = {k: preview[k] for k in ('pid', 'started', 'cwd')}
    if observed is not None and observed != expected:
        raise ValueError('PID reutilizado o identidad distinta; no se envió señal')
    owners = listeners(preview['port'])
    if owners - {preview['pid']}:
        raise ValueError('el puerto tiene un listener ajeno; no se envió señal')
    if observed:
        os.kill(preview['pid'], signal.SIGTERM)
    # Sin espera oculta ni escalamiento a SIGKILL. Un proceso que aún termina deja
    # el cierre pendiente y el mismo comando se puede repetir con seguridad.
    if identity(preview['pid']) == expected or listeners(preview['port']):
        raise ValueError('preview aún activo; repite el cierre cuando termine')
