import asyncio
from dataclasses import replace
import pytest
from test_qq_topic_routing import topics,item,create


def test_repeated_announcement_after_designation_uses_new_authorized_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            u=s.unified
            first=msg('星桥读书会：最终确定周六下午举行。',group=90000)
            mid=u.commit(first,'90000',{'title':'星桥读书会','kind':'decision'})
            original=u.cards(first)[0]['contributions'][0].copy()
            assert original['kind']=='proposal'
            other=msg('星桥读书会：我只在周日下午有空，愿意准备提纲。',user=30000,group=90000)
            u.commit(other,'90000',{'event_id':mid,'kind':'commitment'},1)
            condition=u.cards(other)[0]['contributions'][-1].copy()
            u.command(replace(first,text='/events group coordinator '+mid+' 20000'))
            # Repeating identical words is a fresh confirmation, not permission
            # to promote the earlier, unauthorized source in place.
            confirm=msg(first.text,group=90000)
            u.commit(confirm,'90000',{'event_id':mid,'kind':'decision'},3)
            card=u.cards(confirm)[0]
            assert card['revision']==4 and len(card['contributions'])==3
            assert card['contributions'][0]==original and card['contributions'][1]==condition
            decision=card['contributions'][-1]
            assert decision['kind']=='decision' and decision['source']==confirm.key!=original['source']
            assert card['current_decision']==decision['id']
            assert '指定协调人：' in u.summary(card) and '已确认安排：'+confirm.text in u.summary(card)
            # A true repeated confirmation of the current decision stays idempotent.
            u.commit(msg(first.text,group=90000),'90000',{'event_id':mid,'kind':'decision'},4)
            assert u.cards(confirm)[0]['revision']==4
            # A historical decision with the same words cannot suppress a new
            # confirmation that intentionally returns to an earlier arrangement.
            u.commit(msg('星桥读书会：最终确定周日下午举行。',group=90000),'90000',{'event_id':mid,'kind':'decision'},4)
            u.commit(msg(first.text,group=90000),'90000',{'event_id':mid,'kind':'decision'},5)
            final=u.cards(confirm)[0]
            assert final['revision']==6 and final['current_decision']!=decision['id']
            assert final['contributions'][1]==condition
    asyncio.run(run())


@pytest.mark.parametrize('text',[
    '星桥读书会，时间是否已定案？还有谁的条件没满足？',
    '星桥读书会，时间定案了吗？',
    '星桥读书会，目前分别是谁负责什么？时间定了吗？',
])
def test_named_decision_and_unmet_condition_questions_are_read_only(tmp_path,text):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥读书会：我负责整理书单。',group=90000)
            choice[0]={'items':[item('我负责整理书单。',group=create(),mode='commitment')]}
            await s.observe(first,[])
            before=s.unified.cards(first)
            prompts.clear();query=msg(text,group=90000)
            await s.observe(query,[])
            assert not prompts and s.unified.cards(query)==before
            assert s.topics.named_focus(query)=='星桥读书会'
    asyncio.run(run())


def test_additional_topic_does_not_become_single_topic_lookup(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥读书会：我负责整理书单。',group=90000)
            choice[0]={'items':[item('我负责整理书单。',group=create(),mode='commitment')]}
            await s.observe(first,[])
            assert s.topics.named_focus(msg('星桥读书会，时间是否已定案？还有云帆读书会呢？',group=90000))==''
    asyncio.run(run())
