"""Opt-in management of a dedicated QQ instance, including shared installed binaries."""

import json
import hashlib
from contextlib import ExitStack
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from .qq_lifecycle import InstanceLease, InstanceInUse, RestartPolicy, ServiceIntent
from .qq_controls import QQControlsConflict
from .shared_chat_config import revision_of
from .state_storage import atomic_save_json


class NapcatPolicyStore:
    def __init__(self, path):
        self.path=Path(path)

    def load(self):
        data=json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {'enabled':False,'generation':0}
        if not isinstance(data,dict) or set(data)-{'enabled','generation'} or not isinstance(data.get('enabled'),bool):
            raise ValueError('invalid NapCat supervision policy')
        data.setdefault('generation',0)
        if type(data['generation']) is not int or data['generation']<0:
            raise ValueError('invalid policy generation')
        return data | {'revision':revision_of(data)}

    def save(self, enabled, revision):
        if not isinstance(enabled,bool):
            raise ValueError('invalid supervision value')
        with InstanceLease(self.path.with_suffix('.lock')):
            previous=self.load()
            if previous['revision']!=revision:
                raise QQControlsConflict('NapCat守护设置已更新，请重新读取。')
            atomic_save_json(self.path,{'enabled':enabled,'generation':previous['generation']+1})
            return self.load()


def load_install(root):
    base=(Path(root)/'.runtime/napcat').resolve()
    raw=json.loads((base/'install.json').read_text(encoding='utf-8'))
    if not isinstance(raw,dict) or not re.fullmatch(r'[1-9]\d{0,19}',str(raw.get('bot_id',''))):
        raise ValueError('invalid dedicated installation')
    if raw.get('runtime_kind')=='external_shell':return _external_install(base,raw)
    if raw.get('runtime_kind') not in {None,'bundled'}:raise ValueError('unsupported QQ runtime kind')
    result={'bot_id':str(raw['bot_id'])}
    for key in ('install_dir','profile_dir'):
        value=Path(raw.get(key,'')).resolve()
        if not value.is_relative_to(base) or value==base or not value.is_dir():
            raise ValueError('dedicated installation must stay in workspace runtime')
        result[key]=value
    result.update(qq_exe=(result['install_dir']/'QQ.exe').resolve(),launcher=(result['install_dir']/'NapCatWinBootMain.exe').resolve())
    for key in ('qq_exe','launcher'):
        if not result[key].is_relative_to(base) or not result[key].is_file():
            raise ValueError('dedicated executable missing')
    return result


def _external_install(base,raw):
    """Explicitly bound installed QQ; executable sharing never grants ownership."""
    result={'bot_id':str(raw['bot_id']),'runtime_kind':'external_shell'}
    for key in ('profile_dir','runtime_dir'):
        value=Path(raw.get(key,'')).resolve()
        if not value.is_relative_to(base) or value==base or not value.is_dir():
            raise ValueError('managed profile and loader must stay in workspace runtime')
        result[key]=value
    install=Path(raw.get('install_dir',''))
    if not install.is_absolute() or str(install).startswith(('\\\\','//')):raise ValueError('invalid installed QQ directory')
    install=install.resolve();qq=(install/'QQ.exe').resolve()
    if qq.parent!=install or not qq.is_file():raise ValueError('installed QQ executable missing')
    expected=raw.get('qq_exe_sha256','')
    if not re.fullmatch('[a-f0-9]{64}',expected) or hashlib.sha256(qq.read_bytes()).hexdigest()!=expected:
        raise ValueError('installed QQ changed; explicit migration review required')
    version=raw.get('qq_version','')
    if not re.fullmatch(r'\d+\.\d+\.\d+-\d+',version):raise ValueError('invalid installed QQ version')
    current=json.loads((install/'versions/config.json').read_text('utf-8-sig')).get('curVersion')
    if current!=version or not (install/'versions'/version/'resources/app/wrapper.node').is_file():
        raise ValueError('installed QQ version changed; explicit migration review required')
    tag=raw.get('qq_instance_id','')
    if not re.fullmatch('[a-f0-9]{32}',tag):raise ValueError('managed QQ instance marker missing')
    names={'NapCatWinBootMain.exe','NapCatWinBootHook.dll','loadNapCat.js','qqnt.json','napcat.mjs'}
    hashes=raw.get('runtime_hashes')
    if not isinstance(hashes,dict) or set(hashes)!=names:raise ValueError('loader integrity binding missing')
    for name,digest in hashes.items():
        path=(result['runtime_dir']/name).resolve()
        if path.parent!=result['runtime_dir'] or not re.fullmatch('[a-f0-9]{64}',str(digest)) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
            raise ValueError('managed loader changed')
    result.update(install_dir=install,qq_exe=qq,launcher=result['runtime_dir']/'NapCatWinBootMain.exe',
        hook=result['runtime_dir']/'NapCatWinBootHook.dll',work_dir=result['runtime_dir'],qq_instance_id=tag)
    return result


def inspect_process(process):
    command=process.cmdline()
    exe=Path(process.exe()).resolve()
    kind='launcher' if exe.name.casefold()=='napcatwinbootmain.exe' else 'child' if any(a=='--type' or a.startswith('--type=') for a in command) else 'root'
    environment=process.environ()
    return {'pid':process.pid,'created':process.create_time(),'exe':str(exe),'cwd':str(Path(process.cwd()).resolve()),
            'profile':environment.get('NAPCAT_WORKDIR',''),'instance_id':environment.get('SAYA_QQ_INSTANCE_ID',''),
            'kind':kind,'ppid':process.ppid()}


def process_matches(record, config):
    try:
        import psutil
        actual=inspect_process(psutil.Process(int(record['pid'])))
        expected=config['launcher'] if record['kind']=='launcher' else config['qq_exe']
        return (abs(actual['created']-float(record['created']))<.01
                and Path(record['exe']).resolve()==expected==Path(actual['exe']).resolve()
                and Path(record['cwd']).resolve()==config.get('work_dir',config['install_dir'])==Path(actual['cwd']).resolve()
                and Path(record['profile']).resolve()==config['profile_dir']==Path(actual['profile']).resolve()
                and (config.get('runtime_kind')!='external_shell' or record.get('instance_id')==actual.get('instance_id')==config['qq_instance_id'])
                and actual['kind']==record['kind'])
    except Exception:
        return False


def discover(config):
    import psutil
    roots=[]; members=[]; uncertain=False; foreign=False
    for process in psutil.process_iter(['name']):
        if (process.info.get('name') or '').casefold() not in {'qq.exe','napcatwinbootmain.exe'}:
            continue
        try:
            # Only inspect configured executable paths; in shared-binary mode
            # profile AND instance marker distinguish the managed process.
            if Path(process.exe()).resolve() not in {config['qq_exe'],config['launcher']}:
                continue
            item=inspect_process(process)
            if config.get('runtime_kind')=='external_shell' and (item['profile']!=str(config['profile_dir']) or item.get('instance_id')!=config['qq_instance_id']):
                # Normal QQ processes can share the installed executable. Their
                # absence of our BOTH profile and instance marker is not a clash
                # and never makes them eligible for adoption or termination.
                if item['profile']==str(config['profile_dir']) or item.get('instance_id')==config['qq_instance_id']:foreign=True
                continue
            if Path(item['profile']).resolve()!=config['profile_dir'] or Path(item['cwd']).resolve()!=config.get('work_dir',config['install_dir']):
                foreign=True
                continue
            members.append(item)
            if item['kind']=='root':
                roots.append(item)
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied,OSError,ValueError):
            uncertain=True
    return {'roots':roots,'members':members,'uncertain':uncertain,'foreign':foreign}


class NapcatSupervisor:
    def __init__(self, root, directory=None):
        self.root=Path(root)
        self.directory=Path(directory) if directory is not None else self.root/'.runtime/qq-service'
        self.policy=NapcatPolicyStore(self.directory/'napcat-policy.json')
        self.path=self.directory/'napcat-status.json'
        self.owner_path=self.directory/'napcat-owner.json'

    def read_status(self):
        if not self.path.exists(): return {}
        data=json.loads(self.path.read_text(encoding='utf-8'))
        if not isinstance(data,dict): raise ValueError('invalid supervision state')
        return data

    def scan(self, config):
        return discover(config)

    def port_busy(self, _):
        for port in (6099,3000):
            try:
                with socket.create_connection(('127.0.0.1',port),timeout=.25): return True
            except OSError:
                pass
        return False

    def launch(self, config):
        environment=os.environ.copy()
        environment['NAPCAT_WORKDIR']=str(config['profile_dir'])
        command=[str(config['launcher']),config['bot_id']]
        if config.get('runtime_kind')=='external_shell':
            environment.update(SAYA_QQ_INSTANCE_ID=config['qq_instance_id'],
                NAPCAT_PATCH_PACKAGE=str(config['runtime_dir']/'qqnt.json'),NAPCAT_LOAD_PATH=str(config['runtime_dir']/'loadNapCat.js'),
                NAPCAT_INJECT_PATH=str(config['hook']),NAPCAT_MAIN_PATH=str(config['runtime_dir']/'napcat.mjs'))
            command=[str(config['launcher']),str(config['qq_exe']),str(config['hook']),config['bot_id']]
        options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}
        return subprocess.Popen(command,cwd=config.get('work_dir',config['install_dir']),env=environment,
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**options)

    def terminate(self, member, config):
        import psutil
        try:
            process=psutil.Process(member['pid'])
            if abs(process.create_time()-float(member['created']))>=.01 or not process_matches(member,config):
                return False
            # Retain this Process object's cached creation time through terminate.
            process.terminate()
            return True
        except (psutil.Error,OSError,ValueError,KeyError):
            return False

    def start_once(self, timeout=45, clock=None, sleep=None):
        """Explicit startup completes recovery without enabling ongoing supervision."""
        clock=clock or time.monotonic
        sleep=sleep or time.sleep
        self.directory.mkdir(parents=True,exist_ok=True)
        with InstanceLease(self.directory/'napcat-start-request.lock'):
            with InstanceLease(self.directory/'napcat-operation.lock'):
                state=self.read_status()
                # A deliberate start retries once with a fresh budget. Keep the
                # recorded process tree and all user policy/intent unchanged.
                state.update(failures=0,next_attempt=0.,retry_exhausted=False)
                atomic_save_json(self.path,state)
            deadline=clock()+max(0.,min(float(timeout),120.))
            waiting={'exit_grace','cleaning_owned_children','root_reappeared','waiting_launcher',
                     'starting','backoff','launch_failed'}
            last={'phase':'operation_pending'}
            while clock()<deadline:
                try:
                    last=self.tick(mode='running',force_start=True)
                    if last['phase'] not in waiting:
                        return last
                except InstanceInUse:
                    pass  # An existing operation keeps the OS lock; never bypass it.
                sleep(1)
            return last | {'phase':'start_pending','waiting_for':last['phase']}

    def tick(self, now=None, mode='running', force_start=False):
        now=time.time() if now is None else now
        self.directory.mkdir(parents=True,exist_ok=True)
        with ExitStack() as locks:
            locks.enter_context(InstanceLease(self.directory/'napcat-operation.lock'))
            intent=ServiceIntent(self.directory/'intent.json')
            if not force_start and intent.path.exists():
                # Serialize launch/cleanup against the persisted stop operation.
                locks.enter_context(InstanceLease(intent.path.with_suffix('.lock')))
                mode=intent.load()['mode']
            policy=self.policy.load()
            state=self.read_status()
            retry=RestartPolicy()
            retry.failures=min(retry.max_attempts,max(0,int(state.get('failures',0))))
            retry.next_attempt=float(state.get('next_attempt',0))
            if policy['enabled'] and state.get('policy_revision')!=policy['revision']:
                retry=RestartPolicy()  # Explicit disable/enable is a deliberate retry.
            def finish(phase, **changes):
                state.update(changes,phase=phase,updated=now,enabled=policy['enabled'],policy_revision=policy['revision'],**retry.public())
                atomic_save_json(self.path,state)
                return dict(state)
            if mode!='running': return finish('manual_stop')
            if not policy['enabled'] and not force_start: return finish('disabled')
            try: config=load_install(self.root)
            except (OSError,ValueError,KeyError,TypeError): return finish('installation_invalid')
            found=self.scan(config)
            if found['uncertain']: return finish('ownership_unknown')
            if found['foreign']: return finish('foreign_profile')
            if len(found['roots'])>1 or len(found['members'])>64: return finish('multiple_instances')
            if found['roots']:
                root=found['roots'][0]
                atomic_save_json(self.owner_path,{'members':found['members']})
                return finish('running_owned',root_created=root['created'],owned_root=True,lost_at=None,pending_launch=False)
            if state.get('owned_root') and state.get('lost_at') is None:
                retry.exited(now,max(0,now-float(state.get('root_created',now))))
                state['lost_at']=now
            if state.get('owned_root'):
                if now-float(state['lost_at'])<10: return finish('exit_grace')
                # Only a previously recorded dedicated process tree may be cleaned up,
                # and only after its root has disappeared. Never stop a live QQ root.
                owner=json.loads(self.owner_path.read_text(encoding='utf-8')) if self.owner_path.exists() else {}
                known={member['pid']:member for member in owner.get('members',[])}
                for member in found['members']:
                    old=known.get(member['pid'])
                    if not old or not process_matches(old,config): return finish('ownership_unknown')
                for member in found['members']:
                    if self.scan(config)['roots']: return finish('root_reappeared')
                    if not self.terminate(known[member['pid']],config): return finish('ownership_unknown')
                if found['members']: return finish('cleaning_owned_children')
                state['owned_root']=False
                state['lost_at']=None
            if found['members']:
                started=float(state.get('launch_at',now))
                state.setdefault('launch_at',now)
                return finish('startup_timeout' if now-started>120 else 'waiting_launcher')
            if state.get('pending_launch'):
                if now-float(state.get('launch_at',now))<10:
                    return finish('starting')
                retry.exited(now,max(0,now-float(state.get('launch_at',now))))
                state['pending_launch']=False
            if self.port_busy(config): return finish('blocked_foreign_port')
            if retry.failures>=retry.max_attempts: return finish('retry_exhausted')
            if not retry.may_start(now,mode): return finish('backoff')
            try:
                self.launch(config)
                return finish('starting',pending_launch=True,launch_at=now)
            except Exception:
                retry.exited(now,0)
                return finish('launch_failed',pending_launch=False)
