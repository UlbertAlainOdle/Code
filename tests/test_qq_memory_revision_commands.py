import asyncio
from copy import deepcopy
import re
from types import SimpleNamespace
from dataclasses import replace

import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_origin import bind_origin, ingress
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_memory_access import MemoryActor, MemoryAccessDenied
from saya_app.services.qq_memory_management import QQMemoryManagement, QQMemoryReceipt, policy_content_revision
from test_qq_task_scheduler import make, submit


@pytest.fixture
def lab(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    bridge=make(tmp_path/'bridge',None,memory_management_enabled=True)
    bridge.store.initialize(7)
    service=QQMemoryManagement(bridge,kb);bridge.memory_management=service
    actor=MemoryActor('10000','20000',True,True)
    scope='10000:private:20000:shared';sid='session_qq_revisions.jsonl'
    bridge.store.save_conversation(scope,[],sid)
    with bind_origin(ingress('qq',subject='20000',bot_id='10000',message_id='original')):
        item=kb.add_memory('测试茶杯叫蓝叶杯。')
    service.saved_receipt(actor,item['id'],sid)
    return bridge,kb,service,actor,item,sid


def call(lab,text,**kwargs):
    return lab[2].revision_response(lab[3],text,lab[5],**kwargs)


def test_natural_addition_previews_and_unique_explicit_correction_updates(lab):
    proposed=call(lab,'补充蓝叶杯：只在周末使用。')
    assert isinstance(proposed,QQMemoryReceipt) and '尚未提交' in proposed
    assert lab[4]['content']=='测试茶杯叫蓝叶杯。'
    token=re.search(r'确认修改 (\S+)',proposed).group(1)
    result=call(lab,'/memory revision-confirm '+token)
    assert '已更新同一条' in result and '蓝叶杯' in lab[4]['content']
    result=call(lab,'更正蓝叶杯：只在周末使用→只在工作日使用')
    assert '已更新同一条' in result and '只在工作日使用' in lab[4]['content']
    before=record_version(lab[4])
    assert '没有重复保存' in call(lab,'补充蓝叶杯：只在工作日使用。')
    assert record_version(lab[4])==before and len(lab[1].memories)==1


def test_recent_target_is_topic_bound_and_ambiguous_targets_are_not_guessed(lab):
    assert '尚未提交' in call(lab,'补充刚才那条：只在周末使用。')
    lab[1].add_memory('另一个蓝叶杯是绿色的。')
    assert '目标不唯一' in call(lab,'补充蓝叶杯：杯柄向右。')
    assert lab[4]['content']=='测试茶杯叫蓝叶杯。'
    with lab[0].store.connect() as db:db.execute('delete from qq_memory_recent')
    assert '没有找到' in call(lab,'补充刚才那条：只在周末使用。')


def test_recent_pointer_follows_the_fact_actually_updated(lab):
    other=lab[1].add_memory('测试茶杯叫绿叶杯。')
    reply=call(lab,f"/memory append {other['id']} {record_version(other)} 杯柄向左。")
    assert '已更新同一条' in reply
    proposed=call(lab,'补充刚才那条：只在周末使用。')
    assert '绿叶杯' in proposed and '蓝叶杯' not in proposed


def test_management_off_readonly_quotes_and_optout_do_not_mutate(lab):
    before=deepcopy(lab[4])
    lab[0].settings=lab[0].settings.model_copy(update={'memory_management_enabled':False})
    assert '尚未开启' in call(lab,'补充蓝叶杯：只在周末使用。')
    assert lab[4]==before
    lab[0].settings=lab[0].settings.model_copy(update={'memory_management_enabled':True})
    assert '引用或附件' in call(lab,'补充蓝叶杯：只在周末使用。',quoted=True)
    assert '没有修改' in call(lab,'补充蓝叶杯：这次不保存，只在周末使用。')
    lab[0].settings=lab[0].settings.model_copy(update={'memory_write_enabled':False})
    with pytest.raises(MemoryAccessDenied):call(lab,'补充蓝叶杯：只在周末使用。')
    assert lab[4]==before


@pytest.mark.parametrize('actor', [MemoryActor('10000','30000',True,False),MemoryActor('10000','20000',True,True,'90000')])
def test_other_accounts_and_groups_cannot_preview_owner_revisions(lab,actor):
    with pytest.raises(MemoryAccessDenied):
        lab[2].revision_response(actor,'补充蓝叶杯：只在周末使用。',lab[5])


def test_body_change_invalidates_old_group_grant_and_history_can_restore_as_new_revision(lab):
    b,k,service,actor,item,sid=lab
    grant=service.access.set(actor,item['id'],policy_content_revision(item),'20000',visibility='groups',groups=('90000',))
    old_revision=k.memory_v4_store.get_memory(item['id']).revision
    proposed=call(lab,'补充蓝叶杯：只在周末使用。')
    token=re.search(r'确认修改 (\S+)',proposed).group(1)
    call(lab,'/memory revision-confirm '+token)
    assert service.detail(actor,item['id'])['policy_current'] is False
    assert '版本' in call(lab,'/memory history '+item['id'])
    restore=call(lab,f"/memory restore {item['id']} {old_revision}")
    token=re.search(r'确认修改 (\S+)',restore).group(1)
    assert '已更新同一条' in call(lab,'/memory revision-confirm '+token)
    assert item['content']=='测试茶杯叫蓝叶杯。'
    assert k.memory_v4_store.get_memory(item['id']).revision>old_revision


def test_actual_bridge_receipts_are_sent_after_own_write_without_chat_history_or_model(lab):
    b,k,service,actor,item,sid=lab
    calls=[]
    async def no_model(*a,**kw):
        calls.append(1)
        raise AssertionError('revision controls must not invoke model')
    responder=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),no_model,lambda:'api',lambda:{})
    responder.memory_management=service;b.responder=responder
    async def run():
        await b.start()
        try:
            assert submit(b,'补充蓝叶杯：只在周末使用。')[0]=='queued'
            await b.queue.join()
            preview='\n'.join(x[1] for x in b.client.sent)
            match=re.search(r'确认修改 ([A-Za-z0-9_-]{32})',preview)
            assert match,repr(preview)
            token=match.group(1)
            assert submit(b,'确认修改 '+token,2)[0]=='queued'
            await b.queue.join()
            assert any('已更新同一条' in x[1] for x in b.client.sent)
            assert not calls and item['content'].count('只在周末使用')==1
            assert b.store.conversation('10000:private:20000:shared')[0]==[]
            assert all(e['origin']['channel']=='qq' for e in item['context_events'])
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('fields',[{'media_types':('image',)},{'forward_present':True},{'reply_to':'999'}])
def test_actual_responder_refuses_revision_consent_from_media_forward_or_quote(lab,fields):
    from saya_app.services.qq_bridge import normalize_event
    from test_qq_delivery import event
    b,k,service,actor,item,sid=lab
    async def no_model(*a,**kw):raise AssertionError('must not send a revision request to model')
    responder=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),no_model,lambda:'api',lambda:{})
    responder.memory_management=service
    message,_=normalize_event(event('补充蓝叶杯：只在周末使用。'),b.settings)
    message=replace(message,**fields)
    before=deepcopy(item)
    result=asyncio.run(responder(message,[],sid))
    assert '引用或附件' in result and item==before
