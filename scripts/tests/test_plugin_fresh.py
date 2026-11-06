"""Un servicio persistente debe cargar cambios de plugin y de sus helpers compartidos."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]

class PluginFreshTest(unittest.TestCase):
    def test_mismo_proceso_refresca_plugin_y_dependencia_sin_copiar_implementacion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            library=root/'lib';library.mkdir()
            shutil.copy(ROOT/'adapters/lib/load.js',library/'load.js')
            code="""
import {writeFileSync} from 'node:fs';
const {fresh}=await import(process.argv[1]+'/lib/load.js');
const lib=new URL('file://'+process.argv[1]+'/lib/gestion.js');
const plugin=new URL('file://'+process.argv[1]+'/plugin.js');
writeFileSync(lib,'export const value = 1;');
writeFileSync(plugin,`import {fresh} from './lib/load.js'; const {value}=await fresh(new URL('./lib/gestion.js',import.meta.url)); export {value};`);
const first=await fresh(plugin);
const again=await fresh(plugin);
writeFileSync(lib,'export const value = 2;');
const changed=await fresh(plugin);
writeFileSync(plugin,'export const value = 3;');
const primary=await fresh(plugin);
console.log(JSON.stringify([first.value,first===again,changed.value,first===changed,primary.value]));
"""
            result=subprocess.check_output(['node','--input-type=module','-e',code,str(root)],text=True)
            self.assertEqual(json.loads(result),[1,True,2,False,3])
