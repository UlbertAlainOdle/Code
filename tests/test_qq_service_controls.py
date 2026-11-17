import asyncio
import json
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from saya_app import config
from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.qq_runtime_status import runtime_status


def test_runtime_projection_excludes_owner_and_invalid_private_status(tmp_path):
    directory=tmp_path/'.runtime/qq-service';directory.mkdir(parents=True)
    (directory/'supervisor-owner.json').write_text(json.dumps({'pid':-1,'command':['private-key']}))
    (directory/'napcat-status.json').write_text(json.dumps({'phase':{'private':'input'},'updated':'bad-private-time','pid':99}))
    with patch('saya_app.services.qq_runtime_status.matches_identity',return_value=False):
        result=runtime_status(tmp_path)
    assert result['napcat']['phase']=='unknown' and result['napcat']['stale']
    assert 'private' not in json.dumps(result) and 'pid' not in json.dumps(result)


def test_service_policy_requires_admin_local_origin_and_current_revision(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'PROJECT_ROOT',tmp_path)
    bridge=QQBridge(QQSettings(enabled=True,bot_id='10000',access_token='fake-out',webhook_token='fake-in',
        admin_token='fake-admin',allowed_users={'20000'}),QQStore(tmp_path/'unused.sqlite3'))
    app=FastAPI();app.include_router(routes.router)
    async def run():
        with patch.object(routes,'qq_bridge',bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
                assert (await client.get('/api/qq/service')).status_code==401
                client.headers['Authorization']='Bearer fake-admin'
                before=(await client.get('/api/qq/service')).json()
                assert not before['napcat']['policy']['enabled']
                body={'enabled':True,'revision':before['napcat']['policy']['revision']}
                assert (await client.put('/api/qq/service/napcat-supervision',json=body)).status_code==403
                assert (await client.put('/api/qq/service/napcat-supervision',json=body,headers={'Origin':'https://foreign.example'})).status_code==403
                response=await client.put('/api/qq/service/napcat-supervision',json=body,headers={'Origin':'http://127.0.0.1:8000'})
                assert response.status_code==200 and response.json()['enabled']
                assert (await client.put('/api/qq/service/napcat-supervision',json=body,headers={'Origin':'http://127.0.0.1:8000'})).status_code==409
        assert bridge.task is None and not bridge.store.path.exists()
    asyncio.run(run())
