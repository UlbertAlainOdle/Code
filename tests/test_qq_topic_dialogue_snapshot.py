import asyncio
import json

import pytest

from test_qq_topic_dialogue_sources import discussion
from test_qq_topic_routing import topics


@pytest.mark.parametrize('when', ['model', 'commit'])
@pytest.mark.parametrize('change', ['block', 'window'])
def test_private_route_cannot_commit_an_anchor_from_stale_recent_history(tmp_path, when, change):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, clock, message, choice, prompts):
            dialogue = episodes.topics.dialogue
            dialogue.configure('20000', True)
            prior = message('虚构白榆书店是否应该延长营业时间？')
            ref = dialogue.prepare(prior)
            clock[0] = prior.sent_at + 1735
            current = message('如果再多半小时会怎样？')
            plan = discussion(current.text, '虚构白榆书店')

            def invalidate():
                if change == 'window':
                    clock[0] = prior.sent_at + 1801
                else:
                    with bridge.store.connect() as db:
                        db.execute('update qq_topic_dialogue_units set blocked=1 where id=?', (ref['id'],))

            async def decide(prompt, **kwargs):
                assert prior.text in prompt
                if when == 'model':
                    invalidate()
                return plan

            if when == 'commit':
                original = episodes.people.observe

                async def observe(part, history):
                    invalidate()
                    await original(part, history)

                episodes.people.observe = observe

            episodes.decide = decide
            await episodes.observe(current, [])
            assert not episodes.people._canonical(current.user_id)
            with bridge.store.connect() as db:
                state, raw = db.execute('select state,data from qq_topic_routes where event=?', (current.key,)).fetchone()
                assert not db.execute('select 1 from qq_topic_evidence where source=?', (current.key,)).fetchone()
            audit = json.loads(raw)
            if when == 'model':
                assert state == 'stale' and audit['reason'] == 'dialogue_source_snapshot_changed'
            else:
                assert state == 'partial' and audit['unprocessed']
                assert audit['results'][0]['reason'] == 'dialogue_source_snapshot_changed'
            assert dialogue.valid_ref(prior, ref) == (change == 'window')
    asyncio.run(run())
