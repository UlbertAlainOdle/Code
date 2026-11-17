"""R5 ranking and semantic proposals never modify the source inventory."""
import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from saya_app.services.maintenance_review import (
    PairReading, Reading, Record, Source, review_candidates, source_preview,
)
from saya_app.services.memory_origin import ingress
from saya_app.services.memory_v4 import MemoryEvidence, MemoryItemV4, MemoryV4Store
from saya_app.services.qq_memory_access import MemoryAccessDenied
from test_qq_memory_management import lab
from test_qq_topic_routing import topics


def entry(identifier, text, *, author='20000', mode='assertion', at=0, scope=None, root=None):
    scope = scope or ('memory', '10000', author)
    source = Source(identifier + ':source', root or identifier, author, text, mode, at, subject=author)
    return Record(identifier, '1', scope, (source,))


def reading(record, *, attribute='preference', condition='', value='like', negated=False, period=''):
    source = record.sources[-1]
    return Reading(source.id, 0, source.text, 'coffee', attribute, condition, value, negated, period)


def compare(anchor, candidate, **keys):
    proposal = PairReading(anchor.version, candidate.version, reading(anchor), reading(candidate, **keys))
    return review_candidates(anchor.id, [candidate.id], {anchor.id: anchor, candidate.id: candidate}.get,
                             proposals={candidate.id: proposal})['candidates'][0]


@pytest.mark.parametrize('keys,expected', [
    ({}, 'duplicate_claim'),
    ({'condition': 'at night', 'negated': True}, 'complementary'),
    ({'attribute': 'serving_temperature'}, 'complementary'),
    ({'negated': True}, 'conflict_or_version'),
    ({'condition': None}, 'needs_review'),
    ({'period': None}, 'needs_review'),
    ({'period': '2025'}, 'related_separate'),
])
def test_source_bound_cross_month_paraphrases_can_be_reviewed_without_lexical_match(keys, expected):
    left = entry('left', '咖啡挺合口味')
    right = entry('right', 'Espresso suits me.', at=45 * 86400)
    result = compare(left, right, **keys)
    assert result['relation'] == expected and result['lexical_overlap'] == 0
    assert result['record'].sources[0].id == 'right:source'


def test_tags_and_recent_dates_rank_but_do_not_cross_principals_or_identify_rounds():
    scope = ('group', '10000', '90000')
    left = replace(entry('left', '同一个读书会', scope=scope), tags=('读书会',))
    near = replace(entry('near', left.sources[0].text, scope=scope, at=10), tags=left.tags)
    far = replace(entry('far', left.sources[0].text, scope=scope, at=20), tags=left.tags)
    other = replace(entry('other', left.sources[0].text, author='30000'), tags=left.tags)
    pool = {r.id: r for r in (left, near, far, other)}
    result = review_candidates(left.id, ['far', 'other', 'near', 'near'], pool.get)
    assert [x['record'].id for x in result['candidates']] == ['near', 'far']
    assert compare(left, near)['reason'] == 'same_occurrence_unproven'
    assert compare(replace(left, occurrence=(10,)), replace(near, occurrence=(20,)))['relation'] == 'related_separate'
    assert compare(left, replace(near, split_from=left.id))['reason'] == 'explicit_split'


def test_same_original_evidence_deduplicates_projections_but_preserves_separate_author():
    scope = ('group', '10000', '90000')
    left = entry('left', '我支持这个安排', scope=scope, root='original-message')
    copied = entry('copy', left.sources[0].text, scope=scope, root='original-message')
    independent = entry('other', left.sources[0].text, author='30000', scope=scope)
    pool = {r.id: r for r in (left, copied, independent)}
    rows = review_candidates(left.id, [copied.id, independent.id], pool.get)['candidates']
    assert [(x['relation'], x['shared_source_count']) for x in rows] == [
        ('duplicate_evidence', 1), ('needs_review', 0),
    ]
    assert rows[1]['record'].sources[0].author == '30000'
    moved_span = replace(copied, sources=(replace(copied.sources[0], span=(10, 17)),))
    pool[copied.id] = moved_span
    assert review_candidates(left.id, [copied.id], pool.get)['candidates'][0]['shared_source_count'] == 0


def test_source_preview_preserves_each_projection_and_checks_capacity_before_returning_quotes():
    left = entry('left', '原始咖啡偏好。', root='same')
    right = entry('right', left.sources[0].text, root='same')
    pool = {r.id: r for r in (left, right)}
    def preview():
        review = review_candidates(left.id, [right.id], pool.get)
        return source_preview(review, left.version, pool[right.id].version, source_limit=1)
    result = preview()
    assert result['source_count'] == 1
    assert [row['record_id'] for row in result['sources'][0]] == [left.id, right.id]
    distinct = replace(right, sources=(replace(right.sources[0], root='independent'),))
    pool[right.id] = distinct
    proposal = PairReading(left.version, distinct.version, reading(left), reading(distinct))
    review = review_candidates(left.id, [right.id], pool.get, proposals={right.id: proposal})
    assert source_preview(review, left.version, right.version) == {'status': 'snapshot_changed'}
    assert source_preview(review, left.version, distinct.version, source_limit=1) == {
        'status': 'source_capacity_exceeded', 'source_count': 2, 'source_limit': 1,
    }


def test_shared_occurrence_preserves_agreement_and_disagreement_between_distinct_authors():
    scope = ('group', '10000', '90000')
    common = Source('common', 'schedule', '20000', '读书会安排时间。', 'decision', 10, subject='club')
    left = entry('left', '赞成在图书馆。', scope=scope, mode='opinion')
    right = entry('right', '赞成在图书馆。', scope=scope, author='30000', mode='opinion')
    left = replace(left, sources=(common, replace(left.sources[0], subject='club')), occurrence=(10, 'minute'))
    right = replace(right, sources=(common, replace(right.sources[0], subject='club')), occurrence=left.occurrence)
    assert compare(left, right)['reason'] == 'independent_attribution'
    disagreement = replace(right, sources=(common, replace(right.sources[-1], text='反对在图书馆。')))
    assert compare(left, disagreement, negated=True)['relation'] == 'conflict_or_version'
    assert compare(left, replace(right, occurrence=()))['reason'] == 'same_occurrence_unproven'


@pytest.mark.parametrize('mode', ['question', 'hypothesis', 'reported', 'assistant_context'])
def test_non_assertive_or_assistant_source_cannot_establish_a_fact(mode):
    assert compare(entry('left', '同一材料'), entry('right', '同一材料', mode=mode))['reason'] == (
        'non_assertive_or_unknown_source'
    )


def test_stale_or_fabricated_proposals_are_rechecked_against_exact_source_spans():
    left, right = entry('left', '相似材料'), entry('right', '相似材料多一点')
    proposal = PairReading(left.version, right.version, reading(left), reading(right))
    changed = replace(right, revision='2')
    def check(record, plan):
        return review_candidates(left.id, [right.id], {left.id: left, right.id: record}.get,
                                 proposals={right.id: plan})['candidates'][0]
    assert check(changed, proposal)['reason'] == 'snapshot_changed'
    fabricated = replace(proposal, candidate=replace(proposal.candidate, start=1))
    assert check(right, fabricated)['reason'] == 'invalid_source_span'
    with pytest.raises(ValueError, match='budget'):
        review_candidates(left.id, ['id'] * 129, {}.get)


def test_memory_review_uses_live_native_evidence_and_excludes_blocked_or_old_versions(lab):
    _, kb, service, actor, _ = lab
    kb.update_settings({'memory_v4_read_enabled': True, 'embedding_enabled': False})
    store = kb.memory_v4_store = MemoryV4Store(Path(kb.base_dir) / 'memory4.sqlite3')
    origin = ingress('qq', subject=actor.user_id, bot_id=actor.bot_id, session_id='session_qq_fixture')['origin']

    def seed(key, current, quote, *, owner=actor.user_id, lifecycle='active'):
        item = MemoryItemV4(id=key, canonical_text=current, authority='user_confirmed', lifecycle=lifecycle,
                            metadata={'origin': {**origin, 'verified_subject': owner}})
        source = MemoryEvidence('evidence:' + key, key, 'user_message',
                                'session_qq_fixture:user:' + key, role='assertion', quote=quote)
        store.add_memory(item, [source])

    seed('anchor', '偏好浅烘咖啡', '记住：偏好浅烘咖啡')
    seed('valid', '用户选择保存本次用户的表述：「偏好浅烘咖啡」', '偏好浅烘咖啡')
    seed('changed', '现在偏好深烘咖啡', '偏好浅烘咖啡')
    seed('negated', '不再偏好浅烘咖啡', '偏好浅烘咖啡')
    seed('assistant_selected', '用户选择保存本次助手的表述：「偏好浅烘咖啡」', '偏好浅烘咖啡')
    seed('blocked', '偏好浅烘咖啡', '偏好浅烘咖啡')
    seed('foreign', '偏好浅烘咖啡', '偏好浅烘咖啡', owner='30000')
    seed('expired', '偏好浅烘咖啡', '偏好浅烘咖啡', lifecycle='expired')
    kb.memory_use.set_block('blocked', True)
    before = store.export_state()
    result = service.maintenance_review(actor, 'anchor',
        ['valid', 'changed', 'negated', 'assistant_selected', 'blocked', 'foreign', 'expired'])
    assert [x['record'].id for x in result['candidates']] == ['valid']
    left, right = result['anchor'], result['candidates'][0]['record']
    proposal = PairReading(left.version, right.version, reading(left), reading(right))
    preview = service.maintenance_preview(actor, 'anchor', 'valid', anchor_version=left.version,
                                          candidate_version=right.version, proposal=proposal)
    assert preview['status'] == 'ready' and preview['source_count'] == 2
    assert store.export_state() == before
    kb.memory_use.set_block('valid', True)
    assert service.maintenance_preview(actor, 'anchor', 'valid', anchor_version=left.version,
                                       candidate_version=right.version, proposal=proposal) == {'status': 'snapshot_changed'}
    assert service.maintenance_review(actor, 'blocked', ['valid'])['status'] == 'anchor_unavailable'
    with pytest.raises(MemoryAccessDenied):
        service.maintenance_review(replace(actor, group_id='90000', group_authorized=True), 'anchor', ['valid'])


def test_event_review_reuses_requester_masks_expiry_and_membership(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, now, message, _, _):
            unified = service.unified
            first = message('星桥读书会整理旧书单。', group=90000)
            left = unified.commit(first, '90000', {'title': '星桥读书会'})
            second = message('星桥读书会还要整理新书单。', group=90000)
            right = unified.commit(second, '90000', {'title': '星桥读书会'})
            with bridge.store.connect() as db:
                before = list(db.iterdump())
            review = unified.maintenance_review(first, left, [right], group='90000')
            assert [x['record'].id for x in review['candidates']] == [right]
            assert review['candidates'][0]['relation'] == 'needs_review'
            versions = {'anchor_version': review['anchor'].version,
                        'candidate_version': review['candidates'][0]['record'].version}
            assert unified.maintenance_preview(first, left, right, group='90000', **versions)['status'] == (
                'separate_review_required'
            )
            with bridge.store.connect() as db:
                assert list(db.iterdump()) == before
            outsider = message('活动记录', user=30000, group=90000)
            unified.mark_member('90000', '30000', 'revoked')
            assert unified.maintenance_review(outsider, left, [right], group='90000')['status'] == 'unavailable'
            stop = message('/events group stop ' + right, group=90000)
            unified.command(stop)
            assert unified.maintenance_review(first, left, [right], group='90000')['candidates'] == []
            assert unified.maintenance_preview(first, left, right, group='90000', **versions) == {
                'status': 'snapshot_changed',
            }
            now[0] += 8 * 86400
            assert unified.maintenance_review(first, left, [], group='90000')['status'] == 'anchor_unavailable'
    asyncio.run(run())
