"""PRs del remoto alimentan el mismo buzón que ya despierta al PO."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
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

if __name__=='__main__':unittest.main()
