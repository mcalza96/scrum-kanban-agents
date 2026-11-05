"""Candados SKILL-E: lector único y chequeo de resumen."""
import glob
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))
import gestion  # noqa: E402
import resumen  # noqa: E402


class LectorTest(unittest.TestCase):
    def test_con_resumen_devuelve_resumen(self):
        with tempfile.TemporaryDirectory() as td:
            gr = os.path.join(td, "epic-demo")
            os.makedirs(os.path.join(gr, "tickets"))
            with open(os.path.join(gr, "tickets", "T-1.md"), "w", encoding="utf-8") as f:
                f.write("# T-1 · algo\n\n**Resumen:** Este es el resumen corto.\n\n## Why\n")
            r, p = resumen.leer_resumen("T-1", td)
            self.assertEqual(r, "Este es el resumen corto.")
            self.assertTrue(p.endswith("T-1.md"))

    def test_sin_linea_devuelve_sin_resumen(self):
        with tempfile.TemporaryDirectory() as td:
            gr = os.path.join(td, "epic-demo")
            os.makedirs(os.path.join(gr, "tickets"))
            with open(os.path.join(gr, "tickets", "T-2.md"), "w", encoding="utf-8") as f:
                f.write("# T-2 · algo\n\n## Why\nBody sin resumen\n")
            r, _ = resumen.leer_resumen("T-2", td)
            self.assertEqual(r, resumen.SIN_RESUMEN)

    def test_sin_archivo_devuelve_sin_resumen(self):
        with tempfile.TemporaryDirectory() as td:
            os.makedirs(os.path.join(td, "epic-demo", "tickets"))
            r, p = resumen.leer_resumen("NOEXISTE", td)
            self.assertEqual(r, resumen.SIN_RESUMEN)
            self.assertIsNone(p)

    def test_nunca_devuelve_otra_linea(self):
        with tempfile.TemporaryDirectory() as td:
            gr = os.path.join(td, "epic-demo")
            os.makedirs(os.path.join(gr, "tickets"))
            with open(os.path.join(gr, "tickets", "T-3.md"), "w", encoding="utf-8") as f:
                f.write("# T-3 · algo\n\n## Why\nEsta es la primera línea del cuerpo\nSegunda\n")
            r, _ = resumen.leer_resumen("T-3", td)
            self.assertEqual(r, resumen.SIN_RESUMEN)
            self.assertNotIn("primera línea", r)

    def test_usa_raiz_gestion_orden3(self):
        with tempfile.TemporaryDirectory() as td:
            # config gestion/
            with open(os.path.join(td, ".scrum.toml"), "w", encoding="utf-8") as f:
                f.write('gestion = "gestion"\n')
            gest = os.path.join(td, "gestion", "epic-demo", "tickets")
            os.makedirs(gest, exist_ok=True)
            with open(os.path.join(gest, "T-G.md"), "w", encoding="utf-8") as f:
                f.write("# T-G · x\n\n**Resumen:** Resumen en gestion.\n")
            # también hay archivo en raíz que no debe leerse
            os.makedirs(os.path.join(td, "epic-demo", "tickets"), exist_ok=True)
            with open(os.path.join(td, "epic-demo", "tickets", "T-G.md"), "w", encoding="utf-8") as f:
                f.write("# T-G · x\n\n## Why\nSin resumen en raíz\n")
            r, p = resumen.leer_resumen("T-G", td)
            self.assertEqual(r, "Resumen en gestion.")
            self.assertIn("gestion", p)

    def test_compatibilidad_sin_raiz_opera_sobre_raiz(self):
        with tempfile.TemporaryDirectory() as td:
            # sin .scrum.toml
            os.makedirs(os.path.join(td, "epic-demo", "tickets"), exist_ok=True)
            with open(os.path.join(td, "epic-demo", "tickets", "T-C.md"), "w", encoding="utf-8") as f:
                f.write("# T-C · x\n\n**Resumen:** En raíz sin config.\n")
            r, _ = resumen.leer_resumen("T-C", td)
            self.assertEqual(r, "En raíz sin config.")
            # gestion.raiz_gestion == td
            self.assertEqual(gestion.raiz_gestion(td), os.path.abspath(td))


class ChequeoTest(unittest.TestCase):
    def _mk_repo(self, base, tickets):
        """tickets: list of (id, state, resumen_or_None)"""
        epic = os.path.join(base, "epic-demo")
        os.makedirs(os.path.join(epic, "tickets"), exist_ok=True)
        # STATUS.md
        header = "| Ticket | Lane | Depends on | State | Agent | Report | Updated |\n|---|---|---|---|---|---|---|\n"
        rows = ""
        for tid, state, _res in tickets:
            rows += f"| {tid} título | LANE | — | {state} | — | — | 2026-11-05 |\n"
            if _res is not None:
                # crea archivo con resumen si se pide
                with open(os.path.join(epic, "tickets", f"{tid}.md"), "w", encoding="utf-8") as f:
                    f.write(f"# {tid} · título\n\n**Resumen:** {_res}\n\n## Why\n")
            elif _res is None and state != "closed":
                # para closed sin resumen no creamos archivo o creamos sin resumen según test
                pass
        with open(os.path.join(epic, "STATUS.md"), "w", encoding="utf-8") as f:
            f.write("# Status\n\n" + header + rows)
        # si ticket es cerrado sin resumen, crea archivo sin resumen para probar que no cuenta
        for tid, state, _res in tickets:
            if state.startswith("closed") and _res is None:
                p = os.path.join(epic, "tickets", f"{tid}.md")
                if not os.path.exists(p):
                    with open(p, "w", encoding="utf-8") as fh:
                        fh.write(f"# {tid} · título\n\n## Why\nSin resumen pero cerrado\n")

    def test_abierto_sin_resumen_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_repo(td, [("T-OPEN", "pending", None)])
            faltas = resumen.check(td)
            self.assertEqual(len(faltas), 1)
            self.assertEqual(faltas[0]["id"], "T-OPEN")
            # via CLI exit 1
            rc = resumen.main(["check", td])
            self.assertEqual(rc, 1)

    def test_cerrado_sin_resumen_no_cuenta(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_repo(td, [("T-CLOSED", "closed", None)])
            faltas = resumen.check(td)
            self.assertEqual(faltas, [])
            rc = resumen.main(["check", td])
            self.assertEqual(rc, 0)

    def test_abierto_con_resumen_largo_exit_1(self):
        with tempfile.TemporaryDirectory() as td:
            largo = "a" * 241
            self._mk_repo(td, [("T-LONG", "reported", largo)])
            faltas = resumen.check(td)
            self.assertEqual(len(faltas), 1)
            self.assertIn("excede", faltas[0]["motivo"])

    def test_abierto_con_resumen_ok_exit_0(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_repo(td, [("T-OK", "in progress", "Resumen corto válido")])
            faltas = resumen.check(td)
            self.assertEqual(faltas, [])
            rc = resumen.main(["check", td])
            self.assertEqual(rc, 0)

    def test_json_id_a_resumen_y_ruta(self):
        with tempfile.TemporaryDirectory() as td:
            self._mk_repo(
                td,
                [
                    ("T-A", "pending", "Resumen A"),
                    ("T-B", "reported", None),
                ],
            )
            m = resumen.json_map(td)
            self.assertEqual(m["T-A"]["resumen"], "Resumen A")
            self.assertIsNone(m["T-B"]["resumen"])
            self.assertIn("epic-demo/tickets/T-A.md", m["T-A"]["ruta"])
            # T-B sin resumen tiene ruta None porque no existe archivo; si existe sin resumen, igual ruta
            # creamos T-B con archivo sin resumen para ver ruta
            with open(os.path.join(td, "epic-demo", "tickets", "T-B.md"), "w", encoding="utf-8") as f:
                f.write("# T-B · t\n\n## Why\n")
            m2 = resumen.json_map(td)
            self.assertEqual(m2["T-B"]["ruta"], "epic-demo/tickets/T-B.md")


if __name__ == "__main__":
    unittest.main()
