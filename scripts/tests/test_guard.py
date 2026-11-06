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

    def test_cierre_manual_solo_para_worktrees_administrados(self):
        with tempfile.TemporaryDirectory() as temp:
            project = os.path.realpath(os.path.join(temp, "principal"))
            managed = os.path.realpath(os.path.join(temp, "dev administrado"))
            ordinary = os.path.realpath(os.path.join(temp, "otro"))
            def git(*args):
                return subprocess.check_output(["git", "-C", project, *args], text=True).strip()
            subprocess.run(["git", "init", "-qb", "main", project], check=True)
            git("-c", "user.name=Test", "-c", "user.email=test@example.test", "commit", "--allow-empty", "-qm", "base")
            git("worktree", "add", "-qb", "codex/dev/T-1", managed)
            git("worktree", "add", "-qb", "ordinary", ordinary)
            from pathlib import Path
            gd = Path(Path(managed, ".git").read_text().strip()[8:])
            data = {"id": "T-1", "directory": managed, "project": project,
                    "branch": "codex/dev/T-1", "gestion": os.path.join(temp, "gestion")}
            (gd / "scrum-dev.json").write_text(json.dumps(data))
            cases = [
                (f"git worktree unlock '{managed}'", project, True),
                (f"git worktree remove --force --force '{managed}'", project, True),
                (f"git -C '{project}' worktree remove '{managed}' --force", ordinary, True),
                (f"cd '{project}' && git worktree unlock 'dev administrado'", ordinary, True),
                ("git branch -D codex/dev/T-1", ordinary, True),
                (f"cd '{project}' && git branch --delete --force codex/dev/T-1", ordinary, True),
                ("git update-ref -d refs/heads/codex/dev/T-1", project, True),
                ("git update-ref --delete refs/heads/codex/dev/T-1", project, True),
                ("git branch -df codex/dev/T-1", project, True),
                (f"git worktree unlock '{ordinary}'", project, False),
                (f"git worktree remove --force '{ordinary}'", project, False),
                ("git branch -D ordinary", project, False),
                ("git branch -rd origin/codex/dev/T-1", project, False),
                ("git update-ref -d refs/heads/ordinary", project, False),
                ("git worktree list --porcelain", managed, False),
                ("echo 'git branch -D codex/dev/T-1'", project, False),
                ("python3 scripts/dev_worktree.py cerrar inbox --pedido T-1 --integrado HEAD", project, False),
            ]
            script = (f"const g=await import({json.dumps(os.path.abspath(GUARD))});"
                      "console.log(JSON.stringify(JSON.parse(process.argv[1]).map(([cmd,cwd])=>g.isGitBlocked(cmd,cwd,()=>false))));")
            out = subprocess.check_output(["node", "--input-type=module", "-e", script, json.dumps(cases)], text=True)
            for case, result in zip(cases, json.loads(out)):
                with self.subTest(command=case[0]):
                    self.assertEqual(result["blocked"], case[2], result)
                    if case[2]:
                        self.assertEqual(result["regla"], "git-cierre-administrado")
                        self.assertIn("cerrar '" + os.path.join(temp, "gestion", "inbox") + "'", result["command"])
                        self.assertIn("--pedido 'T-1' --integrado " + git("rev-parse", "main"), result["command"])
            # El hook propaga el rechazo y entrega la instrucción correcta, sin ejecutar Git.
            hook = (f"const {{createGuard}}=await import({json.dumps(os.path.abspath(GUARD))});"
                    "let before;await createGuard(process.argv[1]).setup({location:{directory:process.argv[1]},"
                    "tool:{hook:async(name,fn)=>{if(name==='execute.before')before=fn;}}});"
                    "try{await before({tool:'shell',input:{command:'git branch -D codex/dev/T-1'}});"
                    "console.log('permitido');}catch(e){console.log(e.message);}")
            out = subprocess.check_output(["node", "--input-type=module", "-e", hook, project], text=True)
            self.assertIn("bloqueado: git-cierre-administrado", out)
            self.assertIn("El PO debe cerrar", out)
            self.assertIn("--pedido 'T-1'", out)
            # No se perdió trabajo ni se modificaron refs al inspeccionar los comandos.
            self.assertTrue(Path(managed).is_dir())
            self.assertEqual(git("rev-parse", "codex/dev/T-1"), git("rev-parse", "main"))
            # Incluso un directorio borrado por fuera de Git conserva la protección de su rama.
            shutil.rmtree(managed)
            out = subprocess.check_output(["node", "--input-type=module", "-e", script,
                                           json.dumps([("git branch -D codex/dev/T-1", project)])], text=True)
            self.assertTrue(json.loads(out)[0]["blocked"])

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
