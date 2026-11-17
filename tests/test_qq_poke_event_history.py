import asyncio
import json

import pytest

from test_qq_social import notice, poke
from test_qq_topic_dialogue_sources import discussion
from test_qq_topic_routing import topics
from saya_app.services.qq_expressions import QQExpressions


async def setup_history(b, s, msg, choice):
    s.topics.dialogue.configure('20000', True)
    first = msg('虚构海湾灯塔的蓝光会消失吗？')
    choice[0] = discussion(first.text, '虚构海湾灯塔')
    await s.observe(first, [])
    card_id = s.cards(first)[0]['id']
    tag = s.provenance.direct(first, {})
    scope = first.scope + ':shared'
    session = 'session_qq_poke_fixture.jsonl'
    return first, card_id, tag, scope, session


def record(b, first, scope, session, lines, tag):
    history, _ = b.store.conversation(scope)
    b.store.save_conversation(scope, history + lines, session)
    b.context_salience.record(scope, session, lines, [first.sent_at] * len(lines),
                             first.context_snapshot, event_dependencies=tag, user_dependencies=tag)


@pytest.mark.parametrize('action', ['stop', 'delete'])
def test_signed_poke_omits_invalid_user_and_derived_reply(tmp_path, action):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            first, card_id, tag, scope, session = await setup_history(b, s, msg, choice)
            record(b, first, scope, session, ['Doctor: ' + first.text, 'Saya: 灯塔旧派生线索'], tag)
            record(b, first, scope, session, ['Saya: 无关的公开对话线索'], s.provenance.empty(first))
            if action == 'stop':
                s.command(msg('/events stop ' + card_id))
            else:
                from test_qq_event_operations import deliver
                request = msg('删除虚构海湾灯塔的事件卡。')
                plan = dict(action='delete', scope='personal', quantifier='one', target_ids=[card_id],
                            intent_quote=request.text, mode='request', clarify=False)
                answer = s.operations.propose(request, plan, s.operations.inventory(request, []), request.text)
                await deliver(b, s, request, answer)
                assert '已删除' in str(await s.operations.handle(msg('对的')))
            assert not s.provenance.valid(first, tag)
            b.client.sent.clear()
            seen = []

            async def decide(prompt, **kwargs):
                seen.extend(json.loads(prompt.rsplit('\n', 1)[1])['recent_same_conversation'])
                return {'action': 'text', 'text': '嗯？', 'mood': 'curious'}

            b.expressions = QQExpressions(b, tmp_path)
            b.expressions.configure('20000', 0, 'auto', confirmed=True)
            b.expressions.decide = decide
            assert poke(b)[0] == 'queued'
            await b.queue.join()
            assert seen == ['Saya: 无关的公开对话线索']
            assert [x[1] for x in b.client.sent] == ['嗯？']
    asyncio.run(run())


def test_poke_drops_result_when_source_stops_during_decision(tmp_path):
    async def run():
        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            first, card_id, tag, scope, session = await setup_history(b, s, msg, choice)
            record(b, first, scope, session, ['Doctor: ' + first.text], tag)
            seen = []

            async def decide(prompt, **kwargs):
                seen.extend(json.loads(prompt.rsplit('\n', 1)[1])['recent_same_conversation'])
                s.command(msg('/events stop ' + card_id))
                return {'action': 'text', 'text': '仍在讨论旧话题', 'mood': 'angry'}

            b.expressions = QQExpressions(b, tmp_path)
            b.expressions.configure('20000', 0, 'auto', confirmed=True)
            b.expressions.decide = decide
            assert poke(b)[0] == 'queued'
            await b.queue.join()
            assert seen == ['Doctor: ' + first.text]
            assert not b.client.sent
            with b.store.connect() as db:
                assert db.execute('select state from qq_poke_events').fetchone()[0] == 'silent'
                assert not db.execute("select 1 from qq_affect where event like '%:poke:%'").fetchone()
    asyncio.run(run())


def test_group_poke_uses_requester_scoped_public_history(tmp_path):
    async def run():
        from core_services import KnowledgeBase
        from saya_app.services.qq_group_context import QQGroupContext
        from test_qq_event_provenance import seed

        async with topics(tmp_path) as (b, s, now, msg, choice, prompts):
            b.group_context = QQGroupContext(b, KnowledgeBase(str(tmp_path / 'group-kb')), clock=lambda: now[0])
            b.group_context.configure('20000', '90000', {'context_enabled': True}, 0, confirmed=True)
            source, _ = await seed(s, msg, choice, group=90000, dual=True)
            snapshot = b.group_context.snapshot(source)
            s.provenance.bind_turn(source, {})
            b.group_context.record_turn(source, snapshot, '本群有效的协作线索')
            s.command(msg('/events stop ' + s.people._canonical('20000')[0]['id']))
            seen = {}

            async def decide(prompt, *, actor):
                seen[actor.user_id] = json.loads(prompt.rsplit('\n', 1)[1])['recent_same_conversation']
                return {'action': 'silent'}

            b.expressions = QQExpressions(b, tmp_path)
            b.expressions.configure('20000', 0, 'auto', confirmed=True)
            b.expressions.decide = decide
            for actor in (20000, 30000):
                assert poke(b, notice(group_id=90000, user_id=actor))[0] == 'queued'
                await b.queue.join()
            assert seen['20000'] == []
            assert any(row['text'] == '本群有效的协作线索' for row in seen['30000'])
    asyncio.run(run())
