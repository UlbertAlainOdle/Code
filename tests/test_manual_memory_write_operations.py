from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from core_services import KnowledgeBase
from saya_app.routers import memories
from saya_app.services.qq_rollback import memory_rollback_requirements


@pytest.fixture
def kb(tmp_path,monkeypatch):
    value=KnowledgeBase(str(tmp_path/'knowledge_base'))
    monkeypatch.setattr(memories,'kb_system',value)
    return value


def test_manual_text_is_literal_and_legacy_request_replay_survives_restart(kb,monkeypatch):
    request={'content':'记住上面两条','source':'manual','metadata':{'memory_layer':'episodic'}}
    first=memories.add_memory(request)
    assert first['memory']['content']=='记住上面两条'
    restored=KnowledgeBase(kb.base_dir);monkeypatch.setattr(memories,'kb_system',restored)
    with patch.object(restored,'add_memory',side_effect=AssertionError('duplicate')):second=memories.add_memory(request)
    assert first['memory_id']==second['memory_id'] and second['operation']['replayed']


def test_concurrent_manual_request_is_committed_once(kb):
    request={'content':'虚构手动并发测试。','operation_id':'a'*32}
    with patch.object(kb,'add_memory',wraps=kb.add_memory) as add,ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:memories.add_memory(request),range(2)))
    assert add.call_count==1 and results[0]['memory_id']==results[1]['memory_id']


def test_same_manual_number_cannot_adopt_another_body(kb):
    memories.add_memory({'content':'原手动正文。','operation_id':'a'*32})
    with pytest.raises(HTTPException) as error:memories.add_memory({'content':'替换正文。','operation_id':'a'*32})
    assert error.value.status_code==400 and len(kb.memories)==1


def test_unknown_manual_result_is_queryable_without_repeating_fact_write(kb):
    actual=kb.add_memory
    def fail(*args,**kwargs):actual(*args,**kwargs);raise OSError('after storage')
    request={'content':'查询不能暴露的手动正文。','operation_id':'a'*32}
    with patch.object(kb,'add_memory',fail):first=memories.add_memory(request)
    assert first['status']=='unknown' and len(kb.memories)==1
    with patch.object(kb,'add_memory',side_effect=AssertionError('duplicate')):again=memories.add_memory(request)
    query=memories.manual_memory_result(again['operation']['operation_id'])
    assert query['items']==[{'state':'unknown'}] and '查询不能暴露' not in str(query)


def test_replayed_manual_commit_does_not_show_or_restore_stale_content(kb):
    request={'content':'手动保存的旧正文。','operation_id':'a'*32}
    first=memories.add_memory(request)
    kb.update_memory(first['memory_id'],{'content':'其他地方编辑的新正文。'})
    again=memories.add_memory(request)
    assert again['status']=='success' and again['content_changed'] and again['memory'] is None
    assert kb._find_memory(first['memory_id'])['content']=='其他地方编辑的新正文。'


def test_manual_records_require_compatible_rollback_reader(kb):
    memories.add_memory({'content':'手动写入兼容测试。'})
    assert 'manual_write_operations_v1' in memory_rollback_requirements(Path(kb.base_dir).parent)
