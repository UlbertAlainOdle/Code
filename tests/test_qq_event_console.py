"""Owner-local event consent keeps the original atomic/permission contract."""
import asyncio,json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from saya_app.routers import qq as routes
from saya_app.services.qq_event_console import QQEventConsole
from saya_app.services.qq_responses import ConsoleNotice,ActionProposal
from saya_app.services.qq_rollback import memory_rollback_requirements
from test_qq_topic_routing import topics,item,create
from test_qq_event_operations import seed,deliver


async def offer(b,s,msg,text='忘掉所有个人事件'):
    request=msg(text);assert b.store.admit(request,b.settings)[0]=='queued'
    answer=await s.operations.handle(request)
    assert isinstance(answer,ConsoleNotice) and '【操作回执】' not in answer
    c=s.operations.console;identifier=c.listing(request.user_id)['entries'][0]['id']
    p=c.preview(request.user_id,identifier);assert p['state']=='native_prepared'
    return request,p,answer


def attach(s):
    c=s.operations.console=QQEventConsole(s.operations)
    return c


def test_full_native_preview_confirm_and_duplicate_preserve_original_atomic_audit(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c=attach(s);request,p,answer=await offer(b,s,msg)
            assert len(p['targets'])==2 and all(t['sources'] for t in p['targets'])
            assert all('我负责整理清单' in t['sources'][0]['text'] for t in p['targets'])
            assert '没有执行' in str(await s.operations.handle(msg('对的')))
            # Sending the short hint does not claim delivery of the preview.
            await deliver(b,s,request,answer)
            row,data=c.row('20000',p['id']);assert row['state']=='native_prepared' and not data['receipts']
            assert s.operations.can_deliver(request)
            result=c.decide('20000',p['id'],p['preview_version'],'confirm');assert result['state']=='done'
            assert c.decide('20000',p['id'],p['preview_version'],'confirm')['state']=='done'
            assert all(card['tracking']=='suppressed' for card in s.people._canonical('20000'))
            with b.store.connect() as db:
                audit=db.execute('select event,data from qq_event_operation_results').fetchall()
                assert len(audit)==1 and audit[0]['event']=='native-console:event:'+p['id']
                assert len(json.loads(audit[0]['data'])['changes'])==2
                assert db.execute('select count(*) from qq_event_operation_sessions where state=\'waiting\'').fetchone()[0]==0
            assert len(prompts)==2
    asyncio.run(run())


@pytest.mark.parametrize('change',['new_card','body','epoch','context','topic','policy','owner','shared_off','expiry','restart'])
def test_native_consent_rejects_stale_sources_identity_scope_and_restart(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            if change=='new_card':
                m=msg('另一张虚构的会场清单：我负责摆放纸杯。');choice[0]={'items':[item('我负责摆放纸杯。',personal=create())]};await s.observe(m,[])
            elif change=='body':
                with b.store.connect() as db:
                    r=db.execute('select id,data from qq_episode_cards limit 1').fetchone();data=json.loads(r['data']);data['title']='变化后的标题'
                    db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=?',(json.dumps(data),r['id']))
            elif change=='epoch':b.event_memory_use=type('Epoch',(),{'snapshot':lambda self:'changed'})()
            elif change=='context':
                state=b.context_salience.state();b.context_salience.configure('20000',state['revision'],{'enabled':False})
            elif change=='topic':b.store.save_conversation('10000:private:20000:shared',[],'session_qq_new_topic.jsonl')
            elif change=='policy':
                state=s.policy();s.configure('20000',state['revision'],{'enabled':False})
            elif change=='owner':b.settings=b.settings.model_copy(update={'owner_ids':{'30000'}})
            elif change=='shared_off':b.settings=b.settings.model_copy(update={'shared_memory':False})
            elif change=='expiry':now[0]+=601
            elif change=='restart':c=s.operations.console=QQEventConsole(s.operations)
            with pytest.raises((ValueError,PermissionError)):c.decide('20000',p['id'],p['preview_version'],'confirm')
            with b.store.connect() as db:assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==0
            assert not any(card.get('tracking')=='suppressed' for card in s.people._canonical('20000'))
    asyncio.run(run())


def test_reopened_native_preview_requires_new_full_version_not_reexecution(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            c=s.operations.console=QQEventConsole(s.operations)
            fresh=c.preview('20000',p['id']);assert fresh['preview_version']!=p['preview_version']
            assert c.decide('20000',p['id'],fresh['preview_version'],'confirm')['state']=='done'
    asyncio.run(run())


def test_original_engine_cannot_consume_native_preview_without_local_consent(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            row,data=c.row('20000',p['id']);m=c.message(row,data)
            result=s.operations.execute(m,row,data)
            assert '没有修改任何一条' in result
            assert not any(card.get('tracking')=='suppressed' for card in s.people._canonical('20000'))
    asyncio.run(run())


@pytest.mark.parametrize('channel',['group','nonowner','quote','forward','media'])
def test_other_scope_or_quoted_requests_keep_original_consent_path(tmp_path,channel):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s)
            request=msg('忘掉所有个人事件',group=90000 if channel=='group' else None,user=30000 if channel=='nonowner' else 20000)
            if channel=='quote':request.reply_to='123'
            elif channel=='forward':request.forward_present=True
            elif channel=='media':request.media_types=('image',)
            assert not c.eligible(request)
            result=await s.operations.handle(request)
            assert not isinstance(result,ConsoleNotice)
            assert c.listing('20000')['entries']==[]
    asyncio.run(run())


def test_native_bind_requires_original_admitted_message(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s)
            assert '未能可靠确认' in str(await s.operations.handle(msg('忘掉所有个人事件')))
            assert not c.listing('20000')['entries']
    asyncio.run(run())


def test_parallel_native_confirm_has_one_original_transaction(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            with ThreadPoolExecutor(2) as pool:
                replies=list(pool.map(lambda _:c.decide('20000',p['id'],p['preview_version'],'confirm'),range(2)))
            assert all(r['state']=='done' for r in replies)
            with b.store.connect() as db:assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==1
    asyncio.run(run())


def test_transaction_failure_rolls_back_all_targets_and_never_approves_retry(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            with b.store.connect() as db:db.execute("create trigger fail_second before update on qq_episode_cards when old.id='"+cards[-1]['id']+"' begin select raise(ABORT,'fixture'); end")
            assert c.decide('20000',p['id'],p['preview_version'],'confirm')['state']=='failed'
            assert s.people._canonical('20000')==cards
            with pytest.raises(ValueError):c.decide('20000',p['id'],p['preview_version'],'confirm')
    asyncio.run(run())


def test_decline_never_changes_cards_or_deletes_original_chat(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            assert c.decide('20000',p['id'],p['preview_version'],'reject')['state']=='refused'
            assert c.decide('20000',p['id'],p['preview_version'],'confirm')['state']=='refused'
            assert s.people._canonical('20000')==cards
    asyncio.run(run())


def test_native_http_owner_origin_full_preview_and_rollback_capability(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg)
            app=FastAPI();app.include_router(routes.router)
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://127.0.0.1:8000') as client:
                    path='/api/qq/memory-console/events'
                    assert (await client.get(path,params={'owner_id':'20000'})).status_code==401
                    client.headers['Authorization']='Bearer admin'
                    assert (await client.get(path,params={'owner_id':'30000'})).status_code==403
                    listed=await client.get(path,params={'owner_id':'20000'});assert listed.status_code==200
                    detail=await client.get(path+'/'+p['id'],params={'owner_id':'20000'});assert detail.status_code==200,detail.text
                    assert detail.headers['cache-control']=='no-store' and len(detail.json()['targets'])==2
                    values=dict(owner_id='20000',id=p['id'],preview_version=p['preview_version'],action='confirm')
                    assert (await client.post('/api/qq/memory-console/event-decision',json=values)).status_code==403
                    response=await client.post('/api/qq/memory-console/event-decision',json=values,headers={'Origin':'http://127.0.0.1:8000'})
                    assert response.status_code==200 and response.json()['state']=='done',response.text
            import sqlite3
            target=tmp_path/'rollback/knowledge_base/qq';target.mkdir(parents=True)
            with b.store.connect() as source,sqlite3.connect(target/'bridge.sqlite3') as destination:source.backup(destination)
            assert 'qq_event_console_v1' in memory_rollback_requirements(target.parent.parent)
    asyncio.run(run())


def test_native_event_render_preserves_full_sources_and_scope_warning():
    from saya_qq_event_panel import event_text
    text='虚构的完整来源正文'*500
    output=event_text(dict(state='native_prepared',expires=2000000000,action='stop',scope='both',targets=[
        {'id':'fixture','title':'虚构事件','group':'fixture-group','sources':[{'author':'fixture','text':text}]}]))
    assert text in output and '其他成员的跟进保持' in output


def test_lost_response_after_commit_is_read_back_without_another_execution(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);c=attach(s);_,p,_=await offer(b,s,msg);execute=s.operations.execute
            def lost(*a,**kw):execute(*a,**kw);raise OSError('reply lost')
            with patch.object(s.operations,'execute',side_effect=lost):
                with pytest.raises(OSError):c.decide('20000',p['id'],p['preview_version'],'confirm')
            assert c.preview('20000',p['id'])['state']=='done'
            assert c.decide('20000',p['id'],p['preview_version'],'confirm')['state']=='done'
            with b.store.connect() as db:assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0]==1
    asyncio.run(run())


@pytest.mark.parametrize('change',['none','fourth_body','expired','truncated'])
def test_unarchived_sources_are_full_versioned_and_only_the_previewed_set_is_changed(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice);s.topics.dialogue.configure('20000',True)
            ids=[]
            for i in range(4):
                raw=msg('虚构的未建卡材料'+str(i)+'：'+('我还在整理不同的纸张，具体方式没有定。'*8))
                ids.append(s.topics.dialogue.prepare(raw)['id'])
            if change=='expired':
                with b.store.connect() as db:db.execute('update qq_topic_dialogue_units set expires=? where id=?',(now[0]-1,ids[0]))
            if change=='truncated':
                with b.store.connect() as db:
                    row=db.execute('select data from qq_topic_dialogue_units where id=?',(ids[-1],)).fetchone();data=json.loads(row['data']);data['truncated']=True
                    db.execute('update qq_topic_dialogue_units set data=? where id=?',(json.dumps(data),ids[-1]))
            c=attach(s)
            request=msg('忘掉所有个人事件');b.store.admit(request,b.settings)
            assert isinstance(await s.operations.handle(request),ConsoleNotice)
            identifier=c.listing('20000')['entries'][0]['id'];p=c.preview('20000',identifier)
            if change=='truncated':
                assert p['state']=='incomplete_sources'
                with pytest.raises(ValueError):c.decide('20000',identifier,p['preview_version'],'confirm')
                return
            assert len(p['pending_dialogue']['sources'])==(3 if change=='expired' else 4)
            assert len(p['pending_dialogue']['sources'][-1]['text'])>80
            if change=='fourth_body':
                with b.store.connect() as db:
                    ordered=db.execute('select id,data from qq_topic_dialogue_units order by id').fetchall();row=ordered[-1];data=json.loads(row['data']);data['text']+='后来的条件已经改变。'
                    db.execute('update qq_topic_dialogue_units set data=? where id=?',(json.dumps(data),row['id']))
                with pytest.raises(ValueError):c.decide('20000',identifier,p['preview_version'],'confirm')
                return
            assert c.decide('20000',identifier,p['preview_version'],'confirm')['state']=='done'
            now[0]+=3601
            receipt=c.preview('20000',identifier)
            assert receipt['state']=='done' and not receipt['targets']
            assert '虚构的未建卡材料' not in json.dumps(receipt,ensure_ascii=False)
            with b.store.connect() as db:
                states={r['id']:r['blocked'] for r in db.execute('select id,blocked from qq_topic_dialogue_units')}
            assert all(states[i]==(0 if change=='expired' and i==ids[0] else 1) for i in ids)
    asyncio.run(run())


def test_private_group_following_still_requires_membership_and_keeps_other_members(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await seed(s,msg,choice,count=1,dual=True);c=attach(s)
            async def member(action,params):return {'user_id':20000,'group_id':90000}
            with patch.object(b.client,'call',member,create=True):
                request,p,_=await offer(b,s,msg,'忘掉所有个人和群事件')
                assert p['scope']=='both' and {r['kind'] for r in p['targets']}=={'personal','group'}
                await c.refresh_groups('20000',p['id'])
                fresh=c.preview('20000',p['id'])
                assert c.decide('20000',p['id'],fresh['preview_version'],'confirm')['state']=='done'
            with b.store.connect() as db:
                assert db.execute('select count(*) from qq_shared_events').fetchone()[0]==1
                assert db.execute('select subject from qq_shared_event_stops').fetchone()[0]=='20000'
    asyncio.run(run())
