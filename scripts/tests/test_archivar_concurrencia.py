import concurrent.futures
import datetime
import fcntl
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/'adapters/opencode'))
import archivar
import wake


class ArchivadoConcurrenteTest(unittest.TestCase):
    def test_append_espera_compactacion_y_sobrevive(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            log = root/'.wakes.log'
            log.write_text('2026-01-01 10:00 -0300 PM-a woke ANTIGUO\n')
            attempted = threading.Event()
            original_flock = fcntl.flock
            original_append = archivar.append_new
            future = None
            def writer():
                threading.current_thread().name = 'writer-fixture'
                wake.log(str(root), 'PM-a', 'woke NUEVO')
            def spy(fd, operation):
                if threading.current_thread().name == 'writer-fixture':
                    attempted.set()
                return original_flock(fd, operation)
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                def append(*args, **kwargs):
                    nonlocal future
                    result = original_append(*args, **kwargs)
                    future = pool.submit(writer)
                    self.assertTrue(attempted.wait(5), 'el escritor debe intentar adquirir el candado')
                    self.assertFalse(future.done(), 'el escritor espera la compactación')
                    return result
                with patch.object(fcntl, 'flock', spy), patch.object(archivar, 'append_new', append):
                    archivar.wakes(str(root), datetime.datetime.now().astimezone(), False)
                    future.result(timeout=5)
            self.assertIn('NUEVO', log.read_text())
            self.assertNotIn('ANTIGUO', log.read_text())
            self.assertIn('ANTIGUO', (root/'archivo/wakes-2026-01.log').read_text())

    def test_barrido_no_toca_tableros_y_cli_manual_si(self):
        import io
        import contextlib
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            (root/'.git').mkdir()
            (root/'inbox').mkdir()
            (root/'epic-prueba').mkdir()
            status=root/'epic-prueba/STATUS.md'
            status.write_text('| Ticket | State | Updated |\n|---|---|---|\n'+''.join(f'| T-{i} | closed | 2026-01-01 |\n' for i in range(6)))
            (root/'focus').mkdir()
            focus=root/'focus/PROJECT.md'
            focus.write_text('| Id | Kind | Scope | Human words | Date | Status |\n|---|---|---|---|---|---|\n| D-1 | decision | project | texto | 2026-01-01 | executed |\n')
            before=(status.read_bytes(), focus.read_bytes())
            with patch.object(wake, 'sweep', return_value=[]), patch.object(wake, 'reap', return_value=[]), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(wake.main(['--sweep',temp]),0)
                self.assertEqual(archivar.main([temp]),0)
                self.assertEqual((status.read_bytes(),focus.read_bytes()), before)
                self.assertFalse((root/'epic-prueba/STATUS-archivo.md').exists())
                self.assertEqual(archivar.main([temp,'--tableros','--dry-run']),0)
                self.assertEqual((status.read_bytes(),focus.read_bytes()), before)
                self.assertEqual(archivar.main([temp,'--tableros']),0)
            self.assertTrue((root/'epic-prueba/STATUS-archivo.md').exists())
            self.assertNotEqual(status.read_bytes(),before[0])
            self.assertIn('## Archived',focus.read_text())
