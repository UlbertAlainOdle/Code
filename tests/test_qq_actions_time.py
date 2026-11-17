import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch
import json
import pytest
import httpx
from core_services import MemoryManager,KnowledgeBase
from saya_app.services.conversation import conversation_service
from saya_app.services.qq_action_proposals import QQActionProposals
from saya_app.services.chat_time_context import clock_context,previous_web_time
from saya_app.services.chat_time_context import QQTurnTimes,timestamp_history
from saya_app.services.qq_chat import QQChatResponder
from test_qq_tasks import Store
from test_qq_task_scheduler import make,submit
from test_qq_delivery import Client,config,event
from test_qq_chat_runtime import server


@pytest.fixture
def lab(tmp_path):
    now=[1000.0];service=QQActionProposals(Store(tmp_path/'proposals.sqlite3'),clock=lambda:now[0])
    scope='10000:private:20000:shared';session='session_qq_fixture.jsonl'
    return service,scope,session,now


def prepare(lab,deliver=True):
    service,scope,session,_=lab
    service.ask(scope,session,'search_on',{},'要开启搜索吗？','r1')
    if deliver:service.delivered(scope,session,'要开启搜索吗？')


def test_consent_requires_confirmed_question_and_is_one_time(lab):
    service,scope,session,_=lab;prepare(lab,False)
    assert service.reply(scope,session,'同意','r1',True) is None
    service.delivered(scope,session,'要开启搜索吗？')
    action=service.reply(scope,session,'同意','r1',True)
    assert action['status']=='approved'
    service.finish(action['id'],True)
    assert service.reply(scope,session,'同意','r1',True)['status']=='already'


def test_quote_of_another_message_does_not_authorize_pending_action(lab):
    service,scope,session,_=lab;prepare(lab)
    assert service.reply(scope,session,'同意','r1',True,reply_to='999')['status']=='unrelated_reply'
    assert service.reply(scope,session,'同意','r1',True)['status']=='approved'


@pytest.mark.parametrize('text,state',[('不用','rejected'),('同意','approved'),('好的','approved')])
def test_natural_answers(lab,text,state):
    service,scope,session,_=lab;prepare(lab)
    assert service.reply(scope,session,text,'r1',True)['status']==state


def test_other_scope_session_stale_revision_and_expiry_cannot_consent(lab):
    service,scope,session,now=lab;prepare(lab)
    assert service.reply('10000:private:30000:shared',session,'同意','r1',True) is None
    assert service.reply(scope,'session_qq_other.jsonl','同意','r1',True) is None
    assert service.reply(scope,session,'同意','changed',True)['status']=='stale'
    prepare(lab);now[0]+=601
    assert service.reply(scope,session,'同意','r1',True)['status']=='expired'


def test_topic_change_and_ambiguous_approval_never_execute(lab):
    service,scope,session,_=lab;prepare(lab)
    assert service.reply(scope,session,'好的，但是帮我讲故事','r1',True) is None
    assert service.reply(scope,session,'同意','r1',True) is None
    with pytest.raises(ValueError):service.ask(scope,session,'shell',{'cmd':'anything'},'run?','r1')
    with pytest.raises(ValueError):service.ask('10000:group:90000',session,'search_on',{},'run?','r1')


def test_crash_during_execution_never_retries(lab):
    service,scope,session,_=lab;prepare(lab)
    assert service.reply(scope,session,'同意','r1',True)['status']=='approved'
    recovered=QQActionProposals(service.store,clock=lambda:1001)
    assert recovered.reply(scope,session,'同意','r1',True)['status']=='already'


def test_approval_for_a_different_action_does_not_match(lab):
    service,scope,session,_=lab;prepare(lab)
    assert service.reply(scope,session,'保存吧','r1',True) is None


@pytest.mark.parametrize('kind',['search_on','save_candidate'])
def test_question_delivery_and_consent_use_the_available_host_services(tmp_path,kind):
    async def run():
        bridge=make(tmp_path,None,web_search_mode='off');await bridge.start();calls=[];requests=[]
        proposals=QQActionProposals(bridge.store);bridge.action_proposals=proposals
        async def generate(req,channel_turn):
            requests.append(req)
            async def body():
                yield 'event: memory_candidates\ndata: '+json.dumps({'candidates':[{'id':'candidate-a','title':'某种茶偏好'}]})+'\n\n'
                yield 'event: answer_complete\ndata: {"text":"明白了。"}\n\n'
            return SimpleNamespace(body_iterator=body())
        responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),generate,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test','brave_search_api_key':'fake-search'},lambda c,a:calls.append((c,a)) or '已确认并保存长期记忆。')
        responder.action_proposals=proposals;responder.configuration_provider=bridge.controls;bridge.responder=responder
        try:
            submit(bridge,'今天有什么新闻吗？' if kind=='search_on' else '我喜欢某种茶。');await bridge.queue.join()
            assert any('吗？' in x[1] for x in bridge.client.sent)
            assert not calls
            assert len(requests)==(0 if kind=='search_on' else 1)
            submit(bridge,'同意',2);await bridge.queue.join()
            if kind=='search_on':assert bridge.settings.web_search_mode=='auto'
            else:assert not calls and '完整候选预览服务暂不可用' in bridge.client.sent[-1][1]
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('kind',['search_on','save_candidate'])
def test_real_bridge_requires_management_for_candidate_consent_without_slash_commands(tmp_path,kind):
    async def run():
        calls=[];wire=Client()
        async def model(*a):pytest.fail('consent must not run model')
        bridge=make(tmp_path,model,wire);await bridge.start()
        bridge.action_proposals=QQActionProposals(bridge.store)
        bridge.responder=SimpleNamespace(settings=bridge.settings,memory_actions=lambda c,a:calls.append((c,a)) or '已确认并保存长期记忆。')
        scope='10000:private:20000:shared'
        bridge.store.save_conversation(scope,['Saya: 要执行吗？'],'session_qq_test.jsonl')
        payload={} if kind=='search_on' else {'candidate_id':'candidate-1'}
        bridge.action_proposals.ask(scope,'session_qq_test.jsonl',kind,payload,'要执行吗？',bridge.controls()['revision'])
        bridge.action_proposals.delivered(scope,'session_qq_test.jsonl','要执行吗？')
        try:
            submit(bridge,'同意');await bridge.queue.join()
            if kind=='search_on':assert bridge.settings.web_search_mode=='auto' and '已开启自动搜索' in wire.sent[-1][1]
            else:assert not calls and '完整候选预览服务暂不可用' in wire.sent[-1][1]
            submit(bridge,'同意',2);await bridge.queue.join()
            assert not calls
        finally:await bridge.stop()
    asyncio.run(run())


def test_hours_gap_clock_explicit_unknown_and_history_pointer_unchanged(tmp_path):
    text=clock_context(message_at='2026-10-30T15:00:00+08:00',previous_at='2026-10-30 10:00:00',now='2026-10-30T15:01:00+08:00')
    assert '"elapsed_seconds": 18000' in text and 'Asia/Shanghai' in text
    assert '"elapsed_seconds": null' in clock_context(now='2026-10-30T15:00:00+08:00')
    manager=MemoryManager(str(tmp_path/'chats'));manager.save_message('user','old',session_id='session_web_test.jsonl')
    before=manager.current_session_file
    assert previous_web_time(manager,'session_web_test.jsonl')
    assert manager.current_session_file==before


def test_per_message_times_preserve_order_repeated_text_and_unknown_old_messages(tmp_path):
    clock=QQTurnTimes(Store(tmp_path/'times.sqlite3'))
    scope='10000:private:20000:shared';session='session_qq_test.jsonl'
    lines=['Doctor: OLD_UNKNOWN','Doctor: repeat','Saya: answer','Doctor: repeat','Saya: answer']
    clock.record(scope,session,lines[1:],[1000,1001,19000,19001])
    rows=clock.history(scope,session,lines)
    assert [r['time'] for r in rows]==[None,1000,1001,19000,19001]
    projected=timestamp_history(lines,rows)
    assert 'sent_at=unknown' in projected[0]
    assert '1970-01-01T08:16:40+08:00' in projected[1]
    assert '1970-01-01T13:16:40+08:00' in projected[3]
    assert all(row['time'] is None for row in clock.history(scope,'session_qq_new.jsonl',lines))
    assert all(row['time'] is None for row in clock.history('other',session,lines))
    with clock.store.connect() as db:assert 'OLD_UNKNOWN' not in str(db.execute('select * from qq_turn_times').fetchall())


def test_bridge_records_each_completed_turn_and_passes_times_to_next_request(tmp_path):
    async def run():
        seen=[]
        async def model(message,history,session):seen.append(getattr(message,'history_times',[]));return 'answer'
        bridge=make(tmp_path,model);await bridge.start();bridge.turn_times=QQTurnTimes(bridge.store)
        try:
            submit(bridge,'first');await bridge.queue.join()
            submit(bridge,'second',2);await bridge.queue.join()
            assert seen[0]==[]
            assert len(seen[1])==2 and all(row['time'] for row in seen[1])
            assert seen[1][0]['content']=='first' and seen[1][1]['content']=='answer'
            with bridge.store.connect() as db:assert db.execute('select count(*) from qq_turn_times').fetchone()[0]==4
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('qq',[True,False])
def test_actual_model_sees_recorded_times_on_each_past_message(server,tmp_path,qq):
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'));seen=[]
    sid='session_qq_history.jsonl' if qq else 'session_web_history.jsonl'
    path=tmp_path/'chats'/sid
    path.write_text('\n'.join(json.dumps(x) for x in [
        {'role':'user','content':'PAST_USER_CANARY','time':'2026-10-30 09:00:00'},
        {'role':'ai','content':'PAST_REPLY_CANARY','time':'2026-10-30 09:00:05'}])+'\n',encoding='utf8')
    async def no_candidates(*a,**kw):return []
    async def run():
        async def model(req):
            seen.append(json.loads(req.content));data={'choices':[{'delta':{'content':'new reply'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                req=server.ChatWithMotion(user_input='你好',session_id=sid,model='api',api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off',memory_write_enabled=False)
                response=await server._chat_stream_single(req,channel_turn={'history':['Doctor: PAST_USER_CANARY','Saya: PAST_REPLY_CANARY'], 'shared':True,'is_owner':True,'sender_id':'20000','history_times':[]} if qq else None)
                async for _ in response.body_iterator:pass
        past=[m for m in seen[-1]['messages'] if 'PAST_' in str(m['content']) and m['role'] in {'user','assistant'}]
        assert any('2026-10-30T09:00:00+08:00' in str(m['content']) and 'PAST_USER_CANARY' in str(m['content']) for m in past)
        assert any('2026-10-30T09:00:05+08:00' in str(m['content']) and 'PAST_REPLY_CANARY' in str(m['content']) for m in past)
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),patch.object(conversation_service,'save_credentials',lambda **kw:None),
          patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())


def test_legacy_candidate_callback_cannot_save_without_management(server,tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    candidate=kb.add_memory_candidate('用户喜欢清淡的茶饮。',title='茶饮偏好',session_id='session_qq_test.jsonl')
    assert candidate and '.' in candidate['id']
    async def run():
        bridge=make(tmp_path/'bridge',None);await bridge.start()
        bridge.action_proposals=QQActionProposals(bridge.store)
        bridge.responder=SimpleNamespace(settings=bridge.settings,memory_actions=server._qq_memory_action)
        scope='10000:private:20000:shared'
        bridge.store.save_conversation(scope,[],'session_qq_test.jsonl')
        bridge.action_proposals.ask(scope,'session_qq_test.jsonl','save_candidate',{'candidate_id':candidate['id']},'要记住吗？',bridge.controls()['revision'])
        bridge.action_proposals.delivered(scope,'session_qq_test.jsonl','要记住吗？')
        before=len(kb.memories)
        try:
            submit(bridge,'同意');await bridge.queue.join()
            assert '完整候选预览服务暂不可用' in bridge.client.sent[-1][1]
            assert len(kb.memories)==before
            submit(bridge,'同意',2);await bridge.queue.join()
            assert len(kb.memories)==before
        finally:await bridge.stop()
    with patch.object(server,'kb_system',kb):asyncio.run(run())


@pytest.mark.parametrize('qq',[True,False])
def test_actual_generator_always_sees_clock_even_without_time_keywords(server,tmp_path,qq):
    manager=MemoryManager(str(tmp_path/'chats'));kb=KnowledgeBase(str(tmp_path/'kb'));seen=[]
    async def no_candidates(*a,**kw):return []
    async def run():
        async def model(request):
            seen.append(json.loads(request.content));data={'choices':[{'delta':{'content':'answer'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                if qq:
                    from saya_app.services.qq_bridge import normalize_event
                    msg,_=normalize_event(event('你好'),config())
                    msg.previous_conversation_at=msg.sent_at-18000
                    responder=QQChatResponder(config(),server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{'configured':True,'api_key':'fake','api_url':'http://mock/v1','model':'test'})
                    assert await responder(msg,[],'')=='answer'
                else:
                    req=server.ChatWithMotion(user_input='你好',temporary_chat=True,model='api',api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off')
                    response=await server._chat_stream_single(req)
                    async for _ in response.body_iterator:pass
        content=str(seen[-1]['messages'][-1]['content'])
        assert 'Server conversation clock' in content and 'Asia/Shanghai' in content
        if qq:assert '18000' in content
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),patch.object(server,'maybe_create_autonomous_memory_candidates',no_candidates),
          patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server.companion_router,'check_conflict_for_memory',return_value=None)):
        asyncio.run(run())
