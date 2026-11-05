"""Candado: una ruta vieja del buzón nunca recrea la carpeta (buzón huérfano).

2026-11-05, dos veces: en la mañana los plist con la ruta vieja recrearon inbox/ en la raíz; a las
13:01 el plugin de opencode, con la ruta fija desde las 11:55, recreó portafolio/gestion/ cada minuto
después de que .scrum.toml apuntara a portafolio-gestion, y nadie vigilaba el buzón nuevo.
"""
import os
import re
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../adapters/opencode"))
import inbox  # noqa: E402
import lanzar  # noqa: E402
import wake  # noqa: E402

PLUGIN_DIR = os.path.join(HERE, "../../adapters/opencode")


class BuzonHuerfanoTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, "app")
        self.nuevo = os.path.join(self.tmp, "app-gestion", "inbox")
        os.makedirs(os.path.join(self.repo, ".git"))
        with open(os.path.join(self.repo, ".scrum.toml"), "w") as f:
            f.write(f'gestion = "{os.path.dirname(self.nuevo)}"\n')
        self.viejo = os.path.join(self.repo, "gestion", "inbox")  # la ruta de antes de mudarse

    def test_send_a_ruta_vieja_no_crea_nada(self):
        with self.assertRaises(FileNotFoundError):
            inbox.send(self.viejo, "PM-ui", "PO", "hola")
        self.assertFalse(os.path.exists(os.path.join(self.repo, "gestion")))

    def test_send_al_buzon_que_nombra_scrum_toml_lo_crea(self):
        inbox.send(self.nuevo, "PM-ui", "PO", "hola")  # proyecto nuevo: el primer send crea el buzón
        self.assertTrue(os.path.isfile(os.path.join(self.nuevo, "PM-ui.md")))

    def test_wake_y_lanzar_no_recrean_la_ruta_vieja(self):
        with self.assertRaises(SystemExit):
            wake.mailbox_root(self.viejo)
        db = os.path.join(self.tmp, "opencode.db")
        sqlite3.connect(db).close()
        self.assertEqual(wake.reap_finished(self.viejo, db_path=db), [])
        with self.assertRaises(FileNotFoundError):
            lanzar._append_log(self.viejo, {"x": 1})
        self.assertFalse(os.path.exists(os.path.join(self.repo, "gestion")))

    def test_el_plugin_no_fija_la_ruta_ni_crea_carpetas(self):
        with open(os.path.join(PLUGIN_DIR, "inbox.js"), encoding="utf-8") as f:
            js = f.read()
        self.assertIn("const root = () => join(raizGestion(PROJECT), \"inbox\");", js)
        self.assertEqual(js.count("raizGestion(PROJECT)"), 1)       # solo dentro de root()
        self.assertIsNone(re.search(r"\bROOT\b", js))                # ninguna constante de ruta
        self.assertIn("setInterval(() => {\n      arm();", js)       # re-vigila si el buzón se mudó
        for name in os.listdir(PLUGIN_DIR):
            if name.endswith(".js"):
                with open(os.path.join(PLUGIN_DIR, name), encoding="utf-8") as f:
                    self.assertNotIn("mkdirSync", f.read(), name)


if __name__ == "__main__":
    unittest.main()
