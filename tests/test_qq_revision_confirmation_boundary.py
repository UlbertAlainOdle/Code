"""Confirmation codes are host control tokens, including copied receipt tails."""
import asyncio
from copy import deepcopy
import re
from types import SimpleNamespace

import pytest

from saya_app.services.memory_revisions import revision_confirmation
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_responses import ControlReceipt
from saya_app.services.qq_memory_management import QQMemoryReceipt
from test_qq_delivery import event
from test_qq_memory_revision_commands import lab
from test_qq_task_scheduler import submit


@pytest.mark.parametrize('ending',['','”','」','』','"'])
def test_only_a_single_receipt_closing_quote_is_accepted(ending):
    assert revision_confirmation('确认修改 '+'a'*32+ending)['token']=='a'*32


@pytest.mark.parametrize('text',['确认修改','确认修改 abc','确认修改 '+'a'*32+'””',
    '确认修改 '+'a'*32+' 其他正文','“确认修改 '+'a'*32+'”',
    '发送“确认修改 '+'a'*32+'”'])
def test_malformed_or_whole_quoted_confirmation_stays_in_readonly_control_namespace(text):
    assert revision_confirmation(text)=={'token':None}


def responder(lab):
    async def no_model(*a,**kw):raise AssertionError('confirmation must never invoke an Agent/model')
    b,k,service,actor,item,sid=lab
    result=QQChatResponder(b.settings,lambda **kw:SimpleNamespace(**kw),no_model,lambda:'api',lambda:{})
    result.memory_management=service
    return result


@pytest.mark.parametrize('text',['确认修改 bad-code','确认修改 '+'x'*32+'”'])
def test_invalid_and_unknown_codes_never_reach_model_or_change_fact(lab,text):
    message,_=normalize_event(event(text),lab[0].settings)
    before=deepcopy(lab[4])
    reply=asyncio.run(responder(lab)(message,[],lab[5]))
    assert isinstance(reply,(ControlReceipt,QQMemoryReceipt))
    assert '确认码格式不完整' in reply or '本次没有再次修改' in reply
    assert lab[4]==before


def test_expired_code_with_receipt_tail_remains_expired(lab):
    preview=lab[2].revision_response(lab[3],'补充蓝叶杯：它只在周末使用。',lab[5])
    token=re.search(r'确认修改 ([A-Za-z0-9_-]{32})',preview).group(1)
    with lab[1].sqlite_store._connection() as db:db.execute('update memory_revision_previews set expires=0')
    message,_=normalize_event(event('确认修改 '+token+'”'),lab[0].settings)
    reply=asyncio.run(responder(lab)(message,[],lab[5]))
    assert '本次没有再次修改' in reply and '只在周末使用' not in lab[4]['content']


def test_direct_code_outside_quoted_receipt_is_checked_by_same_scoped_service(lab):
    preview=lab[2].revision_response(lab[3],'补充蓝叶杯：它只在周末使用。',lab[5])
    token=re.search(r'确认修改 ([A-Za-z0-9_-]{32})',preview).group(1)
    message,_=normalize_event(event('确认修改 '+token+'”'),lab[0].settings)
    message.reply_to='12345';message.quote_context={'status':'control_receipt'}
    reply=asyncio.run(responder(lab)(message,[],lab[5]))
    assert '已更新同一条' in reply and lab[4]['content'].count('它只在周末使用')==1
    assert '没有再次修改' in asyncio.run(responder(lab)(message,[],lab[5]))


def test_group_and_other_account_confirmation_is_not_sent_to_model(lab):
    for raw in [event('确认修改 '+'a'*32,group_id=90000),event('确认修改 '+'a'*32,user_id=30000)]:
        message,_=normalize_event(raw,lab[0].settings)
        reply=asyncio.run(responder(lab)(message,[],lab[5]))
        assert '当前没有执行修改' in reply


def test_actual_bridge_accepts_display_quote_tail_once_and_never_queries_token_as_memory(lab):
    b,k,service,actor,item,sid=lab;b.responder=responder(lab)
    async def run():
        await b.start()
        try:
            assert submit(b,'补充蓝叶杯：它只在周末使用。',1)[0]=='queued'
            await b.queue.join()
            text='\n'.join(x[1] for x in b.client.sent)
            token=re.search(r'确认修改 ([A-Za-z0-9_-]{32})',text).group(1)
            assert submit(b,'确认修改 '+token+'”',2)[0]=='queued'
            await b.queue.join()
            assert any('已更新同一条' in x[1] for x in b.client.sent)
            version=record_version(item)
            assert submit(b,'确认修改 '+token+'”',3)[0]=='queued'
            await b.queue.join()
            assert record_version(item)==version and len(k.memories)==1
        finally:await b.stop()
    asyncio.run(run())


def test_missing_explicit_correction_id_returns_a_control_receipt(lab):
    original = deepcopy(lab[4])
    text = f'/memory correct 1234567890abcdef12345678 {record_version(original)} 新正文'
    message, _ = normalize_event(event(text), lab[0].settings)
    reply = asyncio.run(responder(lab)(message, [], lab[5]))
    assert isinstance(reply, QQMemoryReceipt)
    assert '找不到该记忆编号' in reply and 'memory:' in reply
    assert lab[4] == original


def test_exact_id_correction_preview_waits_for_bound_confirmation(lab):
    b, kb, _, _, item, sid = lab
    adapter = responder(lab)
    original = deepcopy(item)

    async def send(text):
        message, _ = normalize_event(event(text), b.settings)
        return await adapter(message, [], sid)

    async def run():
        new_content = '蓝叶杯现在用白色标记。'
        command = f'/memory preview-correct {item["id"]} {record_version(item)} {new_content}'
        preview = await send(command)
        assert isinstance(preview, QQMemoryReceipt)
        assert '尚未提交' in preview and original['content'] in preview and new_content in preview
        assert kb._find_memory(item['id']) == original
        token = re.search(r'确认修改 ([A-Za-z0-9_-]{32})', preview).group(1)
        receipt = await send('确认修改 ' + token)
        assert '已更新同一条' in receipt and item['id'] in receipt
        assert kb._find_memory(item['id'])['content'] == new_content and len(kb.memories) == 1

    asyncio.run(run())
