import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import saya_qq_control as control
import saya_qq_panel as panel


def fake_process(script,*,cwd=None,birth=12):
    root=control.ROOT
    return SimpleNamespace(pid=999,cmdline=lambda:['python.exe',str(script)],exe=lambda:str(root/'python.exe'),
        cwd=lambda:str(cwd or root),create_time=lambda:birth)


def test_auxiliary_ownership_rejects_same_name_other_project_or_inline_command():
    root=control.ROOT;entry=root/'scripts/qwen_tts_worker.py'
    assert control.auxiliary_record(fake_process(entry))['kind']=='语音辅助'
    assert control.auxiliary_record(fake_process(entry,cwd=root.parent)) is None
    assert control.auxiliary_record(fake_process(root.parent/'scripts/qwen_tts_worker.py')) is None
    p=fake_process(entry);p.cmdline=lambda:['python.exe','-c','print('+str(entry)+')']
    assert control.auxiliary_record(p) is None


def test_pid_reuse_or_command_change_blocks_auxiliary_termination():
    p=fake_process(control.ROOT/'scripts/qwen_tts_worker.py');record=control.auxiliary_record(p);p.create_time=lambda:13
    with patch.object(control.psutil,'Process',return_value=p):assert not control.terminate_aux(record)


@pytest.mark.parametrize('status',[{'active_web_requests':1},{'counts':{'voice_rendering':1}},{'counts':{'queued':1}},{'counts':{'sending':1}}])
def test_stop_waits_for_web_voice_and_qq_work(status):assert control.busy(status)


def test_foreign_backend_stop_never_contacts_apis_or_terminates():
    with patch.object(control.service,'read_record',return_value={}),patch.object(control.service,'owns',return_value=False),patch.object(control.service,'port_open',return_value=True),patch.object(control,'local_api') as api:
        with pytest.raises(RuntimeError,match='非受管'):control.stop_system()
        api.assert_not_called()


def test_restart_does_not_start_if_stop_cannot_be_verified():
    from start_saya_qq import start_system
    with patch.object(panel.service,'InstanceLease'),patch.object(panel,'stop_system',side_effect=RuntimeError('remaining')),patch('start_saya_qq.start_system') as start:
        with pytest.raises(RuntimeError,match='remaining'):panel.execute('restart')
        start.assert_not_called()


def test_stop_already_clean_has_no_configuration_or_termination_side_effects():
    clean={'stopped':True}
    with patch.object(control.service,'read_record',return_value={}),patch.object(control.service,'owns',return_value=False),patch.object(control.service,'port_open',return_value=False),patch.object(control,'inventory',return_value=clean),patch.object(control.service,'stop') as stop:
        assert control.stop_system()==clean;stop.assert_not_called()


def test_panel_close_starts_stop_and_does_not_destroy_early():
    p=object.__new__(panel.Panel);p.busy=False;p.closing=False;calls=[];p.perform=lambda a:calls.append(a)
    p.close();assert p.closing and calls==['stop']
    p.busy=True;p.append=lambda t:calls.append('wait');p.close();assert calls==['stop','wait']


@pytest.mark.parametrize('error,stopped,destroyed',[(None,True,True),('unverified',False,False)])
def test_panel_destroys_only_after_verified_stop_otherwise_stays_open(error,stopped,destroyed):
    import queue
    calls=[];p=object.__new__(panel.Panel);p.events=queue.Queue();p.events.put(('done',('stop',{'stopped':stopped},error)))
    p.busy=True;p.buttons=[];p.closing=True;p.show=lambda s:None;p.append=lambda t:None
    p.root=SimpleNamespace(destroy=lambda:calls.append('destroy'),after=lambda *a:None)
    with patch.object(panel.messagebox,'showwarning'):p.poll()
    assert ('destroy' in calls)==destroyed
    if error:assert not p.closing


def test_voice_cleanup_revalidates_birth_and_token_before_each_signal():
    import inspect_saya_voice as voice
    row={'pid':4,'birth':'100','token':'a'*32}
    with patch.object(voice,'scan',return_value={'processes':[row],'uncertain':[],'tokens':['a'*32]}),patch.object(voice,'same',return_value=False),patch.object(voice.os,'kill') as kill:
        voice.cleanup(Path('/fixture'),[]);kill.assert_not_called()


def test_worker_stop_endpoint_requires_auth_confirmation_and_idle_drain(tmp_path):
    import asyncio,httpx
    from fastapi import FastAPI
    from saya_app.routers import qq as routes
    from saya_app.services.qq_bridge import QQBridge,QQSettings,QQStore
    from saya_app.services.service_activity import ServiceActivity
    from saya_app.services.local_tts import local_tts
    from saya_app.services.minecraft_mineflayer import minecraft_bot_service
    b=QQBridge(QQSettings(admin_token='test-admin'),QQStore(tmp_path/'bridge.sqlite3'))
    app=FastAPI();app.include_router(routes.router);app.state.service_activity=ServiceActivity()
    async def run():
        with patch.object(routes,'qq_bridge',b),patch.object(local_tts,'unload') as unload,patch.object(minecraft_bot_service,'stop') as stop:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                path='/api/qq/lifecycle/stop-workers';body={'confirmed':True}
                assert (await c.post(path,json=body)).status_code==401
                c.headers['Authorization']='Bearer test-admin'
                assert (await c.post(path,json=body)).status_code==409
                b.draining=True
                assert (await c.post(path,json={'confirmed':False})).status_code==403
                app.state.service_activity.active_web_requests=1
                assert (await c.post(path,json=body)).status_code==409
                unload.assert_not_called();stop.assert_not_called()
                app.state.service_activity.active_web_requests=0
                assert (await c.post(path,json=body)).json()=={'stopped':True}
                unload.assert_called_once();stop.assert_called_once()
                assert app.state.service_activity.active_web_requests==0
    asyncio.run(run())
