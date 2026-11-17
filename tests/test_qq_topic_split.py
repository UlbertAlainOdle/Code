"""Whole-source partitioning frees slots without hiding facts or raising quotas."""
import asyncio
import json
import re
import sqlite3
from copy import deepcopy
from dataclasses import replace
import pytest
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_topic_lifecycle import source_expiry
from saya_app.services.qq_rollback import check_memory_rollback
from test_qq_delivery import event
from test_qq_task_scheduler import until


def raw(s,identifier):
    with s.store.connect() as db:
        row=db.execute('select revision,data from qq_shared_events where id=?',(identifier,)).fetchone()
    return row[0],json.loads(row[1])


async def seed(s,msg,choice,*,count=4,idle=False,mode='opinion',dual=False):
    if idle:s.unified.lifecycle.configure('20000',0,{'enabled':True})
    first=msg('星桥阅读讨论：我觉得纸质书适合慢读。',group=90000)
    choice[0]={'items':[item('我觉得纸质书适合慢读。',personal=create() if dual else None,group=create(),mode=mode)]}
    await s.observe(first,[])
    card=s.unified.cards(first)[0];mid=card['id']
    for i in range(1,count):
        m=msg('星桥阅读讨论：我觉得阅读方式还需考虑场景'+str(i)+'。',user=30000 if i%2 else 20000,group=90000)
        choice[0]={'items':[item(m.text.split('：')[1],group={'action':'update','event_id':mid,'supersedes':[]},mode=mode)]}
        await s.observe(m,[])
    s.unified.command(msg('/events group coordinator '+mid+' 20000',group=90000))
    return first,s.unified.cards(first)[0]


def preview(s,msg,card,ids=None,title='星桥纸书体验子话题'):
    command='/events group split '+card['id']+' '+','.join(ids or [card['contributions'][0]['id']])+' '+title
    receipt=str(s.unified.command(msg(command,group=90000)))
    token=re.search(r'split-confirm ([a-f0-9]{32})',receipt)
    return receipt,token[1] if token else None


def confirm(s,msg,token,user=20000,group=90000):
    return str(s.unified.command(msg('/events group split-confirm '+token,user=user,group=group)))


def test_full_window_split_preserves_all_sources_and_accepts_fresh_progress(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice,count=32)
            before=raw(s,card['id']);calls=len(prompts)
            receipt,token=preview(s,msg,card,[x['id'] for x in card['contributions'][:4]])
            assert token and '尚未移动' in receipt and raw(s,card['id'])==before
            p_snapshot=s.unified.context(first)
            result=confirm(s,msg,token);assert '已按预览' in result
            cards=s.unified.cards(first);assert len(cards)==2
            parent=next(c for c in cards if c['id']==card['id']);child=next(c for c in cards if c['id']!=card['id'])
            assert len(parent['contributions'])==28 and len(child['contributions'])==4
            after={x['id']:{k:v for k,v in x.items() if k not in {'split_policy','split_expiry'}} for c in cards for x in c['contributions']}
            assert after=={x['id']:x for x in card['contributions']}
            assert child['coordinator']=='20000' and child['state']=='cooling'
            assert not s.unified.valid(first,p_snapshot)
            assert len(prompts)==calls
            assert '已按预览' not in confirm(s,msg,token)
            fresh=msg('星桥阅读讨论：新增阅读观点。',group=90000)
            choice[0]={'items':[item('新增阅读观点。',group={'action':'update','event_id':parent['id'],'supersedes':[]})]}
            await s.observe(fresh,[])
            assert len(next(c for c in s.unified.cards(first) if c['id']==parent['id'])['contributions'])==29
            assert s.unified.policy()['values']['max_contributions']==32
    asyncio.run(run())


@pytest.mark.parametrize('change',['revision','coordinator','stop','policy','membership','expiry','capacity'])
def test_preview_rechecks_state_and_never_moves_on_conflict(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            _,token=preview(s,msg,card);assert token
            if change=='revision':
                with s.store.connect() as db:db.execute('update qq_shared_events set revision=revision+1 where id=?',(card['id'],))
            elif change=='coordinator':s.unified.command(msg('/events group coordinator '+card['id']+' 30000',group=90000))
            elif change=='stop':s.unified.command(msg('/events group stop '+card['id'],user=30000,group=90000))
            elif change=='policy':
                p=s.unified.policy();s.unified.configure('20000',p['revision'],{**p['values'],'group_total':19})
            elif change=='membership':s.unified.mark_member('90000','20000','revoked')
            elif change=='expiry':now[0]+=601
            elif change=='capacity':
                with s.store.connect() as db:
                    body=raw(s,card['id'])[1]
                    for i in range(19):db.execute('insert into qq_shared_events values(?,?,?,?,?)',(f'{i:016x}',s.unified.bot,'90000',1,json.dumps(body)))
            before=raw(s,card['id'])
            result=confirm(s,msg,token)
            assert '已按预览' not in result and raw(s,card['id'])==before
            with s.store.connect() as db:
                assert not any(json.loads(r[0]).get('split_from') for r in db.execute('select data from qq_shared_events'))
    asyncio.run(run())


def test_authorship_stops_root_links_and_cross_projection_correction_follow_moved_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice,dual=True)
            s.unified.command(msg('/events group stop '+card['id'],user=30000,group=90000))
            _,token=preview(s,msg,card);assert token
            result=confirm(s,msg,token);assert '已按预览' in result
            child=next(c for c in s.unified.cards(first) if c['id']!=card['id'])
            assert not s.unified.cards(msg('/events group',user=30000,group=90000))
            old=child['contributions'][0];p=s.people._canonical('20000')[0]
            with s.store.connect() as db:
                links=json.loads(db.execute('select data from qq_topic_evidence where id=?',(old['evidence_id'],)).fetchone()[0])['links']
                assert {'kind':'group','id':child['id']} in links and {'kind':'group','id':card['id']} not in links
            text='我更正自己的观点，纸质书也适合查阅。'
            change=msg('星桥阅读讨论：'+text)
            choice[0]={'items':[item(text,revision='correction',personal={'action':'update','event_id':p['id'],'supersedes':[0]})]}
            await s.observe(change,[])
            assert raw(s,child['id'])[1]['contributions'][0]['superseded']
            assert not any(c['id']==child['id'] for c in s.unified.cards(first))
    asyncio.run(run())


def test_idle_source_is_not_renewed_by_split_or_future_child_continuation(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice,idle=True)
            old=card['contributions'][0];deadline=source_expiry(card,old);attention=deepcopy(card['attention'])
            _,token=preview(s,msg,card);assert token
            assert '已按预览' in confirm(s,msg,token)
            child=next(c for c in s.unified.cards(first) if c['id']!=card['id'])
            assert child['attention']['content_at']==attention['content_at']
            assert source_expiry(child,child['contributions'][0])==deadline
            now[0]+=3700
            text='我也觉得书签很好用。';fresh=msg('星桥纸书体验子话题：'+text,group=90000)
            choice[0]={'items':[item(text,group={'action':'update','event_id':child['id'],'supersedes':[]})]}
            await s.observe(fresh,[])
            current=next(c for c in s.unified.cards(first) if c['id']==child['id'])
            assert source_expiry(current,current['contributions'][0])==deadline
            assert current['contributions'][0]['at']==old['at']
            now[0]=deadline+1
            current=next(c for c in s.unified.cards(first) if c['id']==child['id'])
            assert all(c['id']!=old['id'] for c in current['contributions'])
    asyncio.run(run())


def test_complete_author_chain_is_shown_and_decision_cannot_move(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice,mode='commitment')
            own=[x for x in card['contributions'] if x['actor']=='20000']
            receipt,token=preview(s,msg,card,[own[0]['id']]);assert token
            assert all(x['text'] in receipt for x in own)
            result=confirm(s,msg,token);assert '已按预览' in result
            child=next(c for c in s.unified.cards(first) if c['id']!=card['id'])
            assert {x['id'] for x in child['contributions']}=={x['id'] for x in own}
            with s.store.connect() as db:
                body=raw(s,child['id'])[1];body['contributions'][0]['kind']='decision';body['current_decision']=body['contributions'][0]['id']
                db.execute('update qq_shared_events set data=? where id=?',(json.dumps(body),child['id']))
            child=s.unified.cards(first)[0] if s.unified.cards(first)[0]['id']==child['id'] else s.unified.cards(first)[1]
            receipt,token=preview(s,msg,child,title='星桥正式决定子讨论');assert not token and '决定留在原话题' in receipt
    asyncio.run(run())


def test_deleted_or_stopped_child_does_not_leak_back_as_public_anchor(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            eid=card['contributions'][0]['evidence_id']
            _,token=preview(s,msg,card);assert '已按预览' in confirm(s,msg,token)
            child=next(c for c in s.unified.cards(first) if c['id']!=card['id'])
            s.unified.command(msg('/events group stop '+child['id'],group=90000))
            _,_,_,c=s.topics._candidates(msg('继续星桥阅读讨论',group=90000))
            assert all(x['id']!=eid for x in c['public_anchors'])
            s.unified.command(msg('/events group resume '+child['id'],group=90000))
            receipt=str(s.unified.command(msg('/events group delete '+child['id'],group=90000)))
            deletion=re.search(r'/events group confirm ([a-f0-9]{32})',receipt)[1]
            assert '已按确认删除' in str(s.unified.command(msg('/events group confirm '+deletion,group=90000)))
            _,_,_,c=s.topics._candidates(msg('继续星桥阅读讨论',group=90000))
            assert all(x['id']!=eid for x in c['public_anchors'])
            assert len(s.unified.cards(first))==1
    asyncio.run(run())


def test_full_window_failure_gives_host_coverage_and_split_route_without_writing(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice,count=32)
            before=raw(s,card['id']);text='还有一条新的阅读观点。';fresh=msg('星桥阅读讨论：'+text,group=90000)
            choice[0]={'items':[item(text,group={'action':'update','event_id':card['id'],'supersedes':[]})]}
            await s.observe(fresh,[])
            assert raw(s,card['id'])==before
            assert '/events group split' in fresh.topic_coverage and '尚未写入' in fresh.topic_coverage
            assert fresh.topic_results[0]['status']=='not_committed'
    asyncio.run(run())


def test_sql_failure_rolls_back_movement_and_preserves_preview(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            _,token=preview(s,msg,card);assert token
            before=raw(s,card['id'])
            with s.store.connect() as db:
                db.execute("create trigger block_split before insert on qq_shared_events begin select raise(ABORT,'fixture failure'); end")
            with pytest.raises(sqlite3.IntegrityError):confirm(s,msg,token)
            assert raw(s,card['id'])==before
            with s.store.connect() as db:
                assert db.execute('select count(*) from qq_shared_events').fetchone()[0]==1
                assert db.execute('select count(*) from qq_topic_split_previews where token=?',(token,)).fetchone()[0]==1
    asyncio.run(run())


def test_private_confirmation_requires_current_membership_and_same_actor(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            q=msg('/events group split '+card['id']+' '+card['contributions'][0]['id']+' 星桥纸书体验子话题')
            assert '没有读取' in str(s.unified.command(q))
            s.unified.mark_member('90000','20000')
            previewed=str(s.unified.command(q));token=re.search('split-confirm ([a-f0-9]{32})',previewed)[1]
            before=raw(s,card['id'])
            assert '已按预览' not in confirm(s,msg,token,user=30000)
            assert raw(s,card['id'])==before
            q=msg('/events group split-confirm '+token)
            s.unified.mark_member('90000','20000')
            assert '已按预览' in str(s.unified.command(q))
    asyncio.run(run())


def test_split_sources_and_preview_require_new_reader(tmp_path):
    folder=tmp_path/'knowledge_base/qq';folder.mkdir(parents=True)
    with sqlite3.connect(folder/'bridge.sqlite3') as db:
        db.execute('create table qq_shared_events(id text,data text)')
        db.execute('insert into qq_shared_events values(?,?)',('fixture',json.dumps({'split_from':{'version':1}})))
    missing=check_memory_rollback(tmp_path,{'memory_compatibility':[]})['missing']
    assert 'qq_topic_split_v1' in missing
    assert check_memory_rollback(tmp_path,{'memory_compatibility':missing})['status']=='compatible_declared'
    with pytest.raises(ValueError):source_expiry({}, {'expires':1000,'split_policy':2,'split_expiry':900})


@pytest.mark.parametrize('problem',['missing','quote','link'])
def test_unverifiable_routing_root_rolls_back_the_entire_split(tmp_path,problem):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            _,token=preview(s,msg,card);assert token
            before=raw(s,card['id']);eid=card['contributions'][0]['evidence_id']
            with s.store.connect() as db:
                root=json.loads(db.execute('select data from qq_topic_evidence where id=?',(eid,)).fetchone()[0])
                if problem=='missing':db.execute('delete from qq_topic_evidence where id=?',(eid,))
                else:
                    if problem=='quote':root['quote']='a different authored statement'
                    else:root['links']=[]
                    db.execute('update qq_topic_evidence set data=? where id=?',(json.dumps(root),eid))
            assert '本次没有整理' in confirm(s,msg,token)
            assert raw(s,card['id'])==before
            assert len(s.unified.cards(first))==1
    asyncio.run(run())


def test_real_bridge_commands_preview_and_confirm_without_model_calls(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice);count=len(prompts)
            async def forbidden(*args,**kwargs):raise AssertionError('control command must not call a model')
            s.decide=forbidden;b.responder=forbidden
            command='/events group split '+card['id']+' '+card['contributions'][0]['id']+' 星桥纸书体验子话题'
            assert b.submit(event(command,80000,20000,90000))[0]=='queued'
            await until(lambda:bool(b.client.sent))
            await until(lambda:not b.task_registry.list_active())
            with s.store.connect() as db:token=db.execute('select token from qq_topic_split_previews').fetchone()[0]
            assert b.submit(event('/events group split-confirm '+token,80001,20000,90000))[0]=='queued'
            await until(lambda:len(s.unified.cards(first))==2)
            await until(lambda:not b.task_registry.list_active())
            assert len(prompts)==count
            with s.store.connect() as db:
                kinds={r[0] for r in db.execute('select content_kind from qq_delivery_part_kinds')}
                assert kinds=={'control_receipt'}
            assert any('已按预览' in part[1] for part in b.client.sent)
    asyncio.run(run())


@pytest.mark.parametrize('case',['other_actor','whole_card','bad_source','long_preview'])
def test_invalid_previews_do_not_change_cards(tmp_path,case):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await seed(s,msg,choice)
            if case=='long_preview':
                with s.store.connect() as db:
                    c=raw(s,card['id'])[1]
                    for x in c['contributions'][:3]:x['text']='长段内容'*450
                    db.execute('update qq_shared_events set data=? where id=?',(json.dumps(c),card['id']))
                card=s.unified.cards(first)[0]
            before=raw(s,card['id'])
            ids=[x['id'] for x in card['contributions']] if case=='whole_card' else ['f'*16] if case=='bad_source' else [x['id'] for x in card['contributions'][:3]] if case=='long_preview' else None
            if case=='other_actor':
                receipt=str(s.unified.command(msg('/events group split '+card['id']+' '+card['contributions'][0]['id']+' 星桥子话题测试',user=30000,group=90000)))
                assert '协调人' in receipt
            else:
                receipt,token=preview(s,msg,card,ids);assert not token
            assert raw(s,card['id'])==before
            with s.store.connect() as db:assert db.execute('select count(*) from qq_topic_split_previews').fetchone()[0]==0
    asyncio.run(run())
