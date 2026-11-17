import asyncio
import json

import pytest

from test_qq_topic_dialogue_sources import discussion
from test_qq_topic_routing import topics


async def route(episodes, message, choice, text, anchor, **options):
    incoming = message(text)
    choice[0] = discussion(text, anchor)
    choice[0]['items'][0].update(options)
    await episodes.observe(incoming, [])
    return incoming


def result(bridge, incoming):
    with bridge.store.connect() as db:
        state, data = db.execute('select state,data from qq_topic_routes where event=?', (incoming.key,)).fetchone()
    return state, json.loads(data)


def test_interleaved_named_discussions_keep_independent_cards_across_read_command(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, now, message, choice, prompts):
            episodes.topics.dialogue.configure('20000', True)
            a = '虚构松林书会'
            b = '虚构星湾观测'
            first = await route(episodes, message, choice, f'{a}要怎么选书？', a)
            second = await route(episodes, message, choice, f'{b}要怎么选地点？', b)
            id_a = result(bridge, first)[1]['results'][0]['id']
            id_b = result(bridge, second)[1]['results'][0]['id']
            assert id_a != id_b
            listing = message('/events self')
            assert id_a in str(episodes.command(listing)) and id_b in str(episodes.command(listing))
            again = await route(episodes, message, choice, f'回到{a}，散文合适吗？', a)
            final = await route(episodes, message, choice, f'{b}选河边会太亮吗？', b)
            assert result(bridge, again)[1]['results'][0]['id'] == id_a
            assert result(bridge, final)[1]['results'][0]['id'] == id_b
            cards = {card['id']: card for card in episodes.cards(final)}
            assert [fact['source'] for fact in cards[id_a]['facts']] == [first.key, again.key]
            assert [fact['source'] for fact in cards[id_b]['facts']] == [second.key, final.key]
    asyncio.run(run())


def test_fragment_cannot_borrow_the_other_fragments_anchor(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, now, message, choice, prompts):
            episodes.topics.dialogue.configure('20000', True)
            a = '虚构松林书会'
            b = '虚构星湾观测'
            await route(episodes, message, choice, f'{a}要怎么选书？', a)
            await route(episodes, message, choice, f'{b}要怎么选地点？', b)
            quote = f'{b}河边会太亮吗？'
            current = message(f'{a}先按原安排；{quote}')
            choice[0] = discussion(quote, a)
            await episodes.observe(current, [])
            assert result(bridge, current) == ('failed_plan', {'reason': 'dialogue_topic_anchor_outside_span'})
            assert all(len(card['facts']) == 1 for card in episodes.cards(current))
    asyncio.run(run())


@pytest.mark.parametrize('proof', ['missing', 'invented', 'other_fragment', 'valid'])
def test_new_occurrence_needs_proof_in_the_same_fragment(tmp_path, proof):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, now, message, choice, prompts):
            episodes.topics.dialogue.configure('20000', True)
            anchor = '虚构松林书会'
            first = await route(episodes, message, choice, f'{anchor}周五选散文好吗？', anchor)
            original = result(bridge, first)[1]['results'][0]['id']
            quote = f'{anchor}下周再办一次可行吗？' if proof == 'valid' else f'{anchor}散文合适吗？'
            text = f'虚构星湾观测下周再办一次；{quote}' if proof == 'other_fragment' else quote
            current = message(text)
            choice[0] = discussion(quote, anchor)
            choice[0]['items'][0].update(group_relation='new_occurrence', occurrence_quote={
                'missing': '', 'invented': '下月新一轮', 'other_fragment': '下周再办一次', 'valid': '下周再办一次',
            }[proof])
            await episodes.observe(current, [])
            if proof == 'valid':
                state, audit = result(bridge, current)
                assert state == 'committed' and audit['results'][0]['id'] != original
                assert len(episodes.cards(current)) == 2
            else:
                assert result(bridge, current) == ('failed_plan', {'reason': 'new_occurrence_requires_source'})
                assert len(episodes.cards(current)) == 1
    asyncio.run(run())


def test_same_name_multiple_occurrences_require_explicit_target(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, now, message, choice, prompts):
            episodes.topics.dialogue.configure('20000', True)
            anchor = '虚构松林书会'
            first = await route(episodes, message, choice, f'{anchor}周五选散文好吗？', anchor)
            original = result(bridge, first)[1]['results'][0]['id']
            second = await route(episodes, message, choice, f'{anchor}下周再办一次可行吗？', anchor,
                                 group_relation='new_occurrence', occurrence_quote='下周再办一次')
            other = result(bridge, second)[1]['results'][0]['id']
            current = await route(episodes, message, choice, f'{anchor}读短篇会不会更合适？', anchor,
                                  personal={'action': 'update', 'event_id': other, 'supersedes': []})
            assert result(bridge, current) == ('failed_plan', {'reason': 'ambiguous_dialogue_topic'})
            current = await route(episodes, message, choice, f'{anchor}这次指 {original}，读短篇合适吗？', anchor,
                                  personal={'action': 'update', 'event_id': original, 'supersedes': []})
            assert result(bridge, current)[1]['results'][0]['id'] == original
            assert {card['id']: len(card['facts']) for card in episodes.cards(current)} == {original: 2, other: 1}
    asyncio.run(run())


def test_low_confidence_private_fragment_is_reported_alongside_committed_fragment(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, episodes, now, message, choice, prompts):
            episodes.topics.dialogue.configure('20000', True)
            clear = '虚构松林书会要怎么选书？'
            uncertain = '那个实验可以再试一次吗？'
            current = message(clear + '；' + uncertain)
            clear_item = discussion(clear, '虚构松林书会')['items'][0]
            uncertain_item = discussion(uncertain, '')['items'][0]
            uncertain_item['confidence'] = 0.7
            choice[0] = {'items': [clear_item, uncertain_item]}
            await episodes.observe(current, [])
            state, audit = result(bridge, current)
            assert state == 'partial' and audit['unprocessed']
            assert audit['unresolved'] == [{'item': 1, 'reason': 'low_confidence_dialogue_topic'}]
            assert any(entry['status'] == 'committed' for entry in audit['results'])
            facts = [fact['quote'] for card in episodes.cards(current) for fact in card['facts']]
            assert facts == [clear]
            assert episodes.topics.dialogue.valid_ref(current, episodes.topics.dialogue.prepare(current))
    asyncio.run(run())
