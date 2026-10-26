"""Meaningful policy/identity checks plus stopping only our own isolated worker."""
from dataclasses import replace
from pathlib import Path
import subprocess as sp
import sys
import time
import unittest
from unittest.mock import Mock,patch
from types import SimpleNamespace
import tempfile
import threading
import json
import uuid
from monitor import Policy,Monitor
from quota_rpc import remaining,QuotaClient
import winproc

def data(primary,secondary=0):
    return {'rateLimits':{'primary':{'usedPercent':primary,'windowDurationMins':300},**({'secondary':{'usedPercent':secondary,'windowDurationMins':10080}} if secondary is not None else {})}}

class Tests(unittest.TestCase):
    def isolated_monitor(self):
        m=object.__new__(Monitor);m.lock=threading.RLock();m.policy=Policy()
        m.changed=threading.Event();m.stop=threading.Event();m.reader_pid=None;m.client=None
        m.last_mode=None;m.last_write=0;m.block_count=0
        m.config={};m.test_override_until=None;m.test_timeout_until=None
        m.test_finished=False;m.last_test_command=None
        m.tasks=Mock();m.tasks.pending={}
        m.event=Mock();m.write_status=Mock();m.desktop_stop=Mock()
        return m
    def armed_monitor(self,temp):
        m=self.isolated_monitor();m.home=Path(temp);m.policy.observe(data(20))
        m.config={'test_authorization':{'source':'USER_EXPLICIT_DESKTOP_MANUAL_CONTINUE_DRILL','test_id':'unit-only','nonce':'unit-nonce','expires_at':time.time()+300}}
        return m
    def command(self,m,action,**fields):
        (m.home/'test-control.json').write_text(json.dumps({'test_id':'unit-only','nonce':'unit-nonce','command_id':uuid.uuid4().hex,'action':action,**fields}),encoding='utf-8')
        m.test_control()
    def test_threshold_and_automatic_recovery(self):
        p=Policy();p.observe(data(94));self.assertEqual(p.status(),'ALLOWED')
        p.observe(data(95));self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(96));self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(94));self.assertEqual(p.status(),'ALLOWED')
    def test_minimum_window(self):self.assertEqual(remaining(data(0,99))[0],1)
    def test_weekly_cannot_substitute_for_missing_five_hour_window(self):
        m=self.isolated_monitor();m.observe(data(96,10));self.assertEqual(m.policy.five_hour_percent,4)
        m.observe({'rateLimits':{'secondary':{'usedPercent':10,'windowDurationMins':10080}}})
        self.assertEqual(m.policy.status(),'BLOCKED_LOW_QUOTA');self.assertEqual(m.policy.five_hour_percent,4)
        m.observe(data(94,10));self.assertEqual(m.policy.status(),'ALLOWED');self.assertEqual(m.policy.five_hour_percent,6)
    def test_window_roles_are_identified_by_duration(self):
        payload={'rateLimits':{'primary':{'usedPercent':10,'windowDurationMins':10080},'secondary':{'usedPercent':96,'windowDurationMins':300}}}
        p=Policy();p.observe(payload)
        self.assertEqual(p.five_hour_percent,4);self.assertEqual(p.weekly_percent,90);self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
    def test_missing_ambiguous_or_duplicate_windows_never_allow_recovery(self):
        examples=[data(20,None),{'rateLimits':{'primary':{'usedPercent':10},'secondary':{'usedPercent':10,'windowDurationMins':10080}}}, {'rateLimits':{'primary':{'usedPercent':10,'windowDurationMins':10080},'secondary':{'usedPercent':10,'windowDurationMins':10080}}}]
        for payload in examples:
            with self.subTest(payload=payload):
                with self.assertRaises(ValueError):remaining(payload)
                m=self.isolated_monitor();m.observe(data(96,10));m.observe(payload)
                self.assertEqual(m.policy.status(),'BLOCKED_LOW_QUOTA')
    def test_weekly_high_never_clears_an_explicit_low_five_hour_reading(self):
        p=Policy();p.observe(data(96,0));self.assertEqual(p.percent,4);self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(95,0));self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(94,0));self.assertEqual(p.status(),'ALLOWED')
    def test_invalid_data(self):
        for v in (None,True,'95',float('nan'),float('inf'),101,-1):
            with self.subTest(v=v),self.assertRaises(ValueError):remaining(data(v))
    def test_stale_reading_only_reports_unknown(self):
        p=Policy();p.observe(data(20));p.last-=21;self.assertEqual(p.status(),'QUOTA_UNKNOWN')
        p.observe(data(20));self.assertEqual(p.status(),'ALLOWED')
    def test_startup_without_reading_never_blocks(self):
        p=Policy();self.assertEqual(p.status(),'STARTING')
        p.started-=3600;self.assertEqual(p.status(),'QUOTA_UNKNOWN')
    def test_first_read_timeout_never_blocks(self):
        p=Policy();p.read_error('TimeoutError');self.assertEqual(p.status(),'QUOTA_UNKNOWN')
        self.assertIsNone(p.percent)
    def test_failed_read_preserves_high_value_without_treating_it_as_fresh(self):
        p=Policy();p.observe(data(76));p.read_error('TimeoutError')
        self.assertEqual(p.status(),'QUOTA_UNKNOWN');self.assertEqual(p.percent,24)
        p.observe(data(75));self.assertEqual(p.status(),'ALLOWED');self.assertIsNone(p.invalid)
    def test_invalid_payload_cannot_create_low_quota(self):
        for payload in ({},data(101),data(True),data(None),data(float('nan'))):
            with self.subTest(payload=payload):
                m=self.isolated_monitor();m.observe(data(76));m.observe(payload)
                self.assertEqual(m.policy.status(),'QUOTA_UNKNOWN');self.assertEqual(m.policy.percent,24)
                with patch.object(winproc,'targets') as targets,patch.object(winproc,'terminate') as terminate:
                    self.assertEqual(m.tick(),'QUOTA_UNKNOWN');m.enforce(grace=True)
                    targets.assert_not_called();terminate.assert_not_called();m.desktop_stop.assert_not_called()
    def test_invalid_payload_at_startup_never_blocks(self):
        m=self.isolated_monitor();m.observe({})
        self.assertEqual(m.tick(),'QUOTA_UNKNOWN');self.assertEqual(m.block_count,0)
    def test_confirmed_low_latches_until_confirmed_recovery(self):
        p=Policy();p.observe(data(96));p.last-=3600;p.read_error('TimeoutError')
        self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        with self.assertRaises(ValueError):p.observe(data(None))
        self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(95));self.assertEqual(p.status(),'BLOCKED_LOW_QUOTA')
        p.observe(data(94));self.assertEqual(p.status(),'ALLOWED')
    def test_tick_does_not_stop_on_startup_stale_or_timeout(self):
        for scenario in ('startup','no_reading','stale','timeout'):
            with self.subTest(scenario=scenario):
                m=self.isolated_monitor()
                if scenario=='no_reading':m.policy.started-=3600
                if scenario in ('stale','timeout'):m.policy.observe(data(76))
                if scenario=='stale':m.policy.last-=3600
                if scenario=='timeout':m.policy.read_error('TimeoutError')
                with patch.object(m,'enforce') as enforce:
                    self.assertIn(m.tick(),('STARTING','QUOTA_UNKNOWN'))
                    enforce.assert_not_called();self.assertEqual(m.block_count,0)
    def test_enforcement_guard_refuses_unknown_or_high_quota(self):
        for scenario in ('startup','no_reading','high','stale','timeout'):
            with self.subTest(scenario=scenario):
                m=self.isolated_monitor()
                if scenario=='no_reading':m.policy.started-=3600
                if scenario in ('high','stale','timeout'):m.policy.observe(data(76))
                if scenario=='stale':m.policy.last-=3600
                if scenario=='timeout':m.policy.read_error('TimeoutError')
                with patch.object(winproc,'targets') as targets,patch.object(winproc,'terminate') as terminate:
                    m.enforce(grace=True)
                    targets.assert_not_called();terminate.assert_not_called();m.desktop_stop.assert_not_called()
    def test_only_explicit_low_quota_enters_stop_branch(self):
        m=self.isolated_monitor()
        with patch.object(m,'enforce') as enforce,patch('monitor.sp.Popen') as launch:
            for used in (94,94.999,95,96,94):
                m.observe(data(used));mode=m.tick()
                self.assertEqual(mode,'BLOCKED_LOW_QUOTA' if used>=95 else 'ALLOWED')
            self.assertEqual(enforce.call_count,2);self.assertEqual(m.block_count,1)
            launch.assert_not_called();m.desktop_stop.assert_not_called()
    def test_secondary_window_can_explicitly_trigger_stop(self):
        m=self.isolated_monitor();m.observe(data(10,95))
        with patch.object(m,'enforce') as enforce:
            self.assertEqual(m.tick(),'BLOCKED_LOW_QUOTA');enforce.assert_called_once_with(grace=True)
    def test_low_quota_uses_only_native_task_pause(self):
        m=self.isolated_monitor();m.observe(data(96))
        with patch.object(winproc,'targets') as targets,patch.object(winproc,'terminate') as terminate:
            m.enforce(grace=True);targets.assert_not_called();terminate.assert_not_called()
        m.tasks.pause_running.assert_called_once()
    def test_unknown_never_marks_manual_ready_and_recovery_never_resumes(self):
        m=self.isolated_monitor();m.tasks.pending={'old':{'status':'paused'}}
        self.assertEqual(m.tick(),'STARTING');m.tasks.resume_ready.assert_not_called()
        m.tasks.mark_manual_ready.assert_not_called()
        m.policy.read_error('TimeoutError');self.assertEqual(m.tick(),'QUOTA_UNKNOWN');m.tasks.resume_ready.assert_not_called()
        m.tasks.mark_manual_ready.assert_not_called()
        m.observe(data(94,10));self.assertEqual(m.tick(),'ALLOWED');m.tasks.resume_ready.assert_not_called();m.tasks.mark_manual_ready.assert_called_once()
    def test_legacy_auto_resume_true_cannot_reenable_automatic_tasks(self):
        m=self.isolated_monitor();m.config['auto_resume_monitor_paused_tasks']=True;m.observe(data(10,10))
        self.assertEqual(m.tick(),'ALLOWED');m.tasks.resume_ready.assert_not_called()
    def test_status_never_reports_old_allowed_after_a_new_read_error(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.isolated_monitor();m.home=Path(temp);m.config={'quota_poll_seconds':2,'process_scan_seconds':.25}
            m.observe(data(94,10));m.policy.read_error('TimeoutError')
            Monitor.write_status(m,'ALLOWED')
            state=json.loads((m.home/'status.json').read_text(encoding='utf-8'))
            self.assertEqual(state['mode'],'QUOTA_UNKNOWN');self.assertTrue(state['manual_continue_required'])
            self.assertFalse(state['automatically_resume_monitor_paused_tasks'])
    def test_reader_transport_timeout_never_enforces(self):
        m=self.isolated_monitor();m.observe(data(76));m.config={'quota_poll_seconds':2}
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m.home=Path(temp);client=Mock();client.proc=SimpleNamespace(pid=999999)
            def timeout(*args,**kwargs):m.stop.set();raise TimeoutError('synthetic transport timeout; no real account call')
            client.rpc.side_effect=timeout
            with patch('monitor.QuotaClient',return_value=client),patch.object(m,'enforce') as enforce:
                m.reader();self.assertEqual(m.tick(),'QUOTA_UNKNOWN');enforce.assert_not_called()
            client.close.assert_called_once();self.assertEqual(m.policy.percent,24)
    def test_unarmed_or_wrong_nonce_test_is_ignored(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.armed_monitor(temp);m.config={};self.command(m,'inject4',remaining_percent=4)
            self.assertEqual(m.effective(),('ALLOWED',80,'REAL_ACCOUNT'))
            m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=4,nonce='wrong')
            self.assertEqual(m.effective(),('ALLOWED',80,'REAL_ACCOUNT'))
    def test_explicit_four_percent_does_not_overwrite_real_quota(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=4)
            self.assertEqual(m.effective(),('BLOCKED_LOW_QUOTA',4,'EXPLICIT_TEST_INJECTED_4_PERCENT'))
            m.observe(data(10));self.assertEqual(m.policy.percent,90);self.assertEqual(m.effective()[1],4)
            self.command(m,'release');self.assertEqual(m.effective()[0],'QUOTA_UNKNOWN')
            m.observe({'rateLimits':{'secondary':{'usedPercent':0,'windowDurationMins':10080}}})
            self.assertEqual(m.effective()[0],'QUOTA_UNKNOWN')
            m.observe(data(10));self.assertEqual(m.effective(),('ALLOWED',90,'REAL_ACCOUNT'))
            self.command(m,'inject4',remaining_percent=4);self.assertEqual(m.effective()[0],'ALLOWED')
    def test_release_never_forges_a_high_reading(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=4);m.observe(data(96))
            self.command(m,'release');self.assertEqual(m.effective(),('BLOCKED_LOW_QUOTA',4,'REAL_ACCOUNT'))
    def test_expired_authorization_and_floor_clear_without_new_work(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=4)
            m.config['test_authorization']['expires_at']=time.time()-1;m.test_control()
            self.assertEqual(m.effective()[0],'QUOTA_UNKNOWN');self.assertTrue(m.test_finished)
            m.observe(data(20));self.assertEqual(m.effective(),('ALLOWED',80,'REAL_ACCOUNT'))
            m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=4);m.test_override_until=time.time()-1
            self.assertEqual(m.effective()[0],'QUOTA_UNKNOWN');self.assertTrue(m.test_finished)
    def test_live_timeout_injection_reports_unknown_without_stop(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            m=self.armed_monitor(temp);self.command(m,'timeout_begin');m.policy.last-=21
            with patch.object(m,'enforce') as enforce,patch.object(winproc,'terminate') as terminate:
                self.assertEqual(m.tick(),'QUOTA_UNKNOWN');self.assertTrue(m.timeout_fault_active())
                m.observe(data(96));self.assertEqual(m.policy.percent,80)
                enforce.assert_not_called();terminate.assert_not_called()
            self.command(m,'timeout_end');m.observe(data(10));self.assertEqual(m.effective(),('ALLOWED',90,'REAL_ACCOUNT'))
    def test_test_actions_require_fresh_real_above_five(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            for action in ('inject4','timeout_begin'):
                m=self.armed_monitor(temp);m.policy.read_error('TimeoutError');self.command(m,action,remaining_percent=4)
                self.assertIsNone(m.test_override_until);self.assertFalse(m.timeout_fault_active());self.assertEqual(m.effective()[0],'QUOTA_UNKNOWN')
    def test_non_four_percent_command_is_rejected(self):
        root=Path(__file__).resolve().parent/'test-work';root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temp:
            for percent in (True,5,6,100,None,'4'):
                m=self.armed_monitor(temp);self.command(m,'inject4',remaining_percent=percent)
                self.assertEqual(m.effective(),('ALLOWED',80,'REAL_ACCOUNT'))
    def test_no_models_allowed_in_quota_connection(self):
        p=object.__new__(QuotaClient)
        for method in ('thread/start','turn/start','thread/resume','review/start'):
            with self.subTest(method=method),self.assertRaises(ValueError):p.rpc(method)
    def test_installation_path_scoping(self):
        self.assertFalse(winproc.official_image(r'C:\OtherApp\ChatGPT.exe'))
        self.assertFalse(winproc.official_image(r'C:\OtherApp\codex.exe'))
        self.assertFalse(winproc.official_image(r'C:\Program Files\WindowsApps\OpenAI.ChatGPT_1\app\ChatGPT.exe'))
        self.assertTrue(winproc.official_image(r'D:\WindowsApps\OpenAI.Codex_26.1021.3705.0_x64__test\app\ChatGPT.exe'))
        self.assertFalse(winproc.official_image(r'C:\OtherApp\WindowsApps\OpenAI.Codex_test\app\ChatGPT.exe'))
        local=Path.home()/'AppData/Local/OpenAI/Codex/bin/example/codex.exe'
        self.assertTrue(winproc.official_image(str(local)))
    def test_descendants(self):
        rows={1:(0,'app'),2:(1,'worker'),3:(2,'shell'),4:(0,'unrelated')}
        self.assertEqual(winproc.descendants(rows,{1}),{1,2,3})
    def test_native_isolated_worker_and_pid_identity(self):
        worker=sp.Popen([sys.executable,'-c','import time; time.sleep(60)'],creationflags=sp.CREATE_NO_WINDOW)
        try:
            actual=winproc.identity(worker.pid)
            self.assertIsNotNone(actual)
            wrong=replace(actual,created=actual.created+1)
            self.assertTrue(winproc.terminate(wrong)['refused_identity_changed']);self.assertIsNone(worker.poll())
            self.assertTrue(winproc.terminate(actual)['terminated']);worker.wait(timeout=3)
        finally:
            if worker.poll() is None:worker.kill();worker.wait(timeout=3)
    def test_monitor_tree_exclusion(self):
        roots,items=winproc.targets({__import__('os').getpid()})
        self.assertNotIn(__import__('os').getpid(),items)
        for proc in roots.values():self.assertTrue(winproc.official_image(proc.image))

if __name__=='__main__':unittest.main(verbosity=2)
