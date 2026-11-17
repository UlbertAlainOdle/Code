"""Native transaction/version contracts, isolated stores and no QQ receipts."""
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_health import native_version
from saya_app.services.memory_v4 import MemoryV4Store, WritePipeline, DeterministicValidator
from saya_app.services.memory_versions import MemoryVersionConflict


@pytest.fixture
def native(tmp_path):
    store = MemoryV4Store(tmp_path / 'native.sqlite3')
    result = WritePipeline(store).process('记住：虚构茶杯偏好蓝色。', action={'predicate': 'cup'})
    item = store.get_memory(result['committed'][0]['memory']['id'])
    return store, item


@pytest.mark.parametrize('forget', [False, True])
def test_explicit_old_version_rejects_correction_and_forget(native, forget):
    store, item = native
    expected = native_version(item, False)
    store.update_memory(item.id, {'canonical_text': '另一条已提交的更新。'})
    before = store.get_memory(item.id).to_dict()
    response = WritePipeline(store).process('更正指定虚构茶杯。' if not forget else '忘记指定虚构茶杯。',
        action={'intent': 'explicit_forget' if forget else 'explicit_correct', 'memory_id': item.id,
                'predicate': 'cup', 'content': '虚构茶杯偏好绿色。'}, expected_versions={item.id: expected})
    assert response['status'] == 'rejected' and not response['committed']
    assert store.get_memory(item.id).to_dict() == before and not store.is_tombstoned(item.id)


@pytest.mark.parametrize('delete', [False, True])
def test_independent_store_change_during_validation_cannot_be_overwritten(native, delete):
    store, item = native
    other = MemoryV4Store(store.path)
    class WaitingValidator(DeterministicValidator):
        def validate(self, *args, **kwargs):
            if delete:
                other.tombstone_memory(item.id)
            else:
                other.update_memory(item.id, {'canonical_text': '并发提交保留的紫色杯子。'})
            return super().validate(*args, **kwargs)
    result = WritePipeline(store, validator=WaitingValidator()).process('更正虚构茶杯为绿色。',
        action={'operation': 'correct', 'memory_id': item.id, 'predicate': 'cup', 'content': '虚构茶杯偏好绿色。'})
    assert result['status'] == 'rejected' and not result['committed']
    assert store.is_tombstoned(item.id) if delete else '紫色' in store.get_memory(item.id).canonical_text


def test_new_target_created_after_snapshot_is_not_updated_by_old_create_plan(tmp_path):
    store = MemoryV4Store(tmp_path / 'native.sqlite3')
    other = MemoryV4Store(store.path)
    class WaitingValidator(DeterministicValidator):
        def validate(self, *args, **kwargs):
            WritePipeline(other).process('记住：虚构杯子已经是紫色。', action={'predicate': 'cup'})
            return super().validate(*args, **kwargs)
    result = WritePipeline(store, validator=WaitingValidator()).process('记住：虚构杯子是绿色。', action={'predicate': 'cup'})
    assert result['status'] == 'rejected' and not result['committed']
    assert '紫色' in store.list_memories()[0].canonical_text


def test_checked_native_transaction_rolls_back_nested_delete_versions_and_event(native):
    store, item = native
    before = item.to_dict()
    versions = len(store.list_versions(item.id))
    with pytest.raises(RuntimeError, match='after_nested_write'):
        with store.checked_write({item.id: native_version(item, False)}, lambda: None):
            store.tombstone_memory(item.id)
            raise RuntimeError('after_nested_write')
    assert store.get_memory(item.id).to_dict() == before and not store.is_tombstoned(item.id)
    assert len(store.list_versions(item.id)) == versions
    assert store._write_context.connection is None


def test_transaction_checks_version_again_after_authorization_callback(native):
    store, item = native
    def authorize():
        store.update_memory(item.id, {'canonical_text': '回调中未提交的更改。'})
    with pytest.raises(MemoryVersionConflict):
        with store.checked_write({item.id: native_version(item, False)}, authorize):
            pytest.fail('stale plan entered mutation')
    assert store.get_memory(item.id).to_dict() == item.to_dict()


@pytest.mark.parametrize('change', ['epoch', 'setting'])
def test_kb_source_change_after_validation_rejects_write(tmp_path, change):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    original = DeterministicValidator.validate
    def validate(self, *args, **kwargs):
        if change == 'epoch':
            kb.memory_use.advance()
        else:
            kb.settings['memory_v4_write_enabled'] = False
        return original(self, *args, **kwargs)
    with patch.object(DeterministicValidator, 'validate', validate):
        result = kb.process_memory_write_v4('记住：虚构灯塔使用绿色光。', message_id='changed-source')
    assert result['operation_state'] == 'not_saved' and result['status'] == 'rejected'
    assert not kb.memories and not kb.memory_v4_store.list_memories()


def test_partial_native_result_and_replay_keep_rejections_without_private_body(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    result = kb.process_memory_write_v4('记住：虚构茶杯是绿色；虚构灯塔是蓝色。', message_id='partial',
        action={'proposals': [{'canonical_text': '虚构茶杯是绿色。', 'predicate': 'cup'},
                              {'canonical_text': '如果虚构灯塔变蓝。', 'predicate': 'lighthouse'}]})
    assert result['status'] == result['operation_state'] == 'partial' and len(result['committed']) == 1
    with patch.object(kb, '_process_memory_write_v4_impl', side_effect=AssertionError('must not repeat')):
        replay = kb.process_memory_write_v4('记住：虚构茶杯是绿色；虚构灯塔是蓝色。', message_id='partial',
            action={'proposals': [{'canonical_text': '虚构茶杯是绿色。', 'predicate': 'cup'},
                                  {'canonical_text': '如果虚构灯塔变蓝。', 'predicate': 'lighthouse'}]})
    assert replay['operation_state'] == 'partial' and replay['replayed'] and replay['rejected']
    assert '如果虚构灯塔变蓝' not in str(replay['rejected'])
    assert replay['target_versions'] == result['target_versions']


def test_locked_and_managed_native_target_rejected_by_kb(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    item = kb.capture_explicit_memory('记住：虚构杯子使用红色。')
    kb.memory_v4_store.update_memory(item['id'], {'locked': True})
    result = kb.process_memory_write_v4('忘掉指定条目', message_id='locked',
        action={'intent': 'explicit_forget', 'memory_id': item['id']})
    assert result['status'] == 'rejected' and not kb.memory_v4_store.is_tombstoned(item['id'])
    item['managed_slot'] = 'synthetic_feature'
    result = kb.process_memory_write_v4('更正虚构杯子为蓝色。', message_id='managed',
        action={'intent': 'explicit_correct', 'memory_id': item['id'], 'content': '虚构杯子使用蓝色。'})
    assert result['status'] == 'rejected' and '红色' in kb.memory_v4_store.get_memory(item['id']).canonical_text


def test_replay_with_original_explicit_versions_does_not_revalidate_or_mutate(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    item = kb.capture_explicit_memory('记住：虚构茶杯偏好蓝色。')
    expected = native_version(kb.memory_v4_store.get_memory(item['id']), False)
    opts = {'message_id': 'versioned-correction', 'expected_versions': {item['id']: expected},
            'action': {'intent': 'explicit_correct', 'memory_id': item['id'], 'content': '虚构茶杯偏好绿色。'}}
    first = kb.process_memory_write_v4('更正虚构茶杯。', **opts)
    assert first['operation_state'] == 'committed'
    with patch.object(kb, '_process_memory_write_v4_impl', side_effect=AssertionError('must not repeat')):
        replay = kb.process_memory_write_v4('更正虚构茶杯。', **opts)
    assert replay['replayed'] and replay['target_versions'] == {item['id']: expected}


@pytest.mark.parametrize('refs', [['tool:invented'], ['assistant_context:a']])
def test_structured_evidence_refs_cannot_invent_source_or_adopt_assistant_only(tmp_path, refs):
    store = MemoryV4Store(tmp_path / 'native.sqlite3')
    result = WritePipeline(store).process('记住：虚构杯子是蓝色。', message_id='current',
        context=[{'id': 'u', 'role': 'user', 'content': '虚构旧主题。'},
                 {'id': 'a', 'role': 'assistant', 'content': '虚构助手解释。'}],
        action={'proposal': {'canonical_text': '虚构杯子是蓝色。', 'evidence_refs': refs}})
    assert result['status'] == 'rejected' and not store.list_memories()


def test_only_referenced_evidence_is_committed(tmp_path):
    store = MemoryV4Store(tmp_path / 'native.sqlite3')
    result = WritePipeline(store).process('记住：虚构杯子是蓝色。', message_id='current',
        context=[{'id': 'u', 'role': 'user', 'content': '无关的旧主题。'},
                 {'id': 'a', 'role': 'assistant', 'content': '无关的助手解释。'}],
        action={'proposal': {'canonical_text': '虚构杯子是蓝色。', 'evidence_refs': ['user_message:current']}})
    item = result['committed'][0]['memory']
    assert [e.source_id for e in store.list_evidence(item['id'])] == ['current']


@pytest.mark.parametrize('targeted', [False, True])
def test_native_forget_preserves_other_subject_and_project(tmp_path, targeted):
    store = MemoryV4Store(tmp_path / 'native.sqlite3')
    pipeline = WritePipeline(store)
    items = []
    for scope in ({}, {'scope_type': 'project', 'scope_id': 'a'}, {'scope_type': 'project', 'scope_id': 'b'}, {'subject': 'friend'}):
        result = pipeline.process('记住：虚构范围测试的茶杯。', action=scope)
        items.append(result['committed'][0]['memory']['id'])
    action = {'intent': 'explicit_forget', 'scope_type': 'project', 'scope_id': 'a',
              'content': '虚构范围测试的茶杯'}
    if targeted:
        action['memory_id'] = items[2]
    result = pipeline.process('忘掉虚构范围测试的茶杯', action=action)
    assert result['forgotten'] == ([] if targeted else [items[1]])
    assert all(not store.is_tombstoned(mid) for mid in [items[0], items[2], items[3]])


def test_kb_project_forget_uses_original_scoped_capture_path(tmp_path):
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    global_item = kb.capture_explicit_memory('记住：虚构茶杯的范围标识。')
    project_item = kb.capture_explicit_memory('记住：虚构茶杯的范围标识。', project_id='a')
    kb.capture_explicit_memory_result('忘掉虚构茶杯的范围标识', project_id='a')
    assert kb.memory_v4_store.is_tombstoned(project_item['id'])
    assert not kb.memory_v4_store.is_tombstoned(global_item['id'])


def test_authority_revoked_during_validation_prevents_native_save(tmp_path):
    from saya_app.services.memory_write_authority import bind_write_authority
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    state = {'allowed': True}
    original = DeterministicValidator.validate
    def validate(self, *args, **kwargs):
        state['allowed'] = False
        return original(self, *args, **kwargs)
    with bind_write_authority(lambda: state['allowed']), patch.object(DeterministicValidator, 'validate', validate):
        response = kb.process_memory_write_v4('记住：虚构茶杯是白色。', message_id='permission-change')
    assert response['status'] == 'rejected' and not kb.memories


def test_copied_authority_cannot_authorize_a_write_after_ingress_has_exited(tmp_path):
    from contextvars import copy_context
    from saya_app.services.memory_write_authority import bind_write_authority
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    with bind_write_authority(lambda: True):
        context = copy_context()
    response = context.run(kb.process_memory_write_v4, '记住：虚构茶杯是白色。', message_id='expired-authority')
    assert response['status'] == 'rejected' and not kb.memories


@pytest.mark.parametrize('revoked', [False, True])
def test_actual_responder_binds_current_native_write_permission(tmp_path, revoked):
    import asyncio
    from saya_app.services.qq_bridge import QQMessage
    from saya_app.services.qq_chat import QQChatResponder
    from test_qq_delivery import config
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    responder = QQChatResponder(config(shared_memory=True), None, None, lambda: 'api', lambda: {})
    async def respond(message, history, session):
        if revoked:
            responder.settings = config(shared_memory=True, memory_write_enabled=False)
        return (await asyncio.to_thread(kb.process_memory_write_v4, message.text, message_id='bound-responder'))['status']
    responder._respond = respond
    result = asyncio.run(responder(QQMessage('event', '10000:private:20000', '10000', '20000', '',
        'message', '记住：虚构茶杯是银色。', True), [], 'synthetic-session'))
    assert result == ('rejected' if revoked else 'success')
    assert bool(kb.memories) is not revoked


def test_retained_native_plan_requires_compatible_rollback_reader(tmp_path):
    from saya_app.services.qq_rollback import memory_rollback_requirements
    kb = KnowledgeBase(str(tmp_path / 'knowledge_base'))
    kb.process_memory_write_v4('记住：虚构清单测试茶杯。', message_id='rollback-proof')
    assert 'native_write_plan_versions_v1' in memory_rollback_requirements(tmp_path)


def test_archived_explicit_target_is_not_silently_redirected(native):
    store, item = native
    store.update_memory(item.id, {'lifecycle': 'archived'})
    before = store.get_memory(item.id).to_dict()
    result = WritePipeline(store).process('更正虚构茶杯为绿色。',
        action={'operation': 'correct', 'memory_id': item.id, 'predicate': 'cup', 'content': '虚构茶杯是绿色。'})
    assert result['status'] == 'rejected' and not result['committed']
    assert store.get_memory(item.id).to_dict() == before


def test_repeated_tombstone_preserves_original_time_reason_and_actor_but_can_purge(native):
    store, item = native
    with patch('saya_app.services.memory_v4.store._now', return_value='2026-11-05T12:00:00'):
        store.tombstone_memory(item.id, reason='original_reason', actor='original_actor')
    before = store.list_tombstones()
    with patch('saya_app.services.memory_v4.store._now', return_value='2026-11-06T12:00:00'):
        store.tombstone_memory(item.id, reason='startup_replay', actor='system', purge=True)
    assert store.list_tombstones() == before
    assert store.get_memory(item.id) is None and store.is_tombstoned(item.id)
