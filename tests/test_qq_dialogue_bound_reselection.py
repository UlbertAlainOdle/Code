import asyncio
import json

import pytest

from test_qq_topic_routing import topics
from test_qq_topic_dialogue_sources import discussion
from test_qq_dialogue_prior_spans import span


def test_direct_source_reselection_is_unchanged_without_false_partial(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构灯塔的蓝光为什么被吸收？');choice[0]=discussion(first.text,'虚构灯塔');await s.observe(first,[])
            ref=d.prepare(first);card=s.cards(first)[0];original=card['facts'][0]
            tag=s.provenance.direct(first,{})
            current=msg('虚构灯塔里的红光会怎样？')
            choice[0]=discussion(current.text,'虚构灯塔',sources=[span(ref,first.text)])
            await s.observe(current,[])
            saved=s.cards(current)[0]
            assert saved['id']==card['id'] and saved['revision']==2 and len(saved['facts'])==2
            assert saved['facts'][0]==original and all(x['status']=='committed' for x in current.topic_results)
            assert not getattr(current,'topic_coverage',None)
            with b.store.connect() as db:
                route=db.execute('select state,data from qq_topic_routes where event=?',(current.key,)).fetchone()
                assert route['state']=='committed' and not json.loads(route['data'])['unprocessed']
                assert db.execute('select count(*) from qq_topic_dialogue_links where unit=?',(ref['id'],)).fetchone()[0]==1
            s.command(msg('/events stop '+card['id']));assert not s.provenance.valid(current,tag)
    asyncio.run(run())


@pytest.mark.parametrize('change',['mode','subject','subspan','reassign'])
def test_direct_source_reselection_cannot_change_or_move_existing_evidence(tmp_path,change):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构灯塔的蓝光为什么被吸收？');choice[0]=discussion(first.text,'虚构灯塔');await s.observe(first,[])
            ref=d.prepare(first);card=s.cards(first)[0];selected=span(ref,first.text);target=card['id']
            if change=='mode':selected['mode']='experience'
            if change=='subject':selected['subject']='self'
            if change=='subspan':selected['quote']='蓝光为什么被吸收？'
            if change=='reassign':
                other=msg('虚构山谷为什么会产生回声？');choice[0]=discussion(other.text,'虚构山谷');await s.observe(other,[])
                target=next(c['id'] for c in s.cards(other) if c['id']!=card['id'])
            current=msg('虚构灯塔问题还有一个条件。')
            with pytest.raises(ValueError,match='already_bound'):
                await d.backfill(current,target,[selected],d.context(current))
            saved=next(c for c in s.cards(current) if c['id']==card['id'])
            assert saved==card
    asyncio.run(run())


def test_failed_prior_result_records_only_safe_reason_and_current_commit(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构灯塔的蓝光为什么被吸收？');choice[0]=discussion(first.text,'虚构灯塔');await s.observe(first,[])
            ref=d.prepare(first);current=msg('虚构灯塔的红光会怎样变化？')
            choice[0]=discussion(current.text,'虚构灯塔',sources=[span(ref,first.text,'experience')])
            await s.observe(current,[])
            with b.store.connect() as db:
                route=db.execute('select state,data from qq_topic_routes where event=?',(current.key,)).fetchone()
                body=json.loads(route['data'])
            assert route['state']=='partial' and body['unprocessed']
            assert any(x.get('reason')=='dialogue_prior_span_already_bound' and x['status']=='not_committed' for x in body['results'])
            assert any(x['target']=='personal' and x['status']=='committed' for x in body['results'])
            assert len(s.cards(current)[0]['facts'])==2
    asyncio.run(run())
