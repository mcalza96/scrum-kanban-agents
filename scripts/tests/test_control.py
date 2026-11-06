import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import inbox


class ControlTest(unittest.TestCase):
    def test_control_resuelve_gestion_absoluta_y_muestra_pendientes(self):
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)
            project=base/'app'; project.mkdir()
            root=base/'hermano/inbox'
            root.mkdir(parents=True)
            (project/'.scrum.toml').write_text(f'gestion = "{root.parent}"\n')
            inbox.send(str(root),'PO','PM-test','MENSAJE_FIXTURE')
            # El fixture no consulta la DB operativa ni depende de sqlite3 instalado.
            fakebin=base/'bin'; fakebin.mkdir()
            sqlite=fakebin/'sqlite3'; sqlite.write_text('#!/bin/sh\nexit 0\n'); sqlite.chmod(0o755)
            env={k:v for k,v in os.environ.items() if k not in ('SCRUM_GESTION','GESTION')}
            env['PATH']=str(fakebin)+os.pathsep+env['PATH']
            script=Path(__file__).resolve().parents[2]/'adapters/opencode/control.sh'
            for invocation in [str(script), str(script.relative_to(script.parents[2]))]:
                result=subprocess.run(['bash',invocation,str(project)],cwd=script.parents[2], env=env,capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertIn(f'pedidos en {root}/.lanzar', result.stdout)
                self.assertIn('MENSAJE_FIXTURE', result.stdout)
