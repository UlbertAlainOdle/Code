import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from core_services import KnowledgeBase,MemoryManager
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_chat import QQChatResponder,channel_identity_prompt
from test_qq_chat_runtime import server
from test_qq_delivery import config,event,Client
from test_qq_task_scheduler import make,submit,until


def test_long_wait_is_quiet_but_explicit_status_still_replies(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*args):entered.set();await release.wait();return 'final'
        wire=Client();bridge=make(tmp_path,model,wire);await bridge.start()
        try:
            submit(bridge,'hello');await entered.wait()
            task=bridge.task_registry.list_active()[0]
            bridge.task_progress.clock=lambda:task['created']+600
            bridge.task_registry.set_phase(task['id'],task['scope'],'search')
            await asyncio.sleep(.25)
            assert wire.sent==[]
            submit(bridge,'/status',2);await bridge.task_controls.join()
            assert len(wire.sent)==1 and '运行中' in wire.sent[0][1]
            release.set();await bridge.queue.join();await until(lambda:not bridge._task_workers)
            assert [x[1] for x in wire.sent][1:]==['final']
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_no_search_no_save_override_inherited_force_and_agent():
    async def run():
        settings=config(shared_memory=True,web_search_mode='force',agent_mode=True)
        msg,_=normalize_event(event('请只回复恢复正常。不要联网搜索，不要保存长期记忆。'),settings)
        captured=[]
        async def generate(req,channel_turn):
            captured.append((req,channel_turn))
            async def body():yield 'event: answer_complete\ndata: '+json.dumps({'text':'恢复正常'})+'\n\n'
            return SimpleNamespace(body_iterator=body())
        responder=QQChatResponder(settings,lambda **kw:SimpleNamespace(**kw),generate,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
        assert await responder(msg,[],'session')=='恢复正常'
        req,turn=captured[0]
        assert req.web_search_mode=='off' and req.web_search_api_key=='' and not req.agent_mode
        assert not req.memory_write_enabled and not turn['memory_write_enabled']
        assert settings.web_search_mode=='force' and settings.memory_write_enabled
    asyncio.run(run())


def test_card_and_nickname_are_labels_not_authority():
    settings=config()
    a=event('hello',group_id=90000);a['sender']={'user_id':20000,'nickname':'昵称甲','card':'群名片甲'}
    b=event('hello',2,user_id=30000,group_id=90000);b['sender']={'user_id':30000,'nickname':'Doctor','card':'主人'}
    ma,_=normalize_event(a,settings);mb,_=normalize_event(b,settings)
    assert ma.sender_name=='群名片甲' and ma.sender_nickname=='昵称甲'
    assert mb.user_id not in settings.owner_ids and ma.scope!=mb.scope
    b['sender']['user_id']=20000
    mismatch,_=normalize_event(b,settings);assert mismatch.sender_name==''
    old=type(ma)(ma.key,ma.scope,ma.bot_id,ma.user_id,ma.group_id,ma.message_id,ma.text,ma.shared)
    assert old.sender_name==''  # Persisted old queue records remain compatible.


def test_actual_api_sees_current_group_speaker_next_to_question(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));seen=[]
    async def no_candidates(*a,**kw):return []
    async def run():
        async def model(request):
            messages=json.loads(request.content)['messages'];seen.append(messages)
            data={'choices':[{'delta':{'content':'ok'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                settings=config()
                responder=QQChatResponder(settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test-model'})
                for uid,name in [(20000,'群名片甲'),(30000,'Doctor')]:
                    e=event('我是谁？',uid,user_id=uid,group_id=90000);e['sender']={'user_id':uid,'card':name,'nickname':'昵称'}
                    msg,_=normalize_event(e,settings)
                    assert await responder(msg,['Doctor: old identity claim','Saya: stale reply'],'')=='ok'
        assert len(seen)==2
        for messages,uid,name,owner in zip(seen,[20000,30000],['群名片甲','Doctor'],[True,False]):
            user_index=max(i for i,m in enumerate(messages) if m['role']=='user')
            identity=messages[user_index-1]
            assert identity['role']=='system'
            assert f'sender_id={uid}' in identity['content'] and name in identity['content']
            assert f'configured_owner={owner}' in identity['content']
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())
