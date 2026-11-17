import asyncio,json
from dataclasses import replace
from unittest.mock import patch
import httpx,pytest
from saya_app.services.qq_bridge import OneBotClient,QQMessage
from saya_app.services.qq_forward import QQForward
from saya_app.services.qq_tasks import TaskCancelled
from saya_app.services import qq_delivery
from test_qq_delivery import Client,config,event
from test_qq_task_scheduler import make


class ForwardClient(Client):
    def __init__(self,fail=None):super().__init__();self.self_parts=[];self.forwards=[];self.fail=fail
    async def send_self_part(self,bot,text):
        self.self_parts.append((bot,text))
        if self.fail=='self':raise TimeoutError('unknown')
        return str(100+len(self.self_parts))
    async def send_merged(self,message,receipts):
        self.forwards.append((message,receipts))
        if self.fail=='target':raise TimeoutError('unknown')
        return '999'


def test_bridge_long_answer_self_sends_then_forwards_once_without_model_replay(tmp_path):
    async def run():
        calls=[]
        text='\n\n'.join(f'第{i}段是一段完整的内容，请保持正确顺序和完整语义。' for i in range(9))
        async def model(*args):calls.append(1);return text
        client=ForwardClient();b=make(tmp_path,model,client=client,bubble_strategy='natural',reply_max_messages=5)
        await b.start()
        try:
            b.submit(event('长篇问题'));await b.queue.join()
            assert calls==[1] and client.self_parts and len(client.forwards)==1 and not client.sent
            assert all(bot=='10000' for bot,_ in client.self_parts)
            assert len(client.self_parts)==9 # complete paragraphs remain separate nodes
            assert client.forwards[0][1]==[str(101+i) for i in range(len(client.self_parts))]
            scope='10000:private:20000:shared'
            with b.store.connect() as db:
                assert db.execute("select state from qq_forward_results").fetchone()[0]=='sent'
                result_id=db.execute('select result_id from qq_forward_results').fetchone()[0]
                assert all(row[0]=='sent' for row in db.execute('select state from qq_delivery_parts'))
            b.submit(event('/continue '+result_id,2));await b.queue.join()
            assert calls==[1] and len(client.forwards)==1
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('failure',['self','target'])
def test_unknown_transports_never_replay_self_or_duplicate_target(tmp_path,failure):
    async def run():
        async def model(*a):return '\n\n'.join('这是有完整语义的一段回答，需要独立保留内容。' for _ in range(8))
        c=ForwardClient(failure);b=make(tmp_path,model,client=c,bubble_strategy='natural',reply_max_messages=5)
        await b.start()
        try:
            b.submit(event('长篇问题'));await b.queue.join()
            self_calls=len(c.self_parts);target_calls=len(c.forwards)
            if failure=='self':assert c.sent and target_calls==0
            else:
                assert not c.sent and target_calls==1
                with b.store.connect() as db:assert set(row[0] for row in db.execute('select state from qq_delivery_parts'))=={'unknown'}
            b.submit(event('继续说',2));await b.queue.join()
            assert len(c.self_parts)==self_calls and len(c.forwards)==target_calls
        finally:await b.stop()
    asyncio.run(run())


def test_revocation_between_self_send_and_forward_stops_target(tmp_path):
    async def run():
        c=ForwardClient();b=make(tmp_path,None,client=c);await b.start()
        try:
            msg=QQMessage('key','10000:private:20000','10000','20000','','1','q',False)
            scope=msg.scope+':isolated';result=qq_delivery.create(b.store,msg,scope,'一句完整的说明。'*100,100)
            with pytest.raises(TaskCancelled):
                await b.forward.try_send(msg,scope,result,permission_check=lambda:not c.self_parts)
            assert c.self_parts and not c.forwards
        finally:await b.stop()
    asyncio.run(run())


def test_restart_unknown_target_blocks_all_parts_and_scope_forgery(tmp_path):
    async def run():
        c=ForwardClient();b=make(tmp_path,None,client=c);await b.start()
        try:
            msg=QQMessage('key','10000:private:20000','10000','20000','','1','q',False)
            scope=msg.scope+':isolated';result=qq_delivery.create(b.store,msg,scope,'一句完整的说明。'*100,100)
            assert not await b.forward.try_send(replace(msg,user_id='30000'),scope,result)
            with b.store.connect() as db:db.execute("insert into qq_forward_results values(?,?,'sending','','')",(result,scope))
            QQForward(b)
            with b.store.connect() as db:
                assert db.execute('select state from qq_forward_results').fetchone()[0]=='unknown'
                assert set(x[0] for x in db.execute('select state from qq_delivery_parts'))=={'unknown'}
        finally:await b.stop()
    asyncio.run(run())


def test_native_wire_uses_self_text_and_real_id_nodes_only():
    async def run():
        calls=[]
        def handle(req):calls.append((req.url.path,json.loads(req.content)));return httpx.Response(200,json={'status':'ok','retcode':0,'data':{'message_id':123}})
        c=OneBotClient(config());await c.http.aclose();c.http=httpx.AsyncClient(transport=httpx.MockTransport(handle))
        try:
            receipt=await c.send_self_part('10000','[CQ:at,qq=all]')
            await c.send_merged(QQMessage('k','s','10000','20000','90000','1','q',False),[receipt])
            assert calls[0][1]=={'user_id':10000,'message':[{'type':'text','data':{'text':'[CQ:at,qq=all]'}}]}
            assert calls[1][0]=='/send_group_forward_msg' and calls[1][1]['messages']==[{'type':'node','data':{'id':'123'}}]
            with pytest.raises(ValueError):await c.send_merged(QQMessage('k','s','10000','20000','','1','q',False),['http://untrusted'])
        finally:await c.close()
    asyncio.run(run())


def test_scope_clear_prunes_forward_body_and_rollback_requires_new_reader(tmp_path):
    from types import SimpleNamespace
    from saya_app.services.qq_bridge import QQStore
    from saya_app.services.qq_rollback import check_memory_rollback
    store=QQStore(tmp_path/'knowledge_base/qq/bridge.sqlite3');qq_delivery.initialize(store)
    QQForward(SimpleNamespace(store=store))
    msg=QQMessage('k','s','10000','20000','','1','q',False)
    result=qq_delivery.create(store,msg,'s','body',100)
    with store.connect() as db:
        db.execute("insert into qq_forward_results values(?,'s','unknown','','')",(result,))
        db.execute("insert into qq_forward_sources values(?,0,'PRIVATE_BODY','sent','123')",(result,))
    gate=check_memory_rollback(tmp_path,{'memory_compatibility':[]})
    assert gate['status']=='blocked' and 'qq_self_merged_forward' in gate['missing']
    qq_delivery.clear_scope(store,'s')
    with store.connect() as db:
        assert db.execute('select count(*) from qq_forward_sources').fetchone()[0]==0
        assert db.execute('select count(*) from qq_forward_results').fetchone()[0]==0


def test_long_structured_content_forwards_even_when_it_fits_fewer_bubbles(tmp_path):
    async def run():
        c=ForwardClient();b=make(tmp_path,None,client=c);await b.start()
        try:
            msg=QQMessage('k','10000:private:20000','10000','20000','','1','q',False)
            scope=msg.scope+':isolated';body='```text\n'+'line of generated text\n'*80+'```'
            result=qq_delivery.create(b.store,msg,scope,body,240,strategy='natural',limit=5)
            assert len(qq_delivery.lookup(b.store,scope,result)[1])<5
            assert await b.forward.try_send(msg,scope,result)
            assert len(c.forwards)==1
        finally:await b.stop()
    asyncio.run(run())
