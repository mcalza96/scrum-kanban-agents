"""Candado 2026-11-04: un commit que borra un assert de un test llega al PM antes del cierre, aunque
el mensaje del commit diga «sin debilitar candados» y aunque el dev ya le haya avisado (regla 4)."""
import json
import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import candados  # noqa: E402
import inbox  # noqa: E402
from test_cal27_reap import ReapFinishedTest, wake  # noqa: E402


def git(repo, *args):
    return subprocess.run(["git", "-C", repo, *args], capture_output=True, text=True,
                          check=True).stdout.strip()


def commit(repo, files, msg):
    for path, text in files.items():
        full = os.path.join(repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)
        git(repo, "add", path)
    git(repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", msg)
    return git(repo, "rev-parse", "--short", "HEAD")


TEST = "test('a', () => {\n  assert.ok(x > 0, 'positivo');\n  expect(y).toBe(1);\n});\n"


class CandadosTest(ReapFinishedTest):
    def setUp(self):
        super().setUp()
        git(self.project, "init", "-q")
        self.base = commit(self.project, {"lib/a.test.ts": TEST, "lib/a.ts": "assert(x)\n"}, "base")

    def test_lista_asserts_borrados_y_cambiados_solo_en_tests(self):
        sha = commit(self.project, {
            "lib/a.test.ts": TEST.replace("  assert.ok(x > 0, 'positivo');\n", "")
                                 .replace("toBe(1)", "toBeDefined()") + "assert.equal(z, 2);\n",
            "lib/a.ts": "// sin assert\n"}, "SIM-1 sin debilitar candados")
        found = candados.removed(self.project, [sha])
        self.assertEqual([(p, line) for _, p, line in found],
                         [("lib/a.test.ts", "assert.ok(x > 0, 'positivo');"),
                          ("lib/a.test.ts", "expect(y).toBe(1);")])
        self.assertEqual(candados.summary(found), "2 assert borrados en lib/a.test.ts")
        self.assertEqual(candados.main([self.project, sha]), 1)

    def test_agregar_asserts_no_es_tocar_candados(self):
        sha = commit(self.project, {"lib/a.test.ts": TEST + "assert.ok(w);\n",
                                    "scripts/tests/test_x.py": "assert 1\n"}, "más candados")
        self.assertEqual(candados.removed(self.project, [sha]), [])
        self.assertEqual(candados.removed(self.project, ["noexiste"]), [])

    def dev_que_borra(self, with_send):
        sha = commit(self.project, {"lib/a.test.ts": TEST.replace("  expect(y).toBe(1);\n", "")}, "x")
        self.add_session("ses_dev_gate", title="dev CAL-27", with_commit=False, with_send=with_send)
        self.db.execute("insert into session_v2 values (?,?,?,?,?,?,?,?)",
                        ("ses_parent", None, "PM-calidad", self.project, None, 1, 2, 1))
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_parent", "user", 0, json.dumps({"text": "TO: PM-calidad"})))
        tool = {"type": "tool", "state": {"input": {"command": "git commit --only lib/a.test.ts -m x"},
                                          "content": [{"type": "text", "text": f"[main {sha}] x"}]}}
        self.db.execute("insert into session_message values (?,?,?,?)",
                        ("ses_dev_gate", "assistant", 8, json.dumps({"content": [tool]})))
        self.db.commit()
        wake.reap_finished(self.root, self.db_path)
        return sha, [m.text for m in inbox.messages(self.root, "PM-calidad")]

    def test_dev_que_avisa_igual_le_llega_el_candado_al_pm(self):
        sha, msgs = self.dev_que_borra(with_send=True)
        self.assertEqual(len(msgs), 1, msgs)
        self.assertIn("Candados tocados: 1 assert borrados en lib/a.test.ts", msgs[0])
        self.assertIn(sha, msgs[0])
        self.assertIn("OK del dueño", msgs[0])

    def test_dev_que_no_avisa_lleva_el_candado_en_el_aviso(self):
        _, msgs = self.dev_que_borra(with_send=False)
        self.assertEqual(len(msgs), 1, msgs)
        self.assertIn("terminó y entregó sin avisarte", msgs[0])
        self.assertIn("Candados tocados", msgs[0])


for _name in [n for n in dir(ReapFinishedTest) if n.startswith("test_")]:
    setattr(CandadosTest, _name, None)  # corren en su propio módulo, no dos veces
del ReapFinishedTest

if __name__ == "__main__":
    unittest.main()
