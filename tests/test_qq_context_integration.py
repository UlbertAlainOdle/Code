import asyncio
import json
from unittest.mock import patch
import httpx
import pytest
from fastapi import FastAPI
from core_services import KnowledgeBase,MemoryManager
from saya_app.routers import qq as routes
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_context_links import QQContextLinks,web_history_source
from saya_app.services.conversation import conversation_service
from test_qq_chat_runtime import server
from test_qq_task_scheduler import make,submit
from test_qq_delivery import Client,config,event


def make_context_lab(tmp_path):
    manager=MemoryManager(str(tmp_path/'chats'))
    manager.save_message('user','SELECTED_WEB_CANARY',session_id='session_web_a.jsonl')
    manager.save_message('user','OTHER_WEB_CANARY',session_id='session_web_b.jsonl')
    async def model(*args):return 'ordinary answer'
    bridge=make(tmp_path/'bridge',model)
    bridge.context_links=QQContextLinks(bridge.store,web_history_source(manager))
    bridge.context_sessions=manager.get_session_list
    return bridge,manager


def test_web_confirmation_auth_scope_cas_and_revoke(tmp_path):
    async def run():
        bridge,manager=make_context_lab(tmp_path);await bridge.start()
        app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as client:
                    assert (await client.get('/api/qq/context-links')).status_code==401
                    headers={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'}
                    assert (await client.post('/api/qq/ui-session',headers=headers)).status_code==200
                    data=(await client.get('/api/qq/context-links')).json()
                    assert data['owner_id']=='20000' and len(data['sessions'])==2 and not data['state']['enabled']
                    body={'owner_id':'20000','generation':0,'revision':data['revision'],'session_id':'session_web_a.jsonl','action':'select'}
                    assert (await client.put('/api/qq/context-links',json=body,headers={'Origin':'http://evil.invalid'})).status_code==401
                    assert (await client.put('/api/qq/context-links',json=body|{'owner_id':'30000'},headers=headers)).status_code==403
                    assert (await client.put('/api/qq/context-links',json=body|{'revision':'stale'},headers=headers)).status_code==409
                    current=manager.current_session_file
                    result=await client.put('/api/qq/context-links',json=body,headers=headers)
                    assert result.status_code==200 and result.json()['state']['enabled']
                    assert manager.current_session_file==current
                    assert (await client.put('/api/qq/context-links',json=body,headers=headers)).status_code==409
                    assert (await client.put('/api/qq/context-links',json=body|{'action':'off','generation':1},headers=headers)).status_code==200
                    assert not bridge.context_links.state('10000:private:20000:shared')['enabled']
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('command',['/reset','/context off','/context new'])
def test_actual_qq_reset_and_stop_cut_links_without_deleting_web(tmp_path,command):
    async def run():
        bridge,manager=make_context_lab(tmp_path);await bridge.start()
        scope='10000:private:20000:shared'
        bridge.context_links.select(scope,'session_web_a.jsonl',0,confirmed=True)
        bridge.store.save_conversation(scope,['Doctor: OLD_CONTEXT','Saya: OLD_ANSWER'],'session_qq_old.jsonl')
        before=(tmp_path/'chats/session_web_a.jsonl').read_bytes()
        try:
            submit(bridge,command);await bridge.queue.join()
            assert not bridge.context_links.state(scope)['enabled']
            assert 'OLD_CONTEXT' not in str(bridge.store.conversation(scope)[0])
            assert (tmp_path/'chats/session_web_a.jsonl').read_bytes()==before
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'shared_memory':False},{'task_control_enabled':False}])
def test_real_shared_switch_revokes_even_without_next_question(tmp_path,change):
    async def run():
        bridge,_=make_context_lab(tmp_path);await bridge.start()
        scope='10000:private:20000:shared';bridge.context_links.select(scope,'session_web_a.jsonl',0,confirmed=True)
        try:
            data=bridge.controls()
            with patch('saya_app.services.qq_bridge.OneBotClient',return_value=Client()):
                await bridge.update_controls(data['settings']|change,data['revision'])
            assert not bridge.context_links.state(scope)['enabled']
        finally:await bridge.stop()
    asyncio.run(run())


def test_selection_and_fresh_topic_rollback_together_on_storage_failure(tmp_path,monkeypatch):
    bridge,_=make_context_lab(tmp_path);bridge.store.initialize(7)
    scope='10000:private:20000:shared'
    bridge.store.save_conversation(scope,['Doctor: ORIGINAL'],'session_qq_old.jsonl')
    def fail(*args):raise RuntimeError('synthetic storage failure')
    monkeypatch.setattr(bridge.context_links,'_start_topic',fail)
    with pytest.raises(RuntimeError):
        bridge.context_links.select(scope,'session_web_a.jsonl',0,confirmed=True,new_session='session_qq_new.jsonl')
    assert bridge.context_links.state(scope)['generation']==0
    assert bridge.store.conversation(scope)[0]==['Doctor: ORIGINAL']


def test_revoking_link_during_real_responder_blocks_late_answer(tmp_path):
    async def run():
        from types import SimpleNamespace
        from saya_app.services.qq_bridge import normalize_event
        bridge,_=make_context_lab(tmp_path);scope='10000:private:20000:shared'
        bridge.context_links.select(scope,'session_web_a.jsonl',0,confirmed=True)
        async def generate(req,channel_turn):
            assert channel_turn['context_link']['session_id']=='session_web_a.jsonl'
            async def body():
                bridge.context_links.revoke(scope)
                yield 'event: answer_complete\ndata: {"text":"LATE_ANSWER"}\n\n'
            return SimpleNamespace(body_iterator=body())
        responder=QQChatResponder(config(shared_memory=True),lambda **kw:SimpleNamespace(**kw),generate,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
        responder.context_links=bridge.context_links
        msg,_=normalize_event(event('continue'),config(shared_memory=True))
        with pytest.raises(RuntimeError,match='qq_context_changed'):await responder(msg,[],'session_qq_test.jsonl')
    asyncio.run(run())


def test_actual_generator_receives_only_selected_history_as_user_data(server,tmp_path):
    bridge,manager=make_context_lab(tmp_path);kb=KnowledgeBase(str(tmp_path/'kb'));captured=[]
    async def no_candidates(*args,**kw):return []
    async def run():
        async def model(req):
            captured.append(json.loads(req.content))
            data={'choices':[{'delta':{'content':'answer'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                responder=QQChatResponder(config(shared_memory=True),server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
                responder.context_links=bridge.context_links
                bridge.context_links.select('10000:private:20000:shared','session_web_a.jsonl',0,confirmed=True)
                from saya_app.services.qq_bridge import normalize_event
                msg,_=normalize_event(event('继续那个方案'),config(shared_memory=True))
                current=manager.current_session_file
                assert await responder(msg,[],'session_qq_test.jsonl')=='answer'
                assert manager.current_session_file==current
        text=json.dumps(captured,ensure_ascii=False)
        assert 'SELECTED_WEB_CANARY' in text and 'OTHER_WEB_CANARY' not in text
        assert all('SELECTED_WEB_CANARY' not in str(m['content']) for m in captured[0]['messages'] if m['role']=='system')
        assert any('archived conversation DATA' in str(m['content']) for m in captured[0]['messages'] if m['role']=='user')
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())
