"""Candado de guard.js: git que pisa trabajo ajeno se bloquea en el checkout principal y se
permite en un worktree propio. Corre la función pura isGitBlocked con node (sin opencode)."""
import json
import os
import shutil
import subprocess
import unittest
import tempfile

GUARD = os.path.join(os.path.dirname(__file__), "../../adapters/opencode/guard.js")

CASES = [
    # (comando, cwd, bloquea, regla)
    ("git stash -u", "/main", True, "git-stash"),
    ("git stash list", "/main", False, None),
    ("git checkout -- inbox/PO.md", "/main", True, "git-checkout"),
    ("git restore --staged a.py", "/main", True, "git-checkout"),
    ("git switch -c x", "/main", True, "git-checkout"),
    ("git reset --hard HEAD", "/main", True, "git-reset-hard"),
    ("git reset --soft HEAD~1", "/main", False, None),
    ("git clean -fd", "/main", True, "git-clean"),
    ("git add -A", "/main", True, "git-add-todo"),
    ("git add .", "/main", True, "git-add-todo"),
    ("git add .opencode/plugin/guard.js", "/main", False, None),
    ("git commit -am x", "/main", True, "git-commit-todo"),
    ("git commit --amend --no-edit", "/main", True, "git-commit-todo"),
    ("git commit --only a.py -m 'no uses git stash -a'", "/main", False, None),
    ("git commit --only a.py --allow-empty -m x", "/main", False, None),
    ("npm test && git -C /main stash", "/wt", True, "git-stash"),
    ("cd /main && git checkout .", "/wt", True, "git-checkout"),
    ('git -C "/main" stash', "/wt", True, "git-stash"),
    ("git -C '/main' stash", "/wt", True, "git-stash"),
    ('cd "/main" && git stash', "/wt", True, "git-stash"),
    ('git --no-pager stash', "/main", True, "git-stash"),
    ('git --no-pager -C "/main" stash', "/wt", True, "git-stash"),
    ('git clean --force', "/main", True, "git-clean"),
    ('git -C "/main" stash list', "/wt", False, None),
    ('echo "git -C /main stash"', "/main", False, None),
    ("git commit --only a.py -m '--amend'", "/main", False, None),
    ('git commit --only a.py -m "-a"', "/main", False, None),
    ('git commit --only a.py --message "--amend"', "/main", False, None),
    ('git commit --only a.py -m "--amend" --amend', "/main", True, "git-commit-todo"),
    # En un worktree propio el árbol es local, pero los stashes siguen siendo globales.
    ("git stash && git checkout -b y && git reset --hard", "/wt", True, "git-stash"),
    ("git checkout -b y && git reset --hard", "/wt", False, None),
    ("cd /main && cd ../wt && git stash", "/x", True, "git-stash"),
    ("git stash list", "/wt", False, None),
    ("git stash show", "/wt", False, None),
    # mencionarlo no es correrlo
    ("echo 'git stash'", "/main", False, None),
    ("cat <<EOF\ngit reset --hard\nEOF", "/main", False, None),
]


@unittest.skipUnless(shutil.which("node"), "node no está instalado")
class GuardGitTest(unittest.TestCase):
    def test_reglas_de_git(self):
        script = (
            f"const g = await import({json.dumps(os.path.abspath(GUARD))});"
            "const cases = JSON.parse(process.argv[1]);"
            "const isShared = (d) => d === '/main';"
            "console.log(JSON.stringify(cases.map(([c, cwd]) => g.isGitBlocked(c, cwd, isShared))));"
        )
        out = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps(CASES)],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        for (cmd, cwd, blocked, regla), res in zip(CASES, json.loads(out.stdout)):
            with self.subTest(cmd=cmd, cwd=cwd):
                self.assertEqual(res["blocked"], blocked, res)
                if blocked:
                    self.assertEqual(res["regla"], regla)


    def test_ruta_git_es_dato_no_codigo_shell(self):
        with tempfile.TemporaryDirectory() as temp:
            project = os.path.join(temp, "$(touch marcador_inyeccion)")
            os.mkdir(project)
            subprocess.run(["git", "init", "-q", project], check=True)
            script = (f"const g = await import({json.dumps(os.path.abspath(GUARD))});"
                      "console.log(g.sharedCheckout(process.argv[1])(process.argv[1]));")
            result = subprocess.run(["node", "--input-type=module", "-e", script, project],
                                    cwd=temp, capture_output=True, text=True, check=True)
            self.assertFalse(os.path.exists(os.path.join(temp, "marcador_inyeccion")))
            self.assertEqual(result.stdout.strip(), "true")

    def test_el_barrido_no_se_apaga(self):
        # 2026-11-05: el plan de pausa de ORDEN-5 hizo bootout del barrido y nadie vio al dueño parado.
        cases = [("launchctl bootout gui/501/cl.jitdata.portafolio.sweep", True),
                 ("cd x && launchctl unload ~/Library/LaunchAgents/a.plist", True),
                 ("sudo launchctl disable gui/501/x", True),
                 ("echo 'launchctl bootout x'", False),
                 ("launchctl list | grep jitdata", False),
                 ("launchctl bootstrap gui/501 a.plist", False)]
        script = (f"const g = await import({json.dumps(os.path.abspath(GUARD))});"
                  "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(c => g.isLaunchctlBlocked(c))));")
        out = subprocess.run(["node", "--input-type=module", "-e", script, json.dumps([c for c, _ in cases])],
                             capture_output=True, text=True, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout), [b for _, b in cases])

if __name__ == "__main__":
    unittest.main()
