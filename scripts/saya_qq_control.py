"""Verified inventory and controlled stop; no process-name-wide termination."""
import json,os,re,subprocess,time
from pathlib import Path
import psutil
import manage_qq_service as service
from saya_app.services.qq_napcat import load_install
from saya_app.services.omni_tts import linux_path
from saya_app.services.state_storage import atomic_save_json

ROOT=service.ROOT
AUX={'scripts/qwen_tts_worker.py':'语音辅助','scripts/watch_qwen_omni.py':'语音退出守护',
     'scripts/start_qq.ps1':'应用启动外壳','minecraft_worker/worker.mjs':'Minecraft辅助'}


def auxiliary_record(process,root=ROOT):
    """Exact entry, executable family and cwd, never a substring in -c code."""
    command=process.cmdline();exe=Path(process.exe());cwd=Path(process.cwd()).resolve();root=Path(root).resolve()
    if exe.name.lower() not in {'python.exe','pythonw.exe','powershell.exe','pwsh.exe','node.exe','wsl.exe'}:return None
    for entry,label in AUX.items():
        script=root/entry
        if str(script).casefold() not in {x.casefold() for x in command}:continue
        if cwd not in {root,root/'minecraft_worker'}:return None
        return {'pid':process.pid,'created':process.create_time(),'exe':str(exe),'cwd':str(cwd),'command':command,'kind':label}
    if exe.name.lower()=='wsl.exe' and linux_path(root/'scripts/run_qwen_omni.py') in command:
        if cwd==root and '--root' in command and command.index('--root')+1<len(command) and command[command.index('--root')+1]==linux_path(root):
            return {'pid':process.pid,'created':process.create_time(),'exe':str(exe),'cwd':str(cwd),'command':command,'kind':'WSL语音启动外壳'}
    return None


def terminate_aux(record):
    try:
        p=psutil.Process(record['pid']);actual=auxiliary_record(p)
        if not actual or actual!=record:return False
        p.terminate();return True
    except (psutil.Error,OSError,ValueError):return False


def voice_inventory(stop=False):
    if not (ROOT/'.runtime/qwen-omni-installed.json').exists():return {'processes':[],'uncertain':[]}
    path=ROOT/'.runtime/qq-service/voice-audit-tokens.json'
    known=json.loads(path.read_text('utf8')) if path.exists() else []
    options=service.process_options()
    result=subprocess.run(['wsl.exe','--list','--running','--quiet'],capture_output=True,timeout=8,**options)
    if result.returncode:raise RuntimeError('WSL运行状态无法核验。')
    raw=result.stdout;names=raw.decode('utf-16-le' if b'\x00' in raw else 'utf8',errors='replace')
    if 'Ubuntu' not in names.split():return {'processes':[],'uncertain':[]}
    command=['wsl.exe','-d','Ubuntu','--','python3',linux_path(ROOT/'scripts/inspect_saya_voice.py'),'--root',linux_path(ROOT)]
    if stop:command.append('--stop')
    result=subprocess.run(command,input=json.dumps({'tokens':known}).encode('utf8'),capture_output=True,timeout=25 if stop else 10,**options)
    if result.returncode:raise RuntimeError('WSL语音进程核验失败。')
    state=json.loads(result.stdout.decode('utf8') if isinstance(result.stdout,bytes) else result.stdout)
    atomic_save_json(path,state['tokens'])
    return state


def inventory(port=8000):
    issues=[];owned=[]
    for name,script,label in [('backend-owner.json',service.RUNNER,'应用后端'),('supervisor-owner.json',service.SCRIPT,'应用守护')]:
        record=service.read_record(name)
        if service.owns(record,script):owned.append({'pid':record['pid'],'kind':label})
        elif record.get('pid') and psutil.pid_exists(record['pid']):
            try:
                if abs(psutil.Process(record['pid']).create_time()-record.get('created',0))<.01:issues.append(label+'记录仍在但归属未确认。')
            except psutil.Error:issues.append(label+'的进程记录无法核验。')
    try:
        found=service.NapcatSupervisor(ROOT).scan(load_install(ROOT))
        owned.extend({'pid':x['pid'],'kind':'专用QQ / NapCat'} for x in found['members'])
        if found['uncertain'] or found['foreign']:issues.append('专用QQ有归属不明的进程，不能确认完全退出。')
    except Exception:issues.append('专用QQ安装绑定/进程无法核验。')
    auxiliary=[]
    for p in psutil.process_iter(['pid','name']):
        if (p.info['name'] or '').lower() not in {'python.exe','pythonw.exe','powershell.exe','pwsh.exe','node.exe','wsl.exe'}:continue
        try:
            value=auxiliary_record(p)
            if value:auxiliary.append(value);owned.append({'pid':value['pid'],'kind':value['kind']})
            elif Path(p.cwd()).resolve()==ROOT and p.pid not in {x['pid'] for x in owned}:
                args=p.cmdline()
                if (str(service.RUNNER) in args or (str(service.SCRIPT) in args and 'watch' in args)
                    or ('-m' in args and 'uvicorn' in args and 'server5-22B----:app' in args)):
                    owned.append({'pid':p.pid,'kind':'未受管项目后端 / 守护（仅提示，保留）'})
                    issues.append('有未受管项目进程，请在其原窗口正常退出。')
        except psutil.NoSuchProcess:pass
        except (psutil.AccessDenied,OSError):
            # Other users' protected processes cannot be owned here. Our
            # recorded backend/QQ checks independently report ambiguity.
            continue
    try:
        voice=voice_inventory();owned.extend({'pid':x['pid'],'kind':'WSL语音 / GPU'} for x in voice['processes'])
        if voice['uncertain']:issues.append('WSL语音进程有不可读取的记录。')
    except Exception:issues.append('WSL语音检查失败，不能确认无GPU残留。')
    ports=[p for p in {port,3000,6099,8091} if service.port_open(p)]
    if ports and not owned:issues.append('相关端口仍被占用，未认领的服务会保留。')
    return {'processes':owned,'auxiliary':auxiliary,'ports':sorted(ports),'issues':issues,
            'stopped':not owned and not ports and not issues,'health':service.probe_health(port) if service.port_open(port) else {}}


def local_api(path,body=None,port=8000,timeout=45):
    from urllib.request import Request,build_opener,ProxyHandler
    req=Request(f'http://127.0.0.1:{port}'+path,data=json.dumps(body).encode() if body is not None else None,
        headers={'Content-Type':'application/json','Origin':f'http://127.0.0.1:{port}'})
    with build_opener(ProxyHandler({})).open(req,timeout=timeout) as response:return json.load(response)


def busy(status):
    return bool(status.get('active_web_requests') or any(status.get('counts',{}).get(k,0) for k in ('queued','generating','sending','voice_rendering','active_tasks')))


def stop_system(port=8000,timeout=90,progress=print):
    record=service.read_record('backend-owner.json');backend=service.owns(record,service.RUNNER)
    if service.port_open(port) and not backend:raise RuntimeError('8000被非受管后端占用。请在其原窗口正常退出，未强杀。')
    initial=inventory(port)  # Capture active voice tokens before unload removes markers.
    if initial['stopped']:return initial
    progress('停止接收新任务，等待正在执行的任务结束……')
    with service.InstanceLease(service.DIRECTORY/'control.lock'):
        service.ServiceIntent(service.DIRECTORY/'intent.json').set('manual_stop')
    if backend:
        deadline=time.monotonic()+timeout
        service.set_drain(port,True)
        try:service.local_request(port,'lifecycle/cancel-pending',{'confirmed':True},timeout=10)
        except Exception as error:
            from urllib.error import HTTPError
            if not isinstance(error,HTTPError) or error.code not in {404,405}:raise
        while True:
            status=service.set_drain(port,True)
            if not busy(status):break
            if time.monotonic()>=deadline:raise RuntimeError('仍有任务运行，已暂停新接收；没有强行结束任务，请稍后再次停止。')
            time.sleep(1)
        progress('释放语音模型及相关辅助服务……')
        service.local_request(port,'lifecycle/stop-workers',{'confirmed':True},timeout=60)
        result=service.stop('manual_stop',port)
        if result['result'] not in {'owned_backend_stopping','no_owned_backend'}:raise RuntimeError('应用退出未确认：'+result['result'])
    deadline=time.monotonic()+45
    while any(service.owns(service.read_record(n),p) for n,p in [('backend-owner.json',service.RUNNER),('supervisor-owner.json',service.SCRIPT)]):
        if time.monotonic()>=deadline:raise RuntimeError('应用或守护仍在退出，未强杀；请检查状态后重试。')
        time.sleep(.3)
    if service.ServiceIntent(service.DIRECTORY/'intent.json').load()['mode']!='manual_stop':raise RuntimeError('另一启动操作改变了意图，停止操作已中止。')
    progress('停止本项目专用QQ / NapCat，核对语音残留……')
    nap=service.NapcatSupervisor(ROOT);config=load_install(ROOT)
    with service.InstanceLease(service.DIRECTORY/'napcat-operation.lock'):
        found=nap.scan(config)
        if found['uncertain'] or found['foreign']:raise RuntimeError('QQ进程归属不明，未执行关闭。')
        for member in sorted(found['members'],key=lambda x:x['kind']!='root'):nap.terminate(member,config)
    voice_inventory(stop=True)
    # Revalidate every native helper against its exact script, executable,
    # cwd, full command and birth time; never stop arbitrary descendants.
    for record in inventory(port)['auxiliary']:terminate_aux(record)
    deadline=time.monotonic()+20
    while True:
        state=inventory(port)
        if state['stopped']:return state
        if time.monotonic()>=deadline:raise RuntimeError('仍检测到进程、占用端口或核验缺口；未标记全部停止，请查看面板。')
        time.sleep(1)
