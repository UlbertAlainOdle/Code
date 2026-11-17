import asyncio
import hashlib

import pytest

from test_qq_topic_dialogue_sources import discussion
from test_qq_topic_routing import topics


def span(ref, quote, mode='question', subject='general', **extra):
    return dict(id=ref['id'], quote=quote, mode=mode, subject=subject, **extra)


def test_two_early_topics_keep_exact_spans_and_shared_raw_dependency(tmp_path):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            a='海湾潮汐为什么变化？';z='我认为山谷回声需要区分反射条件。'
            first=msg(a+'；另外，'+z);choice[0]={'items':[]};await s.observe(first,[])
            raw=s.provenance.direct(first,{})
            derived=s.provenance.inherit(first,s.provenance.empty(first),[{'role':'ai','qq_event_dependencies':raw}])
            ref=d.prepare(first)
            current=msg('海湾潮汐和山谷回声都继续讨论。')
            choice[0]={'items':[
                discussion('海湾潮汐','海湾潮汐',sources=[span(ref,a)])['items'][0],
                discussion('山谷回声','山谷回声',sources=[span(ref,z,'opinion','self')])['items'][0]]}
            await s.observe(current,[])
            cards=s.cards(current);assert len(cards)==2
            early=[(c,f) for c in cards for f in c['facts'] if f['source']==first.key]
            assert len(early)==2
            for card,fact in early:
                quote=fact['quote'];start=first.text.index(quote)
                assert fact['source_span']==[start,start+len(quote)]
                assert fact['parent_digest']==hashlib.sha256(('Doctor: '+first.text).encode()).hexdigest()
                assert fact['speaker']==first.user_id and fact['at']==first.sent_at
                assert fact['expires']==ref['expires']
                assert card['last_user']==current.sent_at and card['latest_source']==current.key
            assert {(f['quote'],f['mode'],f['subject']) for _,f in early}=={(a,'question','general'),(z,'opinion','self')}
            assert all(r['status']=='committed' for r in current.topic_results)
            assert s.provenance.valid(current,raw) and s.provenance.valid(current,derived)
            s.command(msg('/events stop '+early[0][0]['id']))
            assert not s.provenance.valid(current,raw) and not s.provenance.valid(current,derived)
            other=next(c for c in cards if c['id']!=early[0][0]['id'])
            assert any(c['id']==other['id'] for c in s.cards(current))
    asyncio.run(run())


@pytest.mark.parametrize('change',['same','relabel','reassign'])
def test_bound_span_is_idempotent_and_cannot_be_reclassified_or_moved(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('如果海湾潮汐反过来变化呢？');ref=d.prepare(first)
            current=msg('海湾潮汐的假设继续讨论。')
            selected=span(ref,first.text,'hypothesis')
            choice[0]=discussion(current.text,'海湾潮汐',sources=[selected]);await s.observe(current,[])
            card=s.cards(current)[0];before=card['revision']
            if change=='relabel':selected={**selected,'mode':'experience'}
            target=card['id']
            if change=='reassign':
                new=msg('山谷回声为什么重叠？');choice[0]=discussion(new.text,'山谷回声');await s.observe(new,[])
                target=next(c['id'] for c in s.cards(new) if c['id']!=card['id'])
            if change=='same':
                assert await d.backfill(current,target,[selected],d.context(current))==[]
            else:
                with pytest.raises(ValueError,match='already_bound'):
                    await d.backfill(current,target,[selected],d.context(current))
            original=next(c for c in s.people._canonical(current.user_id) if c['id']==card['id'])
            assert original['revision']==before
            assert next(f for f in original['facts'] if f['source']==first.key)['mode']=='hypothesis'
    asyncio.run(run())


@pytest.mark.parametrize('level,friend,allowed',[('important',True,True),('familiar',True,False),('ordinary',True,False),('important',False,False)])
def test_private_source_qualification_uses_existing_grade_and_friendship(tmp_path,level,friend,allowed):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            user='30000';b.relationships.configure('20000',user,'global',0,{'level':level})
            from unittest.mock import patch
            actual=s.unified.friend
            with patch.object(s.unified,'friend',lambda actor:friend if actor==user else actual(actor)):
                m=msg('海湾潮汐为什么变化？',user=int(user));ref=d.prepare(m)
                assert bool(ref)==allowed
                choice[0]=discussion(m.text,'海湾潮汐');await s.observe(m,[])
                assert bool(s.people._canonical(user))==allowed
            assert not s.people._canonical('20000')
    asyncio.run(run())


def test_authorization_loss_during_plan_does_not_commit_or_use_old_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            b.relationships.configure('20000','30000','global',0,{'level':'important'})
            first=msg('海湾潮汐为什么变化？',user=30000);ref=d.prepare(first);assert ref
            current=msg('海湾潮汐的疑问继续讨论。',user=30000)
            async def late(*args,**kwargs):
                revision=b.relationships._config('30000','global')['revision']
                b.relationships.configure('20000','30000','global',revision,{'level':'ordinary'})
                return discussion(current.text,'海湾潮汐',sources=[span(ref,first.text)])
            s.decide=late;await s.observe(current,[])
            assert not s.people._canonical('30000') and not d.valid_ref(current,ref)
    asyncio.run(run())


@pytest.mark.parametrize('bad',['forged','ambiguous','bool_offset','wrong_offset','mode','subject','overlap','budget'])
def test_invalid_early_selection_rejects_whole_plan_before_current_write(tmp_path,bad):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('海湾潮汐？海湾潮汐？');ref=d.prepare(first)
            selected=span(ref,'海湾潮汐？',start=0)
            if bad=='forged':selected['quote']='虚构的新原话'
            if bad=='ambiguous':selected.pop('start')
            if bad=='bool_offset':selected['start']=False
            if bad=='wrong_offset':selected['start']=1
            if bad=='mode':selected['mode']='confirmed_fact'
            if bad=='subject':selected['subject']='assistant'
            sources=[selected]* (4 if bad=='budget' else 2 if bad=='overlap' else 1)
            current=msg('继续讨论海湾潮汐。');choice[0]=discussion(current.text,'海湾潮汐',sources=sources)
            await s.observe(current,[])
            assert not s.people._canonical(current.user_id)
            with b.store.connect() as db:
                row=db.execute('select state from qq_topic_routes where event=?',(current.key,)).fetchone()
                assert row[0]=='failed_plan'
                assert not db.execute('select 1 from qq_topic_dialogue_links').fetchone()
            assert d.valid_ref(first,ref)
    asyncio.run(run())


def test_repeated_early_quote_uses_supplied_offset_and_keeps_hypothesis(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            quote='如果边界会移动呢？';first=msg(quote+'；'+quote);ref=d.prepare(first)
            current=msg('继续讨论这个边界假设。')
            choice[0]=discussion(current.text,'边界',sources=[span(ref,quote,'hypothesis',start=len(quote)+1)])
            await s.observe(current,[])
            facts=s.cards(current)[0]['facts'];old=next(f for f in facts if f['source']==first.key)
            assert old['mode']=='hypothesis' and old['subject']=='general'
            assert old['source_span']==[len(quote)+1,len(first.text)]
    asyncio.run(run())


def test_overlapping_selection_across_items_is_rejected_before_any_write(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('海湾潮汐为什么变化？');ref=d.prepare(first)
            current=msg('海湾潮汐和山谷回声都继续讨论。')
            choice[0]={'items':[
                discussion('海湾潮汐','海湾潮汐',sources=[span(ref,first.text)])['items'][0],
                discussion('山谷回声','山谷回声',sources=[span(ref,'潮汐为什么变化？')])['items'][0]]}
            await s.observe(current,[])
            assert not s.people._canonical(current.user_id)
    asyncio.run(run())


def test_early_span_keeps_whitespace_negation_and_original_deadline(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            quote=' 我不认为浓雾一定降低可见范围，除非灯光条件相同。 '
            first=msg('先说雾海灯塔：'+quote+'；另一个话题以后说。');ref=d.prepare(first)
            current=msg('雾海灯塔的可见范围继续讨论。')
            choice[0]=discussion(current.text,'雾海灯塔',sources=[span(ref,quote,'opinion','self')])
            original=s.retention;s.retention=lambda m:original(m)+86400
            await s.observe(current,[])
            old=next(f for f in s.cards(current)[0]['facts'] if f['source']==first.key)
            assert old['quote']==quote and old['expires']==ref['expires']
            assert old['source_span']==[first.text.index(quote),first.text.index(quote)+len(quote)]
    asyncio.run(run())


@pytest.mark.parametrize('change',['other_actor','group','expired','stopped','disabled','truncated'])
def test_unavailable_prior_sources_never_get_backfilled(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('海湾潮汐为什么变化？'+('附加材料'*500 if change=='truncated' else ''));ref=d.prepare(first)
            current=msg('继续讨论海湾潮汐。',user=30000 if change=='other_actor' else 20000,group=90000 if change=='group' else None)
            if change=='expired':now[0]=ref['expires']+1;current.sent_at=now[0]
            if change=='stopped':
                with b.store.connect() as db:d.block_pending(db,first)
            if change=='disabled':d.configure('20000',False)
            choice[0]=discussion(current.text,'海湾潮汐',sources=[span(ref,'海湾潮汐为什么变化？')])
            await s.observe(current,[])
            assert not any(f['source']==first.key for c in s.people._canonical(current.user_id) for f in c['facts'])
            with b.store.connect() as db:assert not db.execute('select 1 from qq_topic_dialogue_links where unit=?',(ref['id'],)).fetchone()
    asyncio.run(run())
