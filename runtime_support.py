"""Portable setup around the unchanged verified monitor core. No credentials."""
import hashlib,json,os,struct,sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent
CORE=ROOT/'core' if (ROOT/'core').is_dir() else ROOT
if str(CORE) not in sys.path:sys.path.insert(0,str(CORE))
NAMES=('monitor.py','desktop_tasks.py','desktop_task.ps1','quota_rpc.py','winproc.py')

def load_runtime():
    path=ROOT/'runtime.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}

def configure(runtime=None):
    runtime=load_runtime() if runtime is None else runtime
    if runtime.get('codex_home'):os.environ['CODEX_HOME']=runtime['codex_home']
    override=runtime.get('codex_exe') or os.environ.get('CODEX_EXE')
    import quota_rpc
    if override:
        candidate=Path(override).expanduser().resolve()
        if not candidate.is_file() or candidate.name.lower()!='codex.exe':raise FileNotFoundError('CODEX_EXE must point to an existing codex.exe')
        quota_rpc.resolve_codex=lambda:str(candidate)
    return quota_rpc

def core_hashes_valid():
    manifest_file=ROOT/'MANIFEST.json'
    if not manifest_file.exists():manifest_file=ROOT/'package-manifest.json'
    manifest=json.loads(manifest_file.read_text(encoding='utf-8'))
    hashes=manifest['sha256']
    return {name:hashlib.sha256((CORE/name).read_bytes()).hexdigest()==hashes['core/'+name] for name in NAMES}

def prerequisites(codex_exe='',codex_home=''):
    checks={'windows':os.name=='nt','python_3_12_or_later':sys.version_info>=(3,12),'python_64bit':struct.calcsize('P')==8}
    details={};errors=[]
    try:checks['verified_core_hashes']=all(core_hashes_valid().values())
    except (OSError,ValueError,KeyError) as exc:checks['verified_core_hashes']=False;errors.append(type(exc).__name__+': package manifest or core files invalid')
    try:
        runtime=load_runtime();runtime.update({k:v for k,v in {'codex_exe':codex_exe,'codex_home':codex_home}.items() if v})
        rpc=configure(runtime);exe=rpc.resolve_codex();checks['codex_executable_exists']=Path(exe).is_file();details['codex_exe']=exe
    except (OSError,ValueError) as exc:checks['codex_executable_exists']=False;errors.append(type(exc).__name__+': cannot locate codex.exe; supply -CodexExe explicitly')
    home=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex'))).expanduser().resolve();details['codex_home']=str(home)
    db=home/'state_5.sqlite';checks['desktop_catalog_schema']=False
    if db.is_file():
        import sqlite3
        from contextlib import closing
        try:
            with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True,timeout=1)) as conn:
                conn.execute('SELECT id,rollout_path,archived,updated_at FROM threads LIMIT 1').fetchall()
            checks['desktop_catalog_schema']=True
        except sqlite3.Error:errors.append('Desktop catalog schema differs; compatibility adaptation is required')
    else:errors.append('Desktop catalog missing: install/login to Codex desktop and open a chat first')
    return {'passed':all(checks.values()),'checks':checks,'details':details,'errors':errors,'model_turns_started':0,'tasks_interrupted':0,'APP_closed':False}
