"""Regresión del incidente: app y buzón separados, entrega verificable y sin limpieza ajena."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"adapters/opencode"))
import test_dev_worktree as fixtures
import dev_worktree as dw
import gestion
import inbox
import lanzar
import stale_worktrees
import wake

class PipelineTest(unittest.TestCase):
    setUp = fixtures.DevWorktreeTest.setUp
    tearDown = fixtures.DevWorktreeTest.tearDown
    git = fixtures.DevWorktreeTest.git
    commit = fixtures.DevWorktreeTest.commit
    launch = fixtures.DevWorktreeTest.launch
    deliver = fixtures.DevWorktreeTest.deliver

    def queue(self, ticket="T-1"):
        prompt=self.root.parent/(ticket+".md")
        prompt.write_text(f"TO: dev {ticket}\nApp {self.project}/resultado.txt; informes {self.root.parent}/reports; buzón {self.root}/PM-test.md")
        return inbox.lanzar(str(self.root),'PM-test',ticket,str(prompt),project=str(self.project))

    def test_lanzador_usa_worktree_y_preserva_gestion_externa_y_main_sucio(self):
        other=self.project/'ajeno.txt'; other.write_text('no tocar')
        request=self.queue()
        seen=[]
        def run(cmd,text,repo,observed):
            seen.append((text,repo)); observed('ses_pipeline'); return '', '', 0
        with patch.object(lanzar,'_stream_run',side_effect=run):
            self.assertEqual(lanzar.try_launch_one(str(self.root),db_path=str(self.database)),'ses_pipeline')
        entry=inbox.launch_records(str(self.root))[0]
        text,repo=seen[0]
        self.assertEqual(repo,entry['directory'])
        self.assertIn(repo+'/resultado.txt',text)
        self.assertIn(str(self.root.parent)+'/reports',text)
        self.assertIn(str(self.root)+'/PM-test.md',text)
        self.assertIn('--pedido '+request,text)
        self.assertEqual(gestion.inbox_root(repo),str(self.root))
        js=Path(__file__).resolve().parents[2]/'adapters/lib/gestion.js'
        code=f"import {{inboxRoot}} from {json.dumps(str(js))}; console.log(inboxRoot(process.argv[1]));"
        out=subprocess.check_output(['node','--input-type=module','-e',code,repo],text=True)
        self.assertEqual(out.strip(),str(self.root))
        self.assertEqual(other.read_text(),'no tocar')
        self.assertFalse((Path(repo)/'ajeno.txt').exists())

    def test_legacy_sin_project_no_bloquea_pedido_valido_y_no_se_adivina(self):
        old=self.queue('T-old')
        path=self.root/'.lanzar'/(old+'.json')
        data=json.loads(path.read_text()); del data['project']; path.write_text(json.dumps(data))
        os.utime(path,(1,1))
        self.queue('T-new')
        with patch.object(lanzar,'_stream_run',return_value=(json.dumps({'sessionID':'ses_new'}),'',0)) as run:
            self.assertEqual(lanzar.try_launch_one(str(self.root),db_path=str(self.database)),'ses_new')
        self.assertEqual(run.call_count,1)
        self.assertEqual(json.loads(path.read_text())['status'],'blocked')
        self.assertEqual(inbox.launch_records(str(self.root))[0]['ticket'],'T-new')

    def test_preflight_fallido_no_reserva_cupo_ni_invoca_runtime(self):
        request=self.queue()
        with patch.object(dw,'prepare',side_effect=ValueError('plugin no commiteado')), patch.object(lanzar,'_stream_run') as run:
            self.assertIsNone(lanzar.try_launch_one(str(self.root),db_path=str(self.database)))
        self.assertFalse(run.called)
        self.assertEqual(lanzar._active_counts(str(self.root),str(self.database))[0],0)
        evidence=self.root/'.lanzar'/(request+'.json.blocked')
        self.assertEqual(json.loads(evidence.read_text())['status'],'blocked')

    def test_barrido_conserva_incluso_worktree_antiguo_con_cambios(self):
        entry=self.launch()
        (Path(entry['directory'])/'sin-commit').write_text('conservar')
        result=stale_worktrees.reap(str(self.project),'main',set(),0,now=10**12)
        self.assertTrue(any('cierre verificado' in line for line in result),result)
        self.assertTrue(Path(entry['directory']).is_dir())
        self.assertEqual(self.git('branch','--list','conserva/*'),'')

    def test_reporte_solo_no_es_entrega_y_recibo_de_otro_pedido_tampoco(self):
        entry=self.launch()
        self.assertEqual(wake._managed_commits(str(self.root),entry),[])
        sha=self.commit(entry);self.deliver(entry,sha)
        self.assertEqual(wake._managed_commits(str(self.root),entry),[sha])
        path=dw.receipt(str(self.root),entry['id'])
        data=json.loads(path.read_text());data['session']='ses_ajena';path.write_text(json.dumps(data))
        self.assertEqual(wake._managed_commits(str(self.root),entry),[])

    def test_guard_del_worktree_rechaza_escritura_y_checkout_en_principal(self):
        entry=self.launch()
        path=self.project/'ajeno.txt';path.write_text('original')
        guard=Path(__file__).resolve().parents[2]/'adapters/opencode/guard.js'
        code="""
const {createGuard}=await import(process.argv[1]);
let before;
await createGuard(process.argv[2]).setup({location:{directory:process.argv[2]},tool:{hook:async(name,fn)=>{if(name==='execute.before') before=fn;}}});
const errors=[];
for(const event of [
{tool:'write',input:{filePath:process.argv[3]+'/ajeno.txt'}},
{tool:'edit',input:{path:process.argv[3]+'/ajeno.txt'}},
{tool:'write',input:{filePath:process.argv[3]+'/newdir/nested/file.ts'}},
{tool:'shell',input:{command:'git -C '+process.argv[3]+' checkout -- ajeno.txt'}},
{tool:'shell',input:{command:'git stash drop'}},
{tool:'write',input:{filePath:process.argv[2]+'/resultado.txt'}}]) {
try {await before(event); errors.push(null);} catch(e){errors.push(e.message);}
}
console.log(JSON.stringify(errors));
"""
        out=subprocess.check_output(['node','--input-type=module','-e',code,str(guard),entry['directory'],str(self.project)],text=True)
        errors=json.loads(out)
        self.assertTrue(all(errors[:5]),errors)
        self.assertIsNone(errors[5])
        self.assertEqual(path.read_text(),'original')
