from dataclasses import replace
import json
from unittest.mock import patch

import pytest

from core_services import KnowledgeBase, MemoryManager
from saya_app.services.qq_memory_access import MemoryActor
from saya_app.services.qq_memory_management import QQMemoryManagement, record_version
from saya_app.services.qq_memory_operations import QQMemoryOperations
from test_qq_task_scheduler import make


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'));bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    memory=kb.add_memory('仅用于隔离删除验收',title='隔离条目',source='manual')
    scope='10000:private:20000:shared';session='session_qq_fixture.jsonl';now=[1000.];readonly=[False]
    actor=MemoryActor('10000','20000',True,True)
    bridge.store.save_conversation(scope,[],session)
    def authorize(actual, actual_scope, actual_session):
        if (actual != actor or actual_scope!=scope or actual_session!=session or readonly[0]):
            raise PermissionError('not_authorized')
    ops=QQMemoryOperations(bridge.store,kb,authorize,clock=lambda:now[0])
    return kb,bridge,memory,scope,session,actor,ops,now,readonly


def preview(lab):
    kb,_,m,scope,session,actor,ops,_,_=lab
    return ops.preview_delete(actor,scope,session,m['id'],record_version(kb._find_memory(m['id'])))


def test_preview_has_no_delete_confirm_exactly_once_and_tombstone_survives(lab):
    kb,_,m,scope,session,actor,ops,_,_=lab
    p=preview(lab);assert kb._find_memory(m['id'])
    result=ops.confirm_delete(actor,scope,session,p['confirmation_token'])
    assert result['operation_state']=='done' and result['deleted_count']==1
    assert ops.confirm_delete(actor,scope,session,p['confirmation_token'])['status']=='done'
    assert m['id'] in kb.deleted_memory_ids
    assert KnowledgeBase(kb.base_dir)._find_memory(m['id']) is None


@pytest.mark.parametrize('change',['actor','scope','session','readonly'])
def test_another_account_topic_or_revoked_write_cannot_confirm(lab,change):
    kb,_,m,scope,session,actor,ops,_,readonly=lab;p=preview(lab)
    if change=='actor':actor=replace(actor,user_id='30000',is_owner=False)
    elif change=='scope':scope='10000:private:30000:shared'
    elif change=='session':session='session_qq_other.jsonl'
    else:readonly[0]=True
    with pytest.raises(PermissionError):ops.confirm_delete(actor,scope,session,p['confirmation_token'])
    assert kb._find_memory(m['id'])


@pytest.mark.parametrize('change',['expire','edit','relationship'])
def test_expiry_or_changed_target_or_relationship_never_deletes(lab,change):
    kb,_,m,scope,session,actor,ops,now,_=lab;p=preview(lab)
    if change=='expire':now[0]+=601
    elif change=='edit':kb.update_memory(m['id'],{'content':'另一端已更新'})
    else:kb.add_memory('关系测试',source='manual',metadata={'compressed_from':[m['id']]})
    result=ops.confirm_delete(actor,scope,session,p['confirmation_token'])
    assert result.get('status') in {'expired','stale','confirmation_required'}
    assert kb._find_memory(m['id'])


def test_restart_never_replays_executing_or_pending_confirmation(lab):
    _,bridge,_,scope,session,actor,ops,_,_=lab;p=preview(lab)
    with bridge.store.connect() as db:
        db.execute("update qq_memory_operations set state='executing'")
    ops.recover()
    assert ops.confirm_delete(actor,scope,session,p['confirmation_token'])['status']=='unknown'


def test_failed_backend_does_not_issue_delete_success_or_retry(lab):
    kb,_,m,scope,session,actor,ops,_,_=lab;p=preview(lab)
    with patch.object(kb,'delete_memories',side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError):ops.confirm_delete(actor,scope,session,p['confirmation_token'])
    assert ops.confirm_delete(actor,scope,session,p['confirmation_token'])['status']=='unknown'
    assert kb._find_memory(m['id'])


def test_recall_explanation_is_actual_session_audit_and_current_epoch_only(lab,tmp_path):
    kb,bridge,m,scope,session,actor,_,_,_=lab
    manager=MemoryManager(str(tmp_path/'chats'));service=QQMemoryManagement(bridge,kb);service.history_manager=manager
    original_pointer=manager.current_session_file
    assert '没有' in service.recall_audit(actor,session)
    manager.save_message('ai','answer',session_id=session,metadata={'memory_recall':{
        'status':'ready','selected':[{'memory_id':m['id'],'reason':'text_relevance'}]}})
    assert m['title'] in service.command(actor,'/memory why',session)
    assert '没有' in service.recall_audit(actor,'session_qq_other.jsonl')
    kb.update_memory(m['id'],{'content':'新版本'})
    assert m['title'] not in service.recall_audit(actor,session)
    assert manager.current_session_file == original_pointer



def test_pending_restart_is_distinct_from_TTL_and_needs_new_preview(lab):
    kb,bridge,m,scope,session,actor,ops,now,_=lab
    old=preview(lab)
    reopened=KnowledgeBase(kb.base_dir)
    recovered=QQMemoryOperations(bridge.store,reopened,ops.authorize,clock=lambda:now[0])
    recovered.recover()
    assert now[0]<1000+old.get('expires_in',600)
    with patch.object(reopened,'delete_memories',side_effect=AssertionError('old preview must not execute')):
        result=recovered.confirm_delete(actor,scope,session,old['confirmation_token'])
    assert result=={'status':'restart_invalidated','action':'delete','memory_id':m['id']}
    assert reopened._find_memory(m['id'])
    new=recovered.preview_delete(actor,scope,session,m['id'],record_version(reopened._find_memory(m['id'])))
    assert new['confirmation_token']!=old['confirmation_token']
    assert recovered.confirm_delete(actor,scope,session,new['confirmation_token'])['deleted_count']==1
    assert recovered.confirm_delete(actor,scope,session,new['confirmation_token'])['status']=='done'


@pytest.mark.parametrize('state,phrase',[
    ('restart_invalidated','服务已重启，旧确认已作废'),('expired','确认已过期'),
    ('stale','条目内容版本已变化'),('unknown','结果待核对'),('done','已处理'),
])
def test_confirmation_receipt_distinguishes_recovery_states_without_repeating(lab,state,phrase):
    kb,b,m,scope,session,actor,ops,_,_=lab
    b.settings=b.settings.model_copy(update={'shared_memory':True,'memory_write_enabled':True,'memory_management_enabled':True})
    p=preview(lab)
    with b.store.connect() as db:db.execute('update qq_memory_operations set state=?',(state,))
    service=QQMemoryManagement(b,kb);service.operations=ops
    with patch.object(kb,'delete_memories',side_effect=AssertionError('must not repeat')):
        text=str(service.command(actor,'/memory confirm '+p['confirmation_token'],session))
    assert phrase in text and '已删除这条长期记忆' not in text
    if state in {'restart_invalidated','expired','stale'}:
        assert '/memory show '+m['id'] in text and '/memory delete '+m['id'] in text
    assert kb._find_memory(m['id'])
