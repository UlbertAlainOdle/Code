import ast,base64,contextlib,io,json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx,numpy as np,pytest
from saya_app.services.local_tts import LocalTTS,VoiceSettings
from test_qq_voice import wav


def test_transformers_ephemeral_protocol_has_no_web_preview_or_settings_write(tmp_path):
    service=LocalTTS(tmp_path,tmp_path/'tts');settings=VoiceSettings(engine='transformers')
    def rpc(operation,**kw):
        if operation=='status':return {'ephemeral_audio':True}
        assert kw['ephemeral'] is True and kw['text']=='你好。'
        return {'audio_base64':base64.b64encode(wav()).decode()}
    with patch.object(service,'rpc',side_effect=rpc),patch.object(service,'unload') as unload:
        assert service.synthesize_ephemeral('你好。',settings)==wav()
        assert not service.data.exists();unload.assert_not_called()


def test_old_running_worker_cannot_publish_private_voice_as_web_preview(tmp_path):
    service=LocalTTS(tmp_path,tmp_path/'tts');calls=[]
    def rpc(operation,**kw):calls.append(operation);return {'running':True}
    with patch.object(service,'rpc',side_effect=rpc),patch.object(service,'unload') as unload:
        with pytest.raises(RuntimeError,match='空闲重载'):service.synthesize_ephemeral('你好。',VoiceSettings(engine='transformers'))
        assert calls==['status'] and not service.data.exists();unload.assert_not_called()


def test_cancellation_while_waiting_worker_capability_cannot_start_later_synthesis(tmp_path):
    from saya_app.services.qq_tasks import TaskCancelled
    service=LocalTTS(tmp_path,tmp_path/'tts');permitted=True;calls=[]
    def checkpoint():
        if not permitted:raise TaskCancelled('cancelled')
    def rpc(operation,**kw):
        nonlocal permitted
        calls.append(operation);permitted=False;return {'ephemeral_audio':True}
    with patch('saya_app.services.qq_tasks.checkpoint_current',checkpoint),patch.object(service,'rpc',side_effect=rpc):
        with pytest.raises(TaskCancelled):service.synthesize_ephemeral('你好。',VoiceSettings(engine='transformers'))
    assert calls==['status'] and not service.data.exists()


def test_omni_ephemeral_uses_existing_voice_bounded_bytes_no_unload_or_public_asset(tmp_path):
    service=LocalTTS(tmp_path,tmp_path/'tts');settings=VoiceSettings(engine='vllm_omni',temperature=.7)
    with patch.object(service.omni,'synthesize',return_value=wav()) as synth,patch.object(service,'_watch_idle'),patch.object(service,'_unload_worker') as unload:
        assert service.synthesize_ephemeral('你好。',settings)==wav()
        assert synth.call_args.kwargs['max_bytes']==2*1024*1024
        assert synth.call_args.args[1].temperature==.7 and not service.data.exists();unload.assert_not_called()


def test_cloud_or_disabled_tts_never_receives_private_qq_answer(tmp_path):
    service=LocalTTS(tmp_path,tmp_path/'tts')
    with patch.object(service,'rpc') as rpc,patch.object(service.omni,'synthesize') as omni:
        for settings in [VoiceSettings(enabled=False),VoiceSettings(provider='elevenlabs')]:
            with pytest.raises(ValueError):service.synthesize_ephemeral('private text',settings)
        rpc.assert_not_called();omni.assert_not_called()


def worker_function(tmp_path,samples=None,output=None):
    source=ast.parse(Path('scripts/qwen_tts_worker.py').read_text(encoding='utf-8-sig'))
    main=next(n for n in source.body if isinstance(n,ast.FunctionDef) and n.name=='main')
    execute=next(n for n in main.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
    fake_torch=SimpleNamespace(cuda=SimpleNamespace(reset_peak_memory_stats=lambda:None),manual_seed=lambda *a:None,inference_mode=contextlib.nullcontext)
    fake_sf=SimpleNamespace(write=lambda stream,*a,**kw:stream.write(output if output is not None else wav()))
    model=SimpleNamespace(generate_voice_design=lambda **kw:([np.ones(samples or 6000,dtype=np.float32)*.1],24000))
    namespace={'torch':fake_torch,'np':np,'sf':fake_sf,'io':io,'base64':base64,'time':__import__('time'),
        'data':tmp_path/'data','load':lambda *a:None,'model':model,'status':lambda:{'ephemeral_audio':True}}
    exec(compile(ast.Module(body=[execute],type_ignores=[]),'<actual-worker-execute>','exec'),namespace)
    return namespace['execute']


def test_actual_worker_ephemeral_branch_returns_bounded_wav_and_creates_no_preview(tmp_path):
    execute=worker_function(tmp_path)
    request={'op':'synthesize','identifier':'a'*32,'text':'你好。','settings':VoiceSettings().model_dump(),'ephemeral':True}
    result=execute(request)
    assert base64.b64decode(result['audio_base64'])==wav() and not (tmp_path/'data').exists()


@pytest.mark.parametrize('oversize',['duration','bytes'])
def test_actual_worker_rejects_large_ephemeral_audio_before_writing_cache(tmp_path,oversize):
    execute=worker_function(tmp_path,samples=61*24000 if oversize=='duration' else None,output=b'a'*(2*1024*1024+1) if oversize=='bytes' else None)
    with pytest.raises(ValueError):execute({'op':'synthesize','identifier':'a'*32,'text':'test','settings':VoiceSettings().model_dump(),'ephemeral':True})
    assert not (tmp_path/'data').exists()


def test_bounded_omni_download_refuses_oversize_and_finishes_shared_request_once(tmp_path):
    service=LocalTTS(tmp_path,tmp_path/'tts');actual_client=httpx.Client;calls=[]
    def handler(request):calls.append(request);return httpx.Response(200,content=b'x'*1001)
    with (patch.object(service.omni,'begin'),patch.object(service.omni,'finished') as finished,
        patch('saya_app.services.omni_tts.httpx.Client',lambda **kw:actual_client(transport=httpx.MockTransport(handler)))):
        with pytest.raises(ValueError):service.omni.synthesize('test',VoiceSettings(),max_bytes=1000)
        assert len(calls)==1;finished.assert_called_once()
