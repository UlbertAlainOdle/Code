import hashlib,json,zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from saya_app.services import qq_napcat as nap
from saya_app.services import qq_runtime_migration as migration


def external_lab(root,monkeypatch):
    base=root/'.runtime/napcat';base.mkdir(parents=True);profile=base/'profile';profile.mkdir()
    state=base/'install.json';state.write_text(json.dumps({'bot_id':'10000','profile_dir':str(profile),'install_dir':str(base/'old')}))
    desktop=root/'desktop-qq';app=desktop/'versions/9.9.36-53644/resources/app';app.mkdir(parents=True)
    exe=desktop/'QQ.exe';exe.write_bytes(b'installed QQ')
    for name in ['wrapper.node','major.node','application.asar','application.json']:(app/name).write_bytes(b'fixture')
    (desktop/'versions/config.json').write_text(json.dumps({'curVersion':'9.9.36-53644'}))
    archive=base/'NapCat.Shell.v4.18.41.verified.zip'
    with zipfile.ZipFile(archive,'w') as bundle:
        for name in migration.LOADER_FILES:bundle.writestr(name,b'fixture loader '+name.encode())
    monkeypatch.setattr(migration,'SHELL_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    stage=migration.stage_external_runtime(root,exe)
    return state,exe,stage


def test_staging_never_changes_active_config_or_existing_QQ_and_validates_bound_executable(tmp_path,monkeypatch):
    state,exe,stage=external_lab(tmp_path,monkeypatch)
    before=state.read_bytes();files={str(p):p.read_bytes() for p in exe.parent.rglob('*') if p.is_file()}
    assert hashlib.sha256(before).hexdigest()==stage['previous_install_sha256'] and stage['writes_to_existing_QQ']==0
    assert {str(p):p.read_bytes() for p in exe.parent.rglob('*') if p.is_file()}==files
    state.write_text(json.dumps(stage['candidate']));config=nap.load_install(tmp_path)
    assert config['qq_exe']==exe and config['profile_dir'].is_relative_to(tmp_path/'.runtime/napcat')
    assert config['work_dir']==config['runtime_dir'] and config['work_dir']!=exe.parent
    exe.write_bytes(b'updated QQ')
    with pytest.raises(ValueError,match='changed'):nap.load_install(tmp_path)


@pytest.mark.parametrize('mutate',[
    lambda raw,root:raw.update(profile_dir=str(root/'desktop-qq')),
    lambda raw,root:raw.update(runtime_dir=str(root/'desktop-qq')),
    lambda raw,root:raw.update(qq_instance_id=''),
    lambda raw,root:raw.update(qq_version='../../other'),
    lambda raw,root:raw['runtime_hashes'].update({'../private':'a'*64}),
])
def test_external_binding_cannot_expand_managed_profile_loader_or_identity(tmp_path,monkeypatch,mutate):
    state,_,stage=external_lab(tmp_path,monkeypatch);mutate(stage['candidate'],tmp_path)
    state.write_text(json.dumps(stage['candidate']))
    with pytest.raises(ValueError):nap.load_install(tmp_path)


def test_auto_updated_QQ_and_modified_loader_are_rejected_until_review(tmp_path,monkeypatch):
    state,exe,stage=external_lab(tmp_path,monkeypatch);state.write_text(json.dumps(stage['candidate']))
    version_file=exe.parent/'versions/config.json';version_file.write_text(json.dumps({'curVersion':'9.9.37-99999'}))
    with pytest.raises(ValueError,match='version changed'):nap.load_install(tmp_path)
    version_file.write_text(json.dumps({'curVersion':'9.9.36-53644'}))
    (Path(stage['candidate']['runtime_dir'])/'loadNapCat.js').write_bytes(b'unknown changed code')
    with pytest.raises(ValueError,match='loader changed'):nap.load_install(tmp_path)


def owned_record(config,pid=10):
    return {'pid':pid,'created':100.,'exe':str(config['qq_exe']),'cwd':str(config['work_dir']),
        'profile':str(config['profile_dir']),'instance_id':config['qq_instance_id'],'kind':'root','ppid':1}


def test_shared_executable_never_adopts_daily_QQ_or_confuses_ambiguous_marker(tmp_path,monkeypatch):
    state,exe,stage=external_lab(tmp_path,monkeypatch);state.write_text(json.dumps(stage['candidate']));config=nap.load_install(tmp_path)
    records=[owned_record(config),owned_record(config,11)|{'profile':'','instance_id':'','cwd':str(exe.parent)}]
    processes=[SimpleNamespace(info={'name':'QQ.exe'},exe=lambda:str(exe),index=i) for i in range(2)]
    with patch('psutil.process_iter',return_value=processes),patch.object(nap,'inspect_process',side_effect=lambda p:records[p.index]):
        found=nap.discover(config)
        assert len(found['roots'])==1 and [x['pid'] for x in found['members']]==[10] and not found['foreign']
        records[1]['instance_id']=config['qq_instance_id']
        assert nap.discover(config)['foreign']
    owner=records[0]
    with patch('psutil.Process'),patch.object(nap,'inspect_process',return_value=owner):
        assert nap.process_matches(owner,config)
        assert not nap.process_matches(owner|{'instance_id':'a'*32},config)
        assert not nap.process_matches(owner|{'created':10.},config)


def test_external_launch_uses_official_arguments_existing_profile_and_instance_marker(tmp_path,monkeypatch):
    state,exe,stage=external_lab(tmp_path,monkeypatch);state.write_text(json.dumps(stage['candidate']));config=nap.load_install(tmp_path)
    with patch.object(nap.subprocess,'Popen') as launch:
        nap.NapcatSupervisor(tmp_path).launch(config)
    args,kw=launch.call_args
    assert args[0]==[str(config['launcher']),str(exe),str(config['hook']),'10000']
    assert kw['cwd']==config['runtime_dir'] and kw['env']['NAPCAT_WORKDIR']==str(config['profile_dir'])
    assert kw['env']['SAYA_QQ_INSTANCE_ID']==config['qq_instance_id']
    assert kw['env']['NAPCAT_PATCH_PACKAGE']==str(config['runtime_dir']/'qqnt.json')


def test_shell_archive_is_verified_before_extraction_and_paths_cannot_escape(tmp_path,monkeypatch):
    state,exe,_=external_lab(tmp_path,monkeypatch);before=state.read_bytes();archive=state.parent/'NapCat.Shell.v4.18.41.verified.zip'
    with zipfile.ZipFile(archive,'a') as z:z.writestr('../outside',b'invalid')
    with pytest.raises(ValueError,match='mismatch'):migration.stage_external_runtime(tmp_path,exe)
    monkeypatch.setattr(migration,'SHELL_SHA256',hashlib.sha256(archive.read_bytes()).hexdigest())
    with pytest.raises(ValueError,match='unsafe'):migration.stage_external_runtime(tmp_path,exe)
    assert state.read_bytes()==before and not (state.parent/'outside').exists()


def test_external_media_never_trusts_shared_Tencent_directory(tmp_path):
    from saya_app.services.qq_media import QQMedia,MediaUnavailable
    # Reuse the established fixture's bridge without altering live installation.
    from test_qq_task_scheduler import make
    b=make(tmp_path/'bridge',None);b.store.initialize(7)
    shared=tmp_path/'Tencent';shared.mkdir();secret=shared/'WeChat-private.txt';secret.write_bytes(b'private')
    runtime=tmp_path/'loader';runtime.mkdir();profile=tmp_path/'profile';profile.mkdir()
    public=runtime/'protocol-image.bin';public.write_bytes(b'fixture')
    config={'bot_id':b.settings.bot_id,'profile_dir':profile,'install_dir':shared,'runtime_dir':runtime}
    with patch.object(nap,'load_install',return_value=config):
        media=QQMedia(b,tmp_path)
        assert media._read_bytes({'file':str(public)},100)==b'fixture'
        with pytest.raises(MediaUnavailable,match='cache_path_outside'):media._read_bytes({'file':str(secret)},100)


def test_migration_activation_is_CAS_and_preserves_identity_and_profile(tmp_path,monkeypatch):
    state,_,stage=external_lab(tmp_path,monkeypatch);before=state.read_bytes()
    monkeypatch.setattr(nap,'load_install',lambda _: {'legacy':'fixture'})
    monkeypatch.setattr(nap,'discover',lambda _: {'members':[],'uncertain':False,'foreign':False})
    with pytest.raises(ValueError,match='confirmation'):migration.activate_external_runtime(tmp_path,stage)
    assert state.read_bytes()==before
    candidate=stage['candidate'].copy();stage['candidate']=candidate|{'bot_id':'30000'}
    with pytest.raises(ValueError,match='identity'):migration.activate_external_runtime(tmp_path,stage,confirmed=True)
    assert state.read_bytes()==before
    stage['candidate']=candidate;migration.activate_external_runtime(tmp_path,stage,confirmed=True)
    assert json.loads(state.read_text())==candidate
    with pytest.raises(ValueError,match='changed since staging'):migration.activate_external_runtime(tmp_path,stage,confirmed=True)


def test_migration_never_replaces_live_old_runtime_or_discards_concurrent_install_edit(tmp_path,monkeypatch):
    state,_,stage=external_lab(tmp_path,monkeypatch);before=state.read_bytes()
    monkeypatch.setattr(nap,'load_install',lambda _: {'legacy':'fixture'})
    monkeypatch.setattr(nap,'discover',lambda _: {'members':[{}],'uncertain':False,'foreign':False})
    with pytest.raises(ValueError,match='must be stopped'):migration.activate_external_runtime(tmp_path,stage,confirmed=True)
    assert state.read_bytes()==before
    changed=json.loads(before);changed['note']='new user edit';state.write_text(json.dumps(changed));edited=state.read_bytes()
    with pytest.raises(ValueError,match='changed since staging'):migration.activate_external_runtime(tmp_path,stage,confirmed=True)
    assert state.read_bytes()==edited
