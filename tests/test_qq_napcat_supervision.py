import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from saya_app.services.qq_controls import QQControlsConflict
from saya_app.services.qq_napcat import NapcatSupervisor, NapcatPolicyStore, load_install, process_matches


def installation(root):
    directory=root / '.runtime/napcat/install'
    profile=root / '.runtime/napcat/profile'
    directory.mkdir(parents=True); profile.mkdir()
    for name in ('QQ.exe','NapCatWinBootMain.exe'):
        (directory/name).touch()
    path=root / '.runtime/napcat/install.json'
    path.write_text(json.dumps({'install_dir':str(directory),'profile_dir':str(profile),'bot_id':'10000'}))
    return load_install(root)


def record(config, pid=10, kind='root'):
    return {'pid':pid,'created':100.,'cwd':str(config['install_dir']),
            'exe':str(config['qq_exe'] if kind!='launcher' else config['launcher']),
            'profile':str(config['profile_dir']),'kind':kind,'ppid':1}


def fake_scan(roots=(), members=(), uncertain=False, foreign=False):
    return {'roots':list(roots),'members':list(members),'uncertain':uncertain,'foreign':foreign}


def supervisor(root, monkeypatch, scan):
    installation(root)
    item=NapcatSupervisor(root)
    monkeypatch.setattr(item,'scan',lambda _: scan[0])
    monkeypatch.setattr(item,'port_busy',lambda _: False)
    return item


def test_policy_is_opt_in_versioned_and_atomic(tmp_path):
    policy=NapcatPolicyStore(tmp_path/'policy.json')
    before=policy.load()
    assert not before['enabled'] and not policy.path.exists()
    enabled=policy.save(True,before['revision'])
    with pytest.raises(QQControlsConflict): policy.save(False,before['revision'])
    with patch('saya_app.services.qq_napcat.atomic_save_json',side_effect=OSError('disk')):
        with pytest.raises(OSError): policy.save(False,enabled['revision'])
    assert policy.load()['enabled']


def test_install_paths_cannot_escape_workspace_and_invalid_account_is_rejected(tmp_path):
    config=installation(tmp_path)
    assert config['qq_exe'].name=='QQ.exe'
    path=tmp_path/'.runtime/napcat/install.json'
    data=json.loads(path.read_text())
    data['profile_dir']=str(tmp_path.parent)
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_install(tmp_path)
    data['profile_dir']=str(tmp_path/'.runtime/napcat/profile')
    data['bot_id']='--malicious-option'
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError): load_install(tmp_path)


def test_identity_requires_pid_creation_executable_workdir_and_profile(tmp_path):
    config=installation(tmp_path)
    owner=record(config)
    actual=owner.copy()
    with patch('psutil.Process'), patch('saya_app.services.qq_napcat.inspect_process',side_effect=lambda *_:actual):
        assert process_matches(owner,config)
        for key,value in [('created',999.),('exe',str(tmp_path/'daily/QQ.exe')),
                          ('cwd',str(tmp_path.parent)),('profile',str(tmp_path/'other'))]:
            actual=owner | {key:value}
            assert not process_matches(owner,config)


def test_disabled_maintenance_foreign_port_and_unknown_process_never_launch(tmp_path,monkeypatch):
    scan=[fake_scan()]
    item=supervisor(tmp_path,monkeypatch,scan)
    launched=[]
    monkeypatch.setattr(item,'launch',lambda _:launched.append(True))
    assert item.tick(100.,'running')['phase']=='disabled'
    p=item.policy.load(); item.policy.save(True,p['revision'])
    assert item.tick(100.,'maintenance')['phase']=='manual_stop'
    monkeypatch.setattr(item,'port_busy',lambda _:True)
    assert item.tick(101.,'running')['phase']=='blocked_foreign_port'
    monkeypatch.setattr(item,'port_busy',lambda _:False)
    scan[0]=fake_scan(uncertain=True)
    assert item.tick(102.,'running')['phase']=='ownership_unknown'
    scan[0]=fake_scan(foreign=True)
    assert item.tick(103.,'running')['phase']=='foreign_profile'
    assert not launched


def test_existing_root_is_adopted_without_restarting_and_budget_survives_new_watchdog(tmp_path,monkeypatch):
    scan=[fake_scan()]; item=supervisor(tmp_path,monkeypatch,scan)
    config=load_install(tmp_path); owner=record(config)
    scan[0]=fake_scan([owner],[owner])
    p=item.policy.load(); item.policy.save(True,p['revision'])
    with patch.object(item,'launch') as launch:
        assert item.tick(110.,'running')['phase']=='running_owned'
        launch.assert_not_called()
    scan[0]=fake_scan()
    assert item.tick(111.,'running')['phase']=='exit_grace'
    assert item.tick(112.,'running')['failures']==1
    restarted=NapcatSupervisor(tmp_path)
    monkeypatch.setattr(restarted,'scan',lambda _:scan[0])
    monkeypatch.setattr(restarted,'port_busy',lambda _:False)
    monkeypatch.setattr(restarted,'launch',lambda _:SimpleNamespace(pid=123))
    data=restarted.tick(122.,'running')
    assert data['phase']=='starting' and data['failures']==1


def test_five_failures_stop_and_explicit_enable_resets_budget(tmp_path,monkeypatch):
    item=supervisor(tmp_path,monkeypatch,[fake_scan()])
    p=item.policy.load(); item.policy.save(True,p['revision'])
    starts=[]
    def launch(_):
        starts.append(1)
        raise OSError('private credentials must not appear in status')
    monkeypatch.setattr(item,'launch',launch)
    for now in range(100,240): result=item.tick(float(now),'running')
    assert len(starts)==5 and result['phase']=='retry_exhausted'
    assert 'private' not in json.dumps(result)
    fresh=NapcatSupervisor(tmp_path)
    assert fresh.read_status()['failures']==5
    p=item.policy.load(); item.policy.save(False,p['revision'])
    p=item.policy.load(); item.policy.save(True,p['revision'])
    item.tick(300.,'running')
    assert len(starts)==6


def test_reused_or_foreign_members_are_never_terminated(tmp_path,monkeypatch):
    scan=[fake_scan()]; item=supervisor(tmp_path,monkeypatch,scan)
    config=load_install(tmp_path)
    root=record(config); child=record(config,11,'child')
    p=item.policy.load(); item.policy.save(True,p['revision'])
    scan[0]=fake_scan([root],[root,child]); item.tick(110.,'running')
    scan[0]=fake_scan(members=[child])
    item.tick(111.,'running')
    with patch('saya_app.services.qq_napcat.process_matches',return_value=False), patch.object(item,'terminate') as terminate:
        data=item.tick(125.,'running')
        assert data['phase']=='ownership_unknown'
        terminate.assert_not_called()


def test_real_helper_process_identity_exit_and_recovery_without_touching_qq(tmp_path,monkeypatch):
    import os,subprocess,sys,time,psutil
    from saya_app.services import qq_napcat as module
    config=installation(tmp_path)
    config=config | {'qq_exe':Path(sys.executable).resolve(),'launcher':Path(sys.executable).resolve()}
    monkeypatch.setattr(module,'load_install',lambda _:config)
    item=NapcatSupervisor(tmp_path)
    children=[]
    def launch(_):
        child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],cwd=config['install_dir'],
            env=os.environ | {'NAPCAT_WORKDIR':str(config['profile_dir'])},stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        children.append(child)
        return child
    def scan(_):
        records=[]
        for child in children:
            if child.poll() is None:
                try: records.append(module.inspect_process(psutil.Process(child.pid)))
                except psutil.Error: pass
        return fake_scan(records,records)
    monkeypatch.setattr(item,'launch',launch)
    monkeypatch.setattr(item,'scan',scan)
    monkeypatch.setattr(item,'port_busy',lambda _:False)
    p=item.policy.load();item.policy.save(True,p['revision'])
    try:
        now=time.time()
        assert item.tick(now,'running')['phase']=='starting'
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            if scan(config)['roots']: break
            time.sleep(.02)
        assert item.tick(now+1,'running')['phase']=='running_owned'
        owner=scan(config)['roots'][0]
        assert process_matches(owner,config)
        assert not process_matches(owner | {'created':owner['created']-20},config)
        children[0].terminate(); children[0].wait(timeout=5)
        assert item.tick(now+2,'running')['phase']=='exit_grace'
        assert item.tick(now+13,'running')['phase']=='starting'
        assert len(children)==2 and children[0].pid!=children[1].pid
    finally:
        for child in children:
            if child.poll() is None: child.terminate()
            child.wait(timeout=5)


def test_explicit_start_finishes_exit_grace_with_supervision_off_and_resets_only_retry_budget(tmp_path,monkeypatch):
    item=NapcatSupervisor(tmp_path)
    item.directory.mkdir(parents=True)
    item.path.write_text(json.dumps({'phase':'retry_exhausted','failures':5,'next_attempt':999.,'owned_root':True}))
    policy_before=item.policy.load()
    phases=iter(['exit_grace','cleaning_owned_children','starting','running_owned'])
    calls=[];clock=[100.]
    def tick(now=None,mode='running',force_start=False):
        calls.append(force_start)
        return {'phase':next(phases)}
    monkeypatch.setattr(item,'tick',tick)
    result=item.start_once(timeout=10,clock=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
    assert result['phase']=='running_owned' and calls==[True]*4
    assert item.policy.load()==policy_before and not item.policy.path.exists()
    assert item.read_status()['failures']==0 and item.read_status()['owned_root']


def test_explicit_start_is_bounded_and_does_not_retry_ambiguous_ownership(tmp_path,monkeypatch):
    item=NapcatSupervisor(tmp_path);clock=[10.];calls=[]
    def tick(**kwargs): calls.append(True);return {'phase':'exit_grace'}
    monkeypatch.setattr(item,'tick',tick)
    result=item.start_once(timeout=3,clock=lambda:clock[0],sleep=lambda n:clock.__setitem__(0,clock[0]+n))
    assert result['phase']=='start_pending' and len(calls)<=4
    calls.clear()
    monkeypatch.setattr(item,'tick',lambda **kwargs:{'phase':'ownership_unknown'})
    assert item.start_once(timeout=3)['phase']=='ownership_unknown'
