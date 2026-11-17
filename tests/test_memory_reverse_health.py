from copy import deepcopy
from pathlib import Path
import json

import pytest
from core_services import KnowledgeBase
from saya_app.services.memory_health import reverse_inspect
from saya_app.services.memory_versions import MemoryVersionConflict
from saya_app.services.memory_v4 import MemoryItemV4,MemoryEvidence


@pytest.fixture
def kb(tmp_path):return KnowledgeBase(str(tmp_path/'kb'))


def test_reverse_keyset_paging_detects_extra_rows_without_deleting_or_changing_facts(kb):
    items=[kb.add_memory('synthetic current '+str(i)) for i in range(3)]
    extra={**items[0],'id':'z-orphan','content':'PRIVATE_ORPHAN_CANARY'}
    kb.sqlite_store.upsert_memories([extra])
    before=deepcopy(kb.memories);files={p:p.read_bytes() for p in Path(kb.base_dir).glob('*.json')}
    cursor='';version=None;issues=[];checked=0
    for _ in range(5):
        result=reverse_inspect(kb,limit=2,after_id=cursor,expected_version=version)
        version=result['inventory_version'];issues.extend(result['issues']);checked+=result['checked']
        if result['complete']:break
        cursor=result['next_after_id']
    assert checked==4 and issues==[{'id':'z-orphan','code':'legacy_without_JSON_owner'}]
    assert kb.memories==before and all(p.read_bytes()==value for p,value in files.items())
    assert 'PRIVATE_ORPHAN_CANARY' not in json.dumps(result)
    lexical=reverse_inspect(kb,source='lexical')
    assert lexical['issues']==[{'id':'z-orphan','code':'lexical_without_JSON_owner'}]


def test_pending_native_and_recoverable_deleted_rows_are_not_garbage(kb):
    item=MemoryItemV4(id='native-only',canonical_text='synthetic pending native')
    kb.memory_v4_store.upsert_memory(item,[MemoryEvidence(id='e-native',memory_id=item.id,quote=item.canonical_text,source_type='user_message',source_id='test')])
    kb._mark_v4_projection(item.id,True)
    result=reverse_inspect(kb,source='native')
    assert not result['issues'] and result['expected_non_active']['pending_projection']==1
    saved=kb.add_memory('synthetic deleted');kb.delete_memory(saved['id'])
    legacy=reverse_inspect(kb)
    assert not legacy['issues'] and legacy['expected_non_active'].get('deleted_legacy_row',0)>=1


def test_json_and_derived_changes_invalidate_paging_snapshot(kb):
    item=kb.add_memory('synthetic base')
    version=reverse_inspect(kb)['inventory_version']
    kb.update_memory(item['id'],{'content':'changed'})
    with pytest.raises(MemoryVersionConflict):reverse_inspect(kb,expected_version=version)
    version=reverse_inspect(kb)['inventory_version']
    kb.sqlite_store.upsert_memories([{**item,'id':'derivative-only'}])
    with pytest.raises(MemoryVersionConflict):reverse_inspect(kb,expected_version=version)


def test_native_without_projection_and_orphan_vectors_report_actual_landing(kb):
    item=MemoryItemV4(id='native-only',canonical_text='synthetic native')
    kb.memory_v4_store.upsert_memory(item,[MemoryEvidence(id='e',memory_id=item.id,quote=item.canonical_text,source_type='user_message',source_id='test')])
    assert reverse_inspect(kb,source='native')['issues']==[{'id':item.id,'code':'native_without_JSON_projection'}]
    record=kb.add_memory('synthetic vectors');extra={**record,'id':'orphan-vector'};kb.sqlite_store.upsert_memories([extra])
    with kb.sqlite_store._connection() as db:db.execute('insert into memory_embeddings values(?,?,?,?,?,?)',(extra['id'],'fixture',1,b'1234','hash','2026-01-01'))
    assert reverse_inspect(kb,source='vectors')['issues']==[{'id':extra['id'],'code':'vectors_without_JSON_owner'}]
