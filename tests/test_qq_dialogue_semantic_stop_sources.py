import asyncio

from test_qq_topic_routing import topics
from test_qq_topic_dialogue_sources import discussion
from test_qq_event_operations import deliver


async def semantic_stop_one(b,s,msg,identifier):
    request=msg('请停止对虚构风塔的跟踪。')
    plan={'action':'stop','scope':'personal','quantifier':'one','target_ids':[identifier],
          'intent_quote':request.text,'mode':'request','clarify':False}
    answer=s.operations.propose(request,plan,s.operations.inventory(request,[]),request.text)
    await deliver(b,s,request,answer)
    assert '已停止' in str(await s.operations.handle(msg('对的')))


def test_delivered_semantic_stop_allows_new_source_with_old_tags_still_invalid(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(first.text,'虚构风塔');await s.observe(first,[])
            old=s.cards(first)[0];tag=s.provenance.direct(first,{})
            await semantic_stop_one(b,s,msg,old['id'])
            fresh=msg(first.text);choice[0]=discussion(fresh.text,'虚构风塔');await s.observe(fresh,[])
            new=next((c for c in s.cards(fresh) if c['id']!=old['id']),None)
            assert new and len(new['facts'])==1 and new['facts'][0]['source']==fresh.key
            assert not s.provenance.valid(fresh,tag)
            assert s.provenance.valid(fresh,s.provenance.direct(fresh,{}))
    asyncio.run(run())


def test_latest_semantic_stop_prevents_older_pending_input_from_becoming_fresh(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构风塔的回声为什么会重叠？');choice[0]=discussion(first.text,'虚构风塔');await s.observe(first,[])
            old=s.cards(first)[0]
            s.command(msg('/events stop '+old['id']))
            s.command(msg('/events resume '+old['id']))
            pending=msg(first.text);ref=d.prepare(pending);assert ref
            await semantic_stop_one(b,s,msg,old['id'])
            assert d.valid_ref(pending,ref)  # single-card stop does not invent a link to this unfiled source
            choice[0]=discussion(pending.text,'虚构风塔');await s.observe(pending,[])
            assert len(s.people._canonical(first.user_id))==1
            assert s.people._canonical(first.user_id)[0]['tracking']=='suppressed'
    asyncio.run(run())
