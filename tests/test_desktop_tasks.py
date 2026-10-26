"""Manual-only task control tests. No real desktop RPC or model inference."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock,patch
from desktop_tasks import DesktopTasks,lifecycle
from monitor import save

THREAD='11111111-1111-1111-1111-111111111111'
class Tests(unittest.TestCase):
    def setUp(self):
        work=Path(__file__).resolve().parent/'test-work';work.mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=work);self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.log=self.home/'rollout.jsonl';self.db=self.home/'state.sqlite'
        with closing(sqlite3.connect(self.db)) as db,db:
            db.execute('CREATE TABLE threads(id TEXT,rollout_path TEXT,archived INTEGER,updated_at INTEGER)')
            db.execute('INSERT INTO threads VALUES(?,?,0,1)',(THREAD,str(self.log)))
        self.events=Mock();self.tasks=DesktopTasks(self.home,self.events,save);self.tasks.db=self.db
        self.append('task_started','old-turn');self.calls=[];self.tasks.call=self.call
    def append(self,kind,turn='old-turn',user=False):
        item={'type':'response_item','payload':{'type':'message','role':'user','content':[]}} if user else {'type':'event_msg','payload':{'type':kind,'turn_id':turn}}
        with self.log.open('a',encoding='utf-8') as f:f.write(json.dumps(item)+'\n')
    def call(self,thread,operation,*args):
        self.calls.append(operation)
        if operation=='Pause':
            self.append('turn_aborted');return {'interrupt_ack':True,'interrupted_turn_id':'old-turn','app_closed':False}
        raise AssertionError('Unexpected native operation: '+operation)
    def pause(self):
        self.tasks.pause_running(force=True);self.assertEqual(self.tasks.pending[THREAD]['status'],'paused')
    def test_pause_keeps_APP_and_records_acknowledged_task(self):
        self.pause();self.assertEqual(self.calls,['Pause']);self.assertTrue(self.tasks.pending[THREAD]['manual_continue_required'])
    def test_explicit_recovery_only_marks_manual_readiness(self):
        self.pause();self.tasks.mark_manual_ready(6,90,1234)
        item=self.tasks.pending[THREAD]
        self.assertEqual(item['status'],'manual_ready');self.assertEqual(item['quota_observed_at'],1234)
        self.assertEqual(item['recovered_five_hour_remaining_percent'],6);self.assertEqual(self.calls,['Pause'])
    def test_weekly_cannot_replace_five_hour(self):
        self.pause()
        for five,week in ((None,99),(4,99),(5,99),(90,None),(90,4)):
            self.tasks.mark_manual_ready(five,week,1234)
        self.assertEqual(self.tasks.pending[THREAD]['status'],'paused');self.assertEqual(self.calls,['Pause'])
    def test_no_valid_read_timestamp_cannot_mark_ready(self):
        self.pause();self.tasks.mark_manual_ready(90,90,None)
        self.assertEqual(self.tasks.pending[THREAD]['status'],'paused')
    def test_quota_fall_during_recovery_recheck_prevents_ready(self):
        self.pause();self.tasks.mark_manual_ready(90,90,1234,allowed=lambda:False)
        self.assertEqual(self.tasks.pending[THREAD]['status'],'paused');self.assertEqual(self.calls,['Pause'])
    def test_manual_ready_is_written_once_without_native_calls(self):
        self.pause();self.tasks.mark_manual_ready(90,90,1234);self.tasks.mark_manual_ready(90,90,1235)
        ready=[c for c in self.events.call_args_list if c.args[0]=='desktop_task_manual_ready']
        self.assertEqual(len(ready),1);self.assertEqual(self.calls,['Pause'])
    def test_pending_state_survives_monitor_restart_but_never_resumes(self):
        self.pause();other=DesktopTasks(self.home,Mock(),save);other.db=self.db;other.call=Mock()
        other.mark_manual_ready(90,90,1234);other.call.assert_not_called()
        self.assertEqual(other.pending[THREAD]['status'],'manual_ready')
    def test_legacy_resume_operation_is_rejected_before_process_start(self):
        with patch('desktop_tasks.sp.run') as run,self.assertRaises(ValueError):
            DesktopTasks.call(self.tasks,THREAD,'Resume')
        run.assert_not_called()
    def test_legacy_uncertain_resume_record_cannot_send_again(self):
        self.pause();self.tasks.pending[THREAD]['status']='resume_delivery_unknown';self.tasks.store()
        self.tasks.mark_manual_ready(90,90,1234);self.assertEqual(self.calls,['Pause'])
        self.assertEqual(self.tasks.pending[THREAD]['status'],'resume_delivery_unknown')
    def test_new_manual_message_never_causes_automatic_replay(self):
        self.pause();self.append(None,user=True);self.append('task_started','manual-new')
        self.tasks.mark_manual_ready(90,90,1234);self.assertEqual(self.calls,['Pause'])
    def test_idle_task_is_not_enqueued(self):
        self.append('task_complete');self.tasks.pause_running(force=True)
        self.assertFalse(self.calls);self.assertFalse(self.tasks.pending)
    def test_quota_recovers_before_pause_then_no_RPC(self):
        self.tasks.pause_running(force=True,allowed=lambda:False);self.assertFalse(self.calls);self.assertFalse(self.tasks.pending)
    def test_large_tool_output_does_not_hide_a_live_task(self):
        with self.log.open('a',encoding='utf-8') as f:f.write(json.dumps({'type':'response_item','payload':{'type':'function_call_output','output':'x'*300000}})+'\n')
        self.assertTrue(lifecycle(self.log)['active']);self.pause()

if __name__=='__main__':unittest.main(verbosity=2)
