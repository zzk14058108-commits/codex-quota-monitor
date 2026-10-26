"""Portable preflight with a separate fake home; never touches a real account."""
from contextlib import closing
import hashlib,json,os,shutil,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import runtime_support as portable
import quota_rpc

class PortableTests(unittest.TestCase):
    def setUp(self):
        scratch=Path(__file__).resolve().parent/'test-work';scratch.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(prefix='quota portable ',dir=scratch);self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.core=self.root/'core';self.core.mkdir()
        hashes={}
        for name in portable.NAMES:
            shutil.copy2(portable.CORE/name,self.core/name)
            hashes['core/'+name]=hashlib.sha256((self.core/name).read_bytes()).hexdigest()
        (self.root/'MANIFEST.json').write_text(json.dumps({'sha256':hashes}),encoding='utf-8')
        self.home=self.root/'profile';self.home.mkdir();self.exe=self.root/'codex.exe';self.exe.write_bytes(b'fixture; never executed')
        self.patches=[patch.object(portable,'ROOT',self.root),patch.object(portable,'CORE',self.core),patch.object(quota_rpc,'resolve_codex',quota_rpc.resolve_codex),patch.dict(os.environ,{'CODEX_HOME':str(self.home)})]
        for item in self.patches:item.start();self.addCleanup(item.stop)
    def test_manifest_detects_changed_core(self):
        self.assertTrue(all(portable.core_hashes_valid().values()))
        with (self.core/'monitor.py').open('a') as f:f.write('\n# altered\n')
        self.assertFalse(portable.core_hashes_valid()['monitor.py'])
    def test_explicit_codex_executable_is_supported(self):
        rpc=portable.configure({'codex_exe':str(self.exe)})
        self.assertEqual(rpc.resolve_codex(),str(self.exe))
    def test_override_rejects_missing_or_other_program(self):
        other=self.root/'python.exe';other.write_bytes(b'fixture')
        for path in (self.root/'missing/codex.exe',other):
            with self.assertRaises(FileNotFoundError):portable.configure({'codex_exe':str(path)})
    def test_missing_catalog_fails_without_creating_it(self):
        report=portable.prerequisites(codex_exe=str(self.exe))
        self.assertFalse(report['passed']);self.assertFalse((self.home/'state_5.sqlite').exists())
        self.assertEqual(report['tasks_interrupted'],0);self.assertEqual(report['model_turns_started'],0)
    def test_valid_catalog_is_checked_without_changes(self):
        db=self.home/'state_5.sqlite'
        with closing(sqlite3.connect(db)) as conn,conn:conn.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,archived INTEGER,updated_at INTEGER)')
        before=db.read_bytes();report=portable.prerequisites(codex_exe=str(self.exe))
        self.assertTrue(report['passed'],report);self.assertEqual(db.read_bytes(),before)
        self.assertFalse(report['APP_closed'])
    def test_changed_schema_fails_without_guessing(self):
        db=self.home/'state_5.sqlite'
        with closing(sqlite3.connect(db)) as conn,conn:conn.execute('CREATE TABLE threads(other TEXT)')
        report=portable.prerequisites(codex_exe=str(self.exe))
        self.assertFalse(report['checks']['desktop_catalog_schema'])

if __name__=='__main__':unittest.main()
