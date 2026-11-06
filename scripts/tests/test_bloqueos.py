import concurrent.futures
import datetime
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "adapters/opencode"))
import inbox
import wake


class BloqueosTest(unittest.TestCase):
    def test_unico_por_pm_y_mensaje_concurrente_y_envio_fallido_reintentable(self):
        with tempfile.TemporaryDirectory() as root:
            inbox.send(root, "PM-a", "PO", "tarea")
            msgs = inbox.messages(root, "PM-a")
            with patch.object(inbox, "send", side_effect=OSError("fallo")):
                wake._notify_po_bloqueo(root, "PM-a", msgs, "sin sesión")
            self.assertEqual(inbox.messages(root, "PO"), [])
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                list(pool.map(lambda _: wake._notify_po_bloqueo(root, "PM-a", msgs, "sin sesión"), range(12)))
            self.assertEqual(len(inbox.messages(root, "PO")), 1)
            self.assertIn("[bloqueo] PM-a M-1", inbox.messages(root, "PO")[0].text)
            inbox.send(root, "PM-b", "PO", "otra tarea")
            wake._notify_po_bloqueo(root, "PM-b", inbox.messages(root, "PM-b"), "sin sesión")
            self.assertEqual(len(inbox.messages(root, "PO")), 2)
            self.assertEqual(len(Path(root, wake.BLOQUEO_LOG).read_text().splitlines()), 2)
            wake._notify_po_bloqueo(root, "PO", inbox.messages(root, "PO"), "sin sesión")
            self.assertEqual(len(inbox.messages(root, "PO")), 2)

    def test_umbral_no_alerta_reciente_si_alerta_antiguo(self):
        with tempfile.TemporaryDirectory() as root:
            inbox.send(root, "PM-a", "PO", "reciente")
            msgs = inbox.messages(root, "PM-a")
            wake._stale_bloqueos(root, "PM-a", msgs)
            self.assertEqual(inbox.messages(root, "PO"), [])
            old = (datetime.datetime.now().astimezone() - datetime.timedelta(minutes=20)).strftime(inbox.FMT)
            p = Path(inbox.path_of(root, "PM-a"))
            p.write_text(p.read_text().replace(msgs[0].sent, old))
            wake._stale_bloqueos(root, "PM-a", inbox.messages(root, "PM-a"))
            self.assertEqual(len(inbox.messages(root, "PO")), 1)
            self.assertIn("sin leer >10min", inbox.messages(root, "PO")[0].text)
