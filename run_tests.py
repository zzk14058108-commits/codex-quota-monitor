"""Run isolated core checks; never install, schedule or inject a live quota."""
import os,subprocess,sys
from pathlib import Path
root=Path(__file__).resolve().parent;env=dict(os.environ)
env['PYTHONPATH']=str(root/'core')+os.pathsep+str(root/'tests')
raise SystemExit(subprocess.call([sys.executable,'-B','-m','unittest','discover','-s',str(root/'tests'),'-p','test_*.py'],cwd=root,env=env))
