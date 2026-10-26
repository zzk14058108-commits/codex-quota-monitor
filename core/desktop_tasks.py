"""Pause desktop tasks and optionally continue them once in the SAME visible chat."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess as sp
import time
import uuid

NO_WINDOW=getattr(sp,'CREATE_NO_WINDOW',0)
TERMINAL={'task_complete','turn_complete','turn_aborted'}

def turn_configuration(path,turn_id):
    """Read the actual executed turn's configuration, not optional desktop UI overrides."""
    path=Path(path);size=path.stat().st_size;start=max(0,size-16*1024*1024)
    with path.open('rb') as f:
        f.seek(start)
        if start:f.readline()
        for line in f:
            try:record=json.loads(line)
            except (ValueError,UnicodeError):continue
            payload=record.get('payload',{})
            if record.get('type')=='turn_context' and payload.get('turn_id')==turn_id and payload.get('model') and 'effort' in payload:
                return {'turn_id':turn_id,'model':payload['model'],'effort':payload['effort']}
    return None

def lifecycle(path,after=None,marker=None):
    """Read lifecycle metadata only. Never persist prompts or assistant reasoning."""
    path=Path(path);size=path.stat().st_size
    if after is not None and (after>size or size-after>16*1024*1024):raise ValueError('Lifecycle replaced or unread change volume too large')
    start=after if after is not None else max(0,size-256*1024)
    result={'path':str(path),'offset':size,'turn_id':None,'active':False,'new_user_input':False,'new_turn_ids':[],'terminal':None,'matching_resume_message':False}
    with path.open('rb') as f:
        f.seek(start)
        if start and after is None:f.readline()
        for line in f:
            try:record=json.loads(line)
            except (ValueError,UnicodeError):continue
            payload=record.get('payload',{})
            if record.get('type')=='event_msg':
                kind=payload.get('type')
                if kind=='task_started':
                    result.update(turn_id=payload.get('turn_id'),active=True,terminal=None)
                    result['new_turn_ids'].append(payload.get('turn_id'))
                elif kind in TERMINAL:
                    result.update(turn_id=payload.get('turn_id') or result['turn_id'],active=False,terminal=kind)
            elif record.get('type')=='response_item' and payload.get('type')=='message' and payload.get('role')=='user':
                result['new_user_input']=True
                if marker and any(marker in item.get('text','') for item in payload.get('content',[]) if isinstance(item,dict)):
                    result['matching_resume_message']=True
    # A long live turn can place task_started outside the short tail. On the
    # first scan only, expand within a bounded 16MiB before calling it idle.
    if after is None and start and result['turn_id'] is None and size<=16*1024*1024:return lifecycle(path,0)
    return result

class DesktopTasks:
    def __init__(self,home,event,save,auto_resume=False):
        self.home=Path(home);self.event=event;self.save=save;self.last_scan=0
        self.db=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))/'state_5.sqlite'
        self.pending_file=self.home/'paused-tasks.json'
        try:self.pending=json.loads(self.pending_file.read_text(encoding='utf-8'))
        except FileNotFoundError:self.pending={}
        self.test_targets=None
        self.auto_resume=auto_resume;self.jobs={};self.last_resume_scan=0
    def store(self):self.save(self.pending_file,self.pending)
    def catalog(self):
        with closing(sqlite3.connect(self.db.as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
            return {r[0]:{'path':r[1],'archived':bool(r[2])} for r in db.execute('SELECT id,rollout_path,archived FROM threads ORDER BY updated_at DESC')}
    def call(self,thread,operation):
        if operation not in ('Probe','Pause'):raise ValueError('Automatic turn start/resume is disabled; continue manually in the visible chat')
        args=['powershell','-NoProfile','-NonInteractive','-File',str(self.home/'desktop_task.ps1'),'-ThreadId',thread,'-Operation',operation]
        result=sp.run(args,capture_output=True,creationflags=NO_WINDOW,timeout=13)
        if result.returncode:raise RuntimeError(result.stderr.decode('utf-8',errors='replace')[-500:])
        return json.loads(result.stdout.decode('utf-8-sig'))
    def pause_running(self,force=False,allowed=None):
        if not force and time.monotonic()-self.last_scan<1:return
        self.last_scan=time.monotonic();candidates=[]
        for thread,info in self.catalog().items():
            if info['archived'] or (self.test_targets is not None and thread not in self.test_targets):continue
            try:state=lifecycle(info['path'])
            except (OSError,ValueError):continue
            if state['active'] or state['terminal'] is None:candidates.append((thread,state))
        def pause(item):
            thread,state=item
            if allowed is not None and not allowed():return thread,state,{},None
            try:return thread,state,self.call(thread,'Pause'),None
            except Exception as exc:return thread,state,None,type(exc).__name__+': '+str(exc)
        with ThreadPoolExecutor(max_workers=4) as pool:
            for thread,state,reply,error in pool.map(pause,candidates):
                if error:self.event('desktop_pause_error',thread_id=thread,error=error);continue
                turn=reply.get('interrupted_turn_id')
                if reply.get('interrupt_ack') and turn:
                    self.pending[thread]={'thread_id':thread,'turn_id':turn,'path':state['path'],'offset':state['offset'],'paused_at':time.time(),'status':'paused','pause_id':str(uuid.uuid4()),'goal_pause_error':reply.get('goal_pause_error'),'manual_continue_required':True}
                    self.pending[thread]['auto_resume_eligible']=self.auto_resume
                    self.pending[thread]['resume_note']=getattr(self,'resume_note','')
                    # Persist immediately after each actual acknowledged pause.
                    self.store();self.event('desktop_task_paused',thread_id=thread,turn_id=turn,app_closed=False,goal_pause_error=reply.get('goal_pause_error'))
    def mark_manual_ready(self,five_hour,weekly,observed_at,allowed=None):
        """Record a new explicit allowance; never dispatch a user message or turn."""
        if five_hour is None or weekly is None or observed_at is None or five_hour<=5 or weekly<=5:return
        for thread,entry in self.pending.items():
            if entry['status']!='paused':continue
            if self.auto_resume and entry.get('auto_resume_eligible'):continue
            if allowed is not None and not allowed():return
            entry.update(status='manual_ready',manual_ready_at=time.time(),quota_observed_at=observed_at,
                         recovered_five_hour_remaining_percent=five_hour,recovered_weekly_remaining_percent=weekly,
                         manual_continue_required=True)
            self.store()
            self.event('desktop_task_manual_ready',thread_id=thread,turn_id=entry['turn_id'],
                       five_hour_remaining_percent=five_hour,weekly_remaining_percent=weekly,
                       quota_observed_at=observed_at,automatic_resume=False)

    def _dispatch_resume(self,thread,entry,text):
        folder=self.home/'resume-receipts';folder.mkdir(exist_ok=True)
        key=entry['pause_id'];message=folder/(key+'.txt');receipt=folder/(key+'.json')
        message.write_text(text,encoding='utf-8')
        entry.update(status='resume_dispatching',resume_at=time.time(),resume_client_message_id=str(uuid.uuid4()),
                     resume_marker='[quota-resume:'+key+']',receipt_path=str(receipt))
        self.store()  # Persist intent BEFORE starting the helper; never replay an uncertain intent after restart.
        args=['powershell','-NoProfile','-NonInteractive','-File',str(self.home/'desktop_task.ps1'),
              '-ThreadId',thread,'-Operation','Resume','-MessageFile',str(message),'-ClientMessageId',entry['resume_client_message_id'],
              '-ReceiptFile',str(receipt),'-GuardFile',str(self.home/'status.json'),'-PausedAt',str(entry['paused_at']),'-TimeoutMs','45000']
        out=folder/(key+'.stdout');err=folder/(key+'.stderr')
        try:
            with out.open('wb') as stdout,err.open('wb') as stderr:
                proc=sp.Popen(args,stdout=stdout,stderr=stderr,creationflags=NO_WINDOW)
        except OSError:
            entry['status']='resume_not_sent';self.store();raise
        self.jobs[key]={'proc':proc,'thread':thread,'pause_id':key,'started':time.monotonic(),'receipt':receipt}
        self.event('desktop_resume_dispatching',thread_id=thread,pause_id=key,client_message_id=entry['resume_client_message_id'])

    def poll_resumes(self):
        """No blocking wait: quota interruption stays responsive while desktop accepts a turn."""
        for key,job in list(self.jobs.items()):
            proc=job['proc']
            if proc.poll() is None:
                if time.monotonic()-job['started']<=90:continue
                # Terminate only our own stuck IPC helper by its Popen handle,
                # never the desktop APP or its owner, and never retry delivery.
                proc.kill()
                self.event('desktop_resume_helper_timeout',thread_id=job['thread'],app_closed=False)
                continue
            entry=self.pending.get(job['thread'])
            try:receipt=json.loads(job['receipt'].read_text(encoding='utf-8-sig'))
            except (OSError,ValueError):receipt={}
            if entry and entry.get('pause_id')==key:
                ack=receipt.get('stage')=='accepted' and receipt.get('resume_ack') and receipt.get('resumed_turn_id')
                entry.update(status='resumed' if ack else 'resume_delivery_unknown',resume_receipt=receipt,
                             resumed_turn_id=receipt.get('resumed_turn_id'),manual_continue_required=not bool(ack))
                self.store();self.event('desktop_task_resume',thread_id=job['thread'],status=entry['status'],
                    resumed_turn_id=entry.get('resumed_turn_id'),headless_model_started=False)
            del self.jobs[key]
        # A monitor restart must never resubmit an already dispatched message.
        live=set(self.jobs)
        for entry in self.pending.values():
            if entry['status']=='resume_dispatching' and entry.get('pause_id') not in live:
                entry['status']='resume_delivery_unknown';entry['manual_continue_required']=True;self.store()

    def resume_ready(self,five_hour,weekly,observed_at,allowed=None):
        if not self.auto_resume or five_hour is None or weekly is None or observed_at is None or five_hour<=5 or weekly<=5:return
        if time.monotonic()-self.last_resume_scan<1:return
        self.last_resume_scan=time.monotonic()
        for thread,entry in list(self.pending.items()):
            if entry['status']!='paused' or not entry.get('auto_resume_eligible') or observed_at<=entry['paused_at']:continue
            if time.time()<entry.get('retry_after',0):continue
            if allowed is not None and not allowed():return
            try:
                info=self.catalog().get(thread)
                if not info or info['archived']:
                    entry['status']='skipped_archived_or_removed';self.store();continue
                changes=lifecycle(entry['path'],entry['offset'])
                if changes['new_user_input'] or any(t and t!=entry['turn_id'] for t in changes['new_turn_ids']):
                    entry['status']='skipped_user_or_new_turn';self.store();continue
                if changes['terminal'] in ('task_complete','turn_complete'):
                    entry['status']='skipped_already_completed';self.store();continue
                if changes['terminal']!='turn_aborted':continue
                self.call(thread,'Probe')
                if allowed is not None and not allowed():return
                latest=lifecycle(entry['path'],entry['offset'])
                if latest['new_user_input'] or any(t and t!=entry['turn_id'] for t in latest['new_turn_ids']):
                    entry['status']='skipped_user_or_new_turn';self.store();continue
                text=(f'[quota-resume:{entry["pause_id"]}] 这是用户授权的额度监控自动恢复消息。'
                      f'刚刚明确读到五小时窗口（300分钟）剩余{five_hour}%，周窗口（10080分钟）剩余{weekly}%，均大于5%；'
                      '周额度没有替代五小时额度。请在当前原可见会话继续刚才因额度不足中断的原任务，从已有文件和进度接着完成。'
                      '不要重复已完成的外部操作；用户之后的新指令优先。'+entry.get('resume_note',''))
                self._dispatch_resume(thread,entry,text)
            except Exception as exc:
                if entry['status']=='paused':
                    entry['preflight_failures']=entry.get('preflight_failures',0)+1
                    entry['retry_after']=time.time()+30
                    if entry['preflight_failures']>=3:entry['status']='resume_preflight_failed_manual_required'
                self.store();self.event('desktop_resume_error',thread_id=thread,status=entry['status'],error=type(exc).__name__)
