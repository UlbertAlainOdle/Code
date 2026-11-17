"""Actual M2 feedback: copied sending instructions are data, not write consent."""
import asyncio
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest

from saya_app.services.memory_revisions import MemoryRevisions, revision_request
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_responses import ControlReceipt
from test_qq_delivery import event
from test_qq_memory_revision_commands import lab
from test_qq_task_scheduler import submit


@pytest.mark.parametrize('text',[
    '发送“补充蓝叶杯：它只在周末使用。”',
    '请发送“补充蓝叶杯：它只在周末使用。”',
    '“补充蓝叶杯：它只在周末使用。”',
    '输入「更正蓝叶杯：周末→工作日」',
])
def test_quoted_sending_instruction_requests_direct_input_without_authorizing_a_write(lab,text):
    parsed=revision_request(text)
    assert parsed['requires_direct'] is True and parsed['safe'] is False
    assert parsed['direct_text'].startswith(('补充','更正'))
    before=deepcopy(lab[4])
    reply=lab[2].revision_response(lab[3],text,lab[5])
    assert '尚未修改，也没有另存候选' in reply
    assert lab[4]==before and not lab[1].memory_candidates


def test_sensitive_quoted_instruction_is_not_echoed_or_executed(lab):
    reply=lab[2].revision_response(lab[3],'发送“补充蓝叶杯：我的密码是PRIVATE_SECRET_VALUE。”',lab[5])
    assert 'PRIVATE_SECRET_VALUE' not in reply and not lab[1].memory_candidates


def test_missing_old_fragment_has_accurate_reply_not_protection_or_version_error(lab):
    before=record_version(lab[4])
    reply=lab[2].revision_response(lab[3],'更正蓝叶杯：只在周末使用→只在工作日使用',lab[5])
    assert '还没有找到要更正的原片段' in reply and '前一步补充可能尚未确认' in reply
    assert '目标受保护' not in reply and record_version(lab[4])==before


def test_repeated_fragment_correction_is_noop_after_it_is_applied(lab):
    record={'content':'蓝叶杯只在工作日使用。'}
    assert MemoryRevisions.new_content(record,'correct','只在周末使用→只在工作日使用')==record['content']


def test_repeated_old_fragment_requires_a_more_specific_target(lab):
    item=lab[4]
    assert lab[1].update_memory(item['id'],{'content':'蓝叶杯周末使用，另一个杯子也周末使用。'})
    reply=lab[2].revision_response(lab[3],'更正蓝叶杯：周末→工作日',lab[5])
    assert '原片段在这条记忆中出现多次' in reply
    assert item['content'].count('周末')==2


@pytest.mark.parametrize('text',['确认','其他问题'])
def test_quoted_receipt_confirmation_stays_readonly_and_guidance_survives_presentation(lab,text):
    b,k,service,actor,item,sid=lab
    async def no_model(*a,**kw):raise AssertionError('control reference must not invoke model')
    responder=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),no_model,lambda:'api',lambda:{})
    responder.memory_management=service
    message,_=normalize_event(event(text),b.settings)
    message.quote_context={'status':'control_receipt'}
    result=asyncio.run(responder(message,[],sid))
    assert isinstance(result,ControlReceipt) and '不作为人物聊天记录' in result
    if text=='确认':assert '完整的“确认修改＋确认码”' in result
    assert '查看相关操作说明' not in result and not k.memory_candidates


def test_actual_bridge_copied_instruction_does_not_reach_model_then_direct_input_can_preview_and_commit(lab):
    b,k,service,actor,item,sid=lab
    calls=[]
    async def no_model(*a,**kw):
        calls.append(1)
        raise AssertionError('revision copy guidance should stop before model/candidate triage')
    responder=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),no_model,lambda:'api',lambda:{})
    responder.memory_management=service;b.responder=responder
    async def run():
        await b.start()
        try:
            assert submit(b,'发送“补充蓝叶杯：它只在周末使用。”',1)[0]=='queued'
            await b.queue.join()
            assert any('尚未修改，也没有另存候选' in x[1] for x in b.client.sent)
            assert not k.memory_candidates and item['content']=='测试茶杯叫蓝叶杯。'
            assert submit(b,'补充蓝叶杯：它只在周末使用。',2)[0]=='queued'
            await b.queue.join()
            text='\n'.join(x[1] for x in b.client.sent)
            token=re.search(r'确认修改 ([A-Za-z0-9_-]{32})',text).group(1)
            assert submit(b,'确认修改 '+token,3)[0]=='queued'
            await b.queue.join()
            assert any('已更新同一条' in x[1] for x in b.client.sent)
            assert not calls and not k.memory_candidates and len(k.memories)==1
            assert item['content'].count('它只在周末使用')==1
        finally:await b.stop()
    asyncio.run(run())


def test_ordinary_quoted_discussion_is_not_classified_as_revision():
    assert revision_request('他说发送“补充蓝叶杯：它只在周末使用。”是什么意思？') is None
    assert revision_request('“这个杯子的名字很好听。”') is None
