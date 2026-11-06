from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dev_worktree as dw
import install


class DevWorktreeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name).resolve()
        self.project = self.base / 'app'
        self.project.mkdir()
        self.root = self.base / 'gestion/inbox'
        (self.root / '.lanzar').mkdir(parents=True)
        self.git('init', '-q', '-b', 'main')
        self.git('config', 'user.email', 'fixture@example.test')
        self.git('config', 'user.name', 'Fixture')
        self.git('config', 'core.hooksPath', '/dev/null')
        (self.project / '.scrum.toml').write_text(f'gestion = "{self.root.parent}"\n')
        (self.project / '.gitignore').write_text('node_modules/\n')
        install.install(self.project)
        gate = self.project/'scripts/verificar-commit.sh'
        gate.write_text('#!/bin/sh\nprintf "%s\\n" "$1" >> "$SCRUM_TEST_GATE_LOG"\ntest ! -e "$SCRUM_TEST_RED"\n')
        self.git('add', '.')
        self.git('commit', '-qm', 'Base del fixture')
        self.database = self.base/'runtime.db'
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('create table session_v2(id text,time_busy int,time_idle int)')
        self.env = patch.dict(os.environ, {'SCRUM_TEST_GATE_LOG':str(self.base/'gate.log'),
                                          'SCRUM_TEST_RED':str(self.base/'gate-red')})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def git(self, *args, repo=None):
        result = subprocess.run(['git','-C',str(repo or self.project),*args], capture_output=True,text=True,check=True)
        return result.stdout.strip()

    def launch(self, request='T-1--PM-test--001'):
        entry={'id':request,'project':str(self.project),'ticket':'T-1','pm':'PM-test','session':'ses_'+request}
        entry.update(dw.prepare(str(self.root),entry))
        with (self.root/'.lanzar/log').open('a') as f:f.write(json.dumps(entry)+'\n')
        with closing(sqlite3.connect(self.database)) as db, db:db.execute('insert into session_v2 values(?,1,2)',(entry['session'],))
        return entry

    def commit(self, entry, text='entrega'):
        path=Path(entry['directory'])/'resultado.txt'
        path.write_text(text+'\n')
        self.git('add','resultado.txt',repo=entry['directory'])
        self.git('commit','-qm','Entrega del fixture',repo=entry['directory'])
        return self.git('rev-parse','HEAD',repo=entry['directory'])

    def deliver(self, entry, sha):
        return dw.deliver(str(self.root),entry['id'],sha,entry['directory'])

    def close(self, entry):
        return dw.close(str(self.root),entry['id'],self.git('rev-parse','main'),str(self.database))

    def integrate(self, entry):
        self.git('merge','--ff-only',entry['branch'])

    def test_crea_desde_main_bloquea_y_recupera_mismo_pedido(self):
        e=self.launch()
        self.assertEqual(dw.metadata(e['directory'])['gestion'],str(self.root.parent))
        self.assertEqual(self.git('rev-parse','HEAD',repo=e['directory']),self.git('rev-parse','main'))
        self.assertIn('locked',self.git('worktree','list','--porcelain'))
        self.assertEqual(dw.prepare(str(self.root),e),dw.metadata(e['directory']))
        self.assertFalse((Path(e['directory'])/'scrum-dev.json').exists())
        other=self.launch('T-1--PM-test--002')
        self.assertNotEqual(e['directory'],other['directory'])
        self.assertNotEqual(e['branch'],other['branch'])
        self.assertEqual(self.git('status','--porcelain',repo=e['directory']),'')

    def test_preparacion_rechaza_plugin_no_commiteado(self):
        path=self.project/'.opencode/plugin/guard.js'
        self.git('rm','.opencode/plugin/guard.js')
        self.git('commit','-qm','Fixture sin plugin')
        path.write_text(install.entries()['.opencode/plugin/guard.js'])
        with self.assertRaises(ValueError):self.launch()
        self.assertNotIn('codex/dev/',self.git('branch','--list'))

    def test_entrega_exige_commit_nuevo_limpio_y_desde_su_worktree(self):
        e=self.launch()
        with self.assertRaises(ValueError):self.deliver(e,e['base'])
        sha=self.commit(e)
        with self.assertRaises(ValueError):dw.deliver(str(self.root),e['id'],sha,str(self.project))
        untracked=Path(e['directory'])/'pendiente.txt';untracked.write_text('sin guardar')
        with self.assertRaises(ValueError):self.deliver(e,sha)
        untracked.unlink()
        result=self.deliver(e,sha)
        self.assertEqual(result['commit'],sha)
        self.assertEqual(result['status'],'delivered')
        self.assertTrue(Path(e['directory']).exists())
        self.assertEqual(result,self.deliver(e,sha))
        self.assertEqual(dw.receipt(str(self.root),e['id']).suffix,'.delivery')

    def test_sin_entrega_o_integracion_no_se_retira(self):
        e=self.launch();sha=self.commit(e)
        with self.assertRaisesRegex(ValueError,'falta una entrega'):self.close(e)
        self.deliver(e,sha)
        with self.assertRaisesRegex(ValueError,'no está integrado'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        self.assertFalse((self.base/'gate.log').exists())

    def test_gate_rojo_busy_y_dirty_preservan_worktree(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        (self.base/'gate-red').touch()
        with self.assertRaisesRegex(ValueError,'gate rechazado'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        (self.base/'gate-red').unlink()
        with closing(sqlite3.connect(self.database)) as db, db:db.execute('update session_v2 set time_busy=3')
        with self.assertRaisesRegex(ValueError,'idle'):self.close(e)
        with closing(sqlite3.connect(self.database)) as db, db:db.execute('update session_v2 set time_busy=1')
        (Path(e['directory'])/'resultado.txt').write_text('edición posterior')
        with self.assertRaisesRegex(ValueError,'sin commit'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        self.assertIn(e['branch'],self.git('branch','--list'))

    def test_verde_retira_solo_despues_de_integrar_y_verificar_sha(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        result=self.close(e)
        self.assertEqual(result['status'],'closed')
        self.assertEqual(result['verified'],sha)
        self.assertEqual((self.base/'gate.log').read_text().strip(),sha)
        self.assertFalse(Path(e['directory']).exists())
        self.assertNotIn(e['branch'],self.git('branch','--list'))
        self.assertEqual(result,self.close(e))
        self.assertEqual((self.base/'gate.log').read_text().splitlines(),[sha])

    def test_runtime_desconocido_o_timestamps_iguales_no_autorizan_retiro(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('update session_v2 set time_busy=time_idle')
        with self.assertRaisesRegex(ValueError,'idle'):self.close(e)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('delete from session_v2')
        with self.assertRaisesRegex(ValueError,'idle'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        self.assertFalse((self.base/'gate.log').exists())

    def test_cherry_pick_integrado_es_valido(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha)
        (self.project/'otro.txt').write_text('otra entrega')
        self.git('add','otro.txt');self.git('commit','-qm','Otro commit en main')
        self.git('cherry-pick',sha)
        integrated=self.git('rev-parse','main')
        self.assertNotEqual(integrated,sha)
        self.assertEqual(self.close(e)['verified'],integrated)

    def test_avance_main_durante_gate_impide_retiro(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        gate=self.project/'scripts/verificar-commit.sh'
        gate.write_text('#!/bin/sh\ngit -c core.hooksPath=/dev/null commit --allow-empty -qm "Avance durante gate"\n')
        with self.assertRaisesRegex(ValueError,'main cambió'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        self.assertIn('locked',self.git('worktree','list','--porcelain'))
