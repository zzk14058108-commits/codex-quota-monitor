"""Standalone Windows account quota monitor, independent of skills and chats."""
import argparse
from dataclasses import asdict
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import subprocess as sp
import sys
import threading
import time
import uuid

from quota_rpc import QuotaClient,remaining,NO_WINDOW
from desktop_tasks import DesktopTasks
import winproc

VERSION='1.6.1-desktop-auto-resume-plain-text'

def auto_resume_enabled(config):
    return config.get('auto_resume_monitor_paused_tasks') is True and config.get('auto_resume_policy')=='fresh-five-hour-same-visible-v1'

def save(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temp.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8');end=time.monotonic()+1
        while True:
            try:temp.replace(path);return
            except PermissionError:
                if time.monotonic()>=end:raise
                time.sleep(.025)
    finally:
        if temp.exists():temp.unlink()

class Policy:
    def __init__(self,threshold=5,stale=20):
        self.threshold=threshold;self.stale=stale;self.last=None;self.percent=None;self.windows=[];self.started=time.monotonic();self.invalid=None
        self.five_hour_percent=None;self.weekly_percent=None
        self.last_observed_at=None
    def observe(self,data):
        percent,windows=remaining(data)
        self.percent=percent;self.windows=windows;self.last=time.monotonic();self.invalid=None
        self.five_hour_percent=100-windows[0]['usedPercent'];self.weekly_percent=100-windows[1]['usedPercent']
        self.last_observed_at=time.time()
    def read_error(self,error):
        # An unavailable account reading is never evidence of low quota.
        # Keep the last confirmed value for diagnostics and an existing low-quota latch.
        self.invalid=error
    def status(self):
        if self.last is None:
            return 'STARTING' if self.invalid is None and time.monotonic()-self.started<self.stale else 'QUOTA_UNKNOWN'
        # Only an explicit, validated low reading can latch this state. A later
        # read failure cannot create it, and only a validated >5% reading clears it.
        if self.percent<=self.threshold:return 'BLOCKED_LOW_QUOTA'
        if self.invalid is not None or time.monotonic()-self.last>self.stale:return 'QUOTA_UNKNOWN'
        return 'ALLOWED'

class Monitor:
    def __init__(self,home):
        self.home=home;home.mkdir(parents=True,exist_ok=True)
        self.config=json.loads((home/'config.json').read_text(encoding='utf-8'))
        self.policy=Policy(self.config['threshold_percent'],self.config['max_quota_age_seconds'])
        self.lock=threading.RLock();self.stop=threading.Event();self.changed=threading.Event();self.client=None;self.reader_pid=None
        self.last_mode=None;self.last_write=0;self.block_count=0
        self.test_override_until=None;self.test_timeout_until=None
        self.test_finished=False;self.last_test_command=None
        self.logger=logging.getLogger('quota-monitor');self.logger.setLevel(logging.INFO)
        handler=RotatingFileHandler(home/'events.jsonl',maxBytes=5*1024*1024,backupCount=2,encoding='utf-8');handler.setFormatter(logging.Formatter('%(message)s'));self.logger.addHandler(handler)
        self.tasks=DesktopTasks(home,self.event,save,auto_resume=auto_resume_enabled(self.config))
        self.tasks.resume_note=(self.config.get('test_authorization') or {}).get('resume_note','')
    def event(self,event,**data):self.logger.info(json.dumps({'time':time.time(),'event':event,**data},ensure_ascii=False))
    def read_error(self,event,error):
        with self.lock:self.policy.read_error(type(error).__name__)
        self.event(event,error=type(error).__name__);self.changed.set()
    def observe(self,data):
        if self.timeout_fault_active():return
        try:
            with self.lock:
                self.policy.observe(data);percent=self.policy.percent;windows=self.policy.windows
            self.event('quota',remaining_percent=percent,windows=windows);self.changed.set()
        except Exception as exc:self.read_error('invalid_quota',exc)
    def reader(self):
        while not self.stop.is_set():
            client=None
            try:
                # Rotate stderr before starting a replacement, never print account/config content.
                err=self.home/'reader.stderr.log'
                if err.exists() and err.stat().st_size>1024*1024:err.replace(self.home/'reader.stderr.previous.log')
                client=QuotaClient(err,self.observe)
                with self.lock:self.client=client;self.reader_pid=client.proc.pid
                self.event('quota_reader_started',pid=client.proc.pid)
                while not self.stop.is_set():
                    if self.timeout_fault_active():
                        self.read_error('explicit_test_read_timeout',TimeoutError('EXPLICIT_TEST_READ_TIMEOUT'))
                        self.stop.wait(self.config['quota_poll_seconds']);continue
                    self.observe(client.rpc('account/rateLimits/read',timeout=6))
                    self.stop.wait(self.config['quota_poll_seconds'])
            except Exception as exc:self.read_error('quota_read_error',exc)
            finally:
                if client:client.close()
                with self.lock:self.client=None;self.reader_pid=None
            self.stop.wait(2)
    def pause_allowed(self):return self.effective()[0]=='BLOCKED_LOW_QUOTA'
    def resume_allowed(self):
        with self.lock:
            return self.effective()[0]=='ALLOWED' and self.policy.five_hour_percent is not None and self.policy.five_hour_percent>5 and self.policy.weekly_percent is not None and self.policy.weekly_percent>5
    def enforce(self,grace=False):
        if self.pause_allowed():self.tasks.pause_running(force=grace,allowed=self.pause_allowed)
    def timeout_fault_active(self):
        return time.time()<(getattr(self,'test_timeout_until',None) or 0)
    def test_control(self):
        """One nonce-scoped, expiring, explicitly authorized exercise; inert normally."""
        auth=self.config.get('test_authorization')
        if self.test_finished or not isinstance(auth,dict):return
        if auth.get('source') not in ('USER_EXPLICIT_DESKTOP_MANUAL_CONTINUE_DRILL','USER_EXPLICIT_DESKTOP_AUTO_RESUME_DRILL'):return
        now=time.time()
        if now>=auth.get('expires_at',0):
            if self.test_override_until:self.policy.read_error('AwaitingFreshFiveHourReadingAfterTestExpiry')
            self.test_override_until=None;self.test_timeout_until=None;self.test_finished=True
            self.tasks.resume_note=''
            self.event('test_authorization_expired',test_id=auth.get('test_id'));return
        path=self.home/'test-control.json'
        try:command=json.loads(path.read_text(encoding='utf-8'))
        except (OSError,ValueError):return
        if command.get('test_id')!=auth.get('test_id') or command.get('nonce')!=auth.get('nonce'):return
        ident=command.get('command_id')
        if not ident or ident==self.last_test_command:return
        action=command.get('action')
        with self.lock:
            if action in ('timeout_begin','inject4') and self.policy.status()!='ALLOWED':
                self.last_test_command=ident;self.event('test_command_rejected',action=action,reason='fresh real quota above5 is required');return
            if action=='timeout_begin' and not self.test_override_until:
                self.test_timeout_until=min(now+35,auth['expires_at']);self.policy.read_error('ExplicitTestInjectedTimeout')
                self.event('explicit_test_read_timeout_started',test_id=auth['test_id'],expires_at=self.test_timeout_until)
            elif action=='timeout_end':
                self.test_timeout_until=None
                self.event('explicit_test_read_timeout_ended',test_id=auth['test_id'])
            elif action=='inject4' and type(command.get('remaining_percent')) in (int,float) and command['remaining_percent']==4 and not self.test_override_until and not self.timeout_fault_active():
                self.test_override_until=min(now+180,auth['expires_at'])
                self.event('test_override_activated',test_id=auth['test_id'],quota_source='EXPLICIT_TEST_INJECTED_4_PERCENT',injected_window_minutes=300,real_five_hour_remaining_percent=self.policy.five_hour_percent,real_weekly_remaining_percent=self.policy.weekly_percent,expires_at=self.test_override_until)
            elif action=='release':
                if self.test_override_until:self.policy.read_error('AwaitingFreshFiveHourReadingAfterTestRelease')
                self.test_override_until=None;self.test_timeout_until=None;self.test_finished=True
                self.tasks.resume_note=''
                self.event('test_override_released',test_id=auth['test_id'],real_remaining_percent=self.policy.percent)
            else:
                self.event('test_command_rejected',action=action,reason='invalid or unavailable test action')
            self.last_test_command=ident;self.changed.set()
    def effective(self):
        with self.lock:
            until=getattr(self,'test_override_until',None)
            if until and time.time()<until:
                return 'BLOCKED_LOW_QUOTA',min(4,self.policy.percent if self.policy.percent is not None else 4),'EXPLICIT_TEST_INJECTED_4_PERCENT'
            if until:
                self.test_override_until=None;self.test_finished=True
                self.policy.read_error('AwaitingFreshFiveHourReadingAfterTestExpiry');self.event('test_override_expired')
            return self.policy.status(),self.policy.percent,'REAL_ACCOUNT'
    def write_status(self,mode):
        effective_mode,effective_percent,quota_source=self.effective()
        if mode!='STOPPED':mode=effective_mode
        with self.lock:
            value={'mode':mode,'pid':os.getpid(),'heartbeat_at':time.time(),'remaining_percent':effective_percent,'real_remaining_percent':self.policy.percent,
                'quota_age_seconds':None if self.policy.last is None else round(time.monotonic()-self.policy.last,2),
                'quota_observed_at':self.policy.last_observed_at,
                'quota_windows':self.policy.windows,'threshold_percent':self.policy.threshold,'reader_pid':self.reader_pid,
                'scope':'existing local desktop-owned chats; native task interruption only; independent CLI sessions are not controlled',
                'automatic_allow_above_threshold':True,'automatically_open_app':False,
                'automatically_resume_monitor_paused_tasks':auto_resume_enabled(self.config),'manual_continue_required':not auto_resume_enabled(self.config),
                'uses_process_termination_fallback':False,'closes_desktop_app':False,
                'paused_tasks_waiting_for_recovery':sum(1 for task in self.tasks.pending.values() if task['status']=='paused'),
                'model_inference_for_quota_checks':0,
                'manual_ready_task_count':sum(1 for task in self.tasks.pending.values() if task['status']=='manual_ready'),
                'quota_poll_seconds':self.config['quota_poll_seconds'],
                'process_scan_seconds':self.config['process_scan_seconds'],'blocks_observed':self.block_count,
                'version':VERSION,'last_read_error':self.policy.invalid,
                'unknown_quota_policy':'report_only_never_stop',
                'enforcement_basis':quota_source if mode=='BLOCKED_LOW_QUOTA' else None,
                'quota_source':quota_source,'test_override_active':self.test_override_until is not None,
                'five_hour_remaining_percent':min(4,self.policy.five_hour_percent) if quota_source=='EXPLICIT_TEST_INJECTED_4_PERCENT' else self.policy.five_hour_percent,
                'weekly_remaining_percent':self.policy.weekly_percent,
                'real_five_hour_remaining_percent':self.policy.five_hour_percent,'real_weekly_remaining_percent':self.policy.weekly_percent,
                'minimum_remaining_percent':effective_percent,
                'recovery_requires':'explicit fresh 300-minute AND 10080-minute windows both above5; weekly data never substitutes for five-hour data',
                'test_timeout_fault_active':self.timeout_fault_active(),
                'test_id':(self.config.get('test_authorization') or {}).get('test_id') if self.test_override_until or self.timeout_fault_active() else None}
        save(self.home/'status.json',value)
    def tick(self):
        self.tasks.poll_resumes()
        self.test_control()
        mode,effective_percent,quota_source=self.effective()
        auth=self.config.get('test_authorization') or {}
        self.tasks.test_targets=set(auth['target_thread_ids']) if quota_source=='EXPLICIT_TEST_INJECTED_4_PERCENT' and self.policy.status()!='BLOCKED_LOW_QUOTA' and auth.get('target_thread_ids') else None
        transitioned=mode!=self.last_mode
        if transitioned:
            self.event('state_changed',previous=self.last_mode,current=mode,remaining_percent=effective_percent,real_remaining_percent=self.policy.percent,quota_source=quota_source)
            if mode=='BLOCKED_LOW_QUOTA':self.block_count+=1
            self.last_mode=mode
        if mode=='BLOCKED_LOW_QUOTA':self.enforce(grace=transitioned)
        elif mode=='ALLOWED' and self.resume_allowed():
            self.tasks.mark_manual_ready(self.policy.five_hour_percent,self.policy.weekly_percent,self.policy.last_observed_at,allowed=self.resume_allowed)
            if auto_resume_enabled(self.config):
                # Publish the NEW valid reading before the native helper independently checks it.
                self.write_status(mode)
                self.tasks.resume_ready(self.policy.five_hour_percent,self.policy.weekly_percent,self.policy.last_observed_at,allowed=self.resume_allowed)
        if transitioned or time.monotonic()-self.last_write>=1:
            self.write_status(mode);self.last_write=time.monotonic()
        return mode
    def run(self):
        # Named mutex prevents duplicate logon launches. It is not a privilege boundary.
        mutex=winproc.k.CreateMutexW(None,False,'Local\\CodexQuotaMonitor-'+winproc.identity(os.getpid()).owner)
        if not mutex:raise RuntimeError('Cannot create monitor mutex')
        if __import__('ctypes').get_last_error()==183:winproc.k.CloseHandle(mutex);return
        try:
            self.event('monitor_started',pid=os.getpid(),version=VERSION,inference_calls=0)
            threading.Thread(target=self.reader,daemon=True).start()
            while not self.stop.is_set():
                if (self.home/'STOP_MONITOR.request').exists():
                    (self.home/'STOP_MONITOR.request').unlink();self.stop.set();break
                self.tick()
                self.changed.wait(self.config['process_scan_seconds']);self.changed.clear()
        except Exception as exc:
            self.event('monitor_fatal',error=type(exc).__name__);raise
        finally:
            self.stop.set()
            with self.lock:client=self.client
            if client:
                try:client.close()
                except Exception:pass
            self.write_status('STOPPED');winproc.k.CloseHandle(mutex)

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('run','status','probe'));p.add_argument('--home',type=Path,default=Path.home()/'Documents/Codex/quota-monitor')
    args=p.parse_args()
    if args.action=='status':print((args.home/'status.json').read_text(encoding='utf-8'));return
    if args.action=='probe':
        args.home.mkdir(parents=True,exist_ok=True);client=QuotaClient(args.home/'probe.stderr.log',lambda data:None)
        try:
            percent,windows=remaining(client.rpc('account/rateLimits/read'))
            roots,procs=winproc.targets({os.getpid(),client.proc.pid})
            print(json.dumps({'real_remaining_percent':percent,'windows':windows,'codex_targets':[asdict(x) for x in roots.values()],
                'associated_process_count':len(procs),'processes_stopped':0,'model_turns_started':0}))
        finally:client.close()
        return
    Monitor(args.home).run()

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8') if sys.stdout else None
    main()
