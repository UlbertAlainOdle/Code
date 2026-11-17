import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo
import pytest
import json
from unittest.mock import patch
from test_qq_chat_runtime import server
from saya_app.services.qq_context_salience import QQContextSalience,ContextPolicy,chat_day,topic_request
from test_qq_task_scheduler import make,submit


def stamp(text,zone='Asia/Shanghai'):
    return datetime.fromisoformat(text).replace(tzinfo=ZoneInfo(zone)).timestamp()


@pytest.mark.parametrize('zone',['Asia/Shanghai','America/New_York'])
def test_chat_day_calendar_boundary_and_dst(zone):
    assert chat_day(stamp('2026-11-03T03:59:59',zone),zone)=='2026-11-02'
    assert chat_day(stamp('2026-11-03T04:00:00',zone),zone)=='2026-11-03'
    assert chat_day(stamp('2026-11-01T03:30:00',zone),zone)=='2026-10-31'


@pytest.mark.parametrize('text',['他说“我们换个话题吧”是什么意思','不要换个话题','我们换个话题吧是什么意思','先别聊点别的','/search 换个话题'])
def test_quoted_negated_and_discussed_topic_phrases_are_not_operations(text):
    assert topic_request(text) is None


def test_natural_topic_request_keeps_new_question():
    assert topic_request('我们换个话题吧，聊聊电影')=='聊聊电影'
    assert topic_request('前面不谈了')==''


def test_selection_downweights_old_day_unknown_and_old_topic_without_deleting(tmp_path):
    bridge=make(tmp_path,None);bridge.store.initialize(7)
    now=[stamp('2026-11-03T11:00:00')]
    service=QQContextSalience(bridge,clock=lambda:now[0])
    state=service.state();service.configure('20000',state['revision'],{'enabled':True})
    scope='10000:private:20000:shared';sid='session_qq_test.jsonl'
    rows=[{'role':'user','content':'旧话题电影'*20,'time':stamp('2026-11-02T14:00:00')},
          {'role':'ai','content':'未知电影'*20,'time':None},
          {'role':'user','content':'新话题电影'*20,'time':now[0]-20}]
    snap=service.snapshot(scope)
    output=service.select_rows(scope,snap,rows,'电影',budget=200)
    old=[r for r in output if r['content'].startswith(('旧','未知'))]
    assert sum(service.tokens(r['content']) for r in old)<=40
    assert any(r['content'].startswith('新') for r in output)
    assert len(rows)==3
    service.switch(scope,'one')
    assert not service.valid(snap)
    assert service.switch(scope,'one')['generation']==service.snapshot(scope)['generation']


def test_owner_config_conflicts_and_group_focus_isolation(tmp_path):
    bridge=make(tmp_path,None);bridge.store.initialize(7);service=QQContextSalience(bridge)
    with pytest.raises(PermissionError):service.configure('30000',0,{'enabled':True})
    service.configure('20000',0,{'enabled':True})
    with pytest.raises(ValueError):service.configure('20000',0,{'enabled':False})
    a='10000:group:90000:user:20000:isolated';b='10000:group:90000:user:30000:isolated'
    other=service.snapshot(b);service.switch(a,'switch-1')
    assert service.valid(other)


def test_bridge_natural_switch_retains_archive_but_model_gets_new_topic(tmp_path):
    async def run():
        seen=[]
        async def model(message,history,session):
            seen.append((message.text,list(history)));return '电影可以聊科幻片。'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            service=bridge.context_salience
            service.configure('20000',service.state()['revision'],{'enabled':True})
            submit(bridge,'旧话题是天气');await bridge.queue.join()
            submit(bridge,'我们换个话题吧，聊聊电影',2);await bridge.queue.join()
            assert seen[-1][0]=='聊聊电影'
            assert not any('旧话题是天气' in x for x in seen[-1][1])
            history,_=bridge.store.conversation('10000:private:20000:shared')
            assert any('旧话题是天气' in x for x in history)
            assert not any('我们换个话题吧' in x for x in history)
        finally:await bridge.stop()
    asyncio.run(run())


def test_current_generation_is_not_old_due_to_second_resolution(tmp_path):
    bridge=make(tmp_path,None);bridge.store.initialize(7);now=stamp('2026-11-03T12:00:00')+.8
    service=QQContextSalience(bridge,clock=lambda:now);service.configure('20000',0,{'enabled':True})
    scope='10000:private:20000:shared';snap=service.switch(scope,'switch')
    rows=[{'role':'user','content':'NEW_CONTEXT','time':int(now),'qq_context':{'generation':snap['generation']}}]
    assert service.select_rows(scope,snap,rows)==rows


def test_actual_shared_generator_does_not_reload_demoted_history(server,tmp_path):
    import httpx
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    service=QQContextSalience(bridge);service.configure('20000',0,{'enabled':True})
    scope='10000:private:20000:shared';snap=service.switch(scope,'new-topic')
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'));sid='session_qq_filter.jsonl';seen=[]
    manager.save_message('user','OLD_CONTEXT_CANARY',metadata={'qq_context':{'generation':0}},session_id=sid)
    async def run():
        async def model(request):
            seen.append(json.loads(request.content));data={'choices':[{'delta':{'content':'new answer'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                req=server.ChatWithMotion(user_input='全新的问题',session_id=sid,model='api',api_key='fake',
                    api_url='http://mock/v1',recall_mode='off',agent_mode=False,memory_write_enabled=False)
                response=await server._chat_stream_single(req,channel_turn={'history':[],'shared':True,'is_owner':True,
                    'context_selector':lambda lines,rows:service.project(scope,sid,lines,rows,snapshot=snap),'qq_context':snap})
                async for _ in response.body_iterator:pass
        assert seen and 'OLD_CONTEXT_CANARY' not in json.dumps(seen[-1])
        assert 'OLD_CONTEXT_CANARY' in str(manager.load_session_full(sid,make_current=False))
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(conversation_service,'save_credentials',lambda **kw:None)):
        asyncio.run(run())
