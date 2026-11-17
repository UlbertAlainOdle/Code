"""Confirmation, cancellation, and lookup against isolated QQ and memory stores."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
from pathlib import Path
import time
from unittest.mock import patch

import pytest

from saya_app.services.maintenance_review import PairReading
from saya_app.services.memory_origin import ingress
from saya_app.services.memory_v4 import MemoryEvidence, MemoryItemV4, MemoryV4Store
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.qq_tasks import QQTaskGuard, QQTaskRegistry, TaskCancelled
from test_qq_memory_management import lab
from test_qq_r5_maintenance_review import reading
from test_qq_topic_routing import create, item, topics


@pytest.fixture
def memory_links(lab):
    bridge, kb, service, actor, _ = lab
    bridge.settings = bridge.settings.model_copy(update={'memory_write_enabled': True, 'memory_management_enabled': True})
    scope = '10000:private:20000:shared'
    bridge.store.save_conversation(scope, [], 'test-topic')
    kb.update_settings({'memory_v4_read_enabled': True, 'embedding_enabled': False})
    store = kb.memory_v4_store = MemoryV4Store(Path(kb.base_dir) / 'memory4.sqlite3')
    session = 'session_qq_fixture'
    origin = ingress('qq', subject=actor.user_id, bot_id=actor.bot_id, session_id=session)['origin']
    for name, text in [('first', '仅供验收，偏爱浅烘咖啡。'), ('second', '仅供验收，喜欢浅烘咖啡。'),
                       ('third', '仅供验收，偏好浅烘咖啡。'), ('locked', '仅供验收，喜欢咖啡。')]:
        record = MemoryItemV4(id=name, canonical_text=text, authority='user_confirmed',
                              metadata={'origin': origin}, locked=name == 'locked')
        evidence = MemoryEvidence(name + ':source', name, 'user_message',
                                  session + ':user:' + name, role='assertion', quote=text)
        store.add_memory(record, [evidence])
    links = service.maintenance_links(actor, 'test-topic')
    return bridge, kb, service, actor, links, store


def prepare_pair(links, operation, first='first', second='second', **reading_keys):
    left, right = links.load(first), links.load(second)
    plan = PairReading(left.version, right.version, reading(left), reading(right, **reading_keys))
    return links.prepare(operation, first, second, anchor_version=left.version,
                         candidate_version=right.version, proposal=plan)


def test_concurrent_confirm_and_undo_leave_one_durable_link_and_original_records(memory_links):
    bridge, _, service, actor, links, store = memory_links
    operation = 'a' * 32
    originals = store.export_state()
    before = links.result(operation)
    assert before == {'status': 'unavailable'}
    with bridge.store.connect() as db:
        assert not links._has_table(db)
    preview = prepare_pair(links, operation, condition='afternoon')
    assert preview['status'] == 'pending' and preview['source_count'] == 2
    assert prepare_pair(links, operation, condition='afternoon') == preview
    assert links.for_member('first') == before
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = list(pool.map(links.confirm, (operation, operation)))
    assert first == second and first['status'] == 'active'
    resumed = service.maintenance_links(actor, 'test-topic')
    assert resumed.for_member('first') == resumed.for_member('second') == first
    with bridge.store.connect() as db:
        rows = [tuple(x) for x in db.execute('select * from qq_maintenance_links')]
    assert len(rows) == 1 and '咖啡' not in json.dumps(rows, ensure_ascii=False)
    assert resumed.undo(operation) == resumed.undo(operation) == {'status': 'undone', 'id': operation}
    assert resumed.confirm(operation)['status'] == 'undone'
    assert resumed.for_member('first') == before and store.export_state() == originals


def test_concurrent_overlaps_cannot_publish_two_joint_views(memory_links):
    _, _, _, _, links, _ = memory_links
    a, b = 'a' * 32, 'b' * 32
    assert prepare_pair(links, a)['status'] == 'pending'
    assert prepare_pair(links, b, second='third')['status'] == 'pending'
    with ThreadPoolExecutor(max_workers=2) as pool:
        states = {x['status'] for x in pool.map(links.confirm, (a, b))}
    assert states == {'active', 'member_already_linked'}


def test_member_change_or_temporary_stop_permanently_invalidates_old_projection(memory_links):
    _, kb, _, _, links, store = memory_links
    operation = 'a' * 32
    prepare_pair(links, operation)
    assert links.confirm(operation)['status'] == 'active'
    kb.memory_use.set_block('second', True)
    assert links.result(operation) == {'status': 'invalidated', 'id': operation}
    kb.memory_use.set_block('second', False)
    assert links.for_member('second')['status'] == 'unavailable'
    replacement = 'b' * 32
    assert prepare_pair(links, replacement)['status'] == 'pending'
    assert links.confirm(replacement)['status'] == 'active'
    store.update_memory('second', {'canonical_text': '已经改为喜欢深烘。'})
    assert links.result(replacement) == {'status': 'invalidated', 'id': replacement}
    assert links.undo(replacement)['status'] == 'undone'
    assert store.get_memory('second').canonical_text == '已经改为喜欢深烘。'


def test_preview_binds_topic_actor_and_expiry_without_repeating_expired_work(memory_links):
    bridge, _, service, actor, links, _ = memory_links
    operation = 'a' * 32
    now = [time.time()]
    links.clock = lambda: now[0]
    assert prepare_pair(links, operation)['status'] == 'pending'
    assert prepare_pair(links, operation, second='third') == {'status': 'unavailable'}
    with pytest.raises(MemoryAccessDenied):
        service.maintenance_links(replace(actor, user_id='30000'), 'test-topic').result(operation)
    bridge.store.save_conversation('10000:private:20000:shared', [], 'new-topic')
    wrong_topic = service.maintenance_links(actor, 'new-topic')
    assert wrong_topic.confirm(operation) == {'status': 'unavailable'}
    bridge.store.save_conversation('10000:private:20000:shared', [], 'test-topic')
    now[0] += 601
    assert links.result(operation)['status'] == 'expired'
    assert links.confirm(operation)['status'] == prepare_pair(links, operation)['status'] == 'expired'


def test_protected_record_cannot_enter_a_confirmed_link(memory_links):
    _, _, service, actor, links, _ = memory_links
    result = service.maintenance_review(actor, 'first', ['locked'])
    left, right = result['anchor'], result['candidates'][0]['record']
    proposal = PairReading(left.version, right.version, reading(left), reading(right))
    preview = links.prepare('a' * 32, 'first', 'locked', anchor_version=left.version,
                            candidate_version=right.version, proposal=proposal)
    assert preview == {'status': 'snapshot_changed'}


def test_failed_commit_rolls_back_metadata_before_same_operation_is_retried(memory_links):
    _, _, _, _, links, _ = memory_links
    operation = 'a' * 32
    prepare_pair(links, operation)
    update = links._set_state
    def interrupted(*args):
        update(*args)
        raise OSError('simulated interruption before SQLite commit')
    with patch.object(links, '_set_state', side_effect=interrupted), pytest.raises(OSError):
        links.confirm(operation)
    assert links.result(operation)['status'] == 'pending'
    assert links.confirm(operation)['status'] == 'active'


def test_cancel_preview_prevents_late_confirmation(memory_links):
    _, _, _, _, links, _ = memory_links
    operation = 'a' * 32
    prepare_pair(links, operation)
    assert links.undo(operation) == {'status': 'cancelled', 'id': operation}
    assert links.confirm(operation) == {'status': 'cancelled', 'id': operation}
    assert links.for_member('first') == {'status': 'unavailable'}


def test_confirm_rechecks_live_source_capacity_before_recording_success(memory_links):
    _, _, _, _, links, _ = memory_links
    operation = 'a' * 32
    prepare_pair(links, operation)
    links.source_limit = lambda: 1
    assert links.confirm(operation) == {'status': 'invalidated', 'id': operation}
    links.source_limit = lambda: 32
    assert links.confirm(operation) == {'status': 'invalidated', 'id': operation}


def test_confirmation_uses_the_real_task_effect_and_cancelled_tasks_cannot_mutate(memory_links):
    bridge, _, _, _, links, _ = memory_links
    operation = 'a' * 32
    prepare_pair(links, operation)
    registry = QQTaskRegistry(bridge.store)
    scope = '10000:private:20000:shared'
    task = registry.register('maintenance-cancelled', scope, 'f' * 64)
    registry.claim(task['id'], scope)
    registry.cancel(task['id'], scope)
    with QQTaskGuard(registry, task['id'], scope).bind(), pytest.raises(TaskCancelled):
        links.confirm(operation)
    assert links.result(operation)['status'] == 'pending'
    registry.finish(task['id'], scope, 'cancelled')
    task = registry.register('maintenance-confirmed', scope, 'f' * 64)
    registry.claim(task['id'], scope)
    with QQTaskGuard(registry, task['id'], scope).bind():
        assert links.confirm(operation)['status'] == 'active'
        assert links.confirm(operation)['status'] == 'active'
    effects = registry.get(task['id'], scope)['effects'].values()
    assert {(effect['kind'], effect['outcome']) for effect in effects} == {
        ('memory', 'committed'), ('memory', 'failed'),
    }


def test_group_link_requires_both_coordinator_grants_and_respects_revocation(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, _, _):
            unified = service.unified
            initiator = message('星桥读书会只整理原始书目。', group=90000)
            left = unified.commit(initiator, '90000', {'title': '星桥读书会'})
            # Simulate two old projections of the exact same source, each
            # retaining its own immutable card ID.
            right = 'd' * 16
            with bridge.store.connect() as db:
                row = db.execute('select * from qq_shared_events where id=?', (left,)).fetchone()
                db.execute('insert into qq_shared_events values(?,?,?,?,?)',
                           (right, row['bot'], row['grp'], row['revision'], row['data']))
            unified.mark_member('90000', '20000')
            for identifier in (left, right):
                assert '已更新群事件协调人' in unified.command(
                    message(f'/events group coordinator {identifier} 20000', group=90000))
            coordinator = message('复核两个原编号', group=90000)
            links = unified.maintenance_links(coordinator, group='90000')
            a, b = links.load(left), links.load(right)
            operation = 'e' * 32
            versions = {'anchor_version': a.version, 'candidate_version': b.version}
            member = unified.maintenance_links(message('仅成员', group=90000, user=30000), group='90000')
            with pytest.raises(PermissionError, match='coordinator'):
                member.prepare(operation, left, right, **versions)
            with bridge.store.connect() as db:
                original_cards = [tuple(x) for x in db.execute('select * from qq_shared_events order by id')]
            assert links.prepare(operation, left, right, **versions)['source_count'] == 1
            assert links.confirm(operation)['status'] == 'active'
            assert links.for_member(right)['id'] == operation and member.result(operation)['status'] == 'unavailable'
            with bridge.store.connect() as db:
                assert [tuple(x) for x in db.execute('select * from qq_shared_events order by id')] == original_cards
            unified.mark_member('90000', '20000', 'revoked')
            with pytest.raises(PermissionError):
                links.result(operation)
            unified.mark_member('90000', '20000')
            assert links.result(operation) == {'status': 'invalidated', 'id': operation}
            assert links.undo(operation)['status'] == 'undone'
    asyncio.run(run())


def test_personal_link_remains_private_and_stops_with_its_original_card(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, choice, _):
            original = message('我已经整理好旧版书单。')
            choice[0] = {'items': [item(original.text, personal=create(), mode='progress')]}
            await service.observe(original, [])
            left = service.people._canonical('20000')[0]['id']
            right = 'd' * 16
            with bridge.store.connect() as db:
                row = db.execute('select * from qq_episode_cards where id=?', (left,)).fetchone()
                db.execute('insert into qq_episode_cards values(?,?,?,?)',
                           (right, row['scope'], row['revision'], row['data']))
            unified = service.unified
            links = unified.maintenance_links(original)
            a, b = links.load(left), links.load(right)
            operation = 'e' * 32
            assert links.prepare(operation, left, right, anchor_version=a.version,
                                 candidate_version=b.version)['status'] == 'pending'
            assert links.confirm(operation)['status'] == 'active'
            assert links.for_member(right)['source_count'] == 1
            with pytest.raises(PermissionError):
                unified.maintenance_links(message('请求群里读取', group=90000)).for_member(left)
            assert '停止' in service.command(message(f'/events stop {right}'))
            assert links.result(operation) == {'status': 'invalidated', 'id': operation}
            assert links.for_member(left)['status'] == 'unavailable'
    asyncio.run(run())
