from pathlib import Path
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install

SKILL = str(Path(__file__).resolve().parents[2])


class InstallTest(unittest.TestCase):
    def test_instala_carga_skill_y_rechaza_copia(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            self.assertEqual(install.install(project), [])
            self.assertEqual(install.install(project, check=True), [])
            env = dict(os.environ, SCRUM_SKILL=SKILL)
            code = '''import { pathToFileURL } from 'node:url';
for (const name of ['inbox', 'guard']) {
  const { default: plugin } = await import(pathToFileURL(`${process.argv[1]}/.opencode/plugin/${name}.js`));
  if (plugin.id !== `scrum-kanban-agents.${name}` || typeof plugin.setup !== 'function') process.exit(1);
}'''
            subprocess.run(['node', '--input-type=module', '-e', code, temp], env=env, check=True)
            result = subprocess.run([sys.executable, '-c', 'import inbox, gestion; print(inbox.pause.__code__.co_filename); print(gestion.__file__)'], cwd=project/'scripts', env=env, capture_output=True, text=True, check=True)
            self.assertIn(str(Path(SKILL, 'scripts/inbox.py')), result.stdout)
            self.assertIn(str(Path(SKILL, 'scripts/gestion.py')), result.stdout)
            # Una copia que vuelve a aparecer debe fallar, aunque la función sea idéntica.
            path = project/'.opencode/plugin/inbox.js'
            path.write_text(Path(SKILL, 'adapters/opencode/inbox.js').read_text())
            self.assertIn('.opencode/plugin/inbox.js', install.install(project, check=True))
            self.assertIn('.opencode/plugin/inbox.js', install.install(project))

    def test_rechaza_replicas_en_gestion(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp)
            install.install(project)
            (project/".scrum.toml").write_text('gestion = "gestion"\n')
            replica = project/"gestion/adapters/opencode/wake.py"
            replica.parent.mkdir(parents=True)
            replica.write_text("# réplica obsoleta")
            self.assertIn(str(replica), install.install(project, check=True))

    def test_guard_sin_location_protege_proyecto_explicito(self):
        with tempfile.TemporaryDirectory() as temp:
            subprocess.run(["git", "init", "-q", temp], check=True)
            code = """import {createGuard} from '""" + str(Path(SKILL, "adapters/opencode/guard.js")) + """';
let before;
await createGuard(process.argv[1]).setup({tool:{hook: async(name, fn) => {if(name === 'execute.before') before=fn;}}});
try { await before({tool:'shell', input:{command:'git stash'},sessionID:'test'}); process.exit(1); }
catch(e) { if(!e.message.includes('bloqueado: git-stash')) throw e; }
"""
            subprocess.run(['node', '--input-type=module', '-e', code, temp], cwd="/tmp", check=True)
