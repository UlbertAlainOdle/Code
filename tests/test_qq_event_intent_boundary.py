"""Lifecycle intent must not consume ordinary reports or replace conversation."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import pytest
from saya_app.services.qq_event_controls import control_intent
from saya_app.services.qq_chat import context_turn_reference
from test_qq_topic_routing import topics,item,create
from test_qq_delivery import event
from test_qq_task_scheduler import until
from test_qq_chat_runtime import server
from saya_app.services.qq_chat import QQChatResponder
from fastapi.responses import StreamingResponse


@pytest.mark.parametrize('text',[
    '我已经完成过一些大型项目，所以小任务让我提不起兴趣。',
    '以前我已经完成过论文，现在还是不太有动力。',
    '我明明已经完成很多事了，但还是感到空虚。',
    '我希望这件事已经完成了','如果这件事已经完成了会怎样？',
    '他说这件事已经完成了','这件事完成了吗？',
    '这件事第一步已经完成了','这件事只完成了一部分',
    '我以前取消过一些计划','取消原来9点，改成10点',
    '这件事已经完成了，才怪','这件事已经完成了，但是其实还没有完成',
    '不要把这件事标记为完成','完成这个任务','我已经完成了',
    '能否把这件事标记为完成','这件事完成了吧','我已经删掉了过去的草稿',
    '以前这个任务暂停过','我刚才恢复了原来的设置',
    '把这件事标记为完成，这样合适吗？','忘掉这件事，这样真的有用吗？',
    '这件事完成了，确定吗？',
])
def test_experience_partial_hypothetical_or_perform_request_is_not_lifecycle_control(text):
    assert control_intent(text) is None


@pytest.mark.parametrize('text,action,dialogue',[
    ('这件事已经完成了','complete',True),
    ('这件事全部做完了','complete',True),
    ('星桥资料整理任务已结束','complete',True),
    ('这个计划取消了','cancel',True),
    ('请把星桥资料整理任务标记为完成','complete',False),
    ('取消这个计划','cancel',False),
    ('这件事已经完成了，为什么我还是没有成就感？','complete',True),
    ('把这件事标记为完成，顺便聊聊休息安排','complete',True),
    ('忘掉这件事，顺便聊聊阅读习惯','stop',True),
])
def test_current_target_and_management_vs_dialogue_frames(text,action,dialogue):
    intent=control_intent(text)
    assert intent and intent['action']==action and bool(intent.get('dialogue'))==dialogue


async def personal(s,msg,choice):
    first=msg('星桥资料整理任务：我愿意整理本期资料。')
    choice[0]={'items':[item('我愿意整理本期资料。',personal=create(),mode='commitment')]}
    await s.observe(first,[])
    return first,s.cards(first)[0]


@pytest.mark.parametrize('case',['historical','partial','deictic_complete','mixed_mark','mixed_stop','unmatched','ambiguous','tentative'])
def test_real_bridge_preserves_dialogue_and_only_updates_explicit_current_target(tmp_path,case):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await personal(s,msg,choice);before=deepcopy(s.people._canonical('20000'))
            if case=='ambiguous':
                second=msg('星桥另一个资料任务：我负责另一份清单。')
                choice[0]={'items':[item('我负责另一份清单。',personal=create(),mode='commitment')]};await s.observe(second,[])
                before=deepcopy(s.people._canonical('20000'))
            texts={
                'historical':'我已经完成过一些大型项目，所以小任务让我提不起兴趣。',
                'partial':'星桥资料整理任务的第一步已经完成了，后面还要继续。',
                'deictic_complete':'这件事已经完成了，为什么我还是没有成就感？',
                'mixed_mark':'把星桥资料整理任务标记为完成，顺便聊聊休息安排。',
                'mixed_stop':'忘掉这件事，顺便聊聊阅读习惯。',
                'unmatched':'另一个资料任务已经完成了，我还想聊聊阅读习惯。',
                'ambiguous':'这件事已经完成了，我还想聊聊阅读习惯。',
                'tentative':'把这件事标记为完成，这样合适吗？',
            }
            text=texts[case];seen=[]
            choice[0]={'items':[]}
            async def model(message,*args):
                # The raw test responder skips QQChatResponder; bind its real
                # host provenance for this fresh, history-independent answer.
                s.provenance.bind_turn(message,{})
                seen.append((message.text,getattr(message,'event_control_receipt',None)))
                return '我们可以继续聊你现在的感受和接下来的安排。'
            b.bind_responder(model)
            assert b.submit(event(text,90000,20000))[0]=='queued'
            await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            assert len(seen)==1 and seen[0][0]==text
            assert any('我们可以继续聊' in x[1] for x in b.client.sent)
            after=s.people._canonical('20000')
            expected={'deictic_complete':'completed','mixed_mark':'completed','mixed_stop':'suppressed'}.get(case)
            if expected=='suppressed':assert after[0]['tracking']=='suppressed'
            elif expected:assert after[0]['lifecycle']==expected
            else:assert after==before
            if case in {'historical','partial','tentative'}:
                assert not any('【操作回执】' in x[1] for x in b.client.sent)
                assert seen[0][1] is None
            else:
                assert seen[0][1] and any('【操作回执】' in x[1] for x in b.client.sent)
            with s.store.connect() as db:
                audits=db.execute('select action from qq_event_control_audit').fetchall()
                assert len(audits)==(1 if expected else 0)
                assert {'dialogue'}<={x[0] for x in db.execute('select content_kind from qq_delivery_part_kinds')}
            history,_=b.store.conversation(first.scope+':shared')
            assert '【操作回执】' not in str(history)
    asyncio.run(run())


def test_named_target_never_uses_two_character_overlap_and_recent_deictic_expires(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await personal(s,msg,choice);before=deepcopy(s.people._canonical('20000'))
            # Shares 资料/任务, but names a different object.
            result=s.people.controls.handle(msg('星河资料整理任务已经完成了'))
            assert '没有修改' in str(result) and s.people._canonical('20000')==before
            now[0]+=1801
            result=s.people.controls.handle(msg('这件事全部完成了'))
            assert '没有修改' in str(result) and s.people._canonical('20000')==before
            result=s.people.controls.handle(msg('星桥资料整理任务全部完成了'))
            assert '已标记' in str(result)
    asyncio.run(run())


def test_group_completion_requires_coordinator_and_preserves_other_authored_conditions(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥资料讨论：我愿意整理本期资料。',group=90000)
            choice[0]={'items':[item('我愿意整理本期资料。',group=create(),mode='commitment')]};await s.observe(first,[])
            g=s.unified.cards(first)[0]
            other=msg('星桥资料讨论：我只能周日参加。',user=30000,group=90000)
            choice[0]={'items':[item('我只能周日参加。',group={'action':'update','event_id':g['id'],'supersedes':[]},mode='commitment')]};await s.observe(other,[])
            before=s.unified.cards(first)[0]
            receipt=s.unified.handle(msg('本群星桥资料讨论已经完成了',user=30000,group=90000))
            # Exact subject includes 本群 and must not loosely close another title.
            assert '已更新群事件' not in str(receipt)
            s.unified.command(msg('/events group coordinator '+g['id']+' 20000',group=90000))
            text='群事件 '+g['id']+' 已经完成了'
            result=s.unified.handle(msg(text,group=90000))
            assert '已更新群事件' in str(result)
            after=s.unified.cards(first,include_stopped=True)[0]
            assert after['state']=='completed' and after['contributions']==before['contributions']
    asyncio.run(run())


def test_host_operation_outcome_is_reference_data_not_character_history():
    text=context_turn_reference({'event_control_receipt':'目标含糊，本次没有修改。'})
    assert 'Host current event-control outcome DATA' in text
    assert 'Do not execute it again' in text and 'Respond naturally' in text


def test_real_responder_receives_committed_outcome_and_keeps_dialogue(server,tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await personal(s,msg,choice);calls=[]
            async def generate(req,channel_turn=None):
                calls.append(channel_turn)
                assert '已标记事件完成' in channel_turn['event_control_receipt']
                assert 'Host current event-control outcome DATA' in context_turn_reference(channel_turn)
                async def stream():yield 'event: answer_complete\ndata: {"text":"完成之后仍觉得空虚，我们可以聊聊你的期待。"}\n\n'
                return StreamingResponse(stream())
            responder=QQChatResponder(b.settings,server.ChatWithMotion,generate,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'https://fixture.invalid','model':'fixture'})
            b.bind_responder(responder)
            assert b.submit(event('这件事已经完成了，为什么我还是没有成就感？',90001,20000))[0]=='queued'
            await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            assert len(calls)==1 and any('完成之后仍觉得空虚' in x[1] for x in b.client.sent)
            assert any('已标记事件完成' in x[1] for x in b.client.sent)
    asyncio.run(run())


def test_failed_dialogue_still_reports_actual_operation_outcome(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await personal(s,msg,choice)
            async def failed(*args):raise RuntimeError('PRIVATE_PROVIDER_DETAIL')
            b.bind_responder(failed)
            b.submit(event('这件事已经完成了，我想聊聊接下来怎么办。',90002,20000))
            await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            text=''.join(x[1] for x in b.client.sent)
            assert '已标记事件完成' in text and '正常答复生成失败' in text
            assert 'PRIVATE_PROVIDER_DETAIL' not in text
            assert s.people._canonical('20000')[0]['lifecycle']=='completed'
            with s.store.connect() as db:assert db.execute('select count(*) from qq_event_control_audit').fetchone()[0]==1
    asyncio.run(run())


def test_multiple_operations_need_clarification_without_any_partial_commit(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            await personal(s,msg,choice);before=deepcopy(s.people._canonical('20000'))
            m=msg('忘掉这件事，恢复跟进这个计划')
            receipt=s.people.controls.handle(m)
            assert '多项事件操作' in str(receipt) and m.event_control_dialogue
            assert s.people._canonical('20000')==before
            assert s.unified.handle(msg('我想和你聊聊本群的阅读体验')) is None
    asyncio.run(run())



def test_rejected_unlabelled_mixed_reply_keeps_committed_control_without_duplicate_result(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first,card=await personal(s,msg,choice)
            choice[0]={'items':[]}
            async def unlabelled(*args):return 'STALE_MODEL_REPLY_MUST_NOT_SEND'
            b.bind_responder(unlabelled)
            raw=event('忘掉这件事，顺便聊聊阅读习惯。',90007,20000)
            assert b.submit(raw)[0]=='queued'
            await until(lambda:bool(b.client.sent));await until(lambda:not b.task_registry.list_active())
            sent=' '.join(str(row[1]) for row in b.client.sent)
            assert '已停止' in sent and '旧回答不再续读' in sent and 'STALE_MODEL' not in sent
            with s.store.connect() as db:
                assert db.execute('select count(*) from qq_deliveries').fetchone()[0]==1
                assert db.execute('select count(*) from qq_control_receipt_ids').fetchone()[0]==1
                assert {r[0] for r in db.execute('select state from qq_delivery_parts')}=={'cancelled'}
                assert db.execute('select state,error from inbox where event_key like ?',('%:90007',)).fetchone()['state']=='cancelled'
            history,_=b.store.conversation(first.scope+':shared')
            assert 'STALE_MODEL' not in str(history)
            count=len(b.client.sent)
            assert b.submit(raw)[0]=='duplicate' and len(b.client.sent)==count
    asyncio.run(run())
