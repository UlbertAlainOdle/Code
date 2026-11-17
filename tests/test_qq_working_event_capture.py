import asyncio
from copy import deepcopy
import json
from fastapi.responses import StreamingResponse
from core_services import KnowledgeBase
from saya_app.services.qq_chat import QQChatResponder
from test_qq_person_events import lab
from test_qq_chat_runtime import server


def test_generic_acceptance_words_weekend_do_not_merge_cup_and_park_candidates(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    first=kb.add_memory_candidate('Doctor用于M1虚构验收的测试茶杯叫蓝叶杯，它只在周末使用。',session_id='old')
    before=deepcopy(first)
    second=kb.add_memory_candidate('Doctor用于M5虚构验收的计划是周末去星湾公园，先约09:20集合。',session_id='new')
    assert second['id']!=first['id'] and first==before
    assert len(kb.memory_candidates)==2


def test_changed_values_or_negation_stay_separate_instead_of_bumping_old_evidence(tmp_path):
    for index,values in enumerate([('Doctor周末计划去星湾公园，09:20集合。','Doctor周末计划去星湾公园，10:40集合。'),
            ('Doctor喜欢在周末去星湾公园散步。','Doctor不喜欢在周末去星湾公园散步。')]):
        kb=KnowledgeBase(str(tmp_path/str(index)));first=kb.add_memory_candidate(values[0]);before=deepcopy(first)
        second=kb.add_memory_candidate(values[1]);assert second['id']!=first['id'] and first==before


def test_first_followup_turn_is_temporary_only_even_without_any_existing_card(server,tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            calls=[]
            async def generate(req,channel_turn):
                calls.append((req,channel_turn));assert not req.memory_write_enabled and not channel_turn['memory_write_enabled']
                assert channel_turn['working_event_request']
                async def stream():
                    # A stale/incorrect backend payload cannot invite unrelated consent.
                    yield 'event: memory_candidates\ndata: '+json.dumps({'candidates':[{'id':'old','title':'OLD_CUP'}]})+'\n\n'
                    yield 'event: answer_complete\ndata: '+json.dumps({'text':'临时计划：周末09:20集合。'})+'\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            first=msg('帮我跟进M5虚构验收：周末去星湾公园，先约09:20集合。')
            assert s.people._canonical('20000')==[]
            result=await responder(first,[],'working-event-session')
            assert 'OLD_CUP' not in str(result) and calls
            await responder(msg('继续事件 0123456789abcdef，公开补充：集合时间改为10:40。',group=90000),[],'group-working-event-session')
            assert len(calls)==2
    asyncio.run(run())


def test_real_generator_does_not_capture_or_triage_first_working_request(server,tmp_path,monkeypatch):
    import httpx
    from core_services import MemoryManager
    from saya_app.services.conversation import conversation_service
    async def run():
        async with lab(tmp_path/'bridge') as (b,s,now,msg,choice,prompts):
            kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chat'));calls=[]
            async def triage(*args,**kwargs):calls.append('triage');return []
            def provider(request):
                chunk={'choices':[{'delta':{'content':'收到这个临时安排。'},'finish_reason':None}]}
                return httpx.Response(200,text='data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n')
            monkeypatch.setattr(server,'kb_system',kb);monkeypatch.setattr(server,'memory_manager',manager)
            monkeypatch.setattr(server,'refresh_daily_activity_memory',lambda:None)
            monkeypatch.setattr(server,'maybe_create_autonomous_memory_candidates',triage)
            monkeypatch.setattr(conversation_service,'save_credentials',lambda **kwargs:None)
            settings=b.settings.model_copy(update={'agent_mode':False,'web_search_mode':'off'})
            async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
                monkeypatch.setattr(server,'_get_api_httpx_client',lambda:client)
                responder=QQChatResponder(settings,server.ChatWithMotion,server._chat_stream_single,lambda:'api',lambda:{
                    'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
                responder.episodes=s;responder.context_salience=b.context_salience
                result=await responder(msg('帮我跟进M5虚构验收：周末去星湾公园，先约09:20集合。'),[],'session-working-capture')
            assert '临时安排' in result and calls==[] and not kb.memory_candidates and not kb.memories
    asyncio.run(run())


def test_natural_read_does_not_become_new_private_state_or_evidence(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            public=msg('帮我跟进虚构公园计划，集合10:40。',group=90000);await s.observe(public,[])
            before=deepcopy(s.people._canonical('20000'));calls=len(prompts)
            await s.observe(msg('之前那个公园计划，目前几点集合？只回复当前集合时间。'),[])
            assert len(prompts)==calls and s.people._canonical('20000')==before
            assert s.cards(public)
            from saya_app.services.qq_person_events import event_read_query
            assert not event_read_query('之前那个计划，我决定改为周日，几点合适？')
    asyncio.run(run())
