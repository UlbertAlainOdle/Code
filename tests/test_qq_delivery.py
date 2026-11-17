import asyncio
import ast
from dataclasses import replace
import json
import time
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from saya_app.services import qq_delivery as delivery
from saya_app.services.qq_bridge import QQBridge, QQMessage, QQSettings, QQStore, OneBotClient
from saya_app.services.qq_controls import parse_command_setting, QQControlsConflict


def config(**values):
    return QQSettings(**(dict(enabled=True, bot_id='10000', access_token='outbound',
        webhook_token='inbound', allowed_users={'20000','30000'}, allowed_groups={'90000'},
        owner_ids={'20000'}, cooldown_seconds=0) | values))


def event(text='问题', message_id=1, user_id=20000, group_id=None):
    result = dict(time=int(time.time()), self_id=10000, post_type='message',
        message_type='private', sub_type='friend', user_id=user_id, message_id=message_id,
        message=[{'type':'text','data':{'text':text}}])
    if group_id:
        result.update(message_type='group',group_id=group_id,
            message=[{'type':'at','data':{'qq':'10000'}},*result['message']])
    return result


class Client:
    def __init__(self, fail_at=None):
        self.sent, self.fail_at = [], fail_at

    async def send_part(self, message, text, *, reference=False, mention=True):
        self.sent.append((message,text,reference,mention))
        if len(self.sent)==self.fail_at:
            raise TimeoutError('private transport details')
        return str(500+len(self.sent))

    async def send(self, message, text):
        return await self.send_part(message,text)

    async def close(self):
        pass


def test_sentences_emoji_links_and_code_have_safe_bounded_bubbles():
    text = ('句子一。句子二！👩🏽\u200d💻e\u0301🇨🇳' * 45
        + '\nhttps://example.com/search?q=' + 'a'*400 + '&p=2\n'
        + '```python\n' + 'print("你好")\n'*600 + '```\n结尾。')
    parts = delivery.semantic_parts(text, 120)
    assert len(parts)>3 and max(map(len,parts))<=delivery.HARD_PART_CHARS
    assert 'https://example.com/search?q='+'a'*400+'&p=2' in '\n'.join(parts)
    for part in parts:
        assert not part.startswith(('\u200d','\ufe0f','\u0301','🏽','🇳'))
        assert not part.endswith(('\u200d','🇨'))
        assert part.count('```')%2==0
    assert sum(part.count('print("你好")') for part in parts)==600


def test_literal_protocol_html_and_control_tag_cleaning():
    parts = delivery.semantic_parts('[MOTION:wave]<script>x()</script> [CQ:at,qq=all]',120)
    assert parts==['<script>x()</script> [CQ:at,qq=all]']


@pytest.mark.parametrize('control',['MOD_SAYA','MODAL_SAYA','MODEL_SAYA'])
def test_old_continuation_parts_are_filtered_without_replaying_receipts(tmp_path,control):
    store=QQStore(tmp_path/'qq.sqlite3')
    delivery.initialize(store)
    message=QQMessage('k','s','10000','20000','','12','q',False)
    result=delivery.create(store,message,'s','第一段。第二段。',120)
    # Emulate a pre-fix cached result and an already-sent segment.
    with store.connect() as db:
        db.execute("UPDATE qq_delivery_parts SET text='旧段',state='sent',receipt='confirmed' WHERE result_id=?",(result,))
        db.execute("INSERT INTO qq_delivery_parts VALUES(?,1,?,'pending','')",(result,'['+control+': affectionate]旧缓存正文'))
    client=Client()
    asyncio.run(delivery.send_batch(store,client,message,'s',result,3,True))
    assert len(client.sent)==1 and client.sent[0][1]=='[2/2] 旧缓存正文'
    with store.connect() as db:
        assert tuple(db.execute('SELECT state,receipt,text FROM qq_delivery_parts WHERE result_id=? AND seq=0',(result,)).fetchone())==('sent','confirmed','旧段')


@pytest.mark.parametrize('control',['MOD_SAYA','MODAL_SAYA','MODEL_SAYA'])
def test_transport_filters_controls_in_legacy_and_direct_paths(control):
    async def run():
        sent=[]
        def post(request):
            sent.append(json.loads(request.content))
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'message_id':5}})
        client=OneBotClient(config());await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(post))
        message=QQMessage('k','s','10000','20000','','12','q',False)
        await client.send(message,'['+control+' : affectionate]正文')
        assert sent[0]['message']==[{'type':'text','data':{'text':'正文'}}]
        await client.close()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['timeout','missing','malformed'])
def test_native_wire_never_retries_uncertain_transport(failure):
    async def run():
        calls=[]
        def handle(request):
            calls.append(json.loads(request.content))
            if failure=='timeout':
                raise httpx.ReadTimeout('unknown',request=request)
            return httpx.Response(200,json={'status':'ok','retcode':0,
                'data':{} if failure=='missing' else {'message_id':'not-an-id'}})
        client=OneBotClient(config())
        await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        message=QQMessage('key','scope','10000','20000','','-123','question',False)
        with pytest.raises((httpx.ReadTimeout,RuntimeError)):
            await client.send_part(message,'[CQ:at,qq=all]',reference=True)
        assert len(calls)==1
        assert calls[0]['message']==[{'type':'reply','data':{'id':'-123'}},
            {'type':'text','data':{'text':'[CQ:at,qq=all]'}}]
        await client.close()
    asyncio.run(run())


def test_definite_unsupported_reference_falls_back_to_text_once():
    async def run():
        calls=[]
        def handle(request):
            calls.append(json.loads(request.content))
            if len(calls)==1:
                return httpx.Response(200,json={'status':'failed','retcode':100,'wording':'unsupported reply'})
            return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'message_id':5}})
        client=OneBotClient(config());await client.http.aclose()
        client.http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        message=QQMessage('k','s','10000','20000','90000','12','q',False)
        assert await client.send_part(message,'正文',reference=True)=='5'
        assert len(calls)==2
        assert calls[0]['message'][0]['type']=='reply'
        assert not any(p['type']=='reply' for p in calls[1]['message'])
        assert '回复消息 #12' in calls[1]['message'][0]['data']['text']
        await client.close()
    asyncio.run(run())


def test_partial_delivery_continues_twice_across_restart_without_responder(tmp_path):
    async def run():
        calls=[]
        text=''.join(f'第{i:02d}句说明内容。' for i in range(120))
        async def respond(*args):
            calls.append(args)
            return delivery.QQAnswer(text,['已保存长期记忆：真实回执'])
        store=QQStore(tmp_path/'bridge.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_chars=100,reply_max_messages=2),store,respond,client)
        await bridge.start();bridge.submit(event());await bridge.queue.join()
        assert len(client.sent)==2 and '已保存长期记忆' in client.sent[0][1]
        assert '/continue ' not in client.sent[-1][1]
        scope='10000:private:20000:isolated'
        record,parts=delivery.lookup(store,scope)
        result_id=record['id'];original_history=store.conversation(scope)
        assert [p['state'] for p in parts[:3]]==['sent','sent','pending']
        assert parts[0]['receipt']=='501'
        await bridge.stop()
        new_client=Client()
        restored=QQBridge(config(bubble_chars=100,reply_max_messages=2),store,respond,new_client)
        await restored.start()
        for number,command in [(2,'/continue '+result_id),(3,'/continue')]:
            assert restored.submit(event(command,number))[0]=='queued';await restored.queue.join()
        assert len(calls)==1 and store.conversation(scope)==original_history
        assert len(new_client.sent)==4
        assert '[3/' in new_client.sent[0][1] and '[5/' in new_client.sent[2][1]
        assert not any('已保存长期记忆' in p[1] for p in new_client.sent)
        assert all(p[0].message_id=='1' for p in new_client.sent)
        await restored.stop()
    asyncio.run(run())


def test_second_send_unknown_stays_frozen_after_restart(tmp_path):
    async def run():
        async def respond(*args):return '长句说明。'*200
        store=QQStore(tmp_path/'unknown.sqlite3');client=Client(fail_at=2)
        bridge=QQBridge(config(bubble_chars=100),store,respond,client)
        await bridge.start();bridge.submit(event());await bridge.queue.join()
        scope='10000:private:20000:isolated';record,parts=delivery.lookup(store,scope)
        assert [p['state'] for p in parts[:3]]==['sent','unknown','pending']
        assert store.counts()['delivery_unknown']==1 and len(client.sent)==2
        await bridge.stop();new_client=Client()
        restored=QQBridge(config(),store,respond,new_client);await restored.start()
        restored.submit(event('/continue '+record['id'],2));await restored.queue.join()
        assert len(new_client.sent)==1 and '发送结果不明' in new_client.sent[0][1]
        _,after=delivery.lookup(store,scope,record['id'])
        assert [p['state'] for p in after]==[p['state'] for p in parts]
        await restored.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'user_id':30000},{'group_id':90000}])
def test_guessed_id_cannot_cross_sender_or_group(tmp_path,change):
    async def run():
        async def respond(*args):return '私有记忆标记。'*200
        store=QQStore(tmp_path/'auth.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_chars=100,reply_max_messages=1),store,respond,client)
        await bridge.start();bridge.submit(event());await bridge.queue.join()
        record,_=delivery.lookup(store,'10000:private:20000:isolated')
        bridge.submit(event('/continue '+record['id'],2,**change));await bridge.queue.join()
        assert '不属于当前会话' in client.sent[-1][1] and '私有记忆标记' not in client.sent[-1][1]
        await bridge.stop()
    asyncio.run(run())


def test_privacy_toggle_and_reset_cannot_reveal_old_remainders(tmp_path):
    async def run():
        async def respond(*args):return '共享上下文。'*200
        client=Client();store=QQStore(tmp_path/'privacy.sqlite3')
        bridge=QQBridge(config(shared_memory=True,bubble_chars=100,reply_max_messages=1),store,respond,client)
        await bridge.start();bridge.submit(event());await bridge.queue.join()
        record,_=delivery.lookup(store,'10000:private:20000:shared')
        snapshot=bridge.controls();await bridge.update_controls(snapshot['settings']|{'shared_memory':False},snapshot['revision'])
        bridge.submit(event('/continue '+record['id'],2));await bridge.queue.join()
        assert '不属于当前会话' in client.sent[-1][1]
        bridge.submit(event('新问题',3));await bridge.queue.join()
        isolated,_=delivery.lookup(store,'10000:private:20000:isolated')
        bridge.submit(event('/reset',4));await bridge.queue.join()
        assert delivery.lookup(store,'10000:private:20000:isolated',isolated['id'])[0] is None
        await bridge.stop()
    asyncio.run(run())


def test_group_mentions_only_first_bubble_and_no_history_metadata(tmp_path):
    async def run():
        async def respond(*args):return '说明。'*120
        store=QQStore(tmp_path/'group.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_chars=100),store,respond,client)
        await bridge.start();bridge.submit(event(group_id=90000));await bridge.queue.join()
        assert len(client.sent)==3
        assert [(x[2],x[3]) for x in client.sent]==[(True,True),(False,False),(False,False)]
        history,_=store.conversation('10000:group:90000:user:20000:isolated')
        assert len(history)==2 and '[1/' not in history[-1] and '/continue' not in history[-1]
        await bridge.stop()
    asyncio.run(run())


def test_cache_ttl_capacity_and_crashed_sending_state(tmp_path):
    store=QQStore(tmp_path/'limits.sqlite3');store.initialize(7)
    original=QQMessage('key','s','10000','20000','','1','question',False)
    with patch.object(delivery,'MAX_RESULTS',4),patch.object(delivery,'MAX_SCOPE_RESULTS',2):
        ids=[delivery.create(store,replace(original,key=str(i)),str(i%3),'内容。'*40,100) for i in range(8)]
        delivery.prune(store)
        with store.connect() as db:
            assert db.execute('select count(*) from qq_deliveries').fetchone()[0]<=4
            assert max(row[0] for row in db.execute('select count(*) from qq_deliveries group by scope'))<=2
            db.execute("UPDATE qq_delivery_parts SET state='sending' WHERE result_id=? AND seq=0",(ids[-1],))
        store.initialize(7)
        _,parts=delivery.lookup(store,'1',ids[-1]);assert parts[0]['state']=='unknown'
        with store.connect() as db:db.execute('UPDATE qq_deliveries SET expires=0')
        delivery.prune(store)
        with store.connect() as db:
            assert db.execute('select count(*) from qq_deliveries').fetchone()[0]==0
            assert db.execute('select count(*) from qq_delivery_parts').fetchone()[0]==0


def test_additive_delivery_tables_survive_original_store_reader(tmp_path):
    from saya_app.services import qq_bridge
    # Execute the unchanged, fingerprinted pre-upgrade class on an isolated
    # synthetic database. No production data or network operations are used.
    root=Path(__file__).resolve().parents[1]
    original=(root/'tests/fixtures/qq_store_pre03.py').read_text('utf-8')
    node=next(node for node in ast.parse(original).body if isinstance(node,ast.ClassDef) and node.name=='QQStore')
    import hashlib
    # Byte fingerprint is independent of Python 3.12's added AST type_params.
    assert hashlib.sha256((root/'tests/fixtures/qq_store_pre03.py').read_bytes()).hexdigest()=='bf72dd9f30328e9e7c185512bdfe9484832ef61ad4dedcf1af5efd0e4aaadb33'
    namespace=dict(vars(qq_bridge))
    exec(compile(ast.Module(body=[node],type_ignores=[]),'original_qq_store','exec'),namespace)
    store=QQStore(tmp_path/'old-reader.sqlite3');store.initialize(7)
    message=QQMessage('key','s','10000','20000','','1','question',False)
    result_id=delivery.create(store,message,'s','fixture answer。'*80,100)
    store.save_conversation('s',['Doctor: fixture','Saya: answer'],'session_qq_fixture.jsonl')
    old=namespace['QQStore'](store.path);old.initialize(7)
    assert old.conversation('s')==(['Doctor: fixture','Saya: answer'],'session_qq_fixture.jsonl')
    assert old.counts()=={} and delivery.lookup(store,'s',result_id)[0] is not None


def test_legacy_switch_persists_rejects_stale_and_does_not_replay_parts(tmp_path):
    async def run():
        async def respond(*args):return '文字。'*250
        store=QQStore(tmp_path/'legacy.sqlite3');client=Client()
        bridge=QQBridge(config(bubble_chars=100,reply_max_messages=1),store,respond,client)
        await bridge.start();bridge.submit(event());await bridge.queue.join()
        snapshot=bridge.controls();values=snapshot['settings']|{'delivery_mode':'legacy'}
        updated=await bridge.update_controls(values,snapshot['revision'])
        with pytest.raises(QQControlsConflict):await bridge.update_controls(snapshot['settings'],snapshot['revision'])
        bridge.submit(event('/continue',2));await bridge.queue.join()
        assert '旧消息格式' in client.sent[-1][1]
        bridge.submit(event('新的旧格式问答',3));await bridge.queue.join()
        assert client.sent[-1][1]=='文字。'*250 and not client.sent[-1][2]
        await bridge.stop()
        restored=QQBridge(config(),store,respond,Client());await restored.start()
        assert restored.settings.delivery_mode=='legacy'
        await restored.stop()
    asyncio.run(run())


@pytest.mark.parametrize('name,value,expected',[
    ('delivery','chat',('delivery_mode','chat')),('quote','off',('reply_reference',False)),
    ('bubble','180',('bubble_chars',180)),('bubbles','2',('reply_max_messages',2)),
    ('split','natural',('bubble_strategy','natural')),
])
def test_channel_controls_use_existing_parser(name,value,expected):
    assert parse_command_setting(name,value)==expected


def test_channel_controls_cannot_inherit_or_exceed_bounds():
    with pytest.raises(ValueError):parse_command_setting('bubble','inherit')
    with pytest.raises(ValueError):config(bubble_chars=99)
    with pytest.raises(ValueError):config(reply_max_messages=7)
