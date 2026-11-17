import asyncio
from dataclasses import replace
import json
import time
import pytest
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_episode_context import EpisodePolicy
from test_qq_task_scheduler import make
from test_qq_delivery import event
from test_qq_chat_runtime import server


def test_event_reason_boundary_reaches_ordinary_model_when_long_term_recall_is_off(server,tmp_path):
    import httpx
    from unittest.mock import patch
    from core_services import KnowledgeBase
    from r4_web_lab import web_lab
    from test_qq_topic_routing import topics,item,create
    from saya_app.services.qq_chat import QQChatResponder

    async def run():
        async with topics(tmp_path/'bridge') as (bridge,service,_,message,choice,_):
            bridge.settings=bridge.settings.model_copy(update={
                'memory_write_enabled':False,'recall_mode':'off','agent_mode':False,'web_search_mode':'off'})
            source=message('虚构枫堤目录计划由我先核对旧册，新的材料还没收齐。')
            choice[0]={'items':[item(source.text,personal=create(),mode='commitment')]}
            await service.observe(source,[])
            captured=[]
            def model(request):
                payload=json.loads(request.content);captured.append(payload)
                prompt=json.dumps(payload['messages'],ensure_ascii=False)
                assert source.text in prompt
                assert 'Reasons require explicit support in these quotations.' in prompt
                assert 'An older assistant explanation is not a user reason.' in prompt
                response={'choices':[{'delta':{'content':'原话只记录先核对旧册，未明确说明理由。'},'finish_reason':'stop'}]}
                return httpx.Response(200,text='data: '+json.dumps(response)+'\n\ndata: [DONE]\n\n')
            kb=KnowledgeBase(str(tmp_path/'kb'))
            with web_lab(server,tmp_path/'web',kb) as lab:
                async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as client:
                    with patch.object(server,'_get_api_httpx_client',lambda:client):
                        responder=QQChatResponder(bridge.settings,server.ChatWithMotion,server._chat_stream_single,
                            lambda:'api',lambda:lab.credentials)
                        responder.episodes=service;responder.context_salience=bridge.context_salience
                        answer=await responder(message('枫堤目录计划为什么先核对旧册？'),[],
                                               'session_qq_event_reason.jsonl')
            assert '未明确说明理由' in str(answer) and len(captured)==1
    asyncio.run(run())


def test_default_slots_and_invalid_capacity():
    p=EpisodePolicy()
    assert not p.enabled and (p.max_active,p.max_total)==(3,10)
    with pytest.raises(ValueError):EpisodePolicy(max_active=4,max_total=3)


def test_two_related_turns_correction_pause_expiry_and_context_budget(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.episodes
        now=[time.time()];bridge.context_salience.clock=lambda:now[0];service.clock=lambda:now[0]
        bridge.context_salience.configure('20000',0,{'enabled':True})
        service.configure('20000',0,{'enabled':True})
        message,_=normalize_event(event('我计划周末去虚构的星湾公园。'),bridge.settings)
        def prepare(text,mid):
            msg=replace(message,text=text,key='episode-'+str(mid),message_id=str(mid),sent_at=now[0])
            msg.context_snapshot=bridge.context_salience.snapshot(msg.scope+':shared')
            return msg
        async def create(*a,**kw):return {'action':'create','title':'星湾公园','confidence':.95}
        service.decide=create
        try:
            first=prepare(message.text,1);await service.observe(first,[])
            cards=service.cards(first);assert len(cards)==1 and cards[0]['state']=='candidate'
            identifier=cards[0]['id']
            async def update(*a,**kw):return {'action':'update','event_id':identifier,'title':'改去月湖公园','confidence':.95,'supersedes':[0]}
            service.decide=update;now[0]+=2
            second=prepare('更正一下，周末改去月湖公园。',2);await service.observe(second,[])
            cards=service.cards(second);assert cards[0]['state']=='active' and cards[0]['facts'][0]['superseded']
            query=prepare('之前那个公园计划还有什么细节？',3);ctx=service.context(query)
            assert ctx and '月湖公园' in json.dumps(ctx['cards'],ensure_ascii=False)
            assert '星湾公园' not in json.dumps(ctx['cards'],ensure_ascii=False)
            assert len(json.dumps(ctx['cards'],ensure_ascii=False))<=1200
            assert service.valid(query,ctx)
            service.pause(query)
            assert service.context(prepare('换个新话题，聊聊电影。',4)) is None
            assert service.context(prepare('继续之前那个计划。',5))
            now[0]+=86401
            assert not service.cards(query)
            assert not service.valid(query,ctx)
        finally:await bridge.stop()
    asyncio.run(run())


def test_active_total_capacity_and_group_scope_isolation(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.episodes
        now=[time.time()];bridge.context_salience.clock=lambda:now[0];service.clock=lambda:now[0]
        bridge.context_salience.configure('20000',0,{'enabled':True});service.configure('20000',0,{'enabled':True})
        async def create(*a,**kw):return {'action':'create','title':'跟进','confidence':.95}
        service.decide=create
        try:
            for i in range(12):
                now[0]+=61  # separate opportunities; do not bypass production per-minute budget
                msg,_=normalize_event(event('帮我跟进这件事，虚构计划编号'+str(i),i+1),bridge.settings)
                msg.sent_at=now[0];msg.context_snapshot=bridge.context_salience.snapshot(msg.scope+':shared')
                await service.observe(msg,[])
            cards=service.cards(msg)
            assert len(cards)==10 and sum(c['state']=='active' for c in cards)<=3
            group_msg,_=normalize_event(event('帮我跟进这个群的计划',90,group_id=90000),bridge.settings)
            assert service.cards(group_msg)==[]
            stranger=replace(msg,user_id='30000',scope='10000:private:30000',shared=False)
            assert service.cards(stranger)==[]
        finally:await bridge.stop()
    asyncio.run(run())


def test_source_expiry_removes_derived_history_even_after_feature_off(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.episodes
        now=[time.time()];bridge.context_salience.clock=lambda:now[0];service.clock=lambda:now[0]
        bridge.context_salience.configure('20000',0,{'enabled':True});service.configure('20000',0,{'enabled':True})
        async def create(*a,**kw):return {'action':'create','title':'跟进','confidence':.95}
        service.decide=create
        msg,_=normalize_event(event('帮我跟进这件事，虚构旅行的计划。'),bridge.settings)
        scope=msg.scope+':shared';sid='session_qq_derived.jsonl';msg.context_snapshot=bridge.context_salience.snapshot(scope)
        try:
            await service.observe(msg,[])
            msg.text='之前旅行的计划怎么样了？';snapshot=service.context(msg);assert snapshot
            history=['Doctor: 之前的计划','Saya: DERIVED_EVENT_CANARY']
            bridge.store.save_conversation(scope,history,sid)
            bridge.context_salience.record(scope,sid,history,[now[0],now[0]],msg.context_snapshot,
                episode_source=service.source_snapshot(snapshot))
            policy=bridge.context_salience.state();bridge.context_salience.configure('20000',policy['revision'],policy['values']|{'enabled':False})
            projected,_=bridge.context_salience.project(scope,sid,history)
            assert 'DERIVED_EVENT_CANARY' not in str(projected)
        finally:await bridge.stop()
    asyncio.run(run())
