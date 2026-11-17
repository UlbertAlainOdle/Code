"""Protected archive operations against isolated native and compatibility stores."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
import re

import pytest

from saya_app.services.memory_origin import ingress
from saya_app.services.memory_v4 import MemoryEvidence, MemoryItemV4
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_memory_access import MemoryAccessDenied
from test_qq_memory_management import lab
from test_qq_r5_maintenance_commands import bind_memory_responder, run_command


@pytest.fixture
def archive_lab(lab):
    bridge, kb, service, actor, memory = lab
    bridge.settings = bridge.settings.model_copy(update={'memory_write_enabled': True, 'memory_management_enabled': True})
    bridge.store.save_conversation('10000:private:20000:shared', [], 'test-topic')
    kb.update_settings({'memory_v4_read_enabled': True, 'memory_v4_write_enabled': True, 'embedding_enabled': False})
    session = 'session_qq_archive_fixture'
    origin = ingress('qq', subject=actor.user_id, bot_id=actor.bot_id, session_id=session)['origin']
    assert kb.memory_v4_store is not None
    first = MemoryItemV4(id='first', canonical_text=memory['content'], authority='user_confirmed',
                         metadata={'origin': origin})
    source = MemoryEvidence('first-source', 'first', 'user_message', session + ':user:shared',
                            role='assertion', quote=first.canonical_text)
    kb.memory_v4_store.add_memory(first, [source])
    kb.memory_v4_store.add_memory(replace(first, id='second'), [replace(source, id='second-source', memory_id='second')])
    kb.memories.extend(deepcopy(memory) | {'id': name} for name in ('first', 'second'))
    assert kb.save_memories(sync_memory_v4=False)
    return bridge, kb, service, actor, source


def versions(service, actor):
    links = service.maintenance_links(actor, 'test-topic')
    return {f'{side}_version': links.load(member).version
            for side, member in [('keep', 'first'), ('archive', 'second')]}


def preview(service, actor, snapshot):
    return service.maintenance_archive_preview(actor, 'test-topic', 'first', 'second', **snapshot)


def test_exact_duplicate_preview_is_versioned_and_read_only(archive_lab):
    bridge, kb, service, actor, _ = archive_lab
    before = (deepcopy(kb.memories), kb.memory_v4_store.export_state(), kb.memory_use.snapshot())
    snapshot = versions(service, actor)
    result = preview(service, actor, snapshot)
    assert result['status'] == 'ready' and result['relation'] == 'duplicate_evidence'
    assert (result['keep'], result['archive'], result['source_count']) == ('first', 'second', 1)
    assert [row['source'].id for row in result['sources'][0]] == ['first-source', 'second-source']
    assert result['legacy_versions'] == {key: record_version(kb._find_memory(key)) for key in ('first', 'second')}
    assert preview(service, actor, snapshot) == result
    reversed_plan = service.maintenance_archive_preview(actor, 'test-topic', 'second', 'first',
        keep_version=snapshot['archive_version'], archive_version=snapshot['keep_version'])
    assert reversed_plan['plan_version'] != result['plan_version']
    assert before == (kb.memories, kb.memory_v4_store.export_state(), kb.memory_use.snapshot())
    with bridge.store.connect() as db:
        assert not db.execute("select 1 from sqlite_master where name='qq_maintenance_links'").fetchone()


@pytest.mark.parametrize('change', ['extra_assertion', 'non_projected_evidence', 'retention', 'native_meaning'])
def test_archival_cannot_drop_independent_or_unprojected_material(archive_lab, change):
    _, kb, service, actor, source = archive_lab
    store = kb.memory_v4_store
    if change in {'extra_assertion', 'non_projected_evidence'}:
        role = 'assertion' if change == 'extra_assertion' else 'background'
        store.add_evidence(replace(source, id='extra', memory_id='second', source_id=source.source_id + '-extra', role=role))
    elif change == 'retention':
        kb._find_memory('second')['expires_at'] = '2099-01-01'
    else:
        store.update_memory('second', {'predicate': 'separate_meaning'})
    assert preview(service, actor, versions(service, actor))['status'] == 'separate_review_required'


def test_archive_plan_changes_with_legacy_version_and_revocation(archive_lab):
    _, kb, service, actor, _ = archive_lab
    snapshot = versions(service, actor)
    original = preview(service, actor, snapshot)
    for key in ('first', 'second'):
        kb._find_memory(key)['title'] = '同样的新标题'
    title_updated = preview(service, actor, snapshot)
    assert title_updated['plan_version'] != original['plan_version']
    kb.memory_use.set_block('second', True)
    assert preview(service, actor, snapshot) == {'status': 'snapshot_changed'}
    kb.memory_use.set_block('second', False)
    assert preview(service, actor, snapshot)['plan_version'] != title_updated['plan_version']
    kb.memory_v4_store.update_memory('second', {'locked': True})
    assert preview(service, actor, snapshot) == {'status': 'snapshot_changed'}


def test_archive_preview_uses_existing_owner_scope_and_write_policy(archive_lab):
    bridge, kb, service, actor, _ = archive_lab
    snapshot = versions(service, actor)
    with pytest.raises(MemoryAccessDenied):
        preview(service, replace(actor, is_owner=False), snapshot)
    with pytest.raises(MemoryAccessDenied):
        service.maintenance_archive_preview(actor, 'other-topic', 'first', 'second', **snapshot)
    service.set_access(actor, 'second', content_version=record_version(kb._find_memory('second')),
                       expected_revision=0, subject_id=actor.user_id, visibility='public',
                       requesters=['*'], confirmed=True)
    assert preview(service, actor, snapshot) == {'status': 'unavailable'}
    bridge.settings = bridge.settings.model_copy(update={'memory_write_enabled': False})
    with pytest.raises(MemoryAccessDenied):
        preview(service, actor, snapshot)


def prepared(archive_lab, operation='a' * 32):
    _, _, service, actor, _ = archive_lab
    archive = service.maintenance_archive(actor, 'test-topic')
    assert archive.prepare(operation, 'first', 'second', check_preview=lambda _: None)['status'] == 'pending'
    return archive, operation


def test_archive_and_undo_change_one_id_once_and_keep_evidence(archive_lab, monkeypatch):
    bridge, kb, _, _, _ = archive_lab
    archive, operation = prepared(archive_lab)
    store = kb.memory_v4_store
    originals = deepcopy(kb.memories)
    native = store.get_memory('second')
    evidence = store.list_evidence('second')
    def forbidden(**kwargs):
        pytest.fail('archive must not trigger capacity maintenance')
    monkeypatch.setattr(kb, 'run_memory_capacity_maintenance', forbidden)
    assert archive.confirm(operation)['status'] == 'preview_not_delivered'
    assert archive.mark_delivered(operation)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first, second = pool.map(archive.confirm, (operation, operation))
    assert first == second == {'status': 'archived', 'id': operation}
    assert store.get_memory('second').revision == native.revision + 1
    assert not kb.memory_allowed_for_use(kb._find_memory('second'))
    assert kb.memory_allowed_for_use(kb._find_memory('first'))
    assert archive.for_member('first') == archive.for_member('second')
    assert archive.for_member('second')['sources'][0].id == 'first-source'
    with bridge.store.connect() as db:
        plan = db.execute('select plan from qq_maintenance_archives').fetchone()[0]
    assert native.canonical_text not in plan
    assert archive.undo(operation)['status'] == 'undone'
    assert archive.confirm(operation)['status'] == 'undone'
    assert archive.for_member('second')['status'] == 'unavailable'
    assert kb.memories == originals
    restored = store.get_memory('second')
    assert restored == replace(native, revision=native.revision + 2, updated_at=restored.updated_at)
    assert store.list_evidence('second') == evidence


@pytest.mark.parametrize('blocked', ['first', 'second'])
def test_revoked_archival_member_cannot_be_restored_after_reenable(archive_lab, blocked):
    _, kb, _, _, _ = archive_lab
    archive, operation = prepared(archive_lab)
    assert archive.mark_delivered(operation)
    assert archive.confirm(operation)['status'] == 'archived'
    kb.memory_use.set_block(blocked, True)
    assert archive.for_member('second')['status'] == 'unavailable'
    kb.memory_use.set_block(blocked, False)
    assert archive.undo(operation)['status'] == 'invalidated'
    assert kb.memory_v4_store.get_memory('second').lifecycle == 'archived'


def test_native_archive_failure_leaves_unknown_and_does_not_replay(archive_lab, monkeypatch):
    _, kb, _, _, _ = archive_lab
    archive, operation = prepared(archive_lab)
    assert archive.mark_delivered(operation)
    def unavailable(*args, **kwargs):
        raise OSError('isolated failure')
    with monkeypatch.context() as patch:
        patch.setattr(kb.memory_v4_store, 'update_memory', unavailable)
        with pytest.raises(OSError):
            archive.confirm(operation)
    assert archive.confirm(operation)['status'] == archive.result(operation)['status'] == 'unknown'
    assert archive.undo(operation)['status'] == 'unknown'
    assert kb.memory_v4_store.get_memory('second').lifecycle == 'active'
    assert not kb.memory_allowed_for_use(kb._find_memory('second'))
    assert archive.for_member('second')['status'] == 'unavailable'


def test_restore_compat_failure_leaves_archive_hidden_and_unknown(archive_lab, monkeypatch):
    _, kb, _, _, _ = archive_lab
    archive, operation = prepared(archive_lab)
    assert archive.mark_delivered(operation)
    assert archive.confirm(operation)['status'] == 'archived'
    with monkeypatch.context() as patch:
        patch.setattr(kb.sqlite_store, 'upsert_memories', lambda _: {'status': 'error'})
        assert archive.undo(operation)['status'] == 'unknown'
    assert archive.undo(operation)['status'] == archive.result(operation)['status'] == 'unknown'
    assert kb.memory_v4_store.get_memory('second').lifecycle == 'active'
    assert not kb.memory_allowed_for_use(kb.memory_v4_store.get_memory('second'))


def test_reconcile_after_durable_mutation_before_final_receipt(archive_lab, monkeypatch):
    _, kb, service, actor, _ = archive_lab
    archive, operation = prepared(archive_lab)
    assert archive.mark_delivered(operation)
    original_save = archive._save
    def drop_final_state(row, state, plan):
        if state == 'archived':
            raise OSError('interrupted before final receipt')
        original_save(row, state, plan)
    monkeypatch.setattr(archive, '_save', drop_final_state)
    with pytest.raises(OSError):
        archive.confirm(operation)
    revision = kb.memory_v4_store.get_memory('second').revision
    reopened = service.maintenance_archive(actor, 'test-topic')
    assert reopened.result(operation)['status'] == reopened.confirm(operation)['status'] == 'archived'
    assert kb.memory_v4_store.get_memory('second').revision == revision


def test_normalized_qq_copies_keep_archive_mapping_across_backend_restart(archive_lab):
    bridge, kb, _, actor, source = archive_lab
    store = kb.memory_v4_store
    for key in ('first', 'second'):
        row = kb._find_memory(key)
        item = store.get_memory(key)
        detail = {'type': 'memory_v4_write_pipeline', 'memory_id': key}
        row.update(source='write_pipeline', user_confirmed=True, source_detail=detail, origin=item.metadata['origin'],
                   evidence=[{'type': 'user_message', 'role': 'assertion', 'source_id': source.source_id,
                              'quote': source.quote, 'observed_at': source.observed_at}])
        kb.normalize_memory(row)
        store.update_memory(key, {'metadata': {**item.metadata, 'write_pipeline': True}})
    assert kb.save_memories()
    archive, operation = prepared(archive_lab)
    assert archive.mark_delivered(operation)
    assert archive.confirm(operation)['status'] == 'archived'
    restarted = type(kb)(kb.base_dir)
    from saya_app.services.qq_memory_management import QQMemoryManagement
    reopened = QQMemoryManagement(bridge, restarted).maintenance_archive(actor, 'test-topic')
    assert reopened.for_member('second')['status'] == 'archived'
    assert reopened.undo(operation)['status'] == 'undone'
    assert restarted.memory_allowed_for_use(restarted._find_memory('second'))


def test_qq_archive_delivery_original_id_and_undo(archive_lab):
    async def run():
        bridge, kb, service, _, _ = archive_lab
        bind_memory_responder(bridge, service)
        await bridge.start()
        try:
            rows = await run_command(bridge, '/memory archive first second', 301)
            preview_text = '\n'.join(row[1] for row in rows)
            assert '保留编号：first' in preview_text and '归档编号：second' in preview_text
            operation = re.search(r'/memory archive-confirm ([0-9a-f]{32})', preview_text)[1]
            for mid in (302, 303):
                rows = await run_command(bridge, '/memory archive-confirm ' + operation, mid)
                assert '已归档重复记忆' in rows[-1][1]
            for mid, member in [(304, 'first'), (305, 'second')]:
                rows = await run_command(bridge, '/memory archive-show ' + member, mid)
                assert '喜欢茉莉茶' in '\n'.join(row[1] for row in rows)
            rows = await run_command(bridge, '/memory archive-undo ' + operation, 306)
            assert '已撤销归档' in rows[-1][1]
            assert kb.memory_allowed_for_use(kb._find_memory('second'))
        finally:
            await bridge.stop()
    asyncio.run(run())
