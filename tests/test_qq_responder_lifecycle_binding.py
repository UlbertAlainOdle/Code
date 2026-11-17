import asyncio
from types import SimpleNamespace
import json
from fastapi.responses import StreamingResponse
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.qq_chat import QQChatResponder
from test_qq_person_events import lab
from test_qq_chat_runtime import server


def test_late_responder_replacement_receives_existing_services_and_cross_scope_state(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            private=msg('帮我跟进虚构星湾计划，先约09:20集合。');await s.observe(private,[]);mid=s.cards(private)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('继续事件 '+mid+'，公开补充：集合改为10:40。',group=90000),[])
            async def single(req,channel_turn):
                assert channel_turn['event_context']['refs'][0]['id']==mid
                assert '10:40' in str(channel_turn['event_context']['cards'])
                async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'10:40'})+'\n\n'
                return StreamingResponse(body())
            fresh=QQChatResponder(b.settings,lambda **kwargs:SimpleNamespace(**kwargs),single,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            b.bind_responder(fresh)
            for name in ('dialogue_experience','context_salience','forward_input','relationships','episodes'):
                assert getattr(fresh,name) is getattr(b,name)
            result=await fresh(msg('之前那个星湾计划目前几点集合？只回复当前时间。'),[],'private-cross-event')
            assert str(result)=='10:40'
    asyncio.run(run())


def test_actual_server_startup_rebinds_after_start_has_already_created_services(server,tmp_path,monkeypatch):
    async def run():
        async with lab(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            class Coordinator:
                def __init__(self,*args,**kwargs):pass
                async def start(self):pass
                async def stop(self):pass
            import saya_app.services.proactive_runtime as proactive
            monkeypatch.setattr(proactive,'ProactiveRuntime',Coordinator)
            monkeypatch.setattr(server,'qq_bridge',b)
            monkeypatch.setattr(server,'kb_system',KnowledgeBase(str(tmp_path/'kb')))
            monkeypatch.setattr(server,'memory_manager',MemoryManager(str(tmp_path/'chat')))
            monkeypatch.setattr(server.app.state,'service_activity',SimpleNamespace(),raising=False)
            previous=server.companion_router.proactive
            try:
                await server._startup_qq_bridge()
                for name in ('dialogue_experience','context_salience','forward_input','relationships','episodes'):
                    assert getattr(b.responder,name) is getattr(b,name)
                assert b.responder.episodes is s
                from saya_app.routers import qq as routes
                monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
                with TestClient(app) as client:
                    assert client.get('/api/qq/context-services').status_code==401
                    live=client.get('/api/qq/context-services',headers={'Authorization':'Bearer admin'}).json()
                    assert live['ready'] and all(live['bindings'].values())
            finally:server.companion_router.proactive=previous
    asyncio.run(run())
