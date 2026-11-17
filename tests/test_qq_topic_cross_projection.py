"""One authored source may have two projections; corrections follow its identity."""
import asyncio
import json
import sqlite3
from copy import deepcopy
from dataclasses import replace
import pytest
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_rollback import check_memory_rollback


async def pair(s,msg,choice):
    first=msg('星桥阅读讨论：我只在周日上午有空。',group=90000)
    choice[0]={'items':[item('我只在周日上午有空。',personal=create(),group=create(),mode='commitment')]}
    await s.observe(first,[])
    return first,s.cards(first)[0],s.unified.cards(first)[0]


@pytest.mark.parametrize('target',['personal','group','both'])
def test_correction_invalidates_same_source_in_both_views(tmp_path,target):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            other=msg('星桥阅读讨论：我愿意准备提纲。',user=30000,group=90000)
            choice[0]={'items':[item('我愿意准备提纲。',group={'action':'update','event_id':g['id'],'supersedes':[]},mode='commitment')]}
            await s.observe(other,[])
            other_claim=s.unified.cards(first)[0]['contributions'][-1]
            before_p=s.context(first);before_g=s.unified.context(first)
            text='我更正自己的时间，只在周六下午有空。'
            change=msg('星桥阅读讨论：'+text,group=90000 if target!='personal' else None)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',
                personal={'action':'update','event_id':p['id'],'supersedes':[0]} if target!='group' else None,
                group={'action':'update','event_id':g['id'],'supersedes':[g['contributions'][0]['id']]} if target!='personal' else None)]}
            await s.observe(change,[])
            raw_p=s.people._canonical('20000')[0]
            raw_g=s.unified.cards(first)[0]
            assert raw_p['facts'][0].get('superseded')
            assert raw_g['contributions'][0].get('superseded')
            assert raw_g['contributions'][1]==other_claim
            assert raw_p['id']==p['id'] and raw_g['id']==g['id']
            assert not s.valid(first,before_p) and not s.unified.valid(first,before_g)
            # A private replacement invalidates old public evidence without publishing it.
            if target=='personal':assert text not in json.dumps(raw_g,ensure_ascii=False)
            assert len(prompts)==3
            assert all(x['status']=='committed' for x in change.topic_results)
    asyncio.run(run())


@pytest.mark.parametrize('mode',['reported','hypothesis','question'])
def test_non_assertive_revision_cannot_retract_either_projection(tmp_path,mode):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            text='假如我更正为周六下午，会怎样？'
            change=msg('星桥阅读讨论：'+text,group=90000)
            choice[0]={'items':[item(text,mode=mode,revision='correction',
                personal={'action':'update','event_id':p['id'],'supersedes':[0]})]}
            await s.observe(change,[])
            assert s.people._canonical('20000')[0]['revision']==p['revision']
            assert s.unified.cards(first)[0]['revision']==g['revision']
    asyncio.run(run())


@pytest.mark.parametrize('failure',['capacity','stale','transaction'])
def test_unsuccessful_replacement_does_not_retract_sibling(tmp_path,failure,monkeypatch):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            if failure=='capacity':
                with s.store.connect() as db:
                    db.executemany('insert or ignore into qq_person_event_source_guards values(?,?,?,?,?,?,?,?)',
                        [(s.unified.bot,'20000','private',f'{i:064x}',p['id'],now[0],now[0]+3600,0) for i in range(16000)])
            if failure=='transaction':
                with s.store.connect() as db:
                    db.execute("create trigger reject_sibling before update on qq_shared_events begin select raise(ABORT,'fixture failure'); end")
            text='我更正自己的时间，只在周六下午有空。'
            value={'items':[item(text,mode='commitment',revision='correction',personal={
                'action':'update','event_id':p['id'],'supersedes':[0]})]}
            if failure=='stale':
                async def late(*args,**kwargs):
                    with s.store.connect() as db:db.execute('update qq_episode_cards set revision=revision+1 where id=?',(p['id'],))
                    return value
                s.decide=late
            choice[0]=value;change=msg('星桥阅读讨论：'+text)
            if failure=='transaction':
                with pytest.raises(sqlite3.IntegrityError):await s.observe(change,[])
            else:await s.observe(change,[])
            assert not s.people._canonical('20000')[0]['facts'][0].get('superseded')
            assert s.unified.cards(first)[0]['revision']==g['revision']
            assert not s.unified.cards(first)[0]['contributions'][0].get('superseded')
    asyncio.run(run())


def test_revision_drops_old_anchors_cache_and_history_and_preserves_idle_base(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            first,p,g=await pair(s,msg,choice)
            old=g['contributions'][0];attention=deepcopy(g['attention'])
            with s.store.connect() as db:
                for kind,c in [('personal',p),('group',g)]:
                    db.execute('insert into qq_topic_state_cache values(?,?,?,?,?,?,?)',
                        (s.unified.bot,kind,c['id'],'old',now[0],now[0]+10000,'{}'))
            text='我更正自己的时间，只在周六下午有空。';change=msg('星桥阅读讨论：'+text)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',personal={
                'action':'update','event_id':p['id'],'supersedes':[0]})]}
            await s.observe(change,[])
            with s.store.connect() as db:
                assert db.execute("select count(*) from qq_topic_state_cache where kind='group'").fetchone()[0]==0
                root=json.loads(db.execute('select data from qq_topic_evidence where id=?',(old['evidence_id'],)).fetchone()[0])
                assert root['superseded_by']!=old['evidence_id']
            after=s.unified.cards(first,include_stopped=True)[0]
            assert not s.unified.cards(first) and not s.unified.context(first)
            assert after['attention']['content_at']==attention['content_at']
            assert after['attention']['half_life']==attention['half_life']
            assert not after['attention']['pending']
            _,_,_,candidates=s.topics._candidates(msg('继续星桥阅读讨论',group=90000))
            assert all(x['id']!=old['evidence_id'] for x in candidates['public_anchors'])
            projection=s.people.history_projection(first)
            assert s.people.filter_history(first,['Doctor: '+first.text,'Saya: 旧回复'],[],projection)[0]==[]
    asyncio.run(run())


def test_only_exact_linked_evidence_is_retracted_not_similar_text(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            # A separate source with identical words is not a projection of the old source.
            unrelated=msg('另一场阅读讨论：我只在周日上午有空。',group=90000)
            choice[0]={'items':[item('我只在周日上午有空。',group=create(),mode='commitment')]}
            await s.observe(unrelated,[])
            independent=next(c for c in s.unified.cards(first) if c['id']!=g['id'])
            text='我更正自己的时间，只在周六下午有空。';change=msg('星桥阅读讨论：'+text)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',personal={
                'action':'update','event_id':p['id'],'supersedes':[0]})]}
            await s.observe(change,[])
            after=next(c for c in s.unified.cards(first) if c['id']==independent['id'])
            assert after==independent
    asyncio.run(run())


def test_retained_revision_metadata_requires_new_reader(tmp_path):
    base=tmp_path/'knowledge_base/qq';base.mkdir(parents=True)
    with sqlite3.connect(base/'bridge.sqlite3') as db:
        db.execute('create table qq_episode_cards(id text,data text)')
        db.execute('insert into qq_episode_cards values(?,?)',('fixture',json.dumps({'facts':[{'topic_revision_version':1}]})))
    missing=check_memory_rollback(tmp_path,{'memory_compatibility':[]})['missing']
    assert 'qq_topic_revisions_v1' in missing
    assert check_memory_rollback(tmp_path,{'memory_compatibility':missing})['status']=='compatible_declared'


@pytest.mark.parametrize('selected',['personal','group'])
def test_both_targets_need_only_one_exact_supersedes_link(tmp_path,selected):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            text='我更正自己的时间，只在周六下午有空。';change=msg('星桥阅读讨论：'+text,group=90000)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',
                personal={'action':'update','event_id':p['id'],'supersedes':[0] if selected=='personal' else []},
                group={'action':'update','event_id':g['id'],'supersedes':[g['contributions'][0]['id']] if selected=='group' else []})]}
            await s.observe(change,[])
            p2=s.people._canonical('20000')[0];g2=s.unified.cards(first)[0]
            assert p2['facts'][0]['superseded'] and g2['contributions'][0]['superseded']
            assert p2['facts'][-1]['quote']==g2['contributions'][-1]['text']==text
            assert all(x['status']=='committed' for x in change.topic_results)
    asyncio.run(run())


def test_withdrawal_invalidates_current_decision_and_pending_delivery(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            s.unified.command(replace(first,text='/events group coordinator '+g['id']+' 20000'))
            text='最终确定周日上午举办。';decision=msg('星桥阅读讨论：'+text,group=90000)
            choice[0]={'items':[item(text,mode='decision',personal={'action':'update','event_id':p['id'],'supersedes':[]},
                group={'action':'update','event_id':g['id'],'supersedes':[]})]}
            await s.observe(decision,[])
            d=s.unified.cards(first)[0]
            assert d['current_decision']==d['contributions'][-1]['id']
            first.group_event_snapshot=s.unified.context(first)
            first.episode_snapshot=s.context(first)
            s.bind_result(first,'cross-projection-result')
            assert s.valid_delivery(first,'cross-projection-result')
            text='我撤回自己的原定案，时间重新待定。';withdrawal=msg('星桥阅读讨论：'+text)
            choice[0]={'items':[item(text,mode='withdrawal',revision='change',personal={
                'action':'update','event_id':p['id'],'supersedes':[1]})]}
            await s.observe(withdrawal,[])
            after=s.unified.cards(first)[0]
            assert not after.get('current_decision')
            assert s.unified.decision_card(after)=={}
            assert '尚未定案' in s.unified.summary(after)
            assert not s.valid_delivery(first,'cross-projection-result')
    asyncio.run(run())


@pytest.mark.parametrize('invalid',['actor','source','expired'])
def test_unverified_or_expired_root_cannot_authorize_sibling_mutation(tmp_path,invalid):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice);eid=p['facts'][0]['evidence_id']
            with s.store.connect() as db:
                field,value={'actor':('actor','30000'),'source':('source','forged:source'),'expired':('expires',now[0]-1)}[invalid]
                db.execute('update qq_topic_evidence set '+field+'=? where id=?',(value,eid))
            text='我更正自己的时间，只在周六下午有空。';change=msg('星桥阅读讨论：'+text)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',personal={
                'action':'update','event_id':p['id'],'supersedes':[0]})]}
            await s.observe(change,[])
            assert s.unified.cards(first)[0]['revision']==g['revision']
            assert not s.unified.cards(first)[0]['contributions'][0].get('superseded')
    asyncio.run(run())


def test_sibling_invalidation_preserves_stop_and_never_resumes_tracking(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,p,g=await pair(s,msg,choice)
            s.command(msg('/events stop '+p['id']))
            text='我更正自己的时间，只在周六下午有空。';change=msg('星桥阅读讨论：'+text,group=90000)
            choice[0]={'items':[item(text,mode='commitment',revision='correction',group={
                'action':'update','event_id':g['id'],'supersedes':[g['contributions'][0]['id']]})]}
            await s.observe(change,[])
            after=s.people._canonical('20000')[0]
            assert after['facts'][0]['superseded'] and after['tracking']=='suppressed'
            assert not s.context(msg('继续星桥阅读讨论'))
    asyncio.run(run())
