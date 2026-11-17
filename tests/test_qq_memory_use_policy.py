import json
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.memory_versions import record_version
from saya_app.services.memory_v4 import MemoryRetriever, MemoryItemV4, MemoryEvidence


@pytest.fixture
def kb(tmp_path):
    return KnowledgeBase(str(tmp_path/'kb'))


def summary(kb, source):
    return kb.add_memory('摘要记录：偏好青色。', title='来源摘要', source='session_archive',
        metadata={'compressed_from':[source['id']], 'category':'conversation_summary'})


def test_summary_is_invalid_after_source_edit_without_destroying_original(kb):
    parent=kb.add_memory('偏好青色。', title='偏好',source='manual');child=summary(kb,parent)
    assert kb.memory_allowed_for_use(child)
    kb.update_memory(parent['id'],{'content':'改为偏好紫色。'})
    assert not kb.memory_allowed_for_use(child)
    assert kb.format_memory_for_prompt(child)==''
    assert child['id'] not in {x['id'] for x in kb.search_memories('青色')}
    assert kb._find_memory(child['id']) is not None


def test_deleted_parent_cannot_be_hidden_by_relationship_cleanup(kb):
    parent=kb.add_memory('偏好青色。',source='manual');child=summary(kb,parent)
    p=kb.preview_memory_deletion([parent['id']],reason='user_delete')
    r=kb.delete_memories([parent['id']],reason='user_delete',confirmation_token=p['confirmation_token'])
    assert r['status']=='success'
    assert not kb.memory_allowed_for_use(kb._find_memory(child['id']))
    assert not KnowledgeBase(kb.base_dir).memory_allowed_for_use(kb._find_memory(child['id']))


def test_unknown_old_summary_closes_after_edit_but_independent_fact_survives(kb):
    original=kb.add_memory('原始独立事实。',source='manual')
    old={'id':'old-summary','title':'旧摘要','content':'没有可靠来源的派生内容','source':'session_archive','category':'conversation_summary'}
    kb.memories.append(old)
    assert kb.memory_allowed_for_use(old)
    kb.update_memory(original['id'],{'content':'更新原始独立事实。'})
    assert not kb.memory_allowed_for_use(old)
    assert kb.memory_allowed_for_use(kb._find_memory(original['id']))


def test_dependency_archive_and_access_counters_do_not_invalidate_summary(kb):
    parent=kb.add_memory('偏好青色。',source='manual');child=summary(kb,parent)
    kb.update_memory(parent['id'],{'is_archived':True})
    parent=kb._find_memory(parent['id']);parent['access_count']=15
    assert kb.memory_allowed_for_use(child)


def test_do_not_use_is_durable_and_cannot_be_undone_by_generated_metadata(kb):
    parent=kb.add_memory('应当停止使用的偏好。',source='manual');child=summary(kb,parent)
    result=kb.set_memory_use_policy(parent['id'],True,expected_version=record_version(parent))
    assert result['policy_applied'] is True
    assert not kb.memory_allowed_for_use(parent) and not kb.memory_allowed_for_use(child)
    parent['do_not_mention']=False
    assert not kb.memory_allowed_for_use(parent)
    again=KnowledgeBase(kb.base_dir)
    assert not again.memory_allowed_for_use(again._find_memory(parent['id']))
    assert parent['id'] not in {x['id'] for x in kb.get_relevant_memories('偏好',mode='deep',limit=50)}


def test_do_not_use_starts_new_history_projection_and_keeps_files(kb,tmp_path):
    m=kb.add_memory('私人偏好。',source='manual');manager=MemoryManager(str(tmp_path/'chats'))
    session='session_fixture.jsonl';manager.save_message('user','旧用户原文',session_id=session)
    path=manager._safe_session_path(session);before=open(path,'rb').read()
    kb.set_memory_use_policy(m['id'],True,expected_version=record_version(m))
    entries=manager.load_session_full(session,make_current=False)
    assert kb.memory_use.project_entries(entries)==[]
    with kb.memory_use.bind():manager.save_message('user','新话题内容',session_id=session)
    rows=kb.memory_use.project_entries(manager.load_session_full(session,make_current=False))
    assert [x['content'] for x in rows]==['新话题内容']
    assert open(path,'rb').read().startswith(before)


def test_direct_v4_full_read_enforces_do_not_mention_even_without_query(kb):
    item=MemoryItemV4(id='fixture-hidden',canonical_text='不能被工具读出的内容',metadata={'do_not_mention':True})
    store=kb.memory_v4_store
    store.upsert_memory(item,[MemoryEvidence(id='e1',memory_id=item.id,source_type='user',source_id='s1',quote='fixture')])
    assert MemoryRetriever(store).fetch_full([item.id])==[]


def test_actual_v4_tool_read_and_query_exclude_invalidated_summary(kb):
    parent=kb.add_memory('偏好青色。',source='manual');child=summary(kb,parent)
    kb.update_memory(parent['id'],{'content':'改为紫色。'})
    registry=kb._memory_v4_capability_registry(allow_legacy_memory=True)
    read=registry.get('memory.read').handler({'memory_ids':[child['id']]})
    query=registry.get('memory.query').handler({'query':'青色'})
    assert read['items']==[]
    assert child['id'] not in {x['id'] for x in query.get('items',[])}


def test_policy_failure_is_not_a_false_success_and_readonly_is_rechecked(kb):
    m=kb.add_memory('只读事实',source='manual')
    def deny():raise PermissionError('readonly')
    with pytest.raises(PermissionError):kb.set_memory_use_policy(m['id'],True,write_check=deny)
    assert kb.memory_allowed_for_use(m)
    with patch.object(kb.memory_use,'set_block',side_effect=OSError('fixture failure')):
        with pytest.raises(OSError):kb.set_memory_use_policy(m['id'],True)
    assert kb.memory_allowed_for_use(m)


def test_restore_does_not_reintroduce_excluded_old_history(kb,tmp_path):
    m=kb.add_memory('可恢复的事实',source='manual');manager=MemoryManager(str(tmp_path/'chats'))
    session='session_fixture.jsonl';manager.save_message('user','停用前的原文',session_id=session)
    kb.set_memory_use_policy(m['id'],True)
    old_floor=kb.memory_use.history_floor()
    result=kb.set_memory_use_policy(m['id'],False)
    assert result['policy_applied'] and kb.memory_allowed_for_use(kb._find_memory(m['id']))
    assert kb.memory_use.history_floor()==old_floor
    assert kb.memory_use.project_entries(manager.load_session_full(session,make_current=False))==[]


def test_synthesis_cache_and_evidence_do_not_revive_blocked_source(kb):
    from saya_app.services.memory_v4 import BackgroundSynthesizer
    from saya_app.services.memory_v4.models import ValidationError
    m=kb.add_memory('不能继续使用的依据',source='manual')
    store=kb.memory_v4_store;evidence=store.list_evidence(m['id'])[0]
    synth=BackgroundSynthesizer(store,permitted=kb.memory_allowed_for_use,use_epoch=kb.memory_use.snapshot)
    patch_value={'operations':[{'op':'replace','path':'/fixture','value':'原有派生缓存','evidence_refs':[evidence.id]}]}
    assert synth.apply_patch(patch_value)['status']=='success'
    assert synth.current()
    kb.set_memory_use_policy(m['id'],True)
    assert synth.current()=={}
    with pytest.raises(ValidationError):synth.apply_patch(patch_value)


def test_legacy_epoch_upgrades_without_losing_policies_and_closes_v1_reader(kb):
    from saya_app.services.memory_use import MemoryUseState
    old_epoch='a'*32
    kb.memory_use.path.write_text(json.dumps({'version':1,'epoch':old_epoch,'changed':1}),encoding='utf-8')
    assert MemoryUseState(kb.base_dir).snapshot()==old_epoch
    m=kb.add_memory('版本升级事实',source='manual');kb.set_memory_use_policy(m['id'],True)
    data=json.loads(kb.memory_use.path.read_text(encoding='utf-8'))
    assert data['version']==2 and data['policies'][m['id']] is True
    assert data['history_floor']
    assert MemoryUseState(kb.base_dir).snapshot()==data['epoch']
    # The previous reader's strict version==1 boundary rejects this schema;
    # it cannot accept a v1 epoch while ignoring the new privacy directives.
    assert data['version']!=1
