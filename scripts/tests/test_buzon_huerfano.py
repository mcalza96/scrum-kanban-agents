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
        self.assertEqual(wake.reap_finished(self.repo, db_path=db), [])  # buzón aún no creado
        self.assertFalse(os.path.exists(self.nuevo))
        with self.assertRaises(FileNotFoundError):
            lanzar._append_log(self.viejo, {"x": 1})
        self.assertFalse(os.path.exists(os.path.join(self.repo, "gestion")))

    def test_la_cli_resuelve_el_repo_y_rechaza_rutas_que_no_existen(self):
        # 2026-11-05: `read <repo> PO` respondía «no unread messages» con correo en gestion/inbox.
        inbox.send(self.nuevo, "PO", "PM-ui", "hola")
        self.assertEqual(inbox.mailbox_arg(self.repo), self.nuevo)       # el repo → su buzón
        self.assertEqual(inbox.main(["read", self.repo, "PO"]), 0)
        self.assertEqual(inbox.main(["read", self.viejo, "PO"]), 2)      # ruta vieja: error
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


class GestionEnRepoHermanoTest(unittest.TestCase):
    """2026-11-05: con el buzón en portafolio-gestion, wake sacaba el repo del buzón (repo_of) y
    caía en el repo de gestión: la alarma de candados buscaba ahí los commits de la app y callaba
    (89c69be4 dejó `excluidas.length >= 0` sin aviso) y el reap miraba los worktrees equivocados."""

    def test_reap_finished_ve_los_commits_de_la_app(self):
        import json
        import subprocess
        tmp = tempfile.mkdtemp()
        app, gest = os.path.join(tmp, "app"), os.path.join(tmp, "app-gestion")
        for d in (app, gest):
            os.makedirs(d)
            subprocess.run(["git", "init", "-q", d], check=True)
        with open(os.path.join(app, ".scrum.toml"), "w") as f:
            f.write(f'gestion = "{gest}"\n')
        root = os.path.join(gest, "inbox")
        inbox.send(root, "PM-calidad", "PO", "hola")
        def commit(text):
            os.makedirs(os.path.join(app, "lib"), exist_ok=True)
            with open(os.path.join(app, "lib", "a.test.ts"), "w") as f:
                f.write(text)
            g = ["git", "-C", app, "-c", "user.name=t", "-c", "user.email=t@t"]
            subprocess.run(g + ["add", "-A"], check=True)
            subprocess.run(g + ["commit", "-qm", "x"], check=True)
            return subprocess.run(["git", "-C", app, "rev-parse", "--short", "HEAD"],
                                  capture_output=True, text=True).stdout.strip()
        commit("assert.ok(n > 100);\n")
        sha = commit("assert.ok(n >= 0);\n")
        db_path = os.path.join(tmp, "oc.db")
        db = sqlite3.connect(db_path)
        db.executescript(
            "create table session_v2 (id text, parent_id text, title text, directory text,"
            " time_archived int, time_busy int, time_idle int, time_created int);"
            "create table session_message (session_id text, type text, time_created int, data text);")
        now = int(__import__("time").time() * 1000)
        db.execute("insert into session_v2 values (?,?,?,?,?,?,?,?)",
                   ("ses_pm", None, "PM-calidad", app, None, 1, 2, now))
        db.execute("insert into session_v2 values (?,?,?,?,?,?,?,?)",
                   ("ses_dev", "ses_pm", "dev CAL-1", app, None, now - 5000, now - 1000, now - 9000))
        tool = {"type": "tool", "state": {"input": {"command": "git commit -m x"},
                                          "content": [{"type": "text", "text": f"[main {sha}] x"}]}}
        db.execute("insert into session_message values (?,?,?,?)",
                   ("ses_dev", "user", 0, json.dumps({"text": "TO: dev CAL-1"})))
        db.execute("insert into session_message values (?,?,?,?)",
                   ("ses_dev", "assistant", 1, json.dumps({"content": [tool]})))
        db.execute("insert into session_message values (?,?,?,?)",
                   ("ses_dev", "idle", 10, json.dumps({"outcome": "succeeded"})))
        db.commit()
        wake.reap_finished(app, db_path)
        avisos = [m.text for pm in inbox.mailboxes(root) for m in inbox.messages(root, pm)
                  if m.sender == "lanzador"]
        self.assertEqual(len(avisos), 1, avisos)
        self.assertIn("Candados tocados", avisos[0])
        self.assertFalse(hasattr(wake, "repo_of"))  # nada vuelve a sacar el repo del buzón


if __name__ == "__main__":
    unittest.main()
