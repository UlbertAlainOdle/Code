from pathlib import Path
import json
from unittest.mock import patch
import pytest
from core_services import KnowledgeBase
from saya_app.services.memory_versions import record_version


class Provider:
    key='isolated-fixture'
    def __init__(self, change=None):self.calls=[];self.change=change
    def status(self):return {'ready':True}
    def embed(self,texts):
        self.calls.append(list(texts))
        if self.change:
            change,self.change=self.change,None
            change()
        return [[1.0,0.0] for _ in texts]


@pytest.fixture
def kb(tmp_path):
    result=KnowledgeBase(str(tmp_path/'kb'))
    result.settings.update(embedding_enabled=True,embedding_auto_index_new=False,memory_capacity_auto_compact_enabled=False,
        memory_compression_min_group=2,memory_ai_compression_enabled=True)
    return result


@pytest.mark.parametrize('mode',['build','new'])
def test_stopped_memory_never_enters_embedding_request(kb,mode):
    m=kb.add_memory('BLOCKED_EMBEDDING_CANARY',source='manual')
    kb.set_memory_use_policy(m['id'],True)
    provider=Provider();kb.embedding_provider=provider
    if mode=='build':result=kb.build_embedding_index(force=True)
    else:
        kb.settings['embedding_auto_index_new']=True
        result=kb.maybe_index_new_memories([m])
    assert not provider.calls
    assert result['indexed_count']==0
    assert not kb.sqlite_store.embedding_hashes(provider.key)


@pytest.mark.parametrize('mode',['build','new'])
def test_embedding_result_after_policy_change_is_not_committed(kb,mode):
    m=kb.add_memory('embedding source changes while request runs',source='manual')
    provider=Provider(lambda:kb.set_memory_use_policy(m['id'],True));kb.embedding_provider=provider
    with patch.object(kb.sqlite_store,'upsert_embeddings',wraps=kb.sqlite_store.upsert_embeddings) as commit:
        if mode=='build':result=kb.build_embedding_index(force=True)
        else:
            kb.settings['embedding_auto_index_new']=True
            result=kb.maybe_index_new_memories([m])
    assert provider.calls and commit.call_count==0
    assert result['status']=='superseded'
    assert not kb.sqlite_store.embedding_hashes(provider.key)


def source(kb,index):
    return kb.add_memory(f'Orion batch {index} selected {10+index} distinct search candidates.',title='Orion search decision',
        source='manual',category='project',tags=['orion'],
        metadata={'memory_layer':'semantic','retention_policy':'compressible','date':'2020-01-01'})


def test_stopped_sources_not_in_ai_prompt_or_deterministic_summary(kb):
    a,b=source(kb,1),source(kb,2)
    kb.set_memory_use_policy(a['id'],True)
    with patch.object(kb,'_compression_topic_key',return_value='isolated-topic'):
        preview=kb.build_ai_compression_preview(scope='oldest',limit=10)
        merged=kb.merge_memories(days=0,force=True)
    assert preview['group_count']==0
    assert merged.get('merged_count',0)==0
    assert not kb._find_memory(a['id'])['is_archived']


def test_prepared_ai_plan_becomes_invalid_when_source_is_stopped(kb):
    a,b=source(kb,1),source(kb,2)
    with patch.object(kb,'_compression_topic_key',return_value='isolated-topic'):
        preview=kb.build_ai_compression_preview(scope='oldest',limit=10)
    assert preview['group_count']==1
    plan=kb.get_ai_compression_plan(preview['plan_id'])
    kb.set_memory_use_policy(a['id'],True)
    with pytest.raises(ValueError):kb.get_ai_compression_plan(preview['plan_id'])
    with pytest.raises(ValueError):kb.apply_ai_compression_plan(preview['plan_id'],[
        {'group_id':plan['groups'][0]['group_id'],'summary':'A stale model summary that must never be persisted.'}])
    assert len(kb.memories)==2


def test_deleted_legacy_source_does_not_reimport_after_full_restart(kb):
    path=Path(kb.base_dir)/'long_term_memory.json'
    path.write_text(json.dumps([{'id':'legacy-origin','content':'LEGACY_DELETE_CANARY','timestamp':'2020-01-01'}]),encoding='utf-8')
    kb.import_legacy_memories()
    m=next(m for m in kb.memories if m.get('legacy_ref'))
    preview=kb.preview_memory_deletion([m['id']])
    assert kb.delete_memories([m['id']],reason='user_delete',confirmation_token=preview['confirmation_token'])['status']=='success'
    kb.import_legacy_memories()
    assert kb._find_memory(m['id']) is None
    restored=KnowledgeBase(kb.base_dir)
    assert restored._find_memory(m['id']) is None
    assert not any('LEGACY_DELETE_CANARY' in m['content'] for m in restored.memories)
    assert m['id'] in restored.deleted_memory_ids


def test_valid_embedding_and_compression_are_still_available(kb):
    a,b=source(kb,1),source(kb,2)
    provider=Provider();kb.embedding_provider=provider
    assert kb.build_embedding_index(force=True)['indexed_count']==2
    with patch.object(kb,'_compression_topic_key',return_value='isolated-topic'):
        preview=kb.build_ai_compression_preview(scope='oldest',limit=10)
    assert preview['group_count']==1
