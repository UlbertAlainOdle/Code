import asyncio
import json

import pytest

from core_services import KnowledgeBase
from saya_app.services import qq_delivery
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_group_context import GroupPolicy, QQGroupContext
from test_qq_event_operations import deliver, seed
from test_qq_topic_routing import topics


def semantic_stop(card):
    return {
        'action': 'stop', 'scope': 'personal', 'quantifier': 'one',
        'target_ids': [card['id']], 'intent_quote': '停止跟进',
        'mode': 'request', 'clarify': False,
    }


def private_history(b, s, request, now):
    scope = s.operations.scope(request)
    _, session = b.store.conversation(scope)
    line = 'Doctor: 刚才提到的材料是晨光资料。'
    b.store.save_conversation(scope, [line], session)
    tag = s.provenance.empty(request)
    tag['legacy'] = [{'principal': 'person:20000', 'revision': 0, 'expires': now + 65}]
    b.context_salience.record(scope, session, [line], [now], b.context_salience.snapshot(scope),
                              user_dependencies=tag)
    return line


@pytest.mark.parametrize('stage', ['model', 'send', 'receipt', 'confirm', 'unchanged'])
def test_private_semantic_plan_rechecks_the_same_expiring_dialogue(tmp_path, stage):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            cards = await seed(s, msg, choice, count=1)
            request = msg('请停止跟进刚才那件事')
            line = private_history(b, s, request, now[0])
            choice[0] = semantic_stop(cards[0])
            if stage == 'model':
                async def expire_during_model(prompt, **kwargs):
                    assert line in prompt
                    now[0] += 66
                    return choice[0]
                s.decide = expire_during_model
            answer = await s.operations.handle(request, semantic_trigger=True)
            if stage == 'model':
                assert '没有修改' in answer
                assert not getattr(request, 'event_operation_proposal', None)
            else:
                assert '待确认' in answer and line in prompts[-1]
                scope = s.operations.scope(request)
                if stage == 'send':
                    result = qq_delivery.create(b.store, request, scope, answer, 12000, kind='control')
                    now[0] += 66
                    continuation = msg('/continue')
                    assert not s.operations.can_deliver(request)
                    assert not s.operations.can_deliver(continuation, result)
                    await qq_delivery.send_batch(b.store, b.client, continuation, scope, result, 30, False,
                                                 memory_check=lambda: s.operations.can_deliver(continuation, result))
                    with b.store.connect() as db:
                        assert not db.execute("select 1 from qq_delivery_parts where result_id=? and state='sent'",
                                              (result,)).fetchone()
                elif stage == 'receipt':
                    result = qq_delivery.create(b.store, request, scope, answer, 12000, kind='control')
                    await qq_delivery.send_batch(b.store, b.client, request, scope, result, 30, False)
                    now[0] += 66
                    s.operations.delivered(request, result)
                    with b.store.connect() as db:
                        assert db.execute('select state from qq_event_operation_sessions').fetchone()[0] == 'stale'
                else:
                    await deliver(b, s, request, answer)
                    if stage == 'confirm':
                        now[0] += 4
                    else:
                        # Harmless appends do not revoke the exact earlier row.
                        history, session = b.store.conversation(scope)
                        b.store.save_conversation(scope, [*history, 'Doctor: 多谢提醒。'], session)
                    receipt = await s.operations.handle(msg('对的'))
                    assert ('已停止' if stage == 'unchanged' else '没有修改') in receipt
            if stage == 'unchanged':
                assert s.people._canonical('20000')[0]['tracking'] == 'suppressed'
            else:
                assert s.people._canonical('20000') == cards
            with b.store.connect() as db:
                assert db.execute('select count(*) from qq_event_operation_results').fetchone()[0] == (stage == 'unchanged')
    asyncio.run(run())


@pytest.mark.parametrize('change', ['retention', 'body', 'requirement', 'same'])
def test_group_preview_rechecks_the_actual_recent_turn(tmp_path, change):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            card = (await seed(s, msg, choice, count=1))[0]
            context = QQGroupContext(b, KnowledgeBase(str(tmp_path / 'group-kb')), clock=lambda: now[0])
            b.group_context = context
            policy = context.configure('20000', '90000', GroupPolicy(context_enabled=True).model_dump(),
                                       0, confirmed=True)
            request = msg('请停止跟进刚才那件事', group=90000)
            text = '刚才说的材料来自群聊'
            with b.store.connect() as db:
                requirements = []
                if change == 'requirement':
                    db.execute('insert into qq_group_facts values(?,?,?,?,?,?,?,?,?)',
                               ('proof', '10000', '90000', '30000', text, 'test', now[0], now[0]+64, 1))
                    fact = db.execute('select * from qq_group_facts where id=?', ('proof',)).fetchone()
                    requirements.append({'kind': 'group', 'id': 'proof', 'version': record_version(dict(fact))})
                age = policy['values']['retention_seconds']-64 if change == 'retention' else 1
                db.execute('insert into qq_group_turns(bot,grp,topic,speaker,label,role,text,at,requirements) '
                           'values(?,?,?,?,?,?,?,?,?)', ('10000', '90000', policy['topic'], '30000', '乙',
                                                        'user', text, now[0]-age, json.dumps(requirements)))
            choice[0] = semantic_stop(card)
            answer = await s.operations.handle(request, semantic_trigger=True)
            assert '待确认' in answer and text in prompts[-1]
            await deliver(b, s, request, answer)
            if change in {'retention', 'requirement'}:
                now[0] += 4
            if change == 'body':
                with b.store.connect() as db:
                    db.execute('update qq_group_turns set text=?', ('原群记录已变化',))
            receipt = await s.operations.handle(msg('对的', group=90000))
            assert ('已停止' if change == 'same' else '没有修改') in receipt
            if change != 'same':
                assert s.people._canonical('20000') == [card]
    asyncio.run(run())


@pytest.mark.parametrize('legacy', ['bulk', 'semantic'])
def test_legacy_delivered_plan_only_keeps_reconstructible_bulk_consent(tmp_path, legacy):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            card = (await seed(s, msg, choice, count=1))[0]
            request = msg('忘掉当前所有事件' if legacy == 'bulk' else '请停止跟进刚才那件事')
            choice[0] = semantic_stop(card)
            answer = await s.operations.handle(request, semantic_trigger=legacy == 'semantic')
            await deliver(b, s, request, answer)
            with b.store.connect() as db:
                data = json.loads(db.execute('select data from qq_event_operation_sessions').fetchone()[0])
                data['version'] = 1
                data.pop('dialogue')
                db.execute('update qq_event_operation_sessions set data=?', (json.dumps(data),))
            receipt = await s.operations.handle(msg('对的'))
            assert ('已停止' if legacy == 'bulk' else '没有修改') in receipt
    asyncio.run(run())
