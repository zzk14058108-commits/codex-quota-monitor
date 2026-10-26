"""Quota-only account transport. Never creates a model thread or inference turn."""
import json
import math
import os
from pathlib import Path
import queue
import shutil
import subprocess as sp
import threading

NO_WINDOW=getattr(sp,'CREATE_NO_WINDOW',0)

def resolve_codex():
    locals_={Path.home()/'AppData/Local',Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))}
    roots=[root for local in locals_ for root in (local/'OpenAI/Codex/bin',local/'Programs/OpenAI/Codex/bin')]
    candidates=[]
    for root in roots:
        for p in (root/'codex.exe',*root.glob('*/codex.exe')):
            if p.is_file():candidates.append((p.stat().st_mtime_ns,p))
    if candidates:return str(max(candidates,key=lambda x:x[0])[1])
    p=shutil.which('codex.exe')
    if p:return p
    raise FileNotFoundError('No installed Codex executable found')

def remaining(data):
    if not isinstance(data,dict):raise ValueError('Invalid quota response')
    if data.get('ordinaryUsageAllowed') is False:raise ValueError('Account usage is disabled')
    buckets=data.get('rateLimitsByLimitId') or {}
    bucket=buckets.get('codex') or data.get('rateLimits')
    if not isinstance(bucket,dict):raise ValueError('No Codex rate limit bucket')
    by_minutes={}
    for w in (bucket[k] for k in ('primary','secondary') if bucket.get(k) is not None):
        if not isinstance(w,dict):raise ValueError('Invalid quota window')
        used=w.get('usedPercent')
        if type(used) not in (int,float) or not math.isfinite(used) or not 0<=used<=100:raise ValueError('Invalid quota percentage')
        minutes=w.get('windowDurationMins')
        if type(minutes) is not int or minutes not in (300,10080):raise ValueError('Unrecognized quota window duration')
        if minutes in by_minutes:raise ValueError('Duplicate quota window duration')
        by_minutes[minutes]=w
    # This account uses both windows. Incomplete/ambiguous data is UNKNOWN;
    # in particular, a healthy weekly allowance cannot clear a five-hour stop.
    if 300 not in by_minutes:raise ValueError('Missing explicit five-hour (300 minute) quota window')
    if 10080 not in by_minutes:raise ValueError('Missing explicit weekly (10080 minute) quota window')
    windows=[by_minutes[300],by_minutes[10080]]
    return min(100-w['usedPercent'] for w in windows),windows

class QuotaClient:
    ALLOWED=frozenset(('initialize','account/rateLimits/read'))
    def __init__(self,log,on_update):
        self.on_update=on_update;self.pending={};self.lock=threading.RLock();self.seq=0
        self.log=log.open('ab');self.proc=None
        try:
            self.proc=sp.Popen([resolve_codex(),'-c','features.remote_control=false','app-server','--stdio'],stdin=sp.PIPE,stdout=sp.PIPE,stderr=self.log,creationflags=NO_WINDOW)
            threading.Thread(target=self._read,daemon=True).start()
            self.rpc('initialize',{'clientInfo':{'name':'codex_global_quota_monitor','version':'1.0'}})
            self._send({'method':'initialized','params':{}})
        except BaseException:self.close();raise
    def _send(self,value):
        with self.lock:
            self.proc.stdin.write((json.dumps(value)+'\n').encode('utf-8'));self.proc.stdin.flush()
    def _read(self):
        for line in self.proc.stdout:
            try:value=json.loads(line)
            except ValueError:continue
            if 'id' in value and 'method' not in value:
                with self.lock:q=self.pending.get(value['id'])
                if q:q.put(value)
            elif value.get('method')=='account/rateLimits/updated':self.on_update(value['params'])
            elif 'id' in value and 'method' in value:
                self._send({'id':value['id'],'error':{'code':-32000,'message':'Quota monitor cannot execute actions'}})
    def rpc(self,method,params=None,timeout=6):
        if method not in self.ALLOWED:raise ValueError('Quota connection forbids '+method)
        with self.lock:
            self.seq+=1;ident=self.seq;q=self.pending[ident]=queue.Queue()
            self._send({'id':ident,'method':method,'params':params or {}})
        try:
            value=q.get(timeout=timeout)
            if 'error' in value:raise RuntimeError('Account quota read failed')
            return value['result']
        except queue.Empty:raise TimeoutError(method)
        finally:
            with self.lock:self.pending.pop(ident,None)
    def close(self):
        if self.proc and self.proc.poll() is None:
            try:self.proc.stdin.close();self.proc.wait(timeout=2)
            except Exception:self.proc.kill();self.proc.wait(timeout=3)
        if hasattr(self,'log'):self.log.close()
