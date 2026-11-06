"""Repositorios temporales reales para probar el lanzador sin tocar checkouts operativos."""
from pathlib import Path
import subprocess
import shutil
import sys

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import install


def init(project):
    project=Path(project)
    subprocess.run(['git','init','-qb','main',str(project)],check=True,capture_output=True)
    (project/'.gitignore').write_text('node_modules/\n')
    install.install(project)
    subprocess.run(['git','-C',str(project),'add','.opencode','scripts','.gitignore'],check=True,capture_output=True)
    if (project/'.scrum.toml').exists():
        subprocess.run(['git','-C',str(project),'add','.scrum.toml'],check=True,capture_output=True)
    subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','Base'],check=True,capture_output=True)


def cleanup(project):
    project=Path(project).resolve()
    r=subprocess.run(['git','-C',str(project),'worktree','list','--porcelain'],capture_output=True,text=True)
    for line in r.stdout.splitlines():
        if line.startswith('worktree ') and Path(line[9:]).resolve()!=project:
            subprocess.run(['git','-C',str(project),'worktree','unlock',line[9:]],capture_output=True)
            subprocess.run(['git','-C',str(project),'worktree','remove','--force',line[9:]],capture_output=True)
    container=project.parent/'.scrum-worktrees'/project.name
    if container.exists(): shutil.rmtree(container)
