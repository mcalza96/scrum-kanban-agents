"""PRs del remoto alimentan el mismo buzón que ya despierta al PO."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inbox
import pr_watch


def pull(number=1, sha='a'*40, **values):
    item={'number':number,'state':'open','draft':False,'title':'Cambio visible',
          'base':{'ref':'main','repo':{'full_name':'owner/app'}},
          'head':{'sha':sha},'html_url':f'https://github.com/owner/app/pull/{number}'}
    item.update(values)
    return item


class PRWatchTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.base=Path(self.temp.name)
        self.repo=self.base/'app';self.repo.mkdir()
        self.root=self.base/'gestion/inbox';self.root.mkdir(parents=True)
        subprocess.run(['git','init','-q',str(self.repo)],check=True)
        subprocess.run(['git','-C',str(self.repo),'remote','add','origin','https://github.com/owner/app.git'],check=True)
        (self.repo/'.scrum.toml').write_text('gestion = "../gestion"\n')
        self.pages=[[pull()]]
        self.calls=[]
        self.mock=patch.object(pr_watch,'_gh',side_effect=self.gh)
        self.mock.start()

    def tearDown(self):
        self.mock.stop();self.temp.cleanup()

    def gh(self, repo, *args):
        self.calls.append((repo,args))
        if args[0]=='repo':return 'owner/app\n'
        self.assertEqual(args[:3],('api','--paginate','--slurp'))
        self.assertIn('state=open&base=main&per_page=100',args[3])
        return json.dumps(self.pages)

    def scan(self):return pr_watch.scan(str(self.repo))
    def messages(self):return inbox.messages(str(self.root),'PO')

    def test_avisa_todas_las_paginas_solo_abiertos_listos_para_main(self):
        self.pages=[[pull(),pull(2,draft=True),pull(3,state='closed')],
                    [pull(4,base={'ref':'dev'}),pull(5)]]
        result=self.scan()
        self.assertEqual(len(result),2)
        messages=self.messages()
        self.assertEqual(len(messages),2)
        self.assertIn('https://github.com/owner/app/pull/1',messages[0].text)
        self.assertIn('HEAD '+'a'*40,messages[0].text)
        self.assertIn('no autoriza el merge',messages[0].text)
        self.assertIn('pull/5',messages[1].text)
        self.assertEqual(self.calls[0][0],str(self.repo))

    def test_repetir_no_duplica_pero_head_nuevo_despierta_otra_vez(self):
        self.scan();self.assertEqual(self.scan(),[])
        self.pages=[[pull(sha='b'*40)]]
        self.scan()
        self.assertEqual(len(self.messages()),2)
        self.assertIn('b'*40,self.messages()[-1].text)

    def test_concurrencia_solo_entrega_un_aviso(self):
        results=[]
        threads=[threading.Thread(target=lambda:results.append(self.scan())) for _ in range(3)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(len(self.messages()),1)
        self.assertEqual(sum('PO avisado' in line for result in results for line in result),1)

    def test_fallo_envio_no_marca_y_el_siguiente_barrido_reintenta(self):
        with patch.object(inbox,'send',side_effect=OSError('disco lleno')):
            self.assertIn('disco lleno',self.scan()[0])
        self.assertFalse((self.root/'.pr-watch.json').exists())
        self.scan();self.assertEqual(len(self.messages()),1)

    def test_api_fallida_no_parece_cero_prs_ni_bloquea_reintento(self):
        with patch.object(pr_watch,'_gh',side_effect=ValueError('GitHub 503')):
            self.assertIn('GitHub 503',self.scan()[0])
        self.assertFalse((self.root/'.pr-watch.json').exists())
        self.scan();self.assertEqual(len(self.messages()),1)

    def test_recupera_envio_si_cae_antes_de_persistir_estado(self):
        with patch.object(inbox,'write_request',side_effect=OSError('fallo posterior')):
            self.assertIn('fallo posterior',self.scan()[0])
        self.assertEqual(len(self.messages()),1)
        self.assertIn('recuperado',self.scan()[0])
        self.assertEqual(len(self.messages()),1)
        self.assertEqual(self.scan(),[])

    def test_recupera_envio_ya_archivado_sin_duplicar(self):
        with patch.object(inbox,'write_request',side_effect=OSError('fallo posterior')):self.scan()
        archive=self.root/'archivo';archive.mkdir()
        (self.root/'PO.md').rename(archive/'PO-2026-11.md')
        self.assertIn('recuperado',self.scan()[0])
        self.assertEqual(self.messages(),[])
        self.assertEqual(self.scan(),[])

    def test_rechaza_repo_ajeno_y_shape_invalido_sin_marcar(self):
        for bad in [pull(base={'ref':'main','repo':{'full_name':'other/app'}}),
                    pull(head={'sha':'no-sha'}),pull(number=True),pull(html_url='https://github.com/other/app/pull/1')]:
            with self.subTest(pr=bad):
                self.pages=[[bad]]
                self.assertIn('falló',self.scan()[0])
                self.assertEqual(self.messages(),[])
                self.assertFalse((self.root/'.pr-watch.json').exists())
        self.pages={'not':'pages'}
        self.assertIn('paginada inválida',self.scan()[0])

    def test_sin_remote_no_consulta_red_y_sin_buzon_no_lo_recrea(self):
        subprocess.run(['git','-C',str(self.repo),'remote','remove','origin'],check=True)
        self.assertEqual(self.scan(),[]);self.assertEqual(self.calls,[])
        self.root.rmdir()
        self.assertIn('buzón ausente',self.scan()[0])
        self.assertFalse(self.root.exists())

    def test_repo_de_gh_debe_corresponder_a_origin_y_soporta_ssh(self):
        self.assertEqual(pr_watch._origin_name('git@github.com:owner/app.git'),'owner/app')
        self.assertEqual(pr_watch._origin_name('ssh://git@github.com/owner/app.git'),'owner/app')
        with patch.object(pr_watch,'_gh',return_value='other/app'):
            self.assertIn('distinto de origin',self.scan()[0])
        self.assertEqual(self.messages(),[])

    def test_estado_corrupto_se_preserva_no_reenvia_a_ciegas(self):
        state=self.root/'.pr-watch.json';state.write_text('{not-json')
        self.assertIn('falló',self.scan()[0])
        self.assertEqual(state.read_text(),'{not-json')
        self.assertEqual(self.messages(),[])


class ClosureWatchTest(unittest.TestCase):
    tearDown = PRWatchTest.tearDown
    scan = PRWatchTest.scan
    messages = PRWatchTest.messages
    def setUp(self):
        PRWatchTest.setUp(self)
        self.pages=[]
        self.closed=pull(state='closed', merged_at='2026-11-06T22:16:28Z',
                         head={'sha':'b'*40,'ref':'codex/dev/SIM-185--001'})
        self.delivery={'id':'SIM-185--001','project':str(self.repo),
                       'branch':'codex/dev/SIM-185--001','status':'delivered','pr':1}
        self.path=self.root/'.lanzar/SIM-185--001.delivery'
        self.path.parent.mkdir()
        self.save()

    def save(self):self.path.write_text(json.dumps(self.delivery))

    def gh(self, repo, *args):
        if args[0]=='api' and args[-1].startswith('repos/owner/app/pulls/1'):
            self.calls.append((repo,args));return json.dumps(self.closed)
        if args[0]=='api' and 'state=closed' in args[-1]:
            self.calls.append((repo,args));return json.dumps([[self.closed]])
        return PRWatchTest.gh(self,repo,*args)

    def test_avisa_merge_pendiente_una_vez_con_comando_y_no_borra(self):
        self.scan()
        self.assertEqual(len(self.messages()),1)
        message=self.messages()[0].text
        self.assertIn('[cierre:owner/app#1@SIM-185--001]',message)
        self.assertIn(f'cerrar {self.root} --pedido SIM-185--001',message)
        self.assertIn('--sincronizar',message)
        self.assertIn('no uses unlock/remove/branch manuales',message)
        self.assertEqual(self.scan(),[])
        self.assertEqual(json.loads(self.path.read_text()),self.delivery)

    def test_legacy_busca_solo_rama_y_no_avisa_cerrado_sin_merge(self):
        del self.delivery['pr'];self.save()
        self.closed['merged_at']=None
        self.assertEqual(self.scan(),[])
        self.assertTrue(any('head=owner%3Acodex%2Fdev%2FSIM-185--001' in args[-1] for _,args in self.calls))
        self.closed['merged_at']='2026-11-06T22:16:28Z'
        self.scan();self.assertEqual(len(self.messages()),1)

    def test_recibo_closed_preview_vivo_avisa_sin_matar(self):
        self.delivery.update(status='closed',preview={'pid':3503,'port':3106})
        self.save()
        alive=SimpleNamespace(alive=lambda preview:True)
        with patch.dict(sys.modules,{'preview_process':alive}):self.scan()
        self.assertEqual(len(self.messages()),1)
        self.assertEqual(json.loads(self.path.read_text()),self.delivery)

    def test_recibo_closed_preview_apagado_o_incierto_no_inventa_aviso(self):
        self.delivery.update(status='closed',preview={'pid':3503,'port':3106})
        self.save()
        with patch.dict(sys.modules,{'preview_process':SimpleNamespace(alive=lambda preview:False)}):
            self.assertEqual(self.scan(),[])
        with patch.dict(sys.modules,{'preview_process':SimpleNamespace(alive=lambda preview:(_ for _ in ()).throw(ValueError('PID ajeno')))}):
            self.assertIn('PID ajeno',self.scan()[0])
        self.assertEqual(self.messages(),[])

    def test_una_entrega_rota_no_impide_aviso_pr_abierto_ni_otra_entrega(self):
        self.pages=[[pull(2)]]
        (self.path.parent/'mal.delivery').write_text('{roto')
        result=self.scan()
        self.assertEqual(len(self.messages()),2)
        self.assertTrue(any('no verificable' in line for line in result))

    def test_no_acepta_pr_de_otro_repo_o_rama_y_preserva_recibo(self):
        for change in [{'base':{'ref':'main','repo':{'full_name':'other/app'}}},
                       {'head':{'ref':'other-branch'}}, {'number':2}]:
            original=self.closed.copy();self.closed.update(change)
            self.assertIn('no verificable',self.scan()[0])
            self.closed=original
        self.assertEqual(self.messages(),[])
        self.assertTrue(self.path.exists())

    def test_fallo_api_por_entrega_no_impide_open_y_reintenta(self):
        self.pages=[[pull(2)]]
        original=self.gh
        def fail(repo,*args):
            if args[-1].endswith('/pulls/1'):raise ValueError('GH 503 cierre')
            return original(repo,*args)
        with patch.object(pr_watch,'_gh',side_effect=fail):
            result=self.scan()
        self.assertTrue(any('GH 503 cierre' in line for line in result))
        self.assertEqual(len(self.messages()),1)
        self.scan();self.assertEqual(len(self.messages()),2)

    def test_cierre_recupera_envio_tras_fallo_estado(self):
        with patch.object(inbox,'write_request',side_effect=OSError('fallo posterior')):
            self.scan()
        self.assertEqual(len(self.messages()),1)
        self.assertIn('recuperado',self.scan()[0])
        self.assertEqual(self.scan(),[])
        self.assertEqual(len(self.messages()),1)

if __name__=='__main__':unittest.main()
