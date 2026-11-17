import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
import json
import sqlite3
from pathlib import Path
from unittest.mock import patch
import pytest
import httpx
from fastapi import FastAPI
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_topic_lifecycle import contribute,view,source_expiry,deadline
from saya_app.services.qq_rollback import check_memory_rollback
from saya_app.routers import qq as routes
from test_qq_chat_runtime import server


def raw_card(s,identifier):
    with s.store.connect() as db:
        row=db.execute('select revision,data from qq_shared_events where id=?',(identifier,)).fetchone()
    return (row[0],json.loads(row[1])) if row else None


@asynccontextmanager
async def idle(tmp_path):
    async with topics(tmp_path) as state:
        b,s,now,msg,choice,prompts=state
        s.unified.lifecycle.configure('20000',0,{'enabled':True})
        yield state


async def say(s,msg,choice,text='星桥读书会：我觉得这个结局值得讨论。',actor=20000,mode='opinion',personal=False):
    m=msg(text,user=actor,group=90000)
    choice[0]={'items':[item(text,group=create(),personal=create() if personal else None,mode=mode)]}
    await s.observe(m,[])
    return m,s.unified.cards(m)[0]


def test_pending_is_ten_percent_buffer_never_a_permanent_floor():
    base={'state':'active','contributions':[{'id':'a','actor':'20000','kind':'proposal','mode':'opinion','text':'这个结局还没有统一意见。','idle_policy':1,'expires':4600}]}
    contribute(base,'20000',1000,1000);ordinary=deepcopy(base)
    base['contributions'][0].update(kind='commitment',mode='commitment',text='我负责整理资料。')
    contribute(base,'20000',1000,1000)
    for delta in [0,2700,10800]:
        a=view(ordinary,1000+delta);b=view(base,1000+delta)
        assert b['score']/a['score']==pytest.approx(1/.9)
    assert view(ordinary,1000+2701)['stage']=='cooling'
    assert view(base,1000+10800)['stage']=='dormant'
    assert view(base,1000+21601)['stage']=='expired'
    assert not ordinary['attention']['pending']


def test_bounded_heat_tracks_people_and_time_bins_not_message_volume():
    c={'state':'active','contributions':[]}
    for _ in range(50):contribute(c,'20000',1000,1000)
    assert c['attention']['half_life']==2700
    for i in range(1,7):contribute(c,str(20000+i),1000+1800*i,1000+1800*i)
    assert c['attention']['half_life']<=10800
    assert view(c,c['attention']['content_at']+3600)['stage']=='active'
    assert view(c,c['attention']['content_at']+86400)['stage']=='expired'


def test_dated_coordinator_decision_is_queryable_without_keeping_other_chatter_active():
    at=1000
    c={'state':'active','current_decision':'b','contributions':[
        {'id':'a','actor':'30000','kind':'proposal','text':'我觉得周末可以讨论','idle_policy':1,'expires':at+3600},
        {'id':'b','actor':'20000','kind':'decision','text':'最终确定明确日期','idle_policy':1,'expires':at+3600,
            'proposed_timing':{'appointment':{'status':'known','at':at+2*86400}}}]}
    contribute(c,'20000',at,at)
    assert view(c,at+86400)['stage']=='dormant'
    assert source_expiry(c,c['contributions'][0])<at+86400
    assert source_expiry(c,c['contributions'][1])==at+3*86400
    c['state']='completed'
    assert source_expiry(c,c['contributions'][1])<at+86400
    c['state']='active';c['contributions'][1]['proposed_timing']['appointment']['at']=at+365*86400
    contribute(c,'20000',at,at)
    assert source_expiry(c,c['contributions'][1])<=at+7*86400


def test_enable_does_not_rewrite_or_resurrect_existing_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,old=await say(s,msg,choice);original=raw_card(s,old['id'])
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            assert raw_card(s,old['id'])==original
            now[0]+=3601
            assert not s.unified.cards(m) and raw_card(s,old['id']) is None
    asyncio.run(run())


def test_new_sources_survive_one_hour_then_fade_and_expire_without_result(tmp_path):
    async def run():
        async with idle(tmp_path) as (b,s,now,msg,choice,prompts):
            m,c=await say(s,msg,choice)
            before=raw_card(s,c['id'])[1]['contributions'][0].copy()
            assert before['idle_policy']==1
            now[0]+=3700;card=s.unified.cards(m)[0]
            assert card['attention_view']['stage']=='cooling'
            assert card['contributions'][0]['expires']>now[0]
            assert raw_card(s,c['id'])[1]['contributions'][0]==before
            now[0]+=3*3600;assert s.unified.cards(m)[0]['attention_view']['stage']=='dormant'
            assert '当前群没有公共事件' in str(s.unified.command(replace(m,text='/events group')))
            assert c['id'] in str(s.unified.command(replace(m,text='/events group dormant')))
            assert 'dormant' in str(s.unified.command(replace(m,text='/events group show '+c['id'])))
            now[0]+=3*3600
            assert not s.unified.cards(m)
    asyncio.run(run())


def test_real_continuation_keeps_old_new_policy_evidence_but_not_legacy_ttl(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m,c=await say(s,msg,choice,text='星桥读书会：我更希望周六下午。');legacy=raw_card(s,c['id'])[1]['contributions'][0].copy()
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            _,c=await say(s,msg,choice,text='星桥读书会：我只在周日下午有空，愿意准备提纲。',actor=30000,mode='commitment')
            snapshot=s.unified.context(replace(m,text='星桥读书会，目前谁负责什么？时间定了吗？'))
            now[0]+=3700
            _,fresh=await say(s,msg,choice,text='星桥读书会：我已经整理好资料封面。',mode='progress')
            assert fresh['id']==c['id'] and '周日下午' in s.unified.summary(fresh)
            assert not any(x['id']==legacy['id'] for x in fresh['contributions'])
            assert not s.unified.valid(m,snapshot)
            with s.store.connect() as db:
                evidence=db.execute('select expires from qq_topic_evidence where id=?',(fresh['contributions'][0]['evidence_id'],)).fetchone()
                assert evidence and evidence[0]>now[0]
    asyncio.run(run())


def test_references_are_bounded_do_not_change_content_time_or_revive_stops(tmp_path):
    async def run():
        async with idle(tmp_path) as (b,s,now,msg,choice,prompts):
            m,c=await say(s,msg,choice);u=s.unified;at=c['attention']['content_at']
            for text in ['/events self','/events group','/events group show '+c['id'],'收到']:
                u.lifecycle.reference(msg(text,group=90000))
            assert raw_card(s,c['id'])[1]['attention']['reference_credit']==0
            now[0]+=2000
            query=msg('星桥读书会，目前谁负责什么？时间定了吗？',group=90000)
            u.lifecycle.reference(query);r,body=raw_card(s,c['id'])
            assert body['attention']['reference_credit']==1800 and body['attention']['content_at']==at
            u.lifecycle.reference(msg(query.text,group=90000))
            assert raw_card(s,c['id'])[0]==r
            u.command(replace(query,text='/events group stop '+c['id']))
            u.lifecycle.reference(msg('星桥读书会，时间是否已定案？',group=90000))
            assert raw_card(s,c['id'])[0]==r and not u.context(query)
            now[0]+=30000
            assert not u.cards(query)
    asyncio.run(run())


def test_shared_lifecycle_never_extends_personal_projection(tmp_path):
    async def run():
        async with idle(tmp_path) as (b,s,now,msg,choice,prompts):
            m,c=await say(s,msg,choice,mode='opinion',personal=True)
            p=s.people.cards(m)[0];expiry=p['facts'][0]['expires']
            now[0]+=3700
            assert not s.people.cards(m) and s.unified.cards(m)
            assert s.unified.cards(m)[0]['contributions'][0]['expires']>expiry
    asyncio.run(run())


def test_terminal_and_actor_withdrawal_never_become_pending_protection():
    c={'state':'completed','contributions':[{'id':'a','actor':'20000','kind':'commitment','text':'我负责资料。'}]}
    contribute(c,'20000',1000,1000)
    assert not c['attention']['pending']
    c['state']='active';c['contributions'].append({'id':'b','actor':'20000','kind':'progress','subject':'self','text':'我已完成资料。'})
    contribute(c,'20000',1001,1001);assert not c['attention']['pending']
    c['contributions'].append({'id':'c','actor':'20000','kind':'commitment','text':'我负责下一轮资料。'})
    contribute(c,'20000',1002,1002);assert c['attention']['pending']
    c['contributions'].append({'id':'d','actor':'20000','kind':'withdrawal','text':'我退出本次分工。'})
    contribute(c,'20000',1003,1003);assert not c['attention']['pending']


def test_policy_api_auth_cas_and_old_reader_gate(tmp_path):
    async def run():
        async with topics(tmp_path/'lab') as (b,s,now,msg,choice,prompts):
            app=FastAPI();app.include_router(routes.router)
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://fixture') as client:
                    assert (await client.get('/api/qq/topic-lifecycle')).status_code==401
                    client.headers['Authorization']='Bearer admin'
                    payload={'owner_id':'20000','revision':0,'values':{'enabled':True},'confirmed':True}
                    assert (await client.put('/api/qq/topic-lifecycle',json=payload)).status_code==200
                    assert (await client.put('/api/qq/topic-lifecycle',json=payload)).status_code==409
            folder=tmp_path/'gate/knowledge_base/qq';folder.mkdir(parents=True)
            with sqlite3.connect(folder/'bridge.sqlite3') as db:
                db.execute('create table qq_topic_lifecycle_policy(bot text,revision integer,data text)')
                db.execute('insert into qq_topic_lifecycle_policy values(?,?,?)',('10000',1,json.dumps({'version':1,'enabled':True})))
            assert 'qq_topic_lifecycle_v1' in check_memory_rollback(tmp_path/'gate',{'memory_compatibility':[]})['missing']
            assert check_memory_rollback(tmp_path/'gate',{'memory_compatibility':['qq_topic_lifecycle_v1']})['status']=='compatible_declared'
    asyncio.run(run())


def test_enabled_idle_sources_survive_cold_construction_with_nonzero_epoch(tmp_path):
    async def run():
        async with idle(tmp_path) as (b,s,now,msg,choice,prompts):
            from types import SimpleNamespace
            from saya_app.services.qq_episode_context import QQEpisodeContext
            b.event_memory_use=SimpleNamespace(snapshot=lambda:'nonzero-idle-epoch')
            m,card=await say(s,msg,choice);before=raw_card(s,card['id'])
            now[0]+=3700
            restored=QQEpisodeContext(b,clock=lambda:now[0])
            assert restored.unified.lifecycle.policy()['values']['enabled']
            assert restored.unified.cards(m)[0]['id']==card['id']
            assert raw_card(s,card['id'])[1]['contributions']==before[1]['contributions']
            assert restored.epoch()=='nonzero-idle-epoch'
    asyncio.run(run())


def test_real_responder_reads_new_policy_after_legacy_window_with_no_extra_selector(server,tmp_path):
    async def run():
        async with idle(tmp_path) as (b,s,now,msg,choice,prompts):
            from saya_app.services.qq_chat import QQChatResponder
            from fastapi.responses import StreamingResponse
            m,card=await say(s,msg,choice,text='星桥读书会：我只在周日下午有空。',mode='commitment')
            count=len(prompts);now[0]+=3700;calls=[]
            async def generate(req,channel_turn=None):
                calls.append(channel_turn)
                assert '周日下午' in channel_turn['shared_events']['text']
                async def stream():yield 'event: answer_complete\ndata: {"text":"原条件仍是周日下午有空。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            q=msg('星桥读书会，目前谁负责什么？时间定了吗？',group=90000)
            await responder(q,[],'idle-response-fixture')
            assert len(calls)==1 and len(prompts)==count
            assert s.unified.valid(q,calls[0]['shared_events'])
    asyncio.run(run())
