"""Read-only compatibility checks. Never pause, resume or start a model turn."""
import argparse,json,tempfile
from pathlib import Path
import runtime_support

def main():
    p=argparse.ArgumentParser();p.add_argument('--local-only',action='store_true');p.add_argument('--codex-exe',default='');p.add_argument('--codex-home',default='');p.add_argument('--thread-id');p.add_argument('--output',type=Path)
    args=p.parse_args();result=runtime_support.prerequisites(args.codex_exe,args.codex_home)
    if not args.local_only and result['passed']:
        rpc=runtime_support.configure({**runtime_support.load_runtime(),**({'codex_exe':args.codex_exe} if args.codex_exe else {}),**({'codex_home':args.codex_home} if args.codex_home else {})})
        from desktop_tasks import DesktopTasks
        from monitor import save
        with tempfile.TemporaryDirectory(prefix='codex-quota-doctor-',dir=runtime_support.ROOT) as tmp:
            client=None
            try:
                client=rpc.QuotaClient(Path(tmp)/'account.stderr.log',lambda _:None)
                _,windows=rpc.remaining(client.rpc('account/rateLimits/read'))
                result['checks']['explicit_five_hour_and_weekly_read']=True
                result['quota_windows']=[{'minutes':w['windowDurationMins'],'remaining_percent':100-w['usedPercent']} for w in windows]
            except Exception as exc:
                result['checks']['explicit_five_hour_and_weekly_read']=False
                result['errors'].append(type(exc).__name__+': account reading unavailable; UNKNOWN does not mean low quota')
            finally:
                if client:client.close()
            tasks=DesktopTasks(Path(tmp),lambda *a,**kw:None,save)
            try:ids=[args.thread_id] if args.thread_id else [key for key,value in tasks.catalog().items() if not value['archived']][:3]
            except Exception:ids=[]
            native=False
            for thread in ids:
                try:
                    # The frozen helper is copied here because its read-only call resolves relative to home.
                    import shutil
                    shutil.copy2(runtime_support.CORE/'desktop_task.ps1',Path(tmp)/'desktop_task.ps1')
                    settings=tasks.call(thread,'Probe')
                    native=bool(settings.get('owner_client_id'));break
                except Exception:continue
            result['checks']['existing_desktop_owner_readable']=native
            if not native:result['errors'].append('No compatible desktop owner found in the three most recent chats; keep Codex open or pass --thread-id')
    result['passed']=all(result['checks'].values())
    text=json.dumps(result,ensure_ascii=True,indent=2)
    if args.output:args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(text,encoding='utf-8')
    print(text)
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
