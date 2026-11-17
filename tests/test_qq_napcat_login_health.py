import asyncio
import hashlib
import json

import httpx
import pytest

from saya_app.services.qq_napcat_login_health import NapcatLoginProbe
from saya_app.services.qq_health import inspect_health
from saya_app.services.qq_bridge import QQBridge,QQSettings,QQStore


def config(root):
    base=root/'.runtime/napcat';install=base/'install';profile=base/'profile'
    install.mkdir(parents=True);(profile/'config').mkdir(parents=True)
    for name in ('QQ.exe','NapCatWinBootMain.exe'):(install/name).touch()
    (base/'install.json').write_text(json.dumps({'install_dir':str(install),'profile_dir':str(profile),'bot_id':'10000'}))
    (profile/'config/webui.json').write_text(json.dumps({'port':6099,'token':'fake-webui-token'}))


@pytest.mark.parametrize('body,state',[
    ({'isLogin':False,'isOffline':False,'loginPhase':'qrcode_waiting'},'login_required'),
    ({'isLogin':True},'logged_in_unverified'),
    ({'isLogin':False,'isOffline':True},'offline'),
    ({'isLogin':False,'qrLoginAccepted':True,'loginPhase':'initializing'},'login_initializing'),
])
def test_read_only_webui_probe_discards_qrcode_errors_and_reuses_credential(tmp_path,body,state):
    config(tmp_path);calls=[]
    def respond(request):
        calls.append(request.url.path)
        if request.url.path=='/api/auth/login':
            payload=json.loads(request.content)
            assert payload=={'hash':hashlib.sha256(b'fake-webui-token.napcat').hexdigest()}
            return httpx.Response(200,json={'code':0,'data':{'Credential':'fake-ephemeral-credential'}})
        assert request.url.path=='/api/QQLogin/CheckLoginStatus'
        assert request.headers['Authorization']=='Bearer fake-ephemeral-credential'
        return httpx.Response(200,json={'code':0,'data':body | {'qrcodeurl':'private-qr','loginError':'private-error'}})
    async def run():
        probe=NapcatLoginProbe(tmp_path,transport=httpx.MockTransport(respond))
        assert await probe.probe()==state
        assert await probe.probe()==state
        assert calls.count('/api/auth/login')==1
    asyncio.run(run())


def test_webui_auth_failure_is_unknown_and_does_not_repeat_login(tmp_path):
    config(tmp_path);calls=[]
    def respond(request):
        calls.append(request.url.path)
        return httpx.Response(200,json={'code':-1,'message':'private-auth-error'})
    assert asyncio.run(NapcatLoginProbe(tmp_path,transport=httpx.MockTransport(respond)).probe())=='unknown'
    assert calls==['/api/auth/login']


def test_health_reports_relogin_and_network_offline_even_when_onebot_is_not_started(tmp_path):
    class Client:
        async def call(self,*args):raise ConnectionError('private-details')
        async def close(self):pass
    bridge=QQBridge(QQSettings(bot_id='10000'),QQStore(tmp_path/'unused.sqlite3'))
    async def run():
        for state in ('login_required','offline','login_initializing','logged_in_unverified'):
            async def probe():return state
            result=await inspect_health(bridge,{},lambda _:Client(),login_probe=probe)
            assert result['napcat']=='unreachable' and result['qq_login']==state
            assert 'private' not in json.dumps(result)
            if state=='offline': assert not any('扫码' in text for text in result['requires_action'])
    asyncio.run(run())


@pytest.mark.parametrize('payload',[[],{'code':0,'data':['private-bad-data']},{'code':0,'data':{}}])
def test_invalid_management_response_never_becomes_a_login_claim(tmp_path,payload):
    config(tmp_path)
    transport=httpx.MockTransport(lambda _:httpx.Response(200,json=payload))
    assert asyncio.run(NapcatLoginProbe(tmp_path,transport=transport).probe())=='unknown'
