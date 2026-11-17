import asyncio,json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from fastapi import FastAPI
from starlette.responses import StreamingResponse
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_memory_management import QQMemoryManagement,policy_content_revision
from saya_app.services.qq_memory_access import MemoryActor
from saya_app.services.qq_group_context import QQGroupContext,GroupPolicy
from saya_app.services.conversation import conversation_service
from saya_app.services import qq_delivery
from saya_app.routers import qq as routes
from test_qq_delivery import event
from test_qq_task_scheduler import make,until
from test_qq_chat_runtime import server


def submit(bridge,text,mid=1,user=20000,group=90000):
    return bridge.submit_signed(json.dumps(event(text,mid,user,group_id=group)).encode(),'Bearer inbound')


async def lab(tmp_path,single):
    kb=KnowledgeBase(str(tmp_path/'kb'));bridge=make(tmp_path/'bridge',None)
    await bridge.start()
    management=QQMemoryManagement(bridge,kb);bridge.memory_management=management
    group=QQGroupContext(bridge,kb);bridge.group_context=group
    responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),single,lambda:'api',
        lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
    responder.group_context=group;responder.memory_management=management;bridge.responder=responder
    return kb,bridge,group


def configure(group,**values):
    group.configure('20000','90000',GroupPolicy(**values).model_dump(),group.policy('90000')['revision'],confirmed=True)


def response(text):
    async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':text})+'\n\n'
    return StreamingResponse(body())


def test_real_bridge_serializes_public_group_lane_and_preserves_actor_task_scopes(tmp_path):
    async def run():
        entered=asyncio.Event();release=asyncio.Event();calls=[]
        async def single(req,channel_turn):
            calls.append(channel_turn)
            if len(calls)==1:entered.set();await release.wait()
            return response('FIRST_PUBLIC_ANSWER' if len(calls)==1 else 'SECOND_PUBLIC_ANSWER')
        kb,bridge,group=await lab(tmp_path,single);configure(group,context_enabled=True)
        try:
            submit(bridge,'甲提出话题',1);await asyncio.wait_for(entered.wait(),5)
            submit(bridge,'乙接着讨论',2,user=30000)
            await asyncio.sleep(.1);assert len(calls)==1
            release.set();await bridge.queue.join()
            assert len(calls)==2
            assert 'FIRST_PUBLIC_ANSWER' in json.dumps(calls[1]['group_context'])
            assert calls[1]['sender_id']=='30000' and calls[1]['history']==[]
            assert calls[0]['group_context']['history']==[]
            assert not any('FIRST_PUBLIC_ANSWER' in str(x) for x in bridge.store.conversation('10000:group:90000:user:20000:isolated')[0])
            assert not kb.memories
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_revoke_during_generation_or_before_continuation_blocks_old_source(tmp_path):
    async def run():
        release=asyncio.Event();entered=asyncio.Event();calls=[]
        async def single(req,channel_turn):
            calls.append(channel_turn);entered.set();await release.wait()
            return response('PRIVATE_DERIVED_CANARY。'*100)
        kb,bridge,group=await lab(tmp_path,single);configure(group,context_enabled=True,memory_enabled=True)
        record=kb.add_memory('主人喜欢茉莉茶',source='manual');owner=MemoryActor('10000','20000',True,True)
        group.access.set(owner,record['id'],policy_content_revision(record),'20000',visibility='groups',groups=('90000',))
        try:
            submit(bridge,'记忆',1);await asyncio.wait_for(entered.wait(),5)
            assert calls[0]['group_context']['facts']
            policy=group.access.get('10000',record['id'])
            group.access.set(owner,record['id'],policy_content_revision(record),'20000',expected_revision=policy.revision,visibility='private')
            release.set();await bridge.queue.join()
            assert not any('PRIVATE_DERIVED_CANARY' in row[1] for row in bridge.client.sent)
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_actual_web_group_configuration_requires_origin_owner_revision_and_confirmation(tmp_path):
    async def run():
        async def single(*a,**kw):return response('answer')
        _,bridge,group=await lab(tmp_path,single)
        app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as web:
                    assert (await web.get('/api/qq/group-context')).status_code==401
                    h={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'}
                    await web.post('/api/qq/ui-session',headers=h)
                    state=(await web.get('/api/qq/group-context')).json()
                    req={'owner_id':'20000','group_id':'90000','revision':state['revision'],'expected_revision':0,
                        'confirmed':False,'values':GroupPolicy(context_enabled=True).model_dump()}
                    assert (await web.put('/api/qq/group-context',json=req,headers=h)).status_code==403
                    req['confirmed']=True
                    assert (await web.put('/api/qq/group-context',json=req,headers=h)).status_code==200
                    assert (await web.put('/api/qq/group-context',json=req,headers=h)).status_code==409
                    req['expected_revision']=1;req['owner_id']='30000'
                    assert (await web.put('/api/qq/group-context',json=req,headers=h)).status_code==403
        finally:await bridge.stop()
    asyncio.run(run())


def test_reply_wakeup_default_off_same_group_receipt_only_and_rechecked(tmp_path):
    async def run():
        async def single(*a,**kw):return response('reply wake answer')
        _,bridge,group=await lab(tmp_path,single)
        try:
            submit(bridge,'初始问题');await bridge.queue.join()
            receipt=str(500+len(bridge.client.sent))
            raw=event('继续问',2,30000,group_id=90000)
            raw['message']=[{'type':'reply','data':{'id':receipt}},{'type':'text','data':{'text':'继续问'}}]
            assert bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='ignored'
            configure(group,reply_wakeup=True)
            assert bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='queued'
            await bridge.queue.join()
            assert any('reply wake answer' in r[1] for r in bridge.client.sent)
            raw['message_id']=3;raw['message'][0]['data']['id']='999999'
            assert bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='ignored'
            configure(group,reply_wakeup=False)
            raw['message_id']=4;raw['message'][0]['data']['id']=receipt
            assert bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')[0]=='ignored'
        finally:await bridge.stop()
    asyncio.run(run())


def test_actual_generator_receives_only_authorized_group_data_not_private_history(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    manager.save_message('user','PRIVATE_WEB_HISTORY_CANARY',session_id='session_private.jsonl')
    allowed=kb.add_memory('茉莉茶 GROUP_ALLOWED_CANARY',source='manual');kb.add_memory('UNPUBLISHED_PRIVATE_CANARY',source='manual')
    captured=[]
    async def run():
        bridge=make(tmp_path/'bridge',None);await bridge.start()
        service=QQMemoryManagement(bridge,kb);bridge.memory_management=service
        group=QQGroupContext(bridge,kb);bridge.group_context=group;configure(group,memory_enabled=True,context_enabled=True)
        owner=MemoryActor('10000','20000',True,True)
        group.access.set(owner,allowed['id'],policy_content_revision(allowed),'20000',visibility='groups',groups=('90000',))
        def model(req):
            captured.append(json.loads(req.content))
            data={'choices':[{'delta':{'content':'群内正常回答。'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',
                    lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'fixture'})
                responder.group_context=group;responder.memory_management=service;bridge.responder=responder
                try:submit(bridge,'你记得主人喜欢什么吗');await bridge.queue.join()
                finally:await bridge.stop()
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
        patch.object(conversation_service,'save_credentials',lambda **kw:None),
        patch.object(server,'refresh_daily_activity_memory',lambda:None)):
        asyncio.run(run())
    payload=json.dumps(captured,ensure_ascii=False)
    assert 'GROUP_ALLOWED_CANARY' in payload
    assert 'UNPUBLISHED_PRIVATE_CANARY' not in payload and 'PRIVATE_WEB_HISTORY_CANARY' not in payload
    assert any('Group/topic reference DATA' in str(x) for x in captured)
