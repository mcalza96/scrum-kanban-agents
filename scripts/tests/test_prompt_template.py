"""Candado: la plantilla de prompts termina con el cierre completo (references/prompts.md, regla 9)."""
import pathlib
import unittest

TEMPLATE = pathlib.Path(__file__).resolve().parents[2] / "templates" / "PROMPT.md"


class CierreDelDev(unittest.TestCase):
    def test_cierre_tiene_ruta_fila_y_aviso(self):
        texto = TEMPLATE.read_text(encoding="utf-8")
        cierre = texto[texto.index("**Close, in this order."):]
        for paso in ("<exact path>", "git commit --only", "## Logbook", "`reported`", "inbox.py send --to <PM>"):
            self.assertIn(paso, cierre)

    def test_sin_fila_ni_aviso_no_es_entrega(self):
        self.assertIn("counts as not delivered", TEMPLATE.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
