import asyncio
import json
from dataclasses import replace

import pytest

from test_qq_topic_routing import topics
from test_qq_topic_dialogue_sources import discussion


def test_new_identical_input_after_stop_has_new_card_and_old_tags_stay_invalid(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(first.text,'虚构风塔');await s.observe(first,[])
            old=s.cards(first)[0];old_tag=s.provenance.direct(first,{})
            old_derived=s.provenance.inherit(first,s.provenance.empty(first),[{'role':'ai','qq_event_dependencies':old_tag}])
            s.command(msg('/events stop '+old['id']))
            with b.store.connect() as db:
                stopped=tuple(db.execute('select * from qq_episode_cards where id=?',(old['id'],)).fetchone())
                guard=tuple(db.execute('select * from qq_person_event_source_guards where event_id=?',(old['id'],)).fetchone())
            fresh=msg(first.text);choice[0]=discussion(fresh.text,'虚构风塔');await s.observe(fresh,[])
            new=next((c for c in s.cards(fresh) if c['id']!=old['id']),None)
            assert new and len(new['facts'])==1 and new['facts'][0]['source']==fresh.key
            assert new['facts'][0]['at']==fresh.sent_at and new['facts'][0]['expires']==fresh.sent_at+s.retention(fresh)
            tag=s.provenance.direct(fresh,{})
            assert s.provenance.valid(fresh,tag) and not s.provenance.valid(fresh,old_tag) and not s.provenance.valid(fresh,old_derived)
            rows=s.provenance.label_rows(fresh,[{'role':'user','content':first.text,'qq_event_dependencies':old_tag},{'role':'user','content':fresh.text,'qq_event_dependencies':tag}])
            assert len(rows)==1 and rows[0]['qq_event_dependencies']==tag
            with b.store.connect() as db:
                assert tuple(db.execute('select * from qq_episode_cards where id=?',(old['id'],)).fetchone())==stopped
                assert tuple(db.execute('select * from qq_person_event_source_guards where event_id=?',(old['id'],)).fetchone())==guard
            s.command(msg('/events stop '+new['id']))
            assert not s.provenance.valid(fresh,tag) and not s.provenance.valid(fresh,old_tag)
    asyncio.run(run())


@pytest.mark.parametrize('kind',['old_event','pre_stop_pending','missing_audit','paused','legacy_observer'])
def test_old_or_unproved_sources_do_not_bypass_stopped_body_guard(tmp_path,kind):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(first.text,'虚构风塔');await s.observe(first,[])
            old=s.cards(first)[0]
            pending=msg(first.text);d.prepare(pending)
            s.command(msg('/events '+('pause' if kind=='paused' else 'stop')+' '+old['id']))
            if kind=='missing_audit':
                with b.store.connect() as db:db.execute('delete from qq_event_control_audit')
            candidate=replace(first) if kind=='old_event' else pending if kind=='pre_stop_pending' else msg(first.text)
            choice[0]=discussion(candidate.text,'虚构风塔')
            if kind=='legacy_observer':
                candidate.topic_choice={'action':'create','confidence':.95,'title':'虚构风塔','topic_anchor':'虚构风塔','supersedes':[]}
                await s.people.observe(candidate,[])
            else:await s.observe(candidate,[])
            assert len(s.people._canonical(first.user_id))==1
            assert s.people._canonical(first.user_id)[0]['id']==old['id']
    asyncio.run(run())


def test_disabled_dialogue_during_model_wait_prevents_new_capture(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            current=msg('虚构风塔的回声为什么会重叠？')
            async def late(*args,**kwargs):
                d.configure('20000',False)
                return discussion(current.text,'虚构风塔')
            s.decide=late;await s.observe(current,[])
            assert not s.people._canonical(current.user_id)
            with b.store.connect() as db:
                row=db.execute('select state from qq_topic_routes where event=?',(current.key,)).fetchone()
                assert row['state']=='stale'
    asyncio.run(run())


def test_multiple_fresh_cycles_preserve_each_stopped_card_and_body_guard(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True);ids=[];tags=[]
            for turn in range(3):
                current=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(current.text,'虚构风塔');await s.observe(current,[])
                new=next(c for c in s.cards(current) if c['id'] not in ids)
                ids.append(new['id']);tag=s.provenance.direct(current,{});assert s.provenance.valid(current,tag)
                assert all(not s.provenance.valid(current,t) for t in tags)
                tags.append(tag);s.command(msg('/events stop '+new['id']))
            assert len(set(ids))==3 and all(not s.provenance.valid(current,t) for t in tags)
            assert all(c['tracking']=='suppressed' for c in s.people._canonical(current.user_id))
            with b.store.connect() as db:
                guard=db.execute('select event_id,blocked from qq_person_event_source_guards').fetchall()
                assert len(guard)==1 and tuple(guard[0])==(ids[0],1)
    asyncio.run(run())


def test_old_unbound_source_is_not_backfilled_into_fresh_replacement(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(first.text,'虚构风塔');await s.observe(first,[])
            old=s.cards(first)[0];pending=msg(first.text);prior=d.prepare(pending)
            s.command(msg('/events stop '+old['id']))
            current=msg(first.text);choice[0]=discussion(current.text,'虚构风塔',sources=[prior['id']]);await s.observe(current,[])
            new=next(c for c in s.cards(current) if c['id']!=old['id'])
            assert len(new['facts'])==1 and new['facts'][0]['source']==current.key
            assert not d.valid_ref(current,prior)
            assert any(x['target']=='personal_prior' and x['status']=='not_committed' for x in current.topic_results)
    asyncio.run(run())


def test_disabled_after_plan_is_checked_again_inside_card_transaction(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            current=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(current.text,'虚构风塔')
            actual=s.people.observe
            async def disable_before_commit(message,history):
                d.configure('20000',False)
                return await actual(message,history)
            s.people.observe=disable_before_commit;await s.observe(current,[])
            assert not s.people._canonical(current.user_id)
            assert any(x['status']=='not_committed' for x in current.topic_results)
            assert getattr(current,'topic_coverage',None)
    asyncio.run(run())


def test_source_blocked_after_plan_is_not_committed_as_new_card(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            current=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(current.text,'虚构风塔')
            actual=s.people.observe
            async def block_before_commit(message,history):
                with b.store.connect() as db:d.block_pending(db,current)
                return await actual(message,history)
            s.people.observe=block_before_commit;await s.observe(current,[])
            assert not s.people._canonical(current.user_id)
            assert any(x['status']=='not_committed' for x in current.topic_results)
    asyncio.run(run())
