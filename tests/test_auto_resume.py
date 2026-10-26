"""Recovery correctness, supersession and at-most-once delivery; no real model calls."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import Mock,patch
import test_desktop_tasks as fixtures
from test_desktop_tasks import THREAD
from monitor import auto_resume_enabled
from desktop_tasks import turn_configuration

class AutoTests(unittest.TestCase):
    append=fixtures.Tests.append
    pause=fixtures.Tests.pause
    def call(self,thread,operation,*args):
        if operation=='Probe':return {'model':'original-model'}
        return fixtures.Tests.call(self,thread,operation,*args)
    def setUp(self):
        fixtures.Tests.setUp(self)
        self.tasks.auto_resume=True
        self.pause()
        self.entry=self.tasks.pending[THREAD]
        self.observed=self.entry['paused_at']+1
        self.native=Mock();self.native.poll.return_value=None
        self.launch=patch('desktop_tasks.sp.Popen',return_value=self.native).start()
        self.addCleanup(patch.stopall)
    def resume(self,five=6,week=90,observed=None,allowed=lambda:True):
        self.tasks.last_resume_scan=0
        self.tasks.resume_ready(five,week,self.observed if observed is None else observed,allowed=allowed)
    def test_fresh_recovery_dispatches_once_in_original_thread(self):
        self.resume();self.resume()
        self.launch.assert_called_once()
        args=self.launch.call_args.args[0]
        self.assertEqual(args[args.index('-ThreadId')+1],THREAD)
        self.assertEqual(self.entry['status'],'resume_dispatching')
        self.assertTrue(self.entry['resume_client_message_id'])
    def test_weekly_alone_and_boundary_never_dispatch(self):
        for five,week in ((None,90),(4,90),(5,90),(6,None),(6,5)):
            self.resume(five,week)
        self.launch.assert_not_called()
    def test_read_must_be_newer_than_pause(self):
        self.resume(observed=self.entry['paused_at'])
        self.resume(observed=self.entry['paused_at']-1)
        self.launch.assert_not_called()
    def test_new_manual_message_cancels_resume(self):
        self.append(None,user=True);self.resume()
        self.assertEqual(self.entry['status'],'skipped_user_or_new_turn');self.launch.assert_not_called()
    def test_new_turn_cancels_resume(self):
        self.append('task_started','manual-new');self.resume()
        self.launch.assert_not_called()
    def test_completed_work_is_not_resumed(self):
        self.append('task_complete');self.resume()
        self.assertEqual(self.entry['status'],'skipped_already_completed');self.launch.assert_not_called()
    def test_legacy_manual_entries_are_not_replayed(self):
        self.entry['auto_resume_eligible']=False;self.resume()
        self.launch.assert_not_called()
    def test_quota_recheck_prevents_dispatch(self):
        self.resume(allowed=lambda:False);self.launch.assert_not_called()
    def test_quota_can_drop_while_probe_is_running(self):
        self.resume(allowed=Mock(side_effect=[True,False]));self.launch.assert_not_called()
    def test_uncertain_delivery_is_never_retried(self):
        self.resume();self.native.poll.return_value=1;self.tasks.poll_resumes()
        self.assertEqual(self.entry['status'],'resume_delivery_unknown')
        self.resume();self.launch.assert_called_once()
    def test_acknowledged_turn_is_persisted(self):
        self.resume()
        Path(self.entry['receipt_path']).write_text(json.dumps({'stage':'accepted','resume_ack':True,'resumed_turn_id':'accepted-turn'}),encoding='utf-8')
        self.native.poll.return_value=0;self.tasks.poll_resumes()
        self.assertEqual(self.entry['status'],'resumed');self.assertEqual(self.entry['resumed_turn_id'],'accepted-turn')
    def test_restart_never_replays_dispatched_intent(self):
        self.resume()
        from desktop_tasks import DesktopTasks
        other=DesktopTasks(self.home,self.events,self.tasks.save,auto_resume=True)
        other.poll_resumes()
        self.assertEqual(other.pending[THREAD]['status'],'resume_delivery_unknown')
    def test_stuck_helper_is_bounded_and_never_targets_APP(self):
        self.resume()
        job=next(iter(self.tasks.jobs.values()));job['started']-=91
        self.tasks.poll_resumes();self.native.kill.assert_called_once()
        self.native.poll.return_value=1;self.tasks.poll_resumes()
        self.assertEqual(self.entry['status'],'resume_delivery_unknown')
        self.resume();self.launch.assert_called_once()
    def test_preflight_unavailability_has_three_attempt_limit(self):
        self.tasks.call=Mock(side_effect=RuntimeError('owner unavailable'))
        for _ in range(6):
            self.entry['retry_after']=0;self.resume()
        self.assertEqual(self.tasks.call.call_count,3)
        self.assertEqual(self.entry['status'],'resume_preflight_failed_manual_required')
    def test_manual_ready_does_not_swallow_automatic_entry(self):
        self.tasks.mark_manual_ready(6,90,self.observed)
        self.assertEqual(self.entry['status'],'paused');self.resume();self.launch.assert_called_once()
    def test_old_boolean_alone_cannot_enable_new_protocol(self):
        self.assertFalse(auto_resume_enabled({'auto_resume_monitor_paused_tasks':True}))
        self.assertTrue(auto_resume_enabled({'auto_resume_monitor_paused_tasks':True,'auto_resume_policy':'fresh-five-hour-same-visible-v1'}))
    def test_effective_configuration_belongs_to_the_exact_executed_turn(self):
        with self.log.open('a',encoding='utf-8') as f:
            for turn,effort in (('old-turn','xhigh'),('other-turn','low')):
                f.write(json.dumps({'type':'turn_context','payload':{'turn_id':turn,'model':'same-model','effort':effort}})+'\n')
        self.assertEqual(turn_configuration(self.log,'old-turn'),{'turn_id':'old-turn','model':'same-model','effort':'xhigh'})
        self.assertIsNone(turn_configuration(self.log,'missing-turn'))
    def test_missing_configuration_is_unknown_instead_of_equal_defaults(self):
        with self.log.open('a',encoding='utf-8') as f:
            f.write(json.dumps({'type':'turn_context','payload':{'turn_id':'old-turn','model':'same-model'}})+'\n')
        self.assertIsNone(turn_configuration(self.log,'old-turn'))

if __name__=='__main__':unittest.main(verbosity=2)
