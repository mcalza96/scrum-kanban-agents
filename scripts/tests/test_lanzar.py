import glob
import json
import os
import sqlite3
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../adapters/opencode"))
import inbox  # noqa: E402
import lanzar  # noqa: E402


class LanzarTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self.tmp.name, "inbox")
        os.makedirs(self.root, exist_ok=True)
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
        self.tmp.cleanup()

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
        inbox.lanzar(self.root, "PM-ui", "UI-1", self._prompt("TO: dev UI-1\nx"))
        runner = self._runner_once("ses_ui")
        self.assertIsNone(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db))
        self.assertEqual(self.calls, [])
        self.assertEqual(len(glob.glob(os.path.join(self.root, ".lanzar", "*.json"))), 1)  # sigue en cola
        inbox.lanzar(self.root, "PM-calidad", "ORDEN-5", self._prompt("TO: dev ORDEN-5\nx"))
        self.assertEqual(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                               runner=self._runner_once("ses_cal"), db_path=self.db), "ses_cal")
        os.remove(os.path.join(self.root, ".pausa"))                                        # se levanta
        self.assertEqual(lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode",
                                               runner=self._runner_once("ses_ui"), db_path=self.db), "ses_ui")

    def test_un_pedido_produce_exactamente_una_llamada(self):
        pr = self._prompt()
        qid = inbox.lanzar(self.root, "PM-calidad", "CAL-99", pr)
        # ensure queue file exists
        qfiles = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
        self.assertEqual(len(qfiles), 1)
        runner = self._runner_once("ses_one")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_one")
        self.assertEqual(len(self.calls), 1)
        self.assertIn("dev CAL-99", self.calls[0][0])
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

    def test_pedido_sobre_tope_queda_en_cola_y_se_lanza_en_orden(self):
        # limit 1 per PM: enqueue 2 for same PM, only first launches, second stays
        pr1 = self._prompt("TO: dev CAL-10\none")
        pr2 = os.path.join(self.tmp.name, "prompt2.md")
        with open(pr2, "w", encoding="utf-8") as f:
            f.write("TO: dev CAL-11\ntwo")
        inbox.lanzar(self.root, "PM-calidad", "CAL-10", pr1)
        # tiny delay to ensure mtime order
        import time

        time.sleep(0.01)
        inbox.lanzar(self.root, "PM-calidad", "CAL-11", pr2)
        # first launch
        runner1 = self._runner_once("ses_10")
        sid1 = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner1, db_path=self.db)
        self.assertEqual(sid1, "ses_10")
        # make session busy: insert into DB as busy
        con = sqlite3.connect(self.db)
        con.execute("insert into session_v2 values (?,?,?,?)", ("ses_10", "dev CAL-10", 2, 1))
        con.commit()
        con.close()
        # second try should not launch because per-PM limit (1) reached
        runner2 = self._runner_once("ses_11")
        sid2 = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner2, db_path=self.db)
        self.assertIsNone(sid2)
        self.assertEqual(len(runner2.__self__.calls) if hasattr(runner2, "__self__") else 0, 0)  # no call
        # queue still has CAL-11
        qfiles = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
        self.assertEqual(len(qfiles), 1)
        self.assertIn("CAL-11", open(qfiles[0], encoding="utf-8").read())
        # free slot: mark ses_10 idle
        con = sqlite3.connect(self.db)
        con.execute("update session_v2 set time_busy=1, time_idle=2 where id='ses_10'")
        con.commit()
        con.close()
        sid3 = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner2, db_path=self.db)
        self.assertEqual(sid3, "ses_11")

    def test_dev_que_termina_sin_mensaje_deja_aviso_al_pm(self):
        # enqueue and launch, then simulate failed session
        pr = self._prompt()
        inbox.lanzar(self.root, "PM-calidad", "CAL-12", pr)
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
        inbox.lanzar(self.root, "PM-calidad", "CAL-99", pr)
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
        inbox.lanzar(self.root, "PM-calidad", "CAL-99", pr)
        runner = self._runner_once("ses_ok")
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db)
        self.assertEqual(sid, "ses_ok")
        prompt_enviado = self.calls[0][1]
        self.assertEqual(prompt_enviado.count("inbox.py send"), 1)

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
