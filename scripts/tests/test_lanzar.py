import glob
import json
import os
import sqlite3
import sys
import tempfile
import unittest
import threading
from pathlib import Path
from unittest import mock

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../adapters/opencode"))
import inbox  # noqa: E402
import lanzar  # noqa: E402
import worktree_fixture


class LanzarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "inbox")
        os.makedirs(self.root, exist_ok=True)
        self.project = self.tmp.name
        worktree_fixture.init(self.project)
        self.db = os.path.join(self.tmp.name, "opencode.db")
        # minimal DB for lanzar
        con = sqlite3.connect(self.db)
        con.executescript(
            "create table session_v2 (id text, title text, time_busy int, time_idle int);"
            "create table session_message (session_id text, type text, time_created int, data text);"
        )
        con.commit()
        con.close()
        lanzar.DB = self.db
        # patch max for tests? use defaults 1/4
        self.calls = []

    def tearDown(self):
        worktree_fixture.cleanup(self.project)
        self.tmp.cleanup()

    def enqueue(self, *args, **kwargs):
        return inbox.lanzar(*args, **kwargs, project=self.project)

    def _prompt(self, text="TO: dev CAL-99\nhello"):
        p = os.path.join(self.tmp.name, "prompt.md")
        with open(p, "w", encoding="utf-8") as f:
            f.write(text)
        return p

    def _runner_once(self, session_id="ses_test123"):
        def runner(cmd, prompt_text):
            self.calls.append((cmd, prompt_text))
            out = json.dumps({"sessionID": session_id}) + "\n"
            return out, "", 0

        return runner

    def test_raiz_que_no_es_el_buzon_se_rechaza_sin_encolar(self):
        pr = self._prompt()
        project = self.tmp.name  # contiene inbox/: es el proyecto
        with self.assertRaisesRegex(ValueError, "quisiste decir .*inbox"):
            inbox.lanzar(project, "PM-calidad", "CAL-99", pr)
        with self.assertRaisesRegex(ValueError, "no existe"):
            inbox.lanzar(os.path.join(self.tmp.name, "nada"), "PM-calidad", "CAL-99", pr)
        self.assertFalse(os.path.exists(os.path.join(project, ".lanzar")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "nada")))

    def test_con_pausa_solo_se_lanzan_los_devs_de_su_dueno(self):
        # 2026-11-05: .pausa era una convención que ningún código leía.
        with open(os.path.join(self.root, ".pausa"), "w") as f:
            f.write("PM-calidad ORDEN-5\n")
        self.enqueue(self.root, "PM-ui", "UI-1", self._prompt("TO: dev UI-1\nx"))
        runner = self._runner_once("ses_ui")
        self.assertIsNone(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(glob.glob(os.path.join(self.root, ".lanzar", "*.json"))), 1)  # sigue en cola
        self.enqueue(self.root, "PM-calidad", "ORDEN-5", self._prompt("TO: dev ORDEN-5\nx"))
        self.assertEqual(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                               runner=self._runner_once("ses_cal"), db_path=self.db), "ses_cal")
        os.remove(os.path.join(self.root, ".pausa"))                                        # se levanta
        self.assertEqual(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                               runner=self._runner_once("ses_ui"), db_path=self.db), "ses_ui")

    def test_un_pedido_produce_exactamente_una_llamada(self):
        pr = self._prompt()
        qid = self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
        # ensure queue file exists
        qfiles = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
        self.assertEqual(len(qfiles), 1)
        runner = self._runner_once("ses_one")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_one")
        self.assertEqual(len(self.calls), 1)
        # El ticket sigue visible y el pedido identifica la sesión sin confundir devoluciones.
        self.assertIn(f"dev CAL-99 [{qid}]", self.calls[0][0])
        # queue consumed
        qfiles2 = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
        self.assertEqual(len(qfiles2), 0)
        # second call without new enqueue does nothing
        sid2 = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertIsNone(sid2)
        self.assertEqual(len(self.calls), 1)
        # log has one entry with pm->session
        with open(os.path.join(self.root, ".lanzar", "log"), encoding="utf-8") as f:
            lines = [json.loads(l) for l in f]
        self.assertEqual(lines[0]["pm"], "PM-calidad")
        self.assertEqual(lines[0]["session"], "ses_one")
        self.assertEqual(lines[0]["ticket"], "CAL-99")

    def test_un_pm_lanza_varios_devs_y_solo_el_global_encola(self):
        # 2026-11-05 el dueño quitó el tope por PM: un segundo dev del mismo PM sale con el primero
        # vivo; lo único que encola es MAX_GLOBAL, en orden FIFO.
        import time
        prompts = []
        for n in (10, 11, 12):
            pr = os.path.join(self.tmp.name, f"prompt{n}.md")
            with open(pr, "w", encoding="utf-8") as f:
                f.write(f"TO: dev CAL-{n}\nx")
            self.enqueue(self.root, "PM-calidad", f"CAL-{n}", pr)
            time.sleep(0.01)
        self.assertFalse(hasattr(lanzar, "MAX_PER_PM"))
        old = lanzar.MAX_GLOBAL
        lanzar.MAX_GLOBAL = 2
        try:
            for n in (10, 11):
                sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                            runner=self._runner_once(f"ses_{n}"), db_path=self.db)
                self.assertEqual(sid, f"ses_{n}")
                con = sqlite3.connect(self.db)
                con.execute("insert into session_v2 values (?,?,?,?)", (f"ses_{n}", f"dev CAL-{n}", 2, 1))
                con.commit()
                con.close()
            self.assertIsNone(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                                    runner=self._runner_once("ses_12"), db_path=self.db))
            qfiles = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
            self.assertEqual(len(qfiles), 1)
            self.assertIn("CAL-12", open(qfiles[0], encoding="utf-8").read())
            con = sqlite3.connect(self.db)
            con.execute("update session_v2 set time_busy=1, time_idle=2 where id='ses_10'")
            con.commit()
            con.close()
            self.assertEqual(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                                   runner=self._runner_once("ses_12"), db_path=self.db), "ses_12")
        finally:
            lanzar.MAX_GLOBAL = old

    def test_dev_que_termina_sin_mensaje_deja_aviso_al_pm(self):
        # enqueue and launch, then simulate failed session
        pr = self._prompt()
        self.enqueue(self.root, "PM-calidad", "CAL-12", pr)
        runner = self._runner_once("ses_fail")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_fail")
        # insert failed idle message
        con = sqlite3.connect(self.db)
        con.execute("insert into session_v2 values (?,?,?,?)", ("ses_fail", "dev CAL-12", 1, 2))
        con.execute(
            "insert into session_message values (?,?,?,?)",
            ("ses_fail", "idle", 1000, json.dumps({"outcome": "failed", "error": {"message": "limite del proveedor"}})),
        )
        con.commit()
        con.close()
        # session is now idle+failed, not busy, so active counts 0
        # reap should send mailbox to PM
        lanzar._reap_failed(self.root, self.db)
        msgs = inbox.messages(self.root, "PM-calidad")
        self.assertTrue(any("murió sin reportar" in m.text and "ses_fail" in m.text for m in msgs))
        self.assertTrue(any("limite del proveedor" in m.text for m in msgs))
        # second reap does not duplicate
        lanzar._reap_failed(self.root, self.db)
        msgs2 = inbox.messages(self.root, "PM-calidad")
        self.assertEqual(len(msgs), len(msgs2))

    def test_prompt_sin_bloque_de_cierre_se_completa_con_inbox_send(self):
        # M-88 arreglo 1: todo prompt lanzado debe llevar inbox.py send --to <PM>
        pr = self._prompt("TO: dev CAL-99\nsin bloque")
        self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
        runner = self._runner_once("ses_close")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_close")
        self.assertEqual(len(self.calls), 1)
        prompt_enviado = self.calls[0][1]
        self.assertIn("inbox.py send", prompt_enviado)
        self.assertIn("--to PM-calidad", prompt_enviado)
        self.assertIn(self.root, prompt_enviado)
        # helpers directos
        self.assertFalse(lanzar._has_close_block("sin nada", "PM-calidad"))
        self.assertTrue(lanzar._has_close_block(f"inbox.py send --to PM-calidad", "PM-calidad"))
        injected = lanzar._ensure_close_block("hola", "PM-calidad", self.root, "CAL-99")
        self.assertIn("inbox.py send --to PM-calidad", injected)

    def test_prompt_con_bloque_no_se_duplica(self):
        pr = self._prompt(f"hola\npython3 inbox.py send --to PM-calidad --from dev-CAL-99 {self.root} aviso")
        self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
        runner = self._runner_once("ses_ok")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_ok")
        prompt_enviado = self.calls[0][1]
        self.assertEqual(prompt_enviado.count("inbox.py send"), 1)

    def test_sin_id_no_reencola_ni_repite_y_conserva_reserva(self):
        self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        runner = mock.Mock(return_value=("", "fallo simulado", 1))
        self.assertIsNone(lanzar.try_launch_one(self.root, runner=runner, db_path=self.db))
        self.assertEqual(len(list(Path(self.root, ".lanzar").glob("*.json"))), 0)
        claims = list(Path(self.root, ".lanzar").glob("*.claim"))
        self.assertEqual(json.loads(claims[0].read_text())["status"], "uncertain")
        self.assertEqual(lanzar._active_counts(self.root, self.db)[0], 1)
        self.assertIsNone(lanzar.try_launch_one(self.root, runner=runner, db_path=self.db))
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(len(inbox.messages(self.root, "PM-calidad")), 1)
        self.assertNotIn("db_path", inbox.messages(self.root, "PM-calidad")[0].text)
        with self.assertRaisesRegex(ValueError, "en cola"):
            self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt(), retry=True)

    def test_recupera_solo_la_sesion_del_pedido_actual(self):
        with sqlite3.connect(self.db) as db:
            db.execute("insert into session_v2 values ('ses_vieja', 'dev CAL-99', 1, 2)")
        self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        def runner(cmd, text):
            title = cmd[cmd.index("--title") + 1]
            with sqlite3.connect(self.db) as db:
                db.execute("insert into session_v2 values (?, ?, 2, 1)", ("ses_actual", title))
            return "sin evento JSON", "", 0
        self.assertEqual(lanzar.try_launch_one(self.root, runner=runner, db_path=self.db), "ses_actual")
        self.assertEqual(inbox.launch_records(self.root)[0]["session"], "ses_actual")

    def test_no_adopta_sesion_anterior_y_reconcilia_despues_sin_relanzar(self):
        with sqlite3.connect(self.db) as db:
            db.execute("insert into session_v2 values ('ses_vieja', 'dev CAL-99', 1, 2)")
        self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        runner = mock.Mock(return_value=("", "salida perdida", 0))
        self.assertIsNone(lanzar.try_launch_one(self.root, runner=runner, db_path=self.db))
        claim = next(Path(self.root, ".lanzar").glob("*.claim"))
        title = json.loads(claim.read_text())["title"]
        with sqlite3.connect(self.db) as db:
            db.execute("insert into session_v2 values (?, ?, 2, 1)", ("ses_recuperada", title))
        lanzar.try_launch_one(self.root, runner=runner, db_path=self.db)
        self.assertEqual(runner.call_count, 1)
        self.assertFalse(claim.exists())
        self.assertEqual(inbox.launch_records(self.root)[0]["session"], "ses_recuperada")

    def test_dos_lanzadores_respetan_cupo_durante_arranque(self):
        for n in (1, 2):
            self.enqueue(self.root, "PM-calidad", f"CAL-{n}", self._prompt())
        entered, release = threading.Event(), threading.Event()
        def first(cmd, text):
            entered.set()
            if not release.wait(5):
                raise RuntimeError("la prueba no liberó el runner")
            return '{"sessionID":"ses_primera"}', "", 0
        with mock.patch.object(lanzar, "MAX_GLOBAL", 1):
            worker = threading.Thread(target=lanzar.try_launch_one,
                                      args=(self.root,), kwargs={"runner": first, "db_path": self.db})
            worker.start()
            try:
                self.assertTrue(entered.wait(3))
                second = mock.Mock(return_value=('{"sessionID":"ses_segunda"}', "", 0))
                self.assertIsNone(lanzar.try_launch_one(self.root, runner=second, db_path=self.db))
                second.assert_not_called()
                self.assertEqual(len(list(Path(self.root, ".lanzar").glob("*.json"))), 1)
            finally:
                release.set()
                worker.join(5)
            self.assertFalse(worker.is_alive())

    def test_registra_id_antes_de_que_termine_el_cli_real(self):
        fifo = Path(self.tmp.name, "release")
        os.mkfifo(fifo)
        fd = os.open(fifo, os.O_RDWR | os.O_NONBLOCK)
        binary = Path(self.tmp.name, "fake-opencode")
        binary.write_text(f"#!{sys.executable}\nimport json\nprint(json.dumps({{'sessionID':'ses_stream'}}), flush=True)\nwith open({str(fifo)!r}) as f: f.read(1)\n")
        binary.chmod(0o700)
        self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        recorded = threading.Event()
        actual = lanzar._record_session
        results = []
        def record(*args):
            sid = actual(*args)
            recorded.set()
            return sid
        with mock.patch.object(lanzar, "_record_session", side_effect=record):
            worker = threading.Thread(target=lambda: results.append(lanzar.try_launch_one(
                self.root, opencode_bin=str(binary), db_path=self.db)))
            worker.start()
            try:
                self.assertTrue(recorded.wait(3), "el ID debe registrarse mientras el proceso sigue vivo")
                self.assertTrue(worker.is_alive())
                self.assertEqual(inbox.launch_records(self.root)[0]["session"], "ses_stream")
                self.assertEqual(lanzar._active_counts(self.root, self.db)[0], 1)
            finally:
                os.write(fd, b"x")
                worker.join(5)
                os.close(fd)
            self.assertFalse(worker.is_alive())
        self.assertEqual(results, ["ses_stream"])

    def test_caida_tras_log_no_duplica_registro(self):
        self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        lanzar.try_launch_one(self.root, runner=lambda *a: ("", "sin ID", 1), db_path=self.db)
        claim = next(Path(self.root, ".lanzar").glob("*.claim"))
        entry = json.loads(claim.read_text())
        entry["_path"] = str(claim)
        with mock.patch.object(lanzar.os, "remove", side_effect=OSError("caída antes de borrar claim")):
            with self.assertRaises(OSError):
                lanzar._record_session(self.root, entry, "ses_guardada")
        self.assertTrue(claim.exists())
        lanzar.try_launch_one(self.root, runner=mock.Mock(), db_path=self.db)
        self.assertFalse(claim.exists())
        self.assertEqual(len(inbox.launch_records(self.root)), 1)

    def test_caida_antes_de_log_retiene_cupo_y_avisa_una_vez(self):
        qid = self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())
        queued = Path(self.root, ".lanzar", qid + ".json")
        entry = json.loads(queued.read_text())
        entry.update(status="launching", launcher_pid=99999999,
                     title=f"dev CAL-99 [{qid}]")
        claim = queued.with_suffix(".json.claim")
        inbox.write_request(str(queued), entry)
        queued.rename(claim)
        runner = mock.Mock()
        with mock.patch.object(lanzar.os, "kill", side_effect=ProcessLookupError):
            lanzar.try_launch_one(self.root, runner=runner, db_path=self.db)
            lanzar.try_launch_one(self.root, runner=runner, db_path=self.db)
        runner.assert_not_called()
        self.assertEqual(json.loads(claim.read_text())["status"], "uncertain")
        self.assertEqual(lanzar._active_counts(self.root, self.db)[0], 1)
        self.assertEqual(len(inbox.messages(self.root, "PM-calidad")), 1)

    def test_reporte_entregado_bloquea_relanzar_aunque_runtime_no_tenga_fila(self):
        record = {"ticket": "CAL-99", "session": "ses_fuera", "expected": "reporte.md",
                  "id": "pedido-previo", "ts": "2026-01-01T00:00:00-03:00"}
        Path(self.root, ".lanzar").mkdir()
        Path(self.tmp.name, "reporte.md").write_text("Entrega lista para revisar")
        Path(self.root, ".lanzar", "log").write_text(json.dumps(record) + "\n")
        with mock.patch.dict(os.environ, {"OPENCODE_DB": self.db}):
            with self.assertRaisesRegex(ValueError, "pendiente de revisión"):
                self.enqueue(self.root, "PM-calidad", "CAL-99", self._prompt())

    def test_id_registrado_no_observable_bloquea_incluso_retry(self):
        pr = self._prompt()
        self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
        lanzar.try_launch_one(self.root, runner=self._runner_once("ses_no_visible"), db_path=self.db)
        with mock.patch.dict(os.environ, {"OPENCODE_DB": self.db}):
            with self.assertRaisesRegex(ValueError, "no observable"):
                self.enqueue(self.root, "PM-calidad", "CAL-99", pr, retry=True)
        self.assertEqual(len(list(Path(self.root, ".lanzar").glob("*.json"))), 0)

    def test_entrega_pendiente_exige_devolucion_explicita(self):
        pr = self._prompt()
        self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
        lanzar.try_launch_one(self.root, runner=self._runner_once("ses_ok"), db_path=self.db)
        with sqlite3.connect(self.db) as db:
            db.execute("insert into session_v2 values ('ses_ok', 'dev CAL-99', 1, 2)")
            db.execute("insert into session_message values (?, 'idle', 1000, ?)",
                       ("ses_ok", json.dumps({"outcome": "succeeded"})))
        with mock.patch.dict(os.environ, {"OPENCODE_DB": self.db}):
            with self.assertRaisesRegex(ValueError, "pendiente de revisión"):
                self.enqueue(self.root, "PM-calidad", "CAL-99", pr)
            retry = self.enqueue(self.root, "PM-calidad", "CAL-99", pr, retry=True)
        request = Path(self.root, ".lanzar", retry + ".json")
        self.assertTrue(json.loads(request.read_text())["retry"])
        self.assertNotEqual(retry, inbox.launch_records(self.root)[0]["id"])

    def test_estatico_inbox_js_lanza_solo_desde_servicio_y_no_hay_sleep_en_loop(self):
        p = os.path.join(HERE, "../../adapters/opencode/inbox.js")
        txt = open(p, encoding="utf-8").read()
        # debe lanzar desde servicio (spawn lanzar.py) y no desde shell del PM
        self.assertIn("lanzar.py", txt)
        self.assertIn("spawn", txt)
        # no hay `sleep` en un loop (el candado estático del ticket)
        # buscamos sleep dentro de un while/for
        import re
        self.assertIsNone(re.search(r"\bwhile\b.*\bsleep\b", txt, re.I | re.S))
        self.assertIsNone(re.search(r"\bfor\b.*\bsleep\b", txt, re.I | re.S))
        # lanzar.py tampoco tiene sleep en loop
        py = open(os.path.join(HERE, "../../adapters/opencode/lanzar.py"), encoding="utf-8").read()
        self.assertNotIn("time.sleep", py)
        self.assertIsNone(re.search(r"\bwhile\b.*\bsleep\b", py, re.I | re.S))


if __name__ == "__main__":
    unittest.main()
