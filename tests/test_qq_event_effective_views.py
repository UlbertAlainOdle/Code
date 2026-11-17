import asyncio
from dataclasses import replace
import re
import pytest
from test_qq_topic_routing import topics,item,create


@pytest.mark.parametrize('action',['stop','pause','mute','delete'])
def test_personal_control_filters_own_shared_sources_and_pending_answer(tmp_path,action):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('河岸读书讨论：我负责紫鹭清单。',group=90000)
            choice[0]={'items':[item('我负责紫鹭清单。',personal=create(),group=create(),mode='commitment')]}
            await s.observe(m,[]);p=s.cards(m)[0];g=s.unified.cards(m)[0]
            before=s.unified.context(m);assert before
            other=msg('河岸读书讨论：目前谁负责清单？',user=30000,group=90000)
            c=msg('/events '+action+' '+p['id'],group=90000 if action=='mute' else None)
            receipt=s.command(c)
            if action=='delete':
                token=re.search(r'/events confirm ([a-f0-9]{32})',str(receipt))[1]
                assert '已删除' in str(s.command(msg('/events confirm '+token)))
            assert not s.unified.context(replace(m))
            assert not s.unified.valid(m,before)
            assert '紫鹭清单' in s.unified.context(other)['text']
            assert len(s.unified.cards(other))==1
            if action in {'stop','pause'}:
                assert '已恢复' in str(s.command(msg('/events resume '+p['id'])))
                assert s.unified.context(replace(m))
    asyncio.run(run())


def test_hidden_title_and_decision_do_not_leak_via_other_author(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('紫鹭清单是我的私人分工',group=90000)
            choice[0]={'items':[item(m.text,personal=create(),group=create())]};await s.observe(m,[])
            p=s.cards(m)[0];g=s.unified.cards(m)[0]
            other=msg('我只想讨论另一种阅读体验',user=30000,group=90000)
            s.unified.commit(other,'90000',{'event_id':g['id'],'kind':'proposal'},g['revision'])
            s.command(msg('/events stop '+p['id']))
            view=s.unified.cards(m)[0]
            assert '紫鹭' not in s.unified.summary(view)
            assert '另一种阅读体验' in s.unified.summary(view)
    asyncio.run(run())


def test_valid_old_body_evidence_is_retrieved_and_presented_to_router(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            m=msg('晨光资料议题：我负责紫鹭阀门清单。',group=90000)
            choice[0]={'items':[item('我负责紫鹭阀门清单。',group=create(),mode='commitment')]};await s.observe(m,[])
            g=s.unified.cards(m)[0]
            for i in range(5):
                text='普通资料进展'+str(i)
                choice[0]={'items':[item(text,group={'action':'update','event_id':g['id'],'supersedes':[]},mode='progress')]}
                await s.observe(msg('晨光资料议题：'+text,group=90000),[])
            now[0]+=3700;q=msg('紫鹭阀门怎么处理',group=90000)
            assert '紫鹭阀门' in s.unified.context(q)['text']
            assert '紫鹭阀门' in str(s.topics._candidates(q)[3])
    asyncio.run(run())


def test_preview_survives_cooling_but_not_coordinator_or_source_change(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            m=msg('晨光资料议题：普通资料讨论。',group=90000)
            choice[0]={'items':[item('普通资料讨论。',group=create())]};await s.observe(m,[])
            g=s.unified.cards(m)[0]
            s.unified.command(msg('/events group coordinator '+g['id']+' 20000',group=90000))
            now[0]+=2500
            r=s.unified.command(msg('/events group delete '+g['id'],group=90000))
            token=re.search(r'/events group confirm ([a-f0-9]{32})',str(r))[1]
            now[0]+=250;s.unified.prune(force=True)
            assert '已按确认删除' in str(s.unified.command(msg('/events group confirm '+token,group=90000)))
    asyncio.run(run())


def test_natural_name_designation_suffix_normalizes_without_fuzzy_authority(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥资料截止周五。');choice[0]={'items':[item(m.text,personal=create())]};await s.observe(m,[])
            assert '没有修改' in str(s.people.controls.handle(msg('忘记资料的事件')))
            assert '已停止' in str(s.people.controls.handle(msg('忘记星桥资料的事件')))
    asyncio.run(run())


def test_personal_source_natural_expiry_does_not_stop_longer_lived_public_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            s.unified.lifecycle.configure('20000',0,{'enabled':True})
            m=msg('晨光资料议题：我负责紫鹭清单。',group=90000)
            choice[0]={'items':[item('我负责紫鹭清单。',personal=create(),group=create(),mode='commitment')]};await s.observe(m,[])
            now[0]+=3700;s.people.cards(m)
            assert '紫鹭' in s.unified.context(replace(m,text='紫鹭清单怎么处理'))['text']
    asyncio.run(run())


@pytest.mark.parametrize('group_only',[False,True])
def test_effective_source_guards_raw_history_for_requester_only(tmp_path,group_only):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('河岸读书讨论：我负责紫鹭清单。',group=90000)
            choice[0]={'items':[item('我负责紫鹭清单。',personal=None if group_only else create(),group=create(),mode='commitment')]}
            await s.observe(m,[])
            if group_only:s.unified.command(msg('/events group stop '+s.unified.cards(m)[0]['id'],group=90000))
            else:s.command(msg('/events stop '+s.people._canonical('20000')[0]['id']))
            rows=[{'role':'user','speaker_id':'20000','text':m.text},{'role':'assistant','text':'你的紫鹭清单可以稍后处理'}]
            own=s.people.history_projection(m,['20000'])
            assert not s.people.filter_history(m,[],rows,own)[1]
            other=msg('清单呢',user=30000,group=90000)
            view=s.people.history_projection(other,['20000'])
            assert len(s.people.filter_history(other,[],rows,view)[1])==2
    asyncio.run(run())


def test_personal_old_long_body_match_survives_recent_sources_and_excerpt_limit(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            original='普通说明'*300+'紫鹭阀门由我处理。'
            m=msg('晨光资料议题：'+original)
            choice[0]={'items':[item(original,personal=create(),mode='commitment')]};await s.observe(m,[])
            p=s.cards(m)[0]
            for i in range(5):
                text='本次其他进展'+str(i)+'。'+'其余说明'*30
                choice[0]={'items':[item(text,personal={'action':'update','event_id':p['id'],'supersedes':[]},mode='progress')]}
                await s.observe(msg('晨光资料议题：'+text),[])
            snap=s.context(msg('紫鹭阀门怎么处理'))
            assert any(f['quote']==original for f in snap['cards'][0]['facts'])
            assert snap['cards'][0]['partial_evidence'] and s.valid(msg('紫鹭阀门怎么处理'),snap)
    asyncio.run(run())


def test_deleted_group_only_source_is_not_replayed_as_raw_history(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('河岸读书讨论：我负责紫鹭清单。',group=90000)
            choice[0]={'items':[item('我负责紫鹭清单。',group=create(),mode='commitment')]};await s.observe(m,[])
            g=s.unified.cards(m)[0]
            s.unified.command(msg('/events group coordinator '+g['id']+' 20000',group=90000))
            preview=s.unified.command(msg('/events group delete '+g['id'],group=90000))
            token=re.search(r'/events group confirm ([a-f0-9]{32})',str(preview))[1]
            s.unified.command(msg('/events group confirm '+token,group=90000))
            for user in (20000,30000):
                q=msg('目前安排呢',user=user,group=90000)
                view=s.people.history_projection(q,['20000'])
                assert not s.people.filter_history(q,[],[{'role':'user','speaker_id':'20000','text':m.text}],view)[1]
    asyncio.run(run())
