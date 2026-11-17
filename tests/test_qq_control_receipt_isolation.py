"""Transport-visible receipts must never become character conversation."""
import asyncio
import json
from types import SimpleNamespace

from saya_app.services import qq_delivery as delivery, qq_quotes
from saya_app.services.qq_responses import ControlReceipt, ActionProposal, dialogue_body
from saya_app.services.qq_action_proposals import QQActionProposals
from test_qq_task_scheduler import make, submit
from test_qq_delivery import Client, event
from test_qq_chat_runtime import server


def test_receipts_are_typed_not_inferred_from_user_punctuation():
    value=ControlReceipt('设置已开启。')
    assert str(value)=='【操作回执】“设置已开启。”'
    assert dialogue_body(value)==''
    assert dialogue_body('【操作回执】“这是用户举的例子。”')
    assert ControlReceipt(value)==value


def test_mixed_answer_has_separate_parts_and_dialogue_canonical(tmp_path):
    async def run():
        async def model(*args):
            return delivery.QQAnswer('电影可以从这部开始聊。', [ControlReceipt('SAVED_RECEIPT_CANARY')])
        wire=Client();bridge=make(tmp_path,model,wire)
        await bridge.start()
        try:
            submit(bridge,'聊电影');await bridge.queue.join()
            assert any('【操作回执】' in p[1] for p in wire.sent)
            history,_=bridge.store.conversation('10000:private:20000:shared')
            assert '电影可以' in '\n'.join(history)
            assert 'SAVED_RECEIPT_CANARY' not in '\n'.join(history)
            with bridge.store.connect() as db:
                row=db.execute("select * from qq_deliveries where kind='final'").fetchone()
            assert row['canonical']=='电影可以从这部开始聊。'
            _,parts=delivery.lookup(bridge.store,row['scope'],row['id'])
            assert [p['content_kind'] for p in parts]==['control_receipt','dialogue']
        finally:await bridge.stop()
    asyncio.run(run())


def test_natural_consent_does_not_append_confirmation_or_receipt(tmp_path):
    async def run():
        wire=Client()
        async def model(*args):raise AssertionError('consent must not use model')
        bridge=make(tmp_path,model,wire);await bridge.start()
        bridge.action_proposals=QQActionProposals(bridge.store)
        scope='10000:private:20000:shared';session='session_qq_control.jsonl'
        original=['Doctor: 真实聊天','Saya: 真实回答']
        bridge.store.save_conversation(scope,original,session)
        suggestion=bridge.action_proposals.ask(scope,session,'search_on',{},'SEARCH_QUESTION_CANARY',bridge.controls()['revision'])
        assert isinstance(suggestion,ActionProposal)
        bridge.action_proposals.delivered(scope,session,str(suggestion))
        try:
            submit(bridge,'同意');await bridge.queue.join()
            assert bridge.settings.web_search_mode=='auto'
            assert '【操作回执】' in wire.sent[-1][1]
            assert bridge.store.conversation(scope)[0]==original
            with bridge.store.connect() as db:
                assert db.execute("select count(*) from qq_deliveries where kind='final'").fetchone()[0]==0
        finally:await bridge.stop()
    asyncio.run(run())


def test_priority_status_is_marked_without_entering_history(tmp_path):
    async def run():
        async def model(*args):raise AssertionError('status must not use model')
        wire=Client();bridge=make(tmp_path,model,wire);await bridge.start()
        try:
            assert submit(bridge,'/status')[0]=='queued'
            await bridge.task_controls.join()
            assert wire.sent[0][1].startswith('【操作回执】')
            assert not bridge.store.conversation('10000:private:20000:shared')[0]
        finally:await bridge.stop()
    asyncio.run(run())


def test_quote_of_sent_control_receipt_never_supplies_its_text(tmp_path):
    async def run():
        wire=Client();seen=[]
        async def model(message,*args):
            seen.append(getattr(message,'quote_context',None));return '普通问题的回答。'
        bridge=make(tmp_path,model,wire);await bridge.start()
        try:
            submit(bridge,'/whoami');await bridge.queue.join()
            receipt='501'
            async def call(action,payload):
                return {'message_id':receipt,'message_type':'private','sub_type':'friend',
                        'user_id':10000,'sender':{'user_id':10000},'message':[{'type':'text','data':{'text':'CONTROL_SECRET_CANARY'}}]}
            wire.call=call
            raw=event('这个回执是什么意思',2)
            raw['message'].insert(0,{'type':'reply','data':{'id':receipt}})
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound')
            await bridge.queue.join()
            assert not seen or all('CONTROL_SECRET_CANARY' not in str(x) for x in seen)
            with bridge.store.connect() as db:
                assert 'CONTROL_SECRET_CANARY' not in str(db.execute('select history from conversations').fetchall())
        finally:await bridge.stop()
    asyncio.run(run())


def test_actual_shared_generator_does_not_persist_operation_turn(server,tmp_path):
    import httpx
    from unittest.mock import patch
    from core_services import KnowledgeBase,MemoryManager
    from saya_app.services.conversation import conversation_service
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'))
    sid='session_qq_operation.jsonl';seen=[]
    async def run():
        async def model(request):
            seen.append(json.loads(request.content))
            data={'choices':[{'delta':{'content':'OPERATION_REPLY_CANARY'},'finish_reason':'stop'}]}
            return httpx.Response(200,text='data: '+json.dumps(data)+'\n\ndata: [DONE]\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'_get_api_httpx_client',lambda:api):
                req=server.ChatWithMotion(user_input='OPERATION_USER_CANARY',session_id=sid,model='api',
                    api_key='fake',api_url='http://mock/v1',agent_mode=False,recall_mode='off',memory_write_enabled=False)
                response=await server._chat_stream_single(req,channel_turn={'history':[],'shared':True,'is_owner':True,
                    'sender_id':'20000','context_eligible':False})
                async for _ in response.body_iterator:pass
        assert seen
        assert not manager.load_session_full(sid,make_current=False)
    with (patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),
          patch.object(server,'refresh_daily_activity_memory',lambda:None),
          patch.object(conversation_service,'save_credentials',lambda **kw:None)):
        asyncio.run(run())
