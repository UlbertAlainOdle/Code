"""New policy forms retain the existing local UI authentication and CAS."""
import asyncio
from unittest.mock import patch
import httpx
import pytest
from fastapi import FastAPI
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make


@pytest.mark.parametrize('name',['context-policy','forward-input'])
def test_new_policy_http_auth_revision_and_existing_settings_preserved(tmp_path,name):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();app=FastAPI();app.include_router(routes.router)
        origin='http://127.0.0.1:8000';headers={'Origin':origin,'X-Saya-QQ-UI':'1'}
        before=bridge.controls()['settings']
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=origin) as client:
                    path='/api/qq/'+name
                    assert (await client.get(path)).status_code==401
                    assert (await client.post('/api/qq/ui-session',headers=headers)).status_code==200
                    state=(await client.get(path)).json()
                    body={'owner_id':'20000','revision':state['revision'],'confirmed':True,'values':state['values']|{'enabled':True}}
                    assert (await client.put(path,json=body,headers={'Origin':'https://foreign.invalid'})).status_code==401
                    assert (await client.put(path,json=body|{'confirmed':False},headers=headers)).status_code==403
                    assert (await client.put(path,json=body|{'owner_id':'30000'},headers=headers)).status_code==403
                    result=await client.put(path,json=body,headers=headers)
                    assert result.status_code==200,result.text
                    assert result.json()['values']['enabled']
                    assert (await client.put(path,json=body,headers=headers)).status_code==409
                    assert (await client.get(path)).json()['values']['enabled']
            assert bridge.controls()['settings']==before
        finally:await bridge.stop()
    asyncio.run(run())
