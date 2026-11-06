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
        data = dw.deliver(str(self.root),entry['id'],sha,entry['directory'])
        dw.register_preview(str(self.root),entry['id'],1,None,None,None,None)
        return json.loads(dw.receipt(str(self.root),entry['id']).read_text())

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

    def test_sin_registro_de_preview_no_cierra(self):
        e=self.launch();sha=self.commit(e)
        dw.deliver(str(self.root),e['id'],sha,e['directory']);self.integrate(e)
        with self.assertRaisesRegex(ValueError,'falta registrar preview'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())

    def test_preview_que_no_termina_conserva_entrega(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        path=dw.receipt(str(self.root),e['id']);data=json.loads(path.read_text())
        data['preview']={'pid':123,'port':3106,'cwd':e['directory'],'started':'original','sha':sha}
        path.write_text(json.dumps(data))
        with patch('preview_process.stop',side_effect=ValueError('preview aún activo')):
            with self.assertRaisesRegex(ValueError,'preview aún activo'):self.close(e)
        self.assertTrue(Path(e['directory']).exists())
        self.assertEqual(json.loads(path.read_text())['status'],'delivered')

    def test_comando_contiene_root_e_id_reales(self):
        import shlex
        command=shlex.split(dw.close_command(str(self.root),'T-1','a'*40))
        self.assertEqual(command[3],str(self.root))
        self.assertIn('--sincronizar',command)

    def test_sync_no_mueve_main_si_esta_en_uso(self):
        e=self.launch();sha=self.commit(e)
        with patch.object(dw,'git',wraps=dw.git) as run:
            def call(repo,*args):
                if args==('fetch','origin','main'):return ''
                if args==('rev-parse','refs/remotes/origin/main'):return sha
                return dw.git_original(repo,*args)
            dw.git_original=run._mock_wraps
            run.side_effect=call
            with self.assertRaisesRegex(ValueError,'main está en uso'):dw.sync_main(str(self.project),sha)
        self.assertEqual(self.git('rev-parse','main'),e['base'])

    def test_preview_real_se_apaga_y_retira_worktree_adicional(self):
        import socket
        import time
        import preview_process
        e=self.launch();sha=self.commit(e)
        dw.deliver(str(self.root),e['id'],sha,e['directory']);self.integrate(e)
        extra=self.base/'preview'
        self.git('worktree','add','--detach',str(extra),sha)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        proc=subprocess.Popen(['node','-e',f"require('http').createServer((q,r)=>r.end('ok')).listen({port},'127.0.0.1')"],cwd=extra,
                              stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            deadline=time.monotonic()+5
            while preview_process.listeners(port)!={proc.pid}:
                if time.monotonic()>deadline:self.fail('fixture HTTP no escucha')
                time.sleep(.02)
            dw.register_preview(str(self.root),e['id'],6,proc.pid,port,str(extra),sha)
            try:
                result=self.close(e)
            except ValueError as exc:
                if 'preview aún activo' not in str(exc):raise
                proc.wait(timeout=5)
                result=self.close(e)
            proc.wait(timeout=5)
            self.assertEqual(result['status'],'closed')
            self.assertEqual(preview_process.listeners(port),set())
            self.assertFalse(extra.exists())
            self.assertFalse(Path(e['directory']).exists())
        finally:
            if proc.poll() is None:proc.terminate();proc.wait(timeout=5)

    def test_preview_con_commit_unico_no_se_retira(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        extra=self.base/'preview';self.git('worktree','add','--detach',str(extra),sha)
        (extra/'unico.txt').write_text('no integrado')
        self.git('add','unico.txt',repo=extra);self.git('commit','-qm','Preview único',repo=extra)
        unique=self.git('rev-parse','HEAD',repo=extra)
        path=dw.receipt(str(self.root),e['id']);data=json.loads(path.read_text())
        data['preview']={'pid':123,'started':'original','cwd':str(extra),'port':3106,'sha':unique}
        path.write_text(json.dumps(data))
        with patch('preview_process.stop') as stop:
            with self.assertRaisesRegex(ValueError,'no está integrado'):self.close(e)
            stop.assert_not_called()
        self.assertTrue(extra.exists())
        self.assertTrue(Path(e['directory']).exists())

    def test_recibo_closed_reverifica_preview(self):
        e=self.launch();sha=self.commit(e);self.deliver(e,sha);self.integrate(e)
        self.close(e)
        path=dw.receipt(str(self.root),e['id']);data=json.loads(path.read_text())
        data['preview']={'pid':123};path.write_text(json.dumps(data))
        with patch('preview_process.stop',side_effect=ValueError('preview aún activo')) as stop:
            with self.assertRaisesRegex(ValueError,'preview aún activo'):self.close(e)
            stop.assert_called_once_with(data['preview'])

    def test_sync_main_sin_cambiar_checkout_ni_indice_compartido(self):
        e=self.launch();sha=self.commit(e)
        self.git('switch','-c','shared')
        (self.project/'ajeno.txt').write_text('edición de otro actor')
        self.git('add','ajeno.txt')
        before=self.git('diff','--cached')
        self.git('update-ref','refs/remotes/origin/main',sha)
        original=dw.git
        def call(repo,*args):
            if args==('fetch','origin','main'):return ''
            return original(repo,*args)
        with patch.object(dw,'git',side_effect=call):dw.sync_main(str(self.project),sha)
        self.assertEqual(self.git('rev-parse','main'),sha)
        self.assertEqual(self.git('rev-parse','HEAD'),e['base'])
        self.assertEqual(self.git('diff','--cached'),before)
        self.assertEqual((self.project/'ajeno.txt').read_text(),'edición de otro actor')
