import asyncio
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

from saya_app.services import qq_delivery as delivery
from saya_app.services.qq_bridge import QQBridge, QQMessage, QQSettings, QQStore
from saya_app.services.qq_presentation import project_answer, wants_command_help
from test_qq_delivery import config, event, Client


@pytest.mark.parametrize('text,expected', [
    ('你好，很高兴见到你。',1),
    (('先把桌面上暂时用不到的文件收起来，留下今天要处理的内容，这样找东西时不会被干扰。\n\n'
      '再给下载文件夹分几个简单类别，文件多时先按日期整理，不用一开始就建立很复杂的目录。'),2),
    (('先把桌面上暂时用不到的文件收起来，留下今天要处理的内容，这样找东西时不会被干扰。\n\n'
      '再给下载文件夹分几个简单类别，文件多时先按日期整理，不用一开始就建立很复杂的目录。\n\n'
      '最后每周花几分钟清理新下载的文件，养成习惯以后，就不需要一次花很长时间整理了。'),3),
])
def test_medium_complete_paragraphs_split_below_old_threshold(text,expected):
    assert len(text)<240
    parts=delivery.natural_parts(text,240)
    assert len(parts)==expected
    assert ''.join(parts)==text.replace('\n','')


def test_tiny_fragments_merge_and_structured_content_is_kept_whole():
    text='嗯。\n\n先把需要的东西留在桌面，把其他文件移到一个临时文件夹里，整理时会轻松很多。\n\n不用着急。'
    assert len(delivery.natural_parts(text,240))==1
    for value in ['```python\nprint("hello")\n\nprint("world")\n```',
                  '| 名称 | 值 |\n|---|---|\n| A | B |', '1. 第一步\n\n2. 第二步',
                  '> 引用的原话\n\n> 仍属于引用']:
        assert delivery.natural_parts(value,240)==[value]


@pytest.mark.parametrize('paragraphs', [
    ['这个安排挺合适的，先吃点东西再慢慢休息。',
     '等餐的时候可以先把手头的事情放一放，等吃好后再告诉我一声，今天的安排就不用再赶了。'],
    ['这个建议可以先试试看。', '如果不太合适，我们再一起想办法。'],
    ['That sounds good.', 'We can talk again tomorrow.'],
])
def test_short_complete_paragraphs_survive_merging_even_below60_total(paragraphs):
    text='\n\n'.join(paragraphs)
    assert len(paragraphs[0])<30 and len(text)<240
    assert delivery.natural_parts(text,240)==paragraphs


def test_short_complete_paragraphs_reach_two_qq_messages_without_changing_history(tmp_path):
    async def run():
        paragraphs=['这个建议可以先试试看。','如果不太合适，我们再一起想办法。']
        calls=[]
        async def respond(*args):
            calls.append(args)
            return '\n\n'.join(paragraphs)
        store=QQStore(tmp_path/'qq.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_strategy='natural'),store,respond,client)
        await bridge.start()
        try:
            bridge.submit(event('聊聊今天的安排'));await bridge.queue.join()
            assert [item[1] for item in client.sent]==paragraphs
            assert len(calls)==1
            history,_=store.conversation('10000:private:20000:isolated')
            assert history==['Doctor: 聊聊今天的安排','Saya: '+'\n\n'.join(paragraphs)]
        finally:await bridge.stop()
    asyncio.run(run())


def test_query_continue_and_strategy_switch_preserve_frozen_result_and_history(tmp_path):
    async def run():
        calls=[]
        async def respond(*args):
            calls.append(args)
            return '这是当前话题中的一段完整说明，包含足够的背景和细节，不需要把句子从中间拆开。\n\n'*12
        store=QQStore(tmp_path/'qq.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_strategy='natural',reply_max_messages=2),store,respond,client)
        await bridge.start();bridge.submit(event('整理文件'));await bridge.queue.join()
        scope='10000:private:20000:isolated';record,parts=delivery.lookup(store,scope)
        assert len(client.sent)==2
        assert all('/continue' not in text and not text.startswith('[') for _,text,*_ in client.sent)
        original_history=store.conversation(scope)
        before=[(p['seq'],p['text'],p['state'],p['receipt']) for p in parts]
        snapshot=bridge.controls()
        await bridge.update_controls(snapshot['settings']|{'bubble_strategy':'length'},snapshot['revision'])
        bridge.submit(event('/topics',2));await bridge.queue.join()
        assert record['id'] in client.sent[-1][1] and '/continue ' in client.sent[-1][1]
        assert '整理文件' in client.sent[-1][1]
        assert store.conversation(scope)==original_history and len(calls)==1
        bridge.submit(event('继续说',3));await bridge.queue.join()
        assert len(calls)==1 and store.conversation(scope)==original_history
        _,after=delivery.lookup(store,scope,record['id'])
        assert [(p['seq'],p['text'],p['state'],p['receipt']) for p in after[:2]]==before[:2]
        assert [p['state'] for p in after[2:4]]==['sent']*min(2,len(parts)-2)
        assert not client.sent[-1][1].startswith('[') # frozen natural layout
        await bridge.stop()
    asyncio.run(run())


def test_topics_do_not_cross_scope_or_list_control_replies(tmp_path):
    store=QQStore(tmp_path/'qq.sqlite3');store.initialize(7)
    msg=QQMessage('k','s','10000','20000','','1','PRIVATE_TITLE',False)
    delivery.create(store,msg,'private','正文。'*150,100)
    delivery.create(store,replace(msg,key='control'),'other','HELP_PRIVATE',100,kind='control')
    assert delivery.topics(store,'other')=='目前没有没说完的话题。'
    result,parts=delivery.lookup(store,'private')
    with store.connect() as db:
        db.execute("UPDATE qq_delivery_parts SET state='unknown' WHERE result_id=? AND seq=0",(result['id'],))
    listing=delivery.topics(store,'private')
    assert 'PRIVATE_TITLE' in listing and '发送状态不明' in listing and '/continue' not in listing
    delivery.clear_scope(store,'private')
    assert delivery.topics(store,'private')=='目前没有没说完的话题。'
    with store.connect() as db:
        assert db.execute('SELECT count(*) FROM qq_delivery_options WHERE result_id=?',(result['id'],)).fetchone()[0]==0


def test_old_result_without_strategy_sidecar_keeps_original_parts(tmp_path):
    store=QQStore(tmp_path/'qq.sqlite3');store.initialize(7)
    msg=QQMessage('k','s','10000','20000','','1','q',False)
    result=delivery.create(store,msg,'s','旧结果内容。'*50,100)
    with store.connect() as db:db.execute('DELETE FROM qq_delivery_options WHERE result_id=?',(result,))
    before=delivery.lookup(store,'s',result)[1];client=Client()
    asyncio.run(delivery.send_batch(store,client,msg,'s',result,1,True))
    assert client.sent[0][1].startswith('[1/') and '/continue' not in client.sent[0][1]
    after=delivery.lookup(store,'s',result)[1]
    assert [p['text'] for p in before]==[p['text'] for p in after]


def test_old_pending_operational_hint_is_projected_only_on_send(tmp_path):
    store=QQStore(tmp_path/'qq.sqlite3');store.initialize(7)
    msg=QQMessage('k','s','10000','20000','','1','继续说',False)
    result=delivery.create(store,msg,'s','旧正文。请用 /memory-save candidate_123。',240)
    original=delivery.lookup(store,'s',result)[1][0]['text'];client=Client()
    asyncio.run(delivery.send_batch(store,client,msg,'s',result,3,True))
    assert '/memory-save' not in client.sent[0][1] and 'candidate_123' not in client.sent[0][1]
    assert delivery.lookup(store,'s',result)[1][0]['text']==original


@pytest.mark.parametrize('user_text', ['今天聊点什么','不要显示指令'])
def test_unrequested_application_hints_become_prose_but_urls_and_shell_remain(user_text):
    answer='可发送 `/continue 83e4396a80504ea2`。查看 /settings，候选记忆用 /memory-save candidate_123。\n网址 https://example.org/help ；命令 pwd 和 /usr/bin/python'
    result=project_answer(answer,{},set(),user_input=user_text)
    assert '/continue' not in result and '83e4396a80504ea2' not in result
    assert '/settings' not in result and '/memory-save' not in result and 'candidate_123' not in result
    assert 'https://example.org/help' in result and '/usr/bin/python' in result


@pytest.mark.parametrize('user_text', ['怎么续读？','给我操作指令','/help','请实现 /help 路由'])
def test_explicit_help_can_show_exact_commands(user_text):
    answer='/continue 83e4396a80504ea2'
    assert wants_command_help(user_text)
    assert project_answer(answer,{},set(),user_input=user_text)==answer
