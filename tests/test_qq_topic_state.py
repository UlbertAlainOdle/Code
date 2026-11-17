import asyncio
from dataclasses import replace
import json
from copy import deepcopy
import pytest
from test_qq_topic_routing import topics,item,create
from saya_app.services.qq_topic_state import QQTopicState,clauses,PROTECTED


def seed(s,msg,count=6):
    u=s.unified;mid=None
    for index in range(count):
        text='星桥读书会：'+'背景介绍主要为了说明讨论过程，'*20+f'我负责资料项目{index}，我只在周日下午有空。'
        m=msg(text,user=20000 if index%2==0 else 30000,group=90000)
        # First title is an actual quote; the explicit anchor is retained separately.
        if mid is None:mid=u.commit(m,'90000',{'title':'星桥读书会','kind':'commitment'})
        else:u.commit(m,'90000',{'event_id':mid,'kind':'commitment'},index)
    with s.store.connect() as db:
        row=db.execute('select data from qq_shared_events where id=?',(mid,)).fetchone();c=json.loads(row[0])
        c['topic_anchor']='星桥读书会';db.execute('update qq_shared_events set data=? where id=?',(json.dumps(c),mid))
    return msg('星桥读书会，目前分别是谁负责什么？时间定了吗？',group=90000)


def extracted(s,kind,card):
    return {'items':[{'sources':[r['id']],'category':'other','quote':clause}
        for r in s.topic_state.sources(kind,card) for clause in dict.fromkeys(clauses(r['text'])) if PROTECTED.search(clause)]}


def test_long_topic_is_compacted_once_with_all_authors_and_sources(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);card=s.unified.cards(q)[0];calls=[]
            async def decide(prompt,**kwargs):
                calls.append((prompt,kwargs));return extracted(s,'group',card)
            s.decide=decide
            await s.topic_state.prepare(q)
            await s.topic_state.prepare(replace(q))
            assert len(calls)==1 and calls[0][1]['purpose']=='qq_topic_state'
            assert len(calls[0][0])<=10000 and calls[0][1]['output_tokens']==2048
            context=s.unified.context(q)
            assert '结构化原话摘录' in context['text'] and '背景介绍主要为了说明讨论过程' not in context['text']
            assert '20000' in context['text'] and '30000' in context['text']
            assert len(context['refs'][0]['sources'])==6 and s.unified.valid(q,context)
            assert all(f'资料项目{i}' in context['text'] for i in range(6))
            assert s.unified.cards(q)[0]['revision']==card['revision']
            # Read-only details retain the complete original evidence.
            assert '背景介绍主要为了说明讨论过程' in str(s.unified.command(replace(q,text='/events group show '+card['id'])))
    asyncio.run(run())


@pytest.mark.parametrize('mutation',['invent','omit_source','omit_condition','mixed_authors','false_decision','preference_to_constraint','hypothesis'])
def test_state_cannot_invent_or_hide_conditions_or_promote_authority(mutation):
    sources=[{'id':'a','actor':'20000','mode':'commitment','kind':'commitment','text':'我更希望周六下午，我只在周日下午有空'},
        {'id':'b','actor':'30000','mode':'commitment','kind':'commitment','text':'我只在周日下午有空'}]
    value={'items':[{'sources':['a'],'category':'preference','quote':'我更希望周六下午'},
        {'sources':['a'],'category':'constraint','quote':'我只在周日下午有空'},
        {'sources':['b'],'category':'constraint','quote':'我只在周日下午有空'}]}
    if mutation=='invent':value['items'][0]['quote']='我更希望周日早上'
    if mutation=='omit_source':value['items'].pop()
    if mutation=='omit_condition':value['items'].pop(1)
    if mutation=='mixed_authors':value['items'][1]['sources'].append('b')
    if mutation=='false_decision':value['items'][0]['category']='decision'
    if mutation=='preference_to_constraint':value['items'][0]['category']='constraint'
    if mutation=='hypothesis':sources[0]['mode']='hypothesis'
    with pytest.raises(ValueError):QQTopicState.validate(value,sources)


def test_changed_expired_stopped_or_left_group_never_uses_old_state(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);u=s.unified;card=u.cards(q)[0]
            async def decide(*args,**kwargs):return extracted(s,'group',card)
            s.decide=decide;await s.topic_state.prepare(q)
            snapshot=u.context(q);assert '结构化原话摘录' in snapshot['text']
            u.commit(msg('我负责新的资料内容。',group=90000),'90000',{'event_id':card['id'],'kind':'commitment'},card['revision'])
            assert not s.topic_state.cached('group',card) and not u.valid(q,snapshot)
            assert '结构化原话摘录' not in u.context(q)['text']
            u.command(replace(q,text='/events group stop '+card['id']))
            assert not u.context(q)
            u.command(replace(q,text='/events group resume '+card['id']))
            u.notice({'self_id':10000,'notice_type':'group_decrease','group_id':90000,'user_id':20000})
            assert not u.context(q)
            u.mark_member('90000','20000');now[0]+=3601
            assert not s.topic_state.cached('group',card)
    asyncio.run(run())


def test_failure_is_bounded_and_never_reuses_stale_or_partial_state(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);calls=[]
            async def fail(*args,**kwargs):calls.append(1);raise ValueError('fixture')
            s.decide=fail
            await s.topic_state.prepare(q);await s.topic_state.prepare(replace(q))
            assert len(calls)==1 and not s.topic_state.cached('group',s.unified.cards(q)[0])
            assert s.unified.context(q) and '结构化原话摘录' not in s.unified.context(q)['text']
            with s.store.connect() as db:assert db.execute('select state from qq_topic_state_attempts').fetchone()[0]=='failed'
    asyncio.run(run())


def test_change_during_summary_cannot_commit_old_cache(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);u=s.unified;card=u.cards(q)[0]
            async def racing(*args,**kwargs):
                u.commit(msg('我已经退出本次分工。',group=90000),'90000',{'event_id':card['id'],'kind':'withdrawal'},card['revision'])
                return extracted(s,'group',card)
            s.decide=racing;await s.topic_state.prepare(q)
            with s.store.connect() as db:
                assert db.execute('select count(*) from qq_topic_state_cache').fetchone()[0]==0
                assert db.execute('select state from qq_topic_state_attempts').fetchone()[0]=='stale'
    asyncio.run(run())


def test_short_and_ordinary_messages_do_not_trigger_extra_model(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg,count=2)
            async def forbidden(*args,**kwargs):raise AssertionError('unexpected call')
            s.decide=forbidden
            await s.topic_state.prepare(q);await s.topic_state.prepare(msg('继续聊刚才那本书。',group=90000))
            with s.store.connect() as db:assert not db.execute('select 1 from qq_topic_state_attempts').fetchone()
    asyncio.run(run())


def test_personal_state_keeps_every_source_and_stops_with_personal_control(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            mid=None
            for i in range(6):
                text='星桥读书会：'+'背景介绍主要为了说明讨论过程，'*15+f'我负责资料项目{i}。'
                target=create() if mid is None else {'action':'update','event_id':mid,'supersedes':[]}
                choice[0]={'items':[item(text,personal=target,mode='commitment')]}
                m=msg(text);await s.observe(m,[]);mid=s.cards(m)[0]['id']
            q=msg('星桥读书会，目前分别是谁负责什么？时间定了吗？');card=s.cards(q)[0]
            async def decide(*args,**kwargs):return extracted(s,'personal',card)
            s.decide=decide;await s.topic_state.prepare(q)
            snapshot=s.context(q)
            assert 'structured_state' in snapshot['cards'][0] and len(snapshot['refs'][0]['sources'])==6
            assert all(f'资料项目{i}' in snapshot['cards'][0]['structured_state'] for i in range(6))
            assert s.valid(q,snapshot)
            s.command(msg('/events stop '+mid))
            assert not s.context(q) and not s.valid(q,snapshot)
    asyncio.run(run())


def test_new_small_change_is_dirty_until_batch_interval_and_daily_budget_is_hard(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);calls=[]
            async def decide(*args,**kwargs):
                calls.append(1);return extracted(s,'group',s.unified.cards(q)[0])
            s.decide=decide;await s.topic_state.prepare(q)
            card=s.unified.cards(q)[0]
            s.unified.commit(msg('我负责另一份资料。',group=90000),'90000',{'event_id':card['id'],'kind':'commitment'},card['revision'])
            await s.topic_state.prepare(replace(q))
            assert len(calls)==1 and not s.topic_state.cached('group',s.unified.cards(q)[0])
            now[0]+=301;await s.topic_state.prepare(replace(q));assert len(calls)==2
            with s.store.connect() as db:
                for i in range(6):db.execute('insert into qq_topic_state_attempts values(?,?,?,?,?,?,?,?,?)',
                    (s.unified.bot,'group','fixture'+str(i),'fingerprint','30000',now[0]-600,6,100,'failed'))
            card=s.unified.cards(q)[0]
            s.unified.commit(msg('我负责最后一份资料。',group=90000),'90000',{'event_id':card['id'],'kind':'commitment'},card['revision'])
            now[0]+=301;await s.topic_state.prepare(replace(q));assert len(calls)==2
    asyncio.run(run())


def test_summary_stage_usage_and_unknown_are_recorded_separately(tmp_path):
    from saya_app.services import api_usage
    api_usage.record_turn(tmp_path,'fixture',{'prompt_tokens':120,'completion_tokens':30,'total_tokens':150},stage='qq_topic_state',elapsed_ms=27)
    api_usage.record_turn(tmp_path,'fixture',None,False,stage='qq_topic_state')
    entry=next(iter(api_usage.load_ledger(tmp_path)['days'].values()))['fixture']['stages']['qq_topic_state']
    assert entry['requests']==2 and entry['total_tokens']==150 and entry['failed']==1
    assert entry['usage_unknown']==1 and entry['elapsed_ms']==27


def test_only_exact_topic_label_can_be_omitted_from_decision_and_premises():
    source={'id':'a','actor':'20000','mode':'experience','kind':'decision','text':'星桥读书会：最终确定周六下午举行。'}
    value={'items':[{'sources':['a'],'category':'decision','quote':'最终确定周六下午举行'}]}
    assert QQTopicState.validate(value,[source],'a','星桥读书会')==value['items']
    with pytest.raises(ValueError):QQTopicState.validate(value,[source],'a','另一个读书会')
    source.update(kind='proposal',text='如果有人没空，我们暂不定案')
    value={'items':[{'sources':['a'],'category':'proposal','quote':'我们暂不定案'}]}
    with pytest.raises(ValueError):QQTopicState.validate(value,[source])


def test_group_delete_also_removes_derived_clause_body(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            q=seed(s,msg);u=s.unified;card=u.cards(q)[0]
            u.command(replace(q,text='/events group coordinator '+card['id']+' 20000'));card=u.cards(q)[0]
            async def decide(*args,**kwargs):return extracted(s,'group',card)
            s.decide=decide;await s.topic_state.prepare(q)
            assert s.topic_state.cached('group',card)
            receipt=str(u.command(replace(q,text='/events group delete '+card['id'])))
            import re
            token=re.search(r'/events group confirm ([a-f0-9]{32})',receipt)[1]
            u.command(replace(q,text='/events group confirm '+token))
            with s.store.connect() as db:assert db.execute('select count(*) from qq_topic_state_cache').fetchone()[0]==0
    asyncio.run(run())


@pytest.mark.parametrize('stage,limit',[('qq_topic_route',1536),('qq_topic_state',2048)])
def test_minimal_provider_task_has_own_stage_and_output_bound(stage,limit):
    import httpx
    from types import SimpleNamespace
    from saya_app.services.qq_topic_model import complete
    async def run():
        payloads=[];recorded=[]
        def handler(req):
            payloads.append(json.loads(req.content))
            return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':'{"items":[]}'}}],
                'usage':{'prompt_tokens':10,'completion_tokens':3,'total_tokens':13}})
        async def record(*args,**kwargs):recorded.append(kwargs)
        req=SimpleNamespace(user_input='fixture data',api_url='https://fixture.invalid/v1',api_model_name='fixture',max_output_tokens=9999,api_key='fixture-key')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            response=await complete(req,client,record,stage=stage)
            async for _ in response.body_iterator:pass
        assert payloads[0]['max_tokens']==limit and payloads[0]['stream'] is False
        assert len(payloads[0]['messages'])==2 and 'tools' not in payloads[0]
        assert recorded[0]['stage']==stage and recorded[0]['completed'] is True
    asyncio.run(run())
