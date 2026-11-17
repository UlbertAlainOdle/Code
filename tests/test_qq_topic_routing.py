import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import pytest
from test_qq_unified_events import unified
from test_qq_delivery import event
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_topic_model import complete
from saya_app.services import api_usage
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder
from test_qq_task_scheduler import until
from fastapi.responses import StreamingResponse


@asynccontextmanager
async def topics(tmp_path):
    async with unified(tmp_path) as state:
        b,s,now,msg,choice,prompts=state
        p=s.unified.policy()
        s.unified.configure('20000',p['revision'],{**p['values'],'topic_routing_enabled':True},s.unified.preview()['version'])
        yield state


def item(quote,*,personal=None,group=None,mode='opinion',subject='self',revision='append'):
    return {'quote':quote,'title':quote[:20],'mode':mode,'subject':subject,'revision_kind':revision,'confidence':.95,
        'personal':personal,'group':group}


def create():return {'action':'create','supersedes':[]}


def test_invalid_change_mode_cannot_write_and_valid_revision_updates_original(tmp_path):
    async def run():
        async with topics(tmp_path) as (_, service, _, message, choice, _):
            first = message('我准备用蒸锅做南瓜，因为炉灶要留给汤锅。')
            choice[0] = {'items': [item(first.text, personal=create(), mode='proposal')]}
            await service.observe(first, [])
            original = service.people._canonical(first.user_id)
            assert len(original) == 1
            identifier = original[0]['id']
            update = {'action': 'update', 'event_id': identifier, 'supersedes': [0]}
            text = '我改用烤箱做南瓜，原来的蒸锅坏了。'
            choice[0] = {'items': [item(text, personal=update, mode='change', revision='change')]}
            invalid = message(text)
            await service.observe(invalid, [])
            assert service.people._canonical(first.user_id) == original
            assert '自动整理未完成' in invalid.topic_coverage

            choice[0] = {'items': [item(text, personal=update, mode='commitment', revision='change')]}
            valid = message(text)
            await service.observe(valid, [])
            cards = service.people._canonical(first.user_id)
            assert len(cards) == 1 and cards[0]['id'] == identifier
            assert cards[0]['facts'][0]['superseded']
            assert cards[0]['facts'][-1]['quote'] == text
            assert valid.topic_results[0]['status'] == 'committed'
    asyncio.run(run())


def test_full_prior_epoch_cards_explain_capacity_without_showing_old_sources(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, choice, _):
            old = message('我已经整理好旧版书单。')
            choice[0] = {'items': [item(old.text, personal=create(), mode='progress')]}
            await service.observe(old, [])
            scope = service.people.scope('20000')
            with bridge.store.connect() as db:
                payload = db.execute('select data from qq_episode_cards where scope=?', (scope,)).fetchone()[0]
                db.executemany('insert into qq_episode_cards values(?,?,?,?)',
                               [(f'{index:016x}', scope, 1, payload) for index in range(19)])
            bridge.event_memory_use = SimpleNamespace(snapshot=lambda: 'b' * 32)
            info = str(service.command(message('/events self')))
            assert '20张旧来源版本已失效，仍计入保留总量' in info and old.text not in info
            next_input = message('我已经整理好新版展台登记清单。')
            choice[0] = {'items': [item(next_input.text, personal=create(), mode='progress')]}
            await service.observe(next_input, [])
            assert next_input.topic_results == [
                {'span': 0, 'target': 'personal', 'status': 'not_committed', 'reason': 'personal_card_capacity'}]
            assert '本条未建立个人事件卡' in next_input.topic_coverage
            cards = service.people._canonical('20000')
            assert len(cards) == 20 and all(card['epoch'] != service.epoch() for card in cards)
    asyncio.run(run())


def test_prior_epoch_quote_does_not_make_an_uncommitted_topic_look_unchanged(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, choice, _):
            text = '我已经整理好旧版书单。'
            choice[0] = {'items': [item(text, personal=create(), mode='progress')]}
            await service.observe(message(text), [])
            old_id = service.people._canonical('20000')[0]['id']
            bridge.event_memory_use = SimpleNamespace(snapshot=lambda: 'b' * 32)
            repeated = message(text)

            async def skipped(*args, **kwargs):
                return None

            with patch.object(service.people, 'observe', skipped):
                await service.observe(repeated, [])
            assert repeated.topic_results == [{'span': 0, 'target': 'personal', 'status': 'not_committed'}]
            assert old_id not in repr(repeated.topic_results)
    asyncio.run(run())


def test_one_plan_two_targets_shared_evidence_no_second_observer(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            text='我觉得星桥读书会值得继续讨论。'
            choice[0]={'items':[item(text,personal=create(),group=create())]}
            m=msg(text,group=90000)
            await s.unified.capture(m);await s.observe(m,[])
            assert len(prompts)==1
            p=s.cards(m)[0];g=s.unified.cards(m)[0]
            assert p['facts'][0]['evidence_id']==g['contributions'][0]['evidence_id']
            assert p['facts'][0]['source']==g['contributions'][0]['source']==m.key
            assert p['state']=='active' and len(m.topic_results)==2
            with s.store.connect() as db:
                assert db.execute('select count(*) from qq_topic_evidence').fetchone()[0]==1
            await s.topics.capture(replace(m))
            assert len(prompts)==1
    asyncio.run(run())


def test_message_fragments_do_not_share_topic_or_truncate_quotes(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            a='周六聚餐我不去了';z='那部电影的结尾我很喜欢'
            choice[0]={'items':[item(a,personal=create()),item(z,personal=create())]}
            m=msg(a+'；'+z+'。');await s.observe(m,[])
            cards=s.cards(m)
            assert len(prompts)==1 and len(cards)==2
            assert {c['facts'][0]['quote'] for c in cards}=={a,z}
            assert len({c['facts'][0]['evidence_id'] for c in cards})==2
    asyncio.run(run())


def test_short_direct_reply_is_routable_and_keeps_hypothesis_modality(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('换我就辞职');m.reply_to='trusted-reply-id'
            choice[0]={'items':[item(m.text,personal=create(),mode='hypothesis')]}
            await s.observe(m,[])
            fact=s.cards(m)[0]['facts'][0]
            assert fact['mode']=='hypothesis' and fact['quote']=='换我就辞职'
            assert 'trusted-reply-id' in prompts[0]
            snap=s.context(m)
            assert snap['cards'][0]['facts'][0]['mode']=='hypothesis'
    asyncio.run(run())


@pytest.mark.parametrize('text',['你好','好的','/events self','之前那个计划，目前几点集合？','确认修改 test-token','记住我喜欢蓝色'])
def test_control_read_greeting_and_single_save_do_not_route(tmp_path,text):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await s.observe(msg(text),[])
            assert not prompts
    asyncio.run(run())


@pytest.mark.parametrize('bad',[{'quote':'模型编造原话'}, {'subject':'other'}])
def test_invalid_quote_or_other_subject_cannot_become_personal_fact(tmp_path,bad):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            value=item('片段',personal=create());value.update(bad)
            choice[0]={'items':[value]};m=msg('原文片段。');await s.observe(m,[])
            assert not s.cards(m)
    asyncio.run(run())


def test_quote_boundaries_are_resolved_by_host_without_guessing_repeats(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            value=item('片段',personal=create());value['start']=1
            choice[0]={'items':[value]};m=msg('原文片段。');await s.observe(m,[])
            assert s.cards(m)[0]['facts'][0]['source_span']==[2,4]
            repeated=msg('片段片段');await s.observe(repeated,[])
            assert not getattr(repeated,'topic_results',None)
    asyncio.run(run())


def test_change_of_mind_supersedes_current_state_same_id(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥聚餐我不参加');choice[0]={'items':[item(first.text,personal=create())]};await s.observe(first,[])
            mid=s.cards(first)[0]['id']
            second=msg('星桥聚餐我改主意了，还是去')
            choice[0]={'items':[item(second.text,personal={'action':'update','event_id':mid,'supersedes':[0]},revision='change')]}
            await s.observe(second,[])
            assert s.cards(second)[0]['id']==mid
            snap=s.context(second)
            assert [f['quote'] for f in snap['cards'][0]['facts']]==[second.text]
    asyncio.run(run())


def test_group_hard_stop_and_expiry_do_not_recreate_from_late_plan(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥聚餐我建议周日',group=90000)
            choice[0]={'items':[item(first.text,personal=create(),group=create())]};await s.observe(first,[])
            mid=s.cards(first)[0]['id']
            async def late(prompt,**kwargs):
                s.command(msg('/events stop '+mid))
                return {'items':[item('我现在觉得周六也可以',personal=create(),group=create())]}
            s.decide=late;m=msg('我现在觉得周六也可以',group=90000);await s.observe(m,[])
            assert not getattr(m,'topic_results',None) and len(s.unified.cards(first,include_stopped=True))==1
            assert not s.unified.cards(first)
            assert len(s.unified.cards(msg('/events group',user=30000,group=90000)))==1
            assert not s.context(msg('之前星桥聚餐如何？'))
    asyncio.run(run())


def test_unmentioned_group_is_rejected_before_any_router(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            raw=event('没有@的有意义群话题',501,20000,90000)
            raw['message']=[{'type':'text','data':{'text':'没有@的有意义群话题'}}]
            m,reason=normalize_event(raw,b.settings)
            assert m is None and reason=='not_mentioned'
            assert b.submit(raw)==('ignored','not_mentioned') and not prompts
    asyncio.run(run())


def test_dual_context_dedup_preserves_roles_and_budget(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥读书会我愿意参加',group=90000)
            choice[0]={'items':[item(m.text,personal=create(),group=create(),mode='commitment')]};await s.observe(m,[])
            p=s.context(m);ids={f['evidence_id'] for c in p['cards'] for f in c['facts']}
            g=s.unified.context(m,seen_evidence=ids)
            assert m.text not in g['text'] and 'commitment' in g['text'] and '同源片段' in g['text']
            assert s.unified.context(m,budget=0) is None
    asyncio.run(run())


def test_minimal_provider_request_has_no_persona_tools_history_and_records_unknown_usage():
    async def run():
        calls=[];recorded=[]
        def endpoint(request):
            calls.append(json.loads(request.content))
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}]})
        async def record(*args,**kwargs):recorded.append((args,kwargs))
        req=SimpleNamespace(user_input='ROUTE_DATA',api_url='https://example.invalid/v1',api_model_name='fixture',max_output_tokens=1536,api_key='fixture')
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            answer=await complete(req,client,record)
            body=''.join([x async for x in answer.body_iterator])
        assert 'answer_complete' in body and len(calls)==1
        assert len(calls[0]['messages'])==2 and calls[0]['messages'][1]['content']=='ROUTE_DATA'
        assert 'tools' not in calls[0] and calls[0]['max_tokens']==1536
        assert recorded[0][0][1] is None and recorded[0][1]['stage']=='qq_topic_route'
    asyncio.run(run())


def test_stage_usage_keeps_totals_and_marks_unknown(tmp_path):
    api_usage.record_turn(tmp_path,'fixture',None,stage='qq_topic_route',elapsed_ms=12)
    api_usage.record_turn(tmp_path,'fixture',{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},stage='qq_answer')
    entry=next(iter(api_usage.load_ledger(tmp_path)['days'].values()))['fixture']
    assert entry['requests']==2 and entry['total_tokens']==12
    assert entry['stages']['qq_topic_route']['usage_unknown']==1
    assert entry['stages']['qq_answer']['total_tokens']==12


@pytest.mark.parametrize('stage',['qq_topic_route','qq_memory_lookup','qq_library_review','qq_memory_overlap'])
def test_real_generator_routes_without_loading_chat_state(server,tmp_path,monkeypatch,stage):
    async def run():
        calls=[]
        def endpoint(request):
            payload=json.loads(request.content);calls.append(payload)
            assert len(payload['messages'])==2
            assert payload['messages'][1]['content']=='PURE_ROUTER_DATA'
            return httpx.Response(200,json={'choices':[{'message':{'content':'{"items":[]}'},'finish_reason':'stop'}],
                'usage':{'prompt_tokens':17,'completion_tokens':4,'total_tokens':21}})
        monkeypatch.setattr(server,'KNOWLEDGE_BASE_DIR',tmp_path)
        async with httpx.AsyncClient(transport=httpx.MockTransport(endpoint)) as client:
            monkeypatch.setattr(server,'_get_api_httpx_client',lambda:client)
            req=server.ChatWithMotion(user_input='PURE_ROUTER_DATA',model='api',api_key='fixture',api_url='https://example.invalid/v1',api_model_name='fixture',temporary_chat=True,max_output_tokens=1536)
            response=await server._chat_stream_single(req,channel_turn={'internal_json_task':stage})
            assert 'answer_complete' in ''.join([x async for x in response.body_iterator])
        assert len(calls)==1
        entry=next(iter(api_usage.load_ledger(tmp_path)['days'].values()))['fixture']
        assert entry['stages'][stage]['total_tokens']==21
    asyncio.run(run())


def test_fragment_stop_blocks_original_full_message_and_anchor(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            a='我最近考虑星桥转岗';other='电影结尾也很有意思'
            choice[0]={'items':[item(a,personal=create()),item(other,personal=create())]}
            m=msg(a+'；'+other,group=90000);await s.observe(m,[])
            first=next(c for c in s.cards(m) if c['facts'][0]['quote']==a)
            s.command(msg('/events stop '+first['id']))
            snapshot=s.people.history_projection(m)
            retained,_=s.people.filter_history(m,['Doctor: '+m.text,'Saya: old reply'],[],snapshot)
            assert retained==[]
            _,_,_,candidates=s.topics._candidates(msg('继续聊转岗建议',group=90000))
            assert all(x['text']!=a for x in candidates['public_anchors'])
            assert any(c['facts'][0]['quote']==other for c in s.cards(m))
    asyncio.run(run())


def test_same_text_from_two_people_stays_two_authored_claims(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥活动我不参加',group=90000)
            choice[0]={'items':[item(first.text,group=create(),mode='withdrawal')]};await s.observe(first,[])
            mid=s.unified.cards(first)[0]['id']
            second=msg(first.text,user=30000,group=90000)
            choice[0]={'items':[item(second.text,group={'action':'update','event_id':mid,'supersedes':[]},mode='withdrawal')]}
            await s.observe(second,[])
            claims=s.unified.cards(first)[0]['contributions']
            assert len(claims)==2 and {x['actor'] for x in claims}=={'20000','30000'}
            assert len({x['evidence_id'] for x in claims})==2
    asyncio.run(run())


def test_unrelated_group_update_does_not_reject_target_append(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥活动建议周日',group=90000)
            choice[0]={'items':[item(first.text,group=create())]};await s.observe(first,[])
            mid=s.unified.cards(first)[0]['id']
            async def concurrent(prompt,**kwargs):
                unrelated=msg('另一部电影讨论结尾',user=30000,group=90000)
                s.unified.commit(unrelated,'90000',{'title':'电影讨论'})
                return {'items':[item('星桥活动我愿意整理路线',group={'action':'update','event_id':mid,'supersedes':[]},mode='commitment')]}
            s.decide=concurrent;second=msg('星桥活动我愿意整理路线',group=90000);await s.observe(second,[])
            assert any(x['status']=='committed' for x in second.topic_results)
            assert len(next(c for c in s.unified.cards(first) if c['id']==mid)['contributions'])==2
    asyncio.run(run())


def test_real_bridge_captures_before_answer_and_does_not_classify_twice(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            text='星桥读书会我愿意整理书单'
            choice[0]={'items':[item(text,personal=create(),group=create(),mode='commitment')]}
            async def answer(message,history,session):
                assert len(s.people._canonical(message.user_id))==1
                assert len(s.unified.cards(message))==1
                return '可以继续讨论书单。'
            b.responder=answer
            assert b.submit(event(text,700,20000,90000))[0]=='queued'
            await until(lambda:len(b.client.sent)>0)
            await until(lambda:not b.task_registry.list_active())
            assert len(prompts)==1
            assert len(s.people._canonical('20000'))==1
    asyncio.run(run())


def test_real_responder_can_read_both_views_with_shared_budget(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥读书会我愿意整理书单',group=90000)
            choice[0]={'items':[item(first.text,personal=create(),group=create(),mode='commitment')]};await s.observe(first,[])
            calls=[]
            async def generate(req,channel_turn):
                calls.append(channel_turn)
                assert channel_turn.get('event_context') and channel_turn.get('shared_events')
                p=channel_turn['event_context'];g=channel_turn['shared_events']
                policy=b.context_salience.state()['values']
                assert p['tokens']+g['tokens']<=policy['history_token_budget']
                assert p['old_tokens']+g['old_tokens']<=int(policy['history_token_budget']*policy['old_fraction'])
                assert first.text not in g['text']
                async def stream():yield 'event: answer_complete\ndata: {"text":"你愿意整理书单，共同安排尚待确认。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            reply=await responder(msg('星桥读书会目前安排如何？',group=90000),[],'topic-dual-fixture')
            assert '尚待确认' in str(reply) and len(calls)==1
    asyncio.run(run())


def test_hypothetical_date_does_not_set_confirmed_schedule(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('假如星桥资料截止2026-11-06 18:00+08:00，我可能来不及。')
            choice[0]={'items':[item(m.text,personal=create(),mode='hypothesis')]};await s.observe(m,[])
            assert not s.cards(m)[0].get('timing')
    asyncio.run(run())


def test_repeated_statement_reports_unchanged_without_new_evidence(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥活动我愿意整理路线',group=90000)
            choice[0]={'items':[item(first.text,personal=create(),group=create())]};await s.observe(first,[])
            own=s.cards(first)[0];group=s.unified.cards(first)[0]
            choice[0]={'items':[item(first.text,personal={'action':'update','event_id':own['id'],'supersedes':[]},
                group={'action':'update','event_id':group['id'],'supersedes':[]})]}
            repeated=msg(first.text,group=90000);await s.observe(repeated,[])
            assert {x['status'] for x in repeated.topic_results}=={'unchanged'}
            assert len(s.cards(first)[0]['facts'])==len(s.unified.cards(first)[0]['contributions'])==1
            with s.store.connect() as db:assert db.execute('select count(*) from qq_topic_evidence').fetchone()[0]==1
    asyncio.run(run())
