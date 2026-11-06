import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import preview_process as pp

class PreviewProcessTest(unittest.TestCase):
    def setUp(self):
        self.preview={'pid':1234,'started':'original','cwd':'/preview','port':3106,'sha':'a'*40}
        self.expected={k:self.preview[k] for k in ('pid','started','cwd')}

    def test_pid_reutilizado_no_recibe_senal(self):
        with patch.object(pp,'identity',return_value=dict(self.expected,started='nuevo')), patch.object(pp.os,'kill') as kill:
            with self.assertRaisesRegex(ValueError,'reutilizado'):pp.stop(self.preview)
            kill.assert_not_called()

    def test_listener_ajeno_no_recibe_senal(self):
        with patch.object(pp,'identity',return_value=self.expected), patch.object(pp,'listeners',return_value={1234,999}), patch.object(pp.os,'kill') as kill:
            with self.assertRaisesRegex(ValueError,'ajeno'):pp.stop(self.preview)
            kill.assert_not_called()

    def test_terminando_no_autoriza_cierre_y_no_escala(self):
        with patch.object(pp,'identity',return_value=self.expected), patch.object(pp,'listeners',return_value={1234}), patch.object(pp.os,'kill') as kill:
            with self.assertRaisesRegex(ValueError,'aún activo'):pp.stop(self.preview)
            kill.assert_called_once_with(1234,pp.signal.SIGTERM)

    def test_terminado_y_puerto_libre_es_idempotente(self):
        with patch.object(pp,'identity',return_value=None), patch.object(pp,'listeners',return_value=set()), patch.object(pp.os,'kill') as kill:
            pp.stop(self.preview)
            kill.assert_not_called()
