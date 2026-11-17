"""Explicit one-click startup using existing ownership and configuration rules."""
import argparse
import time
import manage_qq_service as service


def start_system(manager=service, *, port=8000, timeout=90, clock=time.monotonic, sleep=time.sleep):
    if not 1 <= port <= 65535 or not 1 <= timeout <= 600:
        raise ValueError('Invalid port or startup timeout.')
    # A legacy uvicorn process is not ours to stop or to silently adopt.
    if manager.port_open(port) and not manager.owns(manager.read_record('backend-owner.json'), manager.RUNNER):
        raise RuntimeError('Port is used by another backend. Close its original server window, then run this launcher again. Existing process preserved.')
    print('Starting/reusing dedicated QQ and NapCat...', flush=True)
    napcat=manager.NapcatSupervisor(manager.ROOT).start_once()
    if napcat['phase'] not in {'running_owned','start_pending'}:
        raise RuntimeError('Dedicated QQ startup was not confirmed. Existing processes preserved; check managed QQ status.')
    print('Starting/reusing Saya backend; waiting for readiness...', flush=True)
    manager.start(port)
    deadline=clock()+timeout
    while clock()<deadline:
        health=manager.probe_health(port)
        if health.get('saya')=='ready':
            if health.get('consumer')=='paused':
                raise RuntimeError('Saya is ready, but QQ reception is disabled. Check the saved QQ settings; no setting was overwritten.')
            if health.get('consumer')=='running' and health.get('napcat')=='reachable':
                if health.get('qq_login')=='matched':return health
                if health.get('qq_login')=='wrong_account':
                    raise RuntimeError('QQ account does not match the configured bot. Log in with the original bot account.')
                if health.get('qq_login')=='login_required':
                    raise RuntimeError('QQ needs phone confirmation. Open http://127.0.0.1:6099/webui/qq_login and finish login, then run again.')
        sleep(1)
    raise RuntimeError('Startup is still pending; no forced restart was performed. Check the QQ panel or phone login, then run again.')


def main():
    import sys
    if sys.stdout is not None:sys.stdout.reconfigure(encoding='utf8')
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8000)
    parser.add_argument('--action',choices=['start','stop','restart','status','panel'],default='start')
    args=parser.parse_args()
    try:
        if args.action=='panel':
            from saya_qq_panel import run_panel
            run_panel(args.port);return 0
        from saya_qq_panel import execute
        state=execute(args.action,args.port)
        if args.action in {'stop','status'}:
            import json
            print(json.dumps({k:v for k,v in state.items() if k!='auxiliary'},ensure_ascii=False),flush=True)
            return 0 if args.action=='status' or state['stopped'] else 1
    except (RuntimeError,ValueError,OSError) as error:
        print('STARTUP NOT COMPLETE: '+str(error),flush=True)
        return 1
    print('READY: Saya + NapCat + matching QQ account + message receiver.',flush=True)
    print(f'QQ settings: http://127.0.0.1:{args.port}/static/qq.html',flush=True)
    print('NapCat: http://127.0.0.1:6099/webui/',flush=True)
    return 0


if __name__=='__main__':raise SystemExit(main())
