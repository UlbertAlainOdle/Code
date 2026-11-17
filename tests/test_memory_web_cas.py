import pytest
from fastapi import HTTPException
from core_services import KnowledgeBase
from saya_app.routers import memories
from saya_app.services.memory_versions import record_version


@pytest.fixture
def kb(tmp_path, monkeypatch):
    result=KnowledgeBase(str(tmp_path/'kb'))
    monkeypatch.setattr(memories,'kb_system',result)
    return result


def test_web_update_returns_locked_committed_version_and_old_draft_conflicts(kb):
    m=kb.add_memory('original', source='manual')
    old=record_version(m)
    response=memories.update_memory({'id':m['id'],'expected_version':old,'updates':{'content':'new'}})
    assert response['content_version']==record_version(kb._find_memory(m['id']))
    assert response['content_version']!=old
    with pytest.raises(HTTPException) as error:
        memories.update_memory({'id':m['id'],'expected_version':old,'updates':{'content':'stale'}})
    assert error.value.status_code==409
    assert kb._find_memory(m['id'])['content']=='new'


def test_bulk_preview_and_confirm_recheck_every_selected_version(kb):
    a,b=[kb.add_memory(text,source='manual') for text in ['original A','original B']]
    ids=[a['id'],b['id']]
    versions={m['id']:record_version(m) for m in [a,b]}
    request={'ids':ids,'expected_versions':versions}
    preview=memories.delete_memories_bulk(request)
    kb.update_memory(b['id'],{'content':'QQ changed B'})
    for r in [request,{**request,'confirmation_token':preview['confirmation_token']}]:
        with pytest.raises(HTTPException) as error: memories.delete_memories_bulk(r)
        assert error.value.status_code==409
    assert kb._find_memory(a['id']) and kb._find_memory(b['id'])
    fresh={m:record_version(kb._find_memory(m)) for m in ids}
    preview=memories.delete_memories_bulk({'ids':ids,'expected_versions':fresh})
    response=memories.delete_memories_bulk({'ids':ids,'expected_versions':fresh,'confirmation_token':preview['confirmation_token']})
    assert response['status']=='success' and response['deleted_count']==2


@pytest.mark.parametrize('versions',[[],{'missing':'a'*64}])
def test_bulk_invalid_or_incomplete_versions_never_delete(kb,versions):
    m=kb.add_memory('keep',source='manual')
    with pytest.raises(HTTPException) as error:
        memories.delete_memories_bulk({'ids':[m['id']],'expected_versions':versions})
    assert error.value.status_code==400 and kb._find_memory(m['id'])


def test_bulk_older_client_remains_compatible(kb):
    m=kb.add_memory('older client',source='manual')
    preview=memories.delete_memories_bulk({'ids':[m['id']]})
    result=memories.delete_memories_bulk({'ids':[m['id']],'confirmation_token':preview['confirmation_token']})
    assert result['status']=='success'
