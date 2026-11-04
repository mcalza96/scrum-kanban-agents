import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "../../adapters/opencode"))
import inbox  # noqa: E402
import lanzar  # noqa: E402
import wake  # noqa: E402


class Cal29VerificaDoneTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.project = os.path.join(self.tmp.name, "proj")
        self.root = os.path.join(self.project, "inbox")
        os.makedirs(self.root, exist_ok=True)
        os.makedirs(os.path.join(self.project, "epic-calidad"), exist_ok=True)
        self.db_path = os.path.join(self.tmp.name, "opencode.db")
        wake.DB = self.db_path
        lanzar.DB = self.db_path
        self.db = sqlite3.connect(self.db_path)
        self.db.executescript(
            "create table session_v2 (id text, parent_id text, title text, directory text, time_archived int, time_busy int, time_idle int, time_created int);"
            "create table session_message (session_id text, type text, time_created int, data text);"
        )
        self.db.commit()
        # helper to add parent PM session for resolve
        self.db.execute(
            "insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
            ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, int(time.time()*1000)),
        )
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})),
        )
        self.db.commit()

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        self.tmp.cleanup()

    def add_dev(self, sid, ticket="CAL-29", parent="ses_parent", outcome="succeeded", with_send=False, busy=1, idle=None, title=None):
        if idle is None:
            idle = int(time.time()*1000)
        if title is None:
            title = f"dev {ticket}"
        self.db.execute(
            "insert into session_v2 (id, parent_id, title, directory, time_archived, time_busy, time_idle, time_created) values (?,?,?,?,?,?,?,?)",
            (sid, parent, title, self.project, None, busy, idle, int(time.time()*1000)),
        )
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            (sid, "user", 1, json.dumps({"text": f"TO: dev {ticket} prompt"})),
        )
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            (sid, "idle", 10, json.dumps({"outcome": outcome})),
        )
        if with_send:
            tool = {"type": "tool", "state": {"input": {"command": "inbox.py send --to PM-calidad --from dev inbox 'reporte'"}}}
            self.db.execute(
                "insert into session_message values (?,?,?,?)",
                (sid, "assistant", 5, json.dumps({"content": [tool]})),
            )
        self.db.commit()

    def write_log(self, sid, ticket="CAL-29", pm="PM-calidad", expected=None):
        os.makedirs(os.path.join(self.root, ".lanzar"), exist_ok=True)
        lp = os.path.join(self.root, ".lanzar", "log")
        entry = {"ts": "2026-11-03T19:00:00-03:00", "pm": pm, "ticket": ticket, "session": sid, "prompt": "/tmp/prompt.md", "from": pm, "id": f"{ticket}--{pm}--x"}
        if expected:
            entry["expected"] = expected
        with open(lp, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # CAL-29 candado 1: succeeded sin reporte ni artefacto no cierra fila (no cuenta)
    def test_succeeded_sin_evidencia_no_cierra_fila(self):
        self.add_dev("ses_no_evid", ticket="CAL-90")
        self.write_log("ses_no_evid", ticket="CAL-90")
        # sin reported, sin archivo, sin send -> no evidencia
        has = wake._has_delivery_evidence(self.db, "ses_no_evid", "CAL-90", self.project, self.root)
        self.assertFalse(has, "sin evidencia no debe contar como éxito (CAL-29)")
        # reap debe avisar "terminó sin entregar" y no marcar como done
        out = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out), 1)
        self.assertIn("terminó sin entregar", inbox.messages(self.root, "PM-calidad")[0].text)

    # CAL-29 candado 1 inverso: succeeded con reporte válido sí cuenta
    def test_succeeded_con_reporte_si_cuenta(self):
        # crea STATUS con reported para CAL-91
        with open(os.path.join(self.project, "epic-calidad", "STATUS.md"), "w", encoding="utf-8") as f:
            f.write("| Ticket | Estado |\n| CAL-91 | reported |\n")
        self.add_dev("ses_con_report", ticket="CAL-91")
        self.write_log("ses_con_report", ticket="CAL-91")
        has = wake._has_delivery_evidence(self.db, "ses_con_report", "CAL-91", self.project, self.root)
        self.assertTrue(has, "con reporte válido debe contar como éxito")
        out = wake.reap_finished(self.root, self.db_path)
        # no debe avisar porque ya tiene entrega
        self.assertEqual(len(out), 0)
        self.assertEqual(len(inbox.messages(self.root, "PM-calidad")), 0)

    # CAL-29 candado 2: succeeded con artefacto archivo sí cuenta (expected del log)
    def test_succeeded_con_artefacto_si_cuenta(self):
        # crea archivo esperado
        os.makedirs(os.path.join(self.project, "epic-calidad", "reports"), exist_ok=True)
        with open(os.path.join(self.project, "epic-calidad", "reports", "CAL-92.md"), "w", encoding="utf-8") as f:
            f.write("# CAL-92 reporte\n")
        self.add_dev("ses_con_file", ticket="CAL-92")
        self.write_log("ses_con_file", ticket="CAL-92", expected="epic-calidad/reports/CAL-92.md")
        has = wake._has_delivery_evidence(self.db, "ses_con_file", "CAL-92", self.project, self.root)
        self.assertTrue(has, "con artefacto archivo debe contar")
        out = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out), 0)

    # CAL-29 candado 3: barrido no avisa por sesiones fuera de ventana
    def test_barrido_no_avisa_fuera_ventana(self):
        old_idle = int(time.time()*1000) - 25*3600*1000  # 25h atrás
        self.add_dev("ses_old", ticket="CAL-93", idle=old_idle)
        self.write_log("ses_old", ticket="CAL-93")
        out = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out), 0, "sesión fuera de ventana 24h no debe avisar")
        self.assertEqual(len(inbox.messages(self.root, "PM-calidad")), 0)

    # CAL-29 candado 3: barrido no avisa por sesiones fuera de lanzar/log
    def test_barrido_no_avisa_fuera_log(self):
        self.add_dev("ses_fuera_log", ticket="CAL-94")
        # no escribo log para este sid — está fuera de lanzar/log
        # escribo otro log para que exista log no vacío
        self.write_log("ses_otro", ticket="CAL-99")
        out = wake.reap_finished(self.root, self.db_path)
        # solo ses_otro no existe como dev, entonces nada
        self.assertEqual(len(out), 0)
        self.assertEqual(len(inbox.messages(self.root, "PM-calidad")), 0)

    # CAL-29 candado: lanzar registra entrega esperada sin bloquear
    def test_lanzar_registra_expected(self):
        prompt = os.path.join(self.tmp.name, "prompt.md")
        with open(prompt, "w", encoding="utf-8") as f:
            f.write("TO: dev CAL-95\ntarea")
        qid = inbox.lanzar(self.root, "PM-calidad", "CAL-95", prompt)
        # verifica que el json en cola tiene expected
        import glob
        qfiles = glob.glob(os.path.join(self.root, ".lanzar", "*.json"))
        self.assertEqual(len(qfiles), 1)
        with open(qfiles[0], encoding="utf-8") as f:
            data = json.load(f)
        self.assertIn("expected", data)
        self.assertIn("CAL-95", data["expected"])
        # try_launch debe crear log con expected y no bloquear
        def runner(cmd, text):
            return json.dumps({"sessionID": "ses_lanz95"}) + "\n", "", 0
        sid = lanzar.try_launch_one(self.root, opencode_bin="/fake/opencode", runner=runner, db_path=self.db_path)
        self.assertEqual(sid, "ses_lanz95")
        with open(os.path.join(self.root, ".lanzar", "log"), encoding="utf-8") as f:
            log = [json.loads(l) for l in f]
        self.assertTrue(any(e.get("expected") and "CAL-95" in e["expected"] for e in log))

    # dedup con flock y un solo aviso
    def test_un_solo_aviso_con_flock(self):
        self.add_dev("ses_dedup", ticket="CAL-96")
        self.write_log("ses_dedup", ticket="CAL-96")
        out1 = wake.reap_finished(self.root, self.db_path)
        out2 = wake.reap_finished(self.root, self.db_path)
        self.assertEqual(len(out1), 1)
        self.assertEqual(len(out2), 0)
        aviso = os.path.join(self.root, ".lanzar", ".aviso-dev")
        with open(aviso, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertEqual(lines.count("ses_dedup"), 1)

    # E1 M-70: filtro anti-timestamps en _commits_of — 1793725787505 no es sha
    def test_commits_of_filtra_timestamp_13digitos(self):
        # datos reales: session_message con git commit + timestamp 13 dígitos y sha abc1234
        self.add_dev("ses_e1", ticket="CAL-29")
        # mensaje assistant que contiene 'git commit' para que _commits_of lo escanee,
        # con un sha real abc1234 y el timestamp decimal 1793725787505 juntos
        data = json.dumps({
            "content": [
                {"type": "tool", "state": {"input": {"command": "git commit -m 'feat abc1234'"}}},
            ],
            "extra": "1793725787505 1793725787505 abc1234",
        })
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            ("ses_e1", "assistant", 6, data),
        )
        # también caso con timestamp como número suelto en el json (time_created estilo)
        data2 = json.dumps({"content": [{"type": "tool", "state": {"input": {"command": "git commit"}} }], "ts": 1793725787505, "note": "sha abc1234 y ts 1793725787505"})
        self.db.execute(
            "insert into session_message values (?,?,?,?)",
            ("ses_e1", "assistant", 7, data2),
        )
        self.db.commit()
        shas = wake._commits_of(self.db, "ses_e1")
        self.assertIn("abc1234", [s.lower() for s in shas], "debe devolver sha real abc1234")
        self.assertNotIn("1793725787505", shas, "no debe devolver timestamp 13 dígitos")
        # verifica regex explícita existe
        self.assertTrue(hasattr(wake, "_TIMESTAMP_13_RE"), "wake debe exponer regex _TIMESTAMP_13_RE")
        self.assertTrue(wake._TIMESTAMP_13_RE.match("1793725787505"))
        self.assertIsNone(wake._TIMESTAMP_13_RE.match("abc1234"))
        # sha hex largo real no se filtra (40 hex)
        self.assertIsNone(wake._TIMESTAMP_13_RE.match("a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"))
        # verifica con dato que parece timestamp pero es sha con letras no se filtra
        self.assertIsNone(wake._TIMESTAMP_13_RE.match("179372578750a"))

    # E2 M-41/M-42: carrera dedup .aviso-dev — dos reap_finished concurrentes misma sid
    def test_carrera_dedup_concurrente_m41_m42(self):
        # usa datos reales de lanzar/log + session_v2 dentro de ventana 24h
        self.add_dev("ses_m41", ticket="M-41")
        self.write_log("ses_m41", ticket="M-41")
        # segunda entrada no necesaria — ambos reap sobre misma sid
        barrier = threading.Barrier(2)
        out1: list[str] = []
        out2: list[str] = []

        def run1():
            barrier.wait()
            out1.extend(wake.reap_finished(self.root, self.db_path))

        def run2():
            barrier.wait()
            out2.extend(wake.reap_finished(self.root, self.db_path))

        t1 = threading.Thread(target=run1)
        t2 = threading.Thread(target=run2)
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)
        self.assertEqual(len(out1) + len(out2), 1, f"M-41+M-42 concurrentes deben generar un solo aviso, got {len(out1)}+{len(out2)}")
        aviso_path = os.path.join(self.root, ".lanzar", ".aviso-dev")
        with open(aviso_path, encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        self.assertEqual(lines.count("ses_m41"), 1, "una sola linea en .aviso-dev para sid concurrente")
        # además solo un mensaje en buzón del PM
        msgs = inbox.messages(self.root, "PM-calidad")
        self.assertEqual(len([m for m in msgs if "ses_m41" in m.text]), 1)


if __name__ == "__main__":
    unittest.main()
