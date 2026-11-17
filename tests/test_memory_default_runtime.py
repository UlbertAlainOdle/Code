"""Regression cases for the default production memory path, no paid calls."""
import json
from pathlib import Path
import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_rules import DEFAULT_FEATURES, recurring_candidate_block_reason
from saya_app.services.memory_v4 import MemoryQuery, MemoryRetriever
from saya_app.services.web_research.policy import should_recall


@pytest.mark.parametrize('state',['settled','missing_marker','retained_row','revoked'])
def test_legacy_sync_skips_only_settled_native_deletions(tmp_path,state):
    from saya_app.services.qq_bridge import QQStore
    from saya_app.services.qq_tasks import QQTaskRegistry,QQTaskGuard
    kb=KnowledgeBase(str(tmp_path/'kb'))
    memory=kb.capture_explicit_memory('记住：这是虚构同步验收材料，冰柜目录使用方形标签。')
    identifier=memory['id'];store=kb.memory_v4_store
    if state in {'settled','retained_row'}:
        assert store.tombstone_memory(identifier,purge=state=='settled')
    kb.deleted_memory_ids[identifier]={'reason':'user_delete'}
    tombstones=store.list_tombstones()
    registry=QQTaskRegistry(QQStore(tmp_path/'guard.sqlite3'))
    task=registry.register('sync','10000:private:20000:shared','a'*64)
    registry.claim(task['id'],task['scope']);guard=QQTaskGuard(registry,task['id'],task['scope'])
    guard.permission_check=lambda:state!='revoked'
    with guard.bind():result=kb._sync_memory_v4([])
    effects=registry.get(task['id'],task['scope'])['effects']
    if state=='revoked':
        assert result['status']=='error' and not effects
        assert store.get_memory(identifier) is not None and not store.is_tombstoned(identifier)
    else:
        assert result['status']=='success' and store.get_memory(identifier) is None and store.is_tombstoned(identifier)
        assert len(effects)==int(state!='settled')
        if state=='settled':assert store.list_tombstones()==tombstones


def test_default_upgrade_enables_core_features_even_from_legacy_settings(tmp_path):
    (tmp_path / 'memory_settings.json').write_text(json.dumps({key: False for key in DEFAULT_FEATURES}))
    kb = KnowledgeBase(str(tmp_path))
    assert all(kb.settings[key] for key in DEFAULT_FEATURES)
    assert kb.get_memory_v4_status()['status'] == 'ready'
    assert kb.embedding_provider.device == 'cpu'


def test_independent_preferences_and_goals_survive_plain_text_saves(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    for text in ['我喜欢乌鸦。', '我喜欢咖啡。', '我计划学习日语。', '我计划锻炼身体。']:
        assert kb.capture_explicit_memory('记住：' + text)
    assert len(kb.memories) == 4
    assert len(kb.memory_v4_store.list_memories()) == 4
    kb.save_memories()
    again = KnowledgeBase(str(tmp_path))
    assert len(again.memory_v4_store.list_memories()) == 4
    assert all(item.metadata.get('write_pipeline') for item in again.memory_v4_store.list_memories())


def test_default_read_retrieves_personal_facts_with_provenance(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    saved = kb.capture_explicit_memory('记住：我最喜欢的动物是乌鸦。')
    result = kb.build_memory_v4_context_with_audit('还记得我最喜欢的动物吗？')
    assert '乌鸦' in result['context']
    assert saved['id'] in {item['id'] for item in result['audit']['selected']}
    assert kb.memory_v4_store.list_evidence(saved['id'])
    assert 'prompt' not in result['audit']


def test_no_cross_project_or_assistant_role_leakage(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    a = kb.capture_explicit_memory('记住：Lantern 项目的代码名是蓝鸦。', project_id='a')
    b = kb.capture_explicit_memory('记住：Lantern 项目的代码名是金狼。', project_id='b')
    global_ids = {i['id'] for i in kb.query_memory_v4('Lantern', bypass_need_planner=True)['items']}
    assert not {a['id'], b['id']} & global_ids
    scoped = kb.query_memory_v4('Lantern', scope_type='project', scope_id='a', bypass_need_planner=True)
    assert a['id'] in {i['id'] for i in scoped['items']}
    assert b['id'] not in {i['id'] for i in scoped['items']}
    private = kb.add_memory('Lantern assistant-only note', source='assistant_role')
    assert private['id'] not in {i['id'] for i in kb.query_memory_v4('Lantern', bypass_need_planner=True)['items']}


def test_legacy_correction_needs_same_topic_and_scope(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    old = kb.add_memory('我喜欢咖啡。', category='preference')
    other = kb.add_memory('我喜欢乌鸦。', category='preference')
    kb.add_memory('现在我喜欢篮球。', category='preference')
    assert not old.get('superseded_by') and not other.get('superseded_by')
    kb.add_memory('我不再喜欢咖啡。', category='preference', metadata={'project_id':'different'})
    assert not old.get('superseded_by')
    change = kb.add_memory('我不再喜欢咖啡，改喝茶。', category='preference')
    assert old['superseded_by'] == change['id']
    assert not other.get('superseded_by')


def test_ambiguous_correction_does_not_write_or_replace(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    kb.capture_explicit_memory('记住：我喜欢咖啡。')
    result = kb.process_memory_write_v4('更正：我喜欢乌鸦。')
    assert result['status'] == 'rejected'
    assert len(kb.memory_v4_store.list_memories()) == 1


def test_explicit_negative_updates_only_the_grounded_preference(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    coffee = kb.capture_explicit_memory('记住：我喜欢咖啡。')
    bird = kb.capture_explicit_memory('记住：我喜欢乌鸦。')
    changed = kb.capture_explicit_memory('记住：我不再喜欢咖啡。')
    assert changed['id'] == coffee['id']
    assert kb.memory_v4_store.get_memory(coffee['id']).revision > 1
    assert '不再' in kb.memory_v4_store.get_memory(coffee['id']).canonical_text
    assert '乌鸦' in kb.memory_v4_store.get_memory(bird['id']).canonical_text
    assert len(kb.memory_v4_store.list_memories()) == 2
    kb.capture_explicit_memory('记住：我的朋友不再喜欢咖啡。')
    assert kb.memory_v4_store.get_memory(coffee['id']).canonical_text == '我不再喜欢咖啡。'


def test_emotional_reference_can_recall_but_privacy_off_wins():
    assert should_recall('auto', '我很难过，你还记得上次考试的事吗？')
    assert not should_recall('off', '我很难过，你还记得上次考试的事吗？')
    assert not should_recall('auto', '你好啊')


def test_repetition_without_assertion_evidence_never_promotes(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    for session in ('one', 'two', 'three', 'four'):
        kb.add_memory_candidate('用户可能喜欢冰咖啡。', confidence=.95, importance_score=.9, session_id=session)
    assert not kb.maybe_auto_confirm_recurring_candidates()
    assert not kb.memories
    assert recurring_candidate_block_reason({'content':'用户喜欢咖啡。'}) == 'user_assertion_evidence_required'


def test_expired_and_superseded_legacy_records_are_not_default_reads(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    expired = kb.add_memory('Lantern planned visit', metadata={'expires_at':'2020-01-01'})
    old = kb.add_memory('我喜欢 Lantern 咖啡。', category='preference')
    kb.add_memory('我不再喜欢 Lantern 咖啡。', category='preference')
    result = kb.query_memory_v4('Lantern', bypass_need_planner=True)
    assert not {expired['id'], old['id']} & {i['id'] for i in result['items']}


def test_do_not_mention_and_structured_fields_survive_legacy_save(tmp_path):
    kb = KnowledgeBase(str(tmp_path))
    saved = kb.capture_explicit_memory('记住：我喜欢喝冰咖啡。')
    before = kb.memory_v4_store.get_memory(saved['id'])
    kb.memory_v4_control('do_not_mention', memory_id=saved['id'], enabled=True)
    kb.save_memories()
    after = kb.memory_v4_store.get_memory(saved['id'])
    assert after.predicate == before.predicate
    assert after.metadata['do_not_mention']
    assert not kb.query_memory_v4('咖啡', bypass_need_planner=True)['items']
