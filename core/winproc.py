"""Windows process identity and scoped stopping, using normal OS APIs."""
import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import dataclass
import os
from pathlib import Path

k=ct.WinDLL('kernel32',use_last_error=True)
a=ct.WinDLL('advapi32',use_last_error=True)
INVALID=ct.c_void_p(-1).value
QUERY=0x1000
class ENTRY(ct.Structure):
    _fields_=[('size',wt.DWORD),('usage',wt.DWORD),('pid',wt.DWORD),('heap',ct.c_size_t),('module',wt.DWORD),('threads',wt.DWORD),('parent',wt.DWORD),('priority',wt.LONG),('flags',wt.DWORD),('exe',wt.WCHAR*260)]
k.CreateToolhelp32Snapshot.argtypes=[wt.DWORD,wt.DWORD];k.CreateToolhelp32Snapshot.restype=wt.HANDLE
k.Process32FirstW.argtypes=[wt.HANDLE,ct.POINTER(ENTRY)];k.Process32NextW.argtypes=k.Process32FirstW.argtypes
k.OpenProcess.argtypes=[wt.DWORD,wt.BOOL,wt.DWORD];k.OpenProcess.restype=wt.HANDLE
k.QueryFullProcessImageNameW.argtypes=[wt.HANDLE,wt.DWORD,wt.LPWSTR,ct.POINTER(wt.DWORD)]
k.GetProcessTimes.argtypes=[wt.HANDLE,ct.POINTER(wt.FILETIME),ct.POINTER(wt.FILETIME),ct.POINTER(wt.FILETIME),ct.POINTER(wt.FILETIME)]
k.CloseHandle.argtypes=[wt.HANDLE];k.TerminateProcess.argtypes=[wt.HANDLE,wt.UINT]
k.CreateMutexW.argtypes=[ct.c_void_p,wt.BOOL,wt.LPCWSTR];k.CreateMutexW.restype=wt.HANDLE
a.OpenProcessToken.argtypes=[wt.HANDLE,wt.DWORD,ct.POINTER(wt.HANDLE)]
a.GetTokenInformation.argtypes=[wt.HANDLE,ct.c_int,ct.c_void_p,wt.DWORD,ct.POINTER(wt.DWORD)]
a.ConvertSidToStringSidW.argtypes=[ct.c_void_p,ct.POINTER(wt.LPWSTR)]
k.LocalFree.argtypes=[ct.c_void_p]

def sid(handle):
    token=wt.HANDLE()
    if not a.OpenProcessToken(handle,8,ct.byref(token)):raise ct.WinError(ct.get_last_error())
    try:
        size=wt.DWORD();a.GetTokenInformation(token,1,None,0,ct.byref(size));buf=ct.create_string_buffer(size.value)
        if not a.GetTokenInformation(token,1,buf,size,ct.byref(size)):raise ct.WinError(ct.get_last_error())
        ptr=ct.cast(buf,ct.POINTER(ct.c_void_p))[0];text=wt.LPWSTR()
        if not a.ConvertSidToStringSidW(ptr,ct.byref(text)):raise ct.WinError(ct.get_last_error())
        try:return text.value
        finally:k.LocalFree(ct.cast(text,ct.c_void_p))
    finally:k.CloseHandle(token)

@dataclass(frozen=True)
class Process:
    pid:int
    parent:int
    name:str
    image:str
    created:int
    owner:str

def identity(pid,parent=0,name='',rights=QUERY):
    handle=k.OpenProcess(rights,False,pid)
    if not handle:return None
    try:
        size=wt.DWORD(32768);image=ct.create_unicode_buffer(size.value)
        if not k.QueryFullProcessImageNameW(handle,0,image,ct.byref(size)):return None
        times=[wt.FILETIME() for _ in range(4)]
        if not k.GetProcessTimes(handle,*[ct.byref(t) for t in times]):return None
        return Process(pid,parent,name,image.value,(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime,sid(handle))
    except OSError:return None
    finally:k.CloseHandle(handle)

def snapshot():
    handle=k.CreateToolhelp32Snapshot(2,0)
    if handle==INVALID:raise ct.WinError(ct.get_last_error())
    result={};entry=ENTRY();entry.size=ct.sizeof(entry)
    try:
        more=k.Process32FirstW(handle,ct.byref(entry))
        while more:
            result[entry.pid]=(entry.parent,entry.exe)
            more=k.Process32NextW(handle,ct.byref(entry))
        return result
    finally:k.CloseHandle(handle)

def official_image(image):
    p=Path(image.removeprefix('\\\\?\\'));name=p.name.lower()
    locals_={Path.home()/'AppData/Local',Path(os.environ.get('LOCALAPPDATA',str(Path.home()/'AppData/Local')))}
    for local in locals_:
        for root in (local/'OpenAI/Codex',local/'Programs/OpenAI/Codex'):
            if p.is_relative_to(root) and (name.startswith('codex') or name=='node_repl.exe'):return True
    windows_apps=Path(os.environ.get('ProgramFiles','C:/Program Files'))/'WindowsApps'
    store=p.parent.parent.parent
    store_volume_root=store.name.lower()=='windowsapps' and store.parent==Path(store.anchor)
    if name=='chatgpt.exe' and p.parent.name.lower()=='app' and p.parent.parent.name.startswith('OpenAI.Codex_') and (store==windows_apps or store_volume_root):return True
    if name=='codex.exe':
        for root in (Path.home()/'.vscode/extensions',Path.home()/'.vscode-insiders/extensions'):
            if p.is_relative_to(root) and p.relative_to(root).parts[0].startswith('openai.chatgpt-'):return True
        parts=[x.lower() for x in p.parts]
        if '@openai' in parts and any(x=='codex' or x.startswith('codex-win32-') for x in parts):return True
    return False

def descendants(rows,roots):
    selected=set(roots)
    while True:
        extra={pid for pid,(parent,_) in rows.items() if parent in selected}
        if extra<=selected:return selected
        selected|=extra

def targets(excluded):
    rows=snapshot();excluded=descendants(rows,set(excluded));owner=identity(os.getpid()).owner;roots={};identities={}
    for pid,(parent,name) in rows.items():
        if pid in excluded or not (name.lower().startswith('codex') or name.lower() in ('chatgpt.exe','node_repl.exe')):continue
        proc=identity(pid,parent,name)
        if proc and proc.owner==owner and official_image(proc.image):roots[pid]=proc;identities[pid]=proc
    all_pids=descendants(rows,set(roots))-excluded
    for pid in all_pids:
        if pid in identities:continue
        parent,name=rows[pid];proc=identity(pid,parent,name)
        if proc and proc.owner==owner:identities[pid]=proc
    return roots,identities

def terminate(expected):
    """Recheck the open handle's PID creation time, owner and image before killing."""
    handle=k.OpenProcess(QUERY|1,False,expected.pid)
    if not handle:
        error=ct.get_last_error()
        return {'pid':expected.pid,'already_exited':error==87,'open_process_error':error,'terminated':False}
    try:
        size=wt.DWORD(32768);image=ct.create_unicode_buffer(size.value)
        times=[wt.FILETIME() for _ in range(4)]
        if not k.QueryFullProcessImageNameW(handle,0,image,ct.byref(size)) or not k.GetProcessTimes(handle,*[ct.byref(t) for t in times]):raise ct.WinError(ct.get_last_error())
        created=(times[0].dwHighDateTime<<32)|times[0].dwLowDateTime
        if created!=expected.created or image.value!=expected.image or sid(handle)!=expected.owner:return {'pid':expected.pid,'refused_identity_changed':True}
        if not k.TerminateProcess(handle,0xC000013A):raise ct.WinError(ct.get_last_error())
        return {'pid':expected.pid,'terminated':True,'image':expected.image}
    finally:k.CloseHandle(handle)
