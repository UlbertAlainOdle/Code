import asyncio
import json
from dataclasses import replace
import pytest
from test_qq_topic_routing import topics,item,create
from test_qq_chat_runtime import server
from saya_app.services.qq_topic_dialogue import QQTopicDialogue


def test_prototype_is_disabled_and_requires_owner_activation(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);q=msg('宇宙边界能不能被观察到？')
            assert d.prepare(q) is None and not s.people._canonical(q.user_id)
            import pytest
            with pytest.raises(PermissionError):d.configure('30000',True)
            d.configure('20000',True);ref=d.prepare(q)
            assert ref and d.prepare(q)==ref and d.valid_ref(q,ref)
            assert not s.people._canonical(q.user_id)
    asyncio.run(run())


def test_pending_context_keeps_actor_scope_and_original_deadline(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);d.configure('20000',True)
            first=msg('宇宙边界是否意味着观察范围的极限？');ref=d.prepare(first)
            later=msg('那它和信息传播的速度有什么关系？');d.prepare(later)
            context=d.context(later)
            assert len(context)==1 and context[0]['text']==first.text and context[0]['author']==first.user_id
            assert not d.valid_ref(replace(first,user_id='30000'),ref)
            assert not d.valid_ref(replace(first,group_id='90000'),ref)
            now[0]+=1801
            assert d.context(msg('继续刚才的讨论。'))==[] and d.valid_ref(first,ref)
    asyncio.run(run())


def test_binding_and_single_stop_invalidate_only_related_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);d.configure('20000',True)
            q=msg('我想继续讨论一次虚构的阅读安排。');ref=d.prepare(q)
            choice[0]={'items':[item(q.text,personal=create())]};await s.observe(q,[])
            card=s.cards(q)[0];d.bind(q,card['id'],card['facts'][0]['evidence_id'])
            other=msg('另一个问题是海边的潮汐为什么变化。');other_ref=d.prepare(other)
            assert d.valid_ref(q,ref) and d.valid_ref(other,other_ref)
            s.command(msg('/events stop '+card['id']))
            assert not d.valid_ref(q,ref) and d.valid_ref(other,other_ref)
    asyncio.run(run())


def test_late_binding_uses_actual_earlier_text_author_and_time(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);d.configure('20000',True)
            first=msg('宇宙边界为什么难以观察？');ref=d.prepare(first)
            second=msg('我认为观察条件需要继续讨论。');d.prepare(second);context=d.context(second)
            choice[0]={'items':[item(second.text,personal=create())]};await s.observe(second,[])
            card=s.cards(second)[0];d.bind(second,card['id'],card['facts'][0]['evidence_id'])
            assert await d.backfill(second,card['id'],[ref['id']],context)==[ref['id']]
            facts=s.people._canonical(first.user_id)[0]['facts']
            old=next(f for f in facts if f['quote']==first.text)
            assert old['speaker']==first.user_id and old['at']==first.sent_at and old['mode']=='question' and old['subject']=='general'
            assert old['expires']==first.sent_at+s.retention(first)
            s.command(msg('/events stop '+card['id']))
            assert not d.valid_ref(first,ref)
    asyncio.run(run())


def test_forgetting_pending_units_blocks_old_sources_but_new_input_is_fresh(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);d.configure('20000',True)
            first=msg('讨论宇宙边界与记忆之间的比喻。');ref=d.prepare(first)
            assert d.pending(first)['count']==1
            with b.store.connect() as db:d.block_pending(db,first)
            assert not d.valid_ref(first,ref) and d.prepare(first) is None
            fresh=msg(first.text);new=d.prepare(fresh)
            assert new['id']!=ref['id'] and d.valid_ref(fresh,new)
    asyncio.run(run())


def test_commands_saves_and_greetings_never_enter_pending_discussion_index(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=QQTopicDialogue(s.topics);d.configure('20000',True)
            for text in ['/events self','你好','记住我喜欢蓝色','帮我把你刚刚阐述的那一层想法存一下','取消']:
                assert d.prepare(msg(text)) is None
            assert d.pending(msg('继续讨论'))['count']==0
    asyncio.run(run())


def discussion(text,anchor,*,mode='question',subject='general',sources=()):
    value=item(text,personal=create(),mode=mode,subject=subject)
    value.update(topic_anchor=anchor,context_sources=list(sources))
    return {'items':[value]}


def test_general_question_followup_and_viewpoint_share_one_owned_card(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构星雾边界为什么难以观察？')
            choice[0]=discussion(first.text,'虚构星雾边界');await s.observe(first,[])
            identifier=s.cards(first)[0]['id']
            read=msg('/events self');await s.observe(read,[])
            second=msg('那它和光的传播速度有什么关系？')
            choice[0]=discussion(second.text,'虚构星雾边界');await s.observe(second,[])
            third=msg('我觉得不能直接把观察极限当成真实边界。')
            choice[0]=discussion(third.text,'虚构星雾边界',mode='opinion',subject='self');await s.observe(third,[])
            cards=s.cards(third);assert len(cards)==1 and cards[0]['id']==identifier
            assert [(f['mode'],f['subject']) for f in cards[0]['facts']]==[('question','general'),('question','general'),('opinion','self')]
            assert all(f['speaker']==first.user_id for f in cards[0]['facts'])
            assert len(prompts)==3 and first.text in prompts[1]
            with b.store.connect() as db:assert db.execute('select count(*) from qq_topic_dialogue_units').fetchone()[0]==3
    asyncio.run(run())


def test_late_card_invalidates_early_raw_and_derived_replies_on_stop(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True);p=s.provenance
            first=msg('虚构星雾边界为什么难以观察？');choice[0]={'items':[]};await s.observe(first,[])
            raw=p.direct(first,{});derived=p.inherit(first,p.empty(first),[{'role':'ai','qq_event_dependencies':raw}])
            assert raw['version']==2 and p.valid(first,derived) and not s.cards(first)
            ref=raw['refs'][0]
            second=msg('那它会限制光信号吗？')
            choice[0]=discussion(second.text,'虚构星雾边界',sources=[ref['id']]);await s.observe(second,[])
            card=s.cards(second)[0]
            assert {f['quote'] for f in card['facts']}=={first.text,second.text}
            assert card['last_user']==second.sent_at and card['latest_source']==second.key
            early=next(f for f in card['facts'] if f['quote']==first.text)
            assert early['at']==first.sent_at and early['expires']==ref['expires']
            assert p.valid(second,raw) and p.valid(second,derived)
            s.command(msg('/events stop '+card['id']))
            assert not p.valid(second,raw) and not p.valid(second,derived)
            assert d.context(msg('我们接着聊另一个话题。'))==[]
    asyncio.run(run())


@pytest.mark.parametrize('new_source',[False,True])
def test_forget_all_freezes_and_blocks_pending_sources_even_without_a_card(tmp_path,new_source):
    from test_qq_event_operations import deliver
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构海湾潮汐会影响月光的倒影吗？');ref=d.prepare(first)
            request=msg('忘掉当前所有事件');answer=await s.operations.handle(request)
            assert '未归档讨论来源' in answer and '共0条' in answer
            await deliver(b,s,request,answer)
            if new_source:d.prepare(msg('虚构山谷回声为什么会重叠？'))
            result=await s.operations.handle(msg('对的'))
            if new_source:
                assert '变化' in result and d.valid_ref(first,ref)
            else:
                assert '同步停止1条未归档讨论来源' in result and not d.valid_ref(first,ref)
                with b.store.connect() as db:
                    audit=json.loads(db.execute('select data from qq_event_operation_results').fetchone()[0])
                    assert audit['pending_dialogue']['count']==1 and not audit['changes']
            assert not s.people._canonical(first.user_id)
    asyncio.run(run())


def test_mixed_message_source_requires_all_card_bindings_to_remain_valid(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            a='海湾潮汐为什么每天变化';z='山谷回声是否可能重叠'
            first=msg(a+'；'+z+'？')
            choice[0]={'items':[discussion(a,'海湾潮汐')['items'][0],discussion(z,'山谷回声')['items'][0]]}
            await s.observe(first,[]);ref=d.prepare(first);cards=s.cards(first)
            assert len(cards)==2 and d.valid_ref(first,ref)
            other=msg('另一个问题是沙漠地表为什么温差明显？');other_ref=d.prepare(other)
            s.command(msg('/events stop '+cards[0]['id']))
            assert not d.valid_ref(first,ref) and d.valid_ref(other,other_ref)
    asyncio.run(run())


def test_foreign_prior_source_rejected_before_any_card_write(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            q=msg('虚构海湾潮汐为什么会变化？')
            choice[0]=discussion(q.text,'虚构海湾潮汐',sources=['forged-foreign-source']);await s.observe(q,[])
            assert not s.people._canonical(q.user_id) and not getattr(q,'topic_results',[])
            with b.store.connect() as db:
                row=db.execute('select state,data from qq_topic_routes where event=?',(q.key,)).fetchone()
                assert row[0]=='failed_plan' and json.loads(row[1])['reason']=='invalid_dialogue_context_source'
    asyncio.run(run())


def test_backfill_capacity_failure_reports_current_commit_and_blocks_unbound_leak(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            seed=msg('虚构海湾潮汐为什么会变化？');choice[0]=discussion(seed.text,'虚构海湾潮汐');await s.observe(seed,[])
            card=s.cards(seed)[0]
            with b.store.connect() as db:
                row=db.execute('select data from qq_episode_cards where id=?',(card['id'],)).fetchone();body=json.loads(row[0]);f=body['facts'][0]
                body['facts'] += [{**f,'quote':'独立有效证据'+str(i),'source':'capacity:'+str(i),'evidence_id':'capacity:'+str(i)} for i in range(30)]
                db.execute('update qq_episode_cards set data=?,revision=revision+1 where id=?',(json.dumps(body),card['id']))
            prior=msg('讨论中的潮汐现象是否依赖引力？');ref=d.prepare(prior)
            current=msg('我认为需要区分观测结果与解释。')
            choice[0]=discussion(current.text,'虚构海湾潮汐',mode='opinion',subject='self',sources=[ref['id']]);await s.observe(current,[])
            updated=s.people._canonical(current.user_id)[0]
            assert len(updated['facts'])==32 and any(f['quote']==current.text for f in updated['facts'])
            assert any(x['status']=='committed' for x in current.topic_results)
            assert any(x['target']=='personal_prior' and x['status']=='not_committed' for x in current.topic_results)
            assert current.topic_coverage and not d.valid_ref(prior,ref)
            s.command(msg('/events stop '+card['id']));assert not d.valid_ref(prior,ref)
    asyncio.run(run())


def test_pending_forget_all_cross_scope_still_works_after_capture_disabled(tmp_path):
    from test_qq_event_operations import deliver
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True)
            first=msg('虚构海湾潮汐的讨论还没有形成卡片。');ref=d.prepare(first)
            d.configure('20000',False)
            request=msg('忘掉所有个人事件',group=90000)
            plan={'action':'stop','scope':'personal','quantifier':'all','target_ids':[],'intent_quote':request.text,'mode':'request','clarify':False}
            answer=s.operations.propose(request,plan,s.operations.inventory(request,[]),request.text)
            assert '未归档讨论来源' in answer
            await deliver(b,s,request,answer)
            assert '同步停止1条' in str(await s.operations.handle(msg('对的',group=90000)))
            assert not d.valid_ref(first,ref)
    asyncio.run(run())


def test_old_reader_blocked_for_enabled_or_retained_dialogue_sources(tmp_path):
    import sqlite3
    from saya_app.services.qq_rollback import check_memory_rollback
    root=tmp_path/'rollback';p=root/'knowledge_base/qq';p.mkdir(parents=True)
    with sqlite3.connect(p/'bridge.sqlite3') as db:
        db.execute('create table qq_topic_dialogue_schema(bot text primary key,version integer,enabled integer)')
        db.execute("insert into qq_topic_dialogue_schema values('10000',1,1)")
    gate=check_memory_rollback(root,{})
    assert 'qq_topic_dialogue_v1' in gate['missing']
    assert check_memory_rollback(root,{'memory_compatibility':gate['required']})['status']=='compatible_declared'
    with sqlite3.connect(p/'bridge.sqlite3') as db:
        db.execute('update qq_topic_dialogue_schema set enabled=0')
        db.execute('create table qq_topic_dialogue_units(id text)');db.execute("insert into qq_topic_dialogue_units values('retained')")
    assert 'qq_topic_dialogue_v1' in check_memory_rollback(root,{})['missing']


def test_actual_responder_tracks_unfiled_source_before_late_binding(server,tmp_path):
    from fastapi.responses import StreamingResponse
    from saya_app.services.qq_chat import QQChatResponder
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            d=s.topics.dialogue;d.configure('20000',True);calls=[]
            async def generate(req,channel_turn):
                channel=channel_turn
                assert channel['qq_event_dependencies']['version']==2
                assert any(x['kind']=='dialogue' for x in channel['qq_event_dependencies']['refs'])
                calls.append(channel)
                async def stream():yield 'event: answer_complete\ndata: {"text":"观测边界取决于光信号能否到达。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{
                'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            responder.episodes=s;responder.context_salience=b.context_salience
            first=msg('虚构星雾边界为什么难以观察？');choice[0]={'items':[]}
            answer=await responder(first,[],'session_qq_r2_unfiled.jsonl')
            tag=first.event_provenance_response
            assert '观测边界' in str(answer) and len(calls)==1 and s.provenance.valid(first,tag)
            ref=next(x for x in tag['refs'] if x['kind']=='dialogue')
            second=msg('那它会限制光信号吗？');choice[0]=discussion(second.text,'虚构星雾边界',sources=[ref['id']]);await s.observe(second,[])
            card=s.cards(second)[0];assert len(card['facts'])==2
            s.command(msg('/events stop '+card['id']))
            assert not s.provenance.valid(second,tag)
    asyncio.run(run())
