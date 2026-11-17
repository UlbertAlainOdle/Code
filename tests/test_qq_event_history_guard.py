import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest
from fastapi.responses import StreamingResponse

from test_qq_person_events import lab
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_episode_context import QQEpisodeContext
from test_qq_chat_runtime import server


def test_hidden_update_filters_raw_text_clock_rows_following_answer_and_shared_group_other_actor(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            public=msg('帮我跟进PUBLIC_OLD计划，集合09:20。',group=90000);await s.observe(public,[])
            mid=s.cards(public)[0]['id']
            initial=s.people.history_projection(public,['30000'])
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('私人补充：PRIVATE_CANARY最新计划另定。'),[])
            actor=replace(public,user_id='30000')
            projected=s.people.history_projection(actor,['20000','30000'])
            rows=[{'role':'user','speaker_id':'20000','text':public.text,'time':now[0]-60},
                {'role':'ai','text':'PUBLIC_DERIVED_OLD'},{'role':'user','speaker_id':'30000','text':'另一个人的普通话'}]
            _,selected=s.people.filter_history(actor,[],rows,projected)
            assert 'PUBLIC_OLD' not in str(selected) and 'PUBLIC_DERIVED_OLD' not in str(selected)
            assert selected[-1]['text']=='另一个人的普通话'
            assert not s.people.valid_projection(public,initial)
            private=s.people.history_projection(public)
            lines,clock=s.people.filter_history(public,['Doctor: '+public.text,'Saya: PUBLIC_DERIVED_OLD','Doctor: 普通话'],
                [{'role':'user','content':public.text},{'role':'ai','content':'PUBLIC_DERIVED_OLD'}],private)
            assert lines==['Doctor: 普通话'] and clock==[]
            assert 'PRIVATE_CANARY' not in json.dumps(projected)
    asyncio.run(run())


def test_superseded_dropped_and_deleted_sources_remain_guarded_with_feature_off_and_restart(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('帮我跟进OLD_SOURCE计划，集合09:20。');await s.observe(first,[])
            mid=s.cards(first)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95,'supersedes':[0]}
            for i in range(9):await s.observe(msg('更正一下，继续原计划，最新阶段'+str(i)),[])
            assert len(s.people._canonical('20000')[0]['facts'])==8
            policy=s.policy();s.configure('20000',policy['revision'],policy['values']|{'enabled':False})
            again=QQEpisodeContext(b,clock=lambda:now[0])
            lines,_=again.people.filter_history(first,['Doctor: '+first.text,'Saya: old answer'],[],again.people.history_projection(first))
            assert lines==[]
            with b.store.connect() as db:again._remove(db,[mid])
            projection=again.people.history_projection(first)
            assert again.people.filter_history(first,['Doctor: '+first.text],[],projection)[0]==[]
            with b.store.connect() as db:
                raw=[dict(r) for r in db.execute('select * from qq_person_event_source_guards')]
                assert len(raw)==10 and 'OLD_SOURCE' not in json.dumps(raw)
    asyncio.run(run())


def test_actual_responder_filters_generator_reload_and_cancels_a_later_private_correction(server,tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            public=msg('帮我跟进PUBLIC_OLD计划，集合09:20。',group=90000);await s.observe(public,[])
            mid=s.cards(public)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('私人补充：PRIVATE_CANARY最新状态另定。'),[])
            captured=[]
            async def generate(req,channel_turn):
                captured.append(channel_turn)
                lines,rows=channel_turn['context_selector'](['Doctor: '+public.text,'Saya: PUBLIC_DERIVED_OLD'],
                    [{'role':'user','content':public.text},{'role':'ai','content':'PUBLIC_DERIVED_OLD'}])
                assert lines==[] and rows==[]
                assert 'PRIVATE_CANARY' not in str(channel_turn)
                async def stream():
                    yield 'event: answer_complete\ndata: '+json.dumps({'text':'当前可见信息里没有最新安排。'})+'\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            query=msg('之前计划现在几点集合？',group=90000)
            answer=await responder(query,['Doctor: '+public.text,'Saya: PUBLIC_DERIVED_OLD'],'session-guard')
            assert '当前可见' in answer and captured
            snapshot=s.source_snapshot(query.episode_snapshot)
            assert s.valid(query,snapshot,history=True)
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            # A new public update creates a new source and invalidates the old derived view.
            await s.observe(msg('继续事件 '+mid+'，公开补充时间改为11:30。',group=90000),[])
            assert not s.valid(query,snapshot,history=True)
    asyncio.run(run())


def test_guard_capacity_rejects_before_model_call_and_future_schema_fails_closed(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            own=msg();await s.observe(own,[]);before=s.people._canonical('20000')
            with b.store.connect() as db:
                db.executemany('insert into qq_person_event_source_guards values(?,?,?,?,?,?,?,?)',[
                    ('10000','30000','private',f'{i:064x}','retired',now[0],now[0]+3600,1) for i in range(15999)])
            count=len(prompts);await s.observe(msg('帮我跟进另一个虚构容量计划。'),[])
            assert len(prompts)==count and s.people._canonical('20000')==before
            with b.store.connect() as db:db.execute('update qq_person_event_history_schema set version=99')
            with pytest.raises(ValueError,match='unsupported_event_history_schema'):QQEpisodeContext(b,clock=lambda:now[0])
    asyncio.run(run())


def test_many_old_event_facts_keep_latest_within_shared_old_budget(tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            policy=b.context_salience.state();b.context_salience.configure('20000',policy['revision'],policy['values']|{'history_token_budget':2400})
            own=msg('帮我跟进这个虚构长期计划。');await s.observe(own,[]);mid=s.cards(own)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            for i in range(8):await s.observe(msg('继续该计划，LATEST_'+str(i)+' '+('虚构背景不代表完成。'*22)),[])
            now[0]+=2*86400;query=msg('之前那个计划的最新阶段是什么？')
            snapshot=s.context(query)
            assert snapshot and 'LATEST_7' in str(snapshot['cards']) and snapshot['old_tokens']<=480
    asyncio.run(run())


def test_real_shared_group_projection_drops_another_actors_obsolete_original_and_derived_answer(server,tmp_path):
    async def run():
        async with lab(tmp_path) as (b,s,now,msg,choice,prompts):
            from core_services import KnowledgeBase
            from saya_app.services.qq_group_context import QQGroupContext,GroupPolicy
            kb=KnowledgeBase(str(tmp_path/'kb'));group=QQGroupContext(b,kb,clock=lambda:now[0]);b.group_context=group
            group.configure('20000','90000',GroupPolicy(context_enabled=True).model_dump(),0,confirmed=True)
            public=msg('帮我跟进PUBLIC_OLD计划，集合09:20。',group=90000)
            group.record_turn(public,group.snapshot(public),'PUBLIC_DERIVED_OLD')
            await s.observe(public,[]);mid=s.cards(public)[0]['id']
            choice[0]={'action':'update','event_id':mid,'confidence':.95}
            await s.observe(msg('私人更新：PRIVATE_CANARY集合安排另定。'),[])
            async def generate(req,channel_turn):
                assert 'PUBLIC_OLD' not in str(channel_turn['group_context'])
                assert 'PUBLIC_DERIVED_OLD' not in str(channel_turn['group_context'])
                assert 'PRIVATE_CANARY' not in str(channel_turn)
                async def stream():yield 'event: answer_complete\ndata: '+json.dumps({'text':'当前公开信息不足。'})+'\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://example.invalid/v1','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience;responder.group_context=group
            query=msg('之前的公园计划现在几点集合？',user=30000,group=90000)
            assert '公开信息不足' in await responder(query,[],'group-guard-session')
            group.record_turn(query,query.group_snapshot,'DERIVED_GUARD_VIEW')
            await s.observe(msg('继续事件 '+mid+'，公开补充：集合改为11:30。',group=90000),[])
            future=msg('之前那件事现在怎样？',user=30000,group=90000)
            assert 'DERIVED_GUARD_VIEW' not in str(group.snapshot(future)['history'])
    asyncio.run(run())
