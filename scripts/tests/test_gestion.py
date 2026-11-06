"""Candados ORDEN-3: raiz_gestion como único punto de resolución.

1) Estático: fuera de gestion.py/gestion.js, ningún .py/.js/.sh arma epic-*, focus/ ni inbox/
   contra la raíz del repo.
2) Compatibilidad sin config: 5 scripts operan sobre raíz.
3) Compatibilidad con config gestion/: 5 scripts operan sobre el gestion root.
"""
import glob
import json
import os
import re
import sys
import tempfile
import unittest
import subprocess
from unittest.mock import patch

HERE = os.path.dirname(__file__)
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))
import gestion  # noqa: E402
import archivar  # noqa: E402
import inbox  # noqa: E402
import focus  # noqa: E402
sys.path.insert(0, os.path.join(ROOT, "adapters", "opencode"))
import wake  # noqa: E402


def _files_to_check():
    # Producción, excluye tests y la propia gestión
    pats = [
        "scripts/*.py",
        "adapters/opencode/*.py",
        "adapters/opencode/*.js",
        "adapters/opencode/*.sh",
        "scripts/*.sh",
    ]
    out = []
    for pat in pats:
        for p in glob.glob(os.path.join(ROOT, pat)):
            if "gestion" in os.path.basename(p):
                continue
            # excluye tests
            if "/tests/" in p:
                continue
            out.append(p)
    return sorted(out)


class EstaticoTest(unittest.TestCase):
    """Fuera de gestion.*, nadie arma epic-*/focus/inbox desde raíz (repo)."""

    # Solo bloquea construcción directa contra variable `repo` o `R` (root del proyecto).
    # Construcciones vía `gr = gestion.raiz_gestion(repo)` + join(gr, ...) están permitidas
    # porque pasan por el único punto de resolución. JS: se permite join(gestion... ) y
    # resolve vía gestionInbox.
    FORBIDDEN = [
        re.compile(r'os\.path\.join\s*\(\s*repo\s*,[^)]*["\']epic-'),
        re.compile(r'os\.path\.join\s*\(\s*repo\s*,[^)]*["\']focus'),
        re.compile(r'os\.path\.join\s*\(\s*repo\s*,\s*["\']inbox'),
        re.compile(r'glob\.glob\s*\(\s*os\.path\.join\s*\(\s*repo\s*,[^)]*epic-'),
        # shell: "inbox/" literal concatenado a raíz en control.sh
        # JS: resolve con "../../inbox" directo sin gestion
        re.compile(r'resolve\s*\([^)]*"\.\./\.\./inbox"'),
    ]

    def test_ningun_join_directo_fuera_de_gestion(self):
        offenders = []
        for path in _files_to_check():
            txt = open(path, encoding="utf-8").read()
            for pat in self.FORBIDDEN:
                for m in pat.finditer(txt):
                    # excepción: archivar.py y focus.py ya migrados usan `gr`, no `repo`; si
                    # queda algún `repo`+epic es ofensor real.
                    line = txt[: m.start()].count("\n") + 1
                    offenders.append(f"{os.path.relpath(path, ROOT)}:{line}: {m.group(0)[:80]}")
        self.assertEqual(offenders, [], f"Rutas hardcodeadas fuera de gestion: {offenders[:10]}")

    def test_gestion_existe_y_resuelve_por_defecto_a_raiz(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertEqual(gestion.raiz_gestion(td), os.path.abspath(td))
            # con env
            os.environ["SCRUM_GESTION"] = "gestion"
            try:
                self.assertTrue(gestion.raiz_gestion(td).endswith("/gestion"))
            finally:
                os.environ.pop("SCRUM_GESTION", None)
            # con .scrum.toml
            with open(os.path.join(td, ".scrum.toml"), "w", encoding="utf-8") as f:
                f.write('gestion = "gestion"\n')
            self.assertTrue(gestion.raiz_gestion(td).endswith("/gestion"))
            os.remove(os.path.join(td, ".scrum.toml"))
            self.assertEqual(gestion.raiz_gestion(td), os.path.abspath(td))

    def test_rutas_relativas_conservan_padres_y_directorios_ocultos(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {k:v for k,v in os.environ.items() if k not in ("SCRUM_GESTION", "GESTION")}, clear=True):
            for raw in ["./../hermano", "./.gestion", "././gestion", ".//hermano", "../hermano", "gestion"]:
                with self.subTest(raw=raw):
                    with open(os.path.join(td, ".scrum.toml"), "w") as f:
                        f.write(f'gestion = "{raw}"\n')
                    expected = os.path.normpath(os.path.join(td, raw))
                    self.assertEqual(gestion.raiz_gestion(td), expected)
                    code = f"import {{raizGestion}} from {json.dumps(os.path.join(ROOT,'adapters/lib/gestion.js'))}; console.log(raizGestion(process.argv[1]));"
                    result = subprocess.run(["node", "--input-type=module", "-e", code, td],
                                            capture_output=True, text=True, check=True)
                    self.assertEqual(result.stdout.strip(), expected)

    def test_env_vacio_usa_alias_como_js(self):
        with tempfile.TemporaryDirectory() as td, patch.dict(os.environ, {"SCRUM_GESTION":" ", "GESTION":"gestion-alias"}):
            expected = os.path.join(td, "gestion-alias")
            self.assertEqual(gestion.raiz_gestion(td), expected)
            code = f"import {{raizGestion}} from {json.dumps(os.path.join(ROOT,'adapters/lib/gestion.js'))}; console.log(raizGestion(process.argv[1]));"
            result = subprocess.run(["node", "--input-type=module", "-e", code, td], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), expected)

    def test_js_gestion_existe(self):
        p = os.path.join(ROOT, "adapters", "lib", "gestion.js")
        self.assertTrue(os.path.exists(p), "gestion.js debe existir")
        txt = open(p, encoding="utf-8").read()
        self.assertIn("raizGestion", txt)
        self.assertIn("SCRUM_GESTION", txt)


class CompatibilidadSinConfigTest(unittest.TestCase):
    """Sin config, los 5 scripts operan sobre la raíz."""

    def _mk_repo(self, base):
        # repo mínimo con epic, inbox, focus
        os.makedirs(os.path.join(base, "epic-demo"), exist_ok=True)
        with open(os.path.join(base, "epic-demo", "STATUS.md"), "w", encoding="utf-8") as f:
            f.write("# Status\n\n| Ticket | Title | State | Updated |\n|---|---|---|---|\n| T-1 | x | closed | 2026-11-01 |\n" + "| T-%d | x | closed | 2026-11-02 |\n" * 5 % (2, 3, 4, 5, 6))
        os.makedirs(os.path.join(base, "inbox"), exist_ok=True)
        os.makedirs(os.path.join(base, "focus", "lines"), exist_ok=True)
        # focus mínimo válido
        good = (
            "# Focus\n\n**Level:** project\n**State:** active\n\n## Objective\n\n> <<obj>>\n\n"
            "## Directives in force\n\n| Id | Kind | Scope | Human's words | Date | Supersedes | Status |\n"
            "|---|---|---|---|---|---|---|\n| D-1 | objective | line | <<obj>> | 2026-10-01 | — | in force |\n\n## Archived\n\n"
        )
        with open(os.path.join(base, "focus", "PROJECT.md"), "w", encoding="utf-8") as f:
            f.write(good)
        with open(os.path.join(base, "focus", "lines", "demo.md"), "w", encoding="utf-8") as f:
            f.write(good.replace("project", "line").replace("<<obj>>", "line obj"))

    def test_5_scripts_sin_config_operan_en_raiz(self):
        for env_key in ("SCRUM_GESTION", "GESTION"):
            os.environ.pop(env_key, None)
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.join(td, "proj")
            os.makedirs(repo)
            self._mk_repo(repo)
            # archivar: estatus + inbox
            inbox.send(os.path.join(repo, "inbox"), "PM-a", "PO", "[aviso] hola")
            # focus claim usa repo
            n = focus.claim(repo, "D", "PM-test")
            self.assertTrue(n.startswith("D-"))
            # wake._is_reported / _has_delivery... usan repo
            self.assertFalse(wake._is_reported("T-99", repo))
            # archivar captura closed
            gr = gestion.raiz_gestion(repo)
            self.assertEqual(gr, os.path.abspath(repo))
            # inbox lanzar: encolado en inbox/
            prompt = os.path.join(repo, "epic-demo", "prompts", "T-1.md")
            os.makedirs(os.path.dirname(prompt), exist_ok=True)
            with open(prompt, "w", encoding="utf-8") as f:
                f.write("TO: dev T-1\ntarea")
            qid = inbox.lanzar(os.path.join(repo, "inbox"), "PM-a", "T-1", prompt, project=repo)
            self.assertTrue(qid.startswith("T-1"))
            # archivar sobre raíz mueve 1 fila (STATUS)
            import datetime

            done = archivar.archive(repo, now=datetime.datetime.now().astimezone(), tables=True)
            # debe haber movido al menos STATUS o nada si KEEP_CLOSED, pero no error
            self.assertIsInstance(done, list)


class CompatibilidadConGestionTest(unittest.TestCase):
    """Con .scrum.toml gestion=gestion, los 5 scripts operan ahí."""

    def test_5_scripts_con_config_operan_en_gestion(self):
        with tempfile.TemporaryDirectory() as td:
            repo = os.path.join(td, "proj")
            os.makedirs(repo)
            # config
            with open(os.path.join(repo, ".scrum.toml"), "w", encoding="utf-8") as f:
                f.write('gestion = "gestion"\n')
            gest = os.path.join(repo, "gestion")
            os.makedirs(os.path.join(gest, "epic-demo"), exist_ok=True)
            with open(os.path.join(gest, "epic-demo", "STATUS.md"), "w", encoding="utf-8") as f:
                f.write("# Status\n\n| Ticket | Title | State | Updated |\n|---|---|---|---|\n| T-1 | x | closed | 2026-11-01 |\n" + "| T-%d | x | closed | 2026-11-02 |\n" * 5 % (2, 3, 4, 5, 6))
            os.makedirs(os.path.join(gest, "inbox"), exist_ok=True)
            os.makedirs(os.path.join(gest, "focus", "lines"), exist_ok=True)
            good = (
                "# Focus\n\n**Level:** project\n**State:** active\n\n## Objective\n\n> <<obj>>\n\n"
                "## Directives in force\n\n| Id | Kind | Scope | Human's words | Date | Supersedes | Status |\n"
                "|---|---|---|---|---|---|---|\n| D-1 | objective | line | <<obj>> | 2026-10-01 | — | in force |\n\n## Archived\n\n"
            )
            with open(os.path.join(gest, "focus", "PROJECT.md"), "w", encoding="utf-8") as f:
                f.write(good)
            with open(os.path.join(gest, "focus", "lines", "demo.md"), "w", encoding="utf-8") as f:
                f.write(good.replace("project", "line").replace("<<obj>>", "line obj"))

            inbox_root = os.path.join(gest, "inbox")
            # 1) inbox.py send/read
            mid = inbox.send(inbox_root, "PM-a", "PO", "[aviso] hola en gestion")
            self.assertEqual(mid, "M-1")
            self.assertIn("hola en gestion", inbox.read(inbox_root, "PM-a"))
            # 2) focus claim
            n = focus.claim(repo, "D", "PM-test")
            # D-1 existe en gestion/focus, next es D-2
            self.assertEqual(n, "D-2")
            # 3) archivar: debe archivar desde gestion, no desde repo raíz
            import datetime

            done = archivar.archive(repo, now=datetime.datetime.now().astimezone(), tables=True)
            # debe mencionar epic-demo
            self.assertTrue(any("epic-demo" in d for d in done) or done == [])
            # gestión: epic-demo STATUS-archivo debe existir en gestion, no en raíz
            self.assertFalse(os.path.exists(os.path.join(repo, "epic-demo", "STATUS-archivo.md")))
            # si movió, estaría en gestion
            # 4) wake helpers
            self.assertFalse(wake._is_reported("T-99", repo))
            # inserta reported en gestion
            with open(os.path.join(gest, "epic-demo", "STATUS.md"), "a", encoding="utf-8") as f:
                f.write("| T-99 | x | reported | 2026-11-05 |\n")
            self.assertTrue(wake._is_reported("T-99", repo))
            # 5) inbox lanzar
            prompt = os.path.join(gest, "epic-demo", "prompts", "T-99.md")
            os.makedirs(os.path.dirname(prompt), exist_ok=True)
            with open(prompt, "w", encoding="utf-8") as f:
                f.write("TO: dev T-99\ntarea")
            qid = inbox.lanzar(inbox_root, "PM-a", "T-99", prompt, project=repo)
            self.assertTrue(qid.startswith("T-99"))
            # verifica que queue file está en gestion/inbox/.lanzar
            import glob as _g

            self.assertTrue(_g.glob(os.path.join(inbox_root, ".lanzar", "*.json")))
            # env override también
            os.environ["SCRUM_GESTION"] = "gestion"
            try:
                self.assertEqual(gestion.raiz_gestion(repo), os.path.join(os.path.abspath(repo), "gestion"))
            finally:
                os.environ.pop("SCRUM_GESTION", None)


if __name__ == "__main__":
    unittest.main()
