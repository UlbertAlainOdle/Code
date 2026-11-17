import asyncio,json,time
from unittest.mock import patch
import httpx,pytest
from fastapi import FastAPI
from core_services import KnowledgeBase
from saya_app.services.qq_contacts import QQContactReview
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make
from test_qq_delivery import event


def request(user=30000,flag='PRIVATE_FLAG_FIXTURE'):
    return {'post_type':'request','request_type':'friend','self_id':10000,'user_id':user,'time':time.time(),
        'flag':flag,'comment':'<script>friend comment</script>'}


def test_friend_requests_never_auto_accept_or_expose_flag_or_allow_stranger(tmp_path):
    async def run():
        bridge=make(tmp_path/'bridge',None,auto_accept_owner_invites=True)
        await bridge.start();bridge.contact_review=QQContactReview(bridge);calls=[]
        async def call(*args):calls.append(args);return {}
        bridge.client.call=call
        try:
            assert bridge.submit_signed(json.dumps(request()).encode(),'Bearer inbound')[0]=='pending'
            assert bridge.submit_signed(json.dumps(request()).encode(),'Bearer inbound')[0]=='duplicate'
            state=bridge.contact_review.state()
            assert not calls and 'PRIVATE_FLAG_FIXTURE' not in json.dumps(state)
            before=bridge.settings.allowed_users
            rid=state['requests'][0]['id']
            await bridge.contact_review.decide('20000',rid,True,confirmed=True)
            assert calls[0][0]=='set_friend_add_request' and calls[0][1]['flag']=='PRIVATE_FLAG_FIXTURE'
            assert bridge.settings.allowed_users==before
            with pytest.raises(ValueError):await bridge.contact_review.decide('20000',rid,True,confirmed=True)
            assert len(calls)==1
            stranger=event('stranger message',user_id=40000)
            assert bridge.submit_signed(json.dumps(stranger).encode(),'Bearer inbound')[0]=='ignored'
        finally:await bridge.stop()
    asyncio.run(run())


def test_friend_timeout_and_restart_unknown_are_not_replayed(tmp_path):
    async def run():
        bridge=make(tmp_path/'bridge',None);await bridge.start();review=QQContactReview(bridge)
        async def fail(*args):raise TimeoutError('private transport')
        bridge.client.call=fail
        try:
            review.submit(request());rid=review.state()['requests'][0]['id']
            with pytest.raises(TimeoutError):await review.decide('20000',rid,True,confirmed=True)
            assert review.state()['requests'][0]['state']=='result_unknown'
            with pytest.raises(ValueError):await QQContactReview(bridge).decide('20000',rid,True,confirmed=True)
            assert review.submit(request())[0]=='duplicate'
        finally:await bridge.stop()
    asyncio.run(run())


def test_friend_review_http_requires_owner_confirmation_and_current_config(tmp_path):
    async def run():
        b=make(tmp_path/'bridge',None);await b.start();b.memory_management=QQMemoryManagement(b,KnowledgeBase(str(tmp_path/'kb')))
        b.contact_review=QQContactReview(b);b.contact_review.submit(request());calls=[]
        async def call(*args):calls.append(args);return {}
        b.client.call=call;app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                    assert (await c.get('/api/qq/contact-requests')).status_code==401
                    h={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'};await c.post('/api/qq/ui-session',headers=h)
                    state=(await c.get('/api/qq/contact-requests')).json();rid=state['requests'][0]['id']
                    value={'owner_id':'20000','revision':state['revision'],'approve':True,'confirmed':False}
                    assert (await c.post('/api/qq/contact-requests/'+rid,json=value,headers=h)).status_code==409
                    value['confirmed']=True;value['revision']='stale'
                    assert (await c.post('/api/qq/contact-requests/'+rid,json=value,headers=h)).status_code==409
                    value['revision']=state['revision'];value['owner_id']='30000'
                    assert (await c.post('/api/qq/contact-requests/'+rid,json=value,headers=h)).status_code==403
                    value['owner_id']='20000'
                    assert (await c.post('/api/qq/contact-requests/'+rid,json=value,headers=h)).status_code==200
                    assert len(calls)==1
        finally:await b.stop()
    asyncio.run(run())
