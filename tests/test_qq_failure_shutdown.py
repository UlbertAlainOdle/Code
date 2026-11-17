import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch
import ssl
import time

import pytest
from test_qq_task_scheduler import make,submit,until
from test_qq_chat_runtime import server


def test_cancelled_generation_finishes_registry_and_does_not_block_next_message(tmp_path):
    async def run():
        async def model(message,*args):
            if message.text=='first':raise asyncio.CancelledError()
            return 'second answered'
        b=make(tmp_path,model);await b.start()
        try:
            submit(b,'first',1);await asyncio.wait_for(b.queue.join(),2)
            await until(lambda:not b._task_workers)
            first=b.task_registry.list_scope('10000:private:20000:shared')[0]
            assert first['state']=='failed' and first['error']=='generation_failed'
            assert any('暂时无法生成回答' in x[1] for x in b.client.sent)
            submit(b,'second',2);await asyncio.wait_for(b.queue.join(),2)
            await until(lambda:not b._task_workers)
            assert any('second answered' in x[1] for x in b.client.sent)
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('self_cancel', [False, True])
def test_cancelled_optional_selector_keeps_generated_answer(tmp_path, self_cancel):
    from test_qq_expressions import lab, row
    async def run():
        b, client = await lab(tmp_path, answer='已核对的回答。')
        async def cancelled_selector(*args):
            if self_cancel:
                asyncio.current_task().cancel()
                await asyncio.sleep(0)
            raise asyncio.CancelledError()
        b.expressions._select = cancelled_selector
        try:
            submit(b, '问题', 1)
            await asyncio.wait_for(b.queue.join(), 2)
            await until(lambda: not b._task_workers)
            assert [item[1] for item in client.sent] == ['已核对的回答。']
            result = row(b)
            assert result['state'] == 'skipped'
            assert json.loads(result['plan'])['decision_reason'] == 'selector_cancelled'
            task = b.task_registry.list_scope('10000:private:20000:shared')[0]
            assert task['state'] == 'completed'
        finally:
            await b.stop()
    asyncio.run(run())


def test_cancelled_optional_plan_keeps_text_and_delivery(tmp_path):
    from test_qq_expressions import lab

    async def run():
        bridge, client = await lab(tmp_path, answer='已核对的回答。')

        async def cancelled_plan(*args):
            asyncio.current_task().cancel()
            await asyncio.sleep(0)

        bridge.expressions.plan = cancelled_plan
        try:
            submit(bridge, '问题')
            await asyncio.wait_for(bridge.queue.join(), 2)
            await until(lambda: not bridge._task_workers)
            assert [item[1] for item in client.sent] == ['已核对的回答。']
            assert not client.reactions
            assert bridge.task_registry.list_scope('10000:private:20000:shared')[0]['state'] == 'completed'
        finally:
            await bridge.stop()

    asyncio.run(run())


@pytest.mark.parametrize('phase', ['generation', 'selector', 'plan'])
def test_actual_worker_cancellation_still_blocks_reply(tmp_path, phase):
    from test_qq_expressions import lab
    async def run():
        entered = asyncio.Event()
        async def blocked(*args):
            entered.set()
            await asyncio.Event().wait()
        if phase in {'selector', 'plan'}:
            b, client = await lab(tmp_path, answer='取消后不能送达。')
            if phase == 'plan':
                b.expressions.plan = blocked
            else:
                b.expressions._select = blocked
        else:
            b = make(tmp_path, blocked)
            await b.start()
            client = b.client
        try:
            submit(b, '问题', 1)
            await asyncio.wait_for(entered.wait(), 2)
            worker = next(iter(b._task_workers))
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            await asyncio.wait_for(b.queue.join(), 2)
            assert not client.sent
            task = b.task_registry.list_scope('10000:private:20000:shared')[0]
            assert task['state'] == 'cancelled'
        finally:
            await b.stop()
    asyncio.run(run())


def test_terminal_inbox_orphan_reconciled_without_faking_inflight_effects(tmp_path):
    async def run():
        async def model(*args):return 'queue released'
        b=make(tmp_path,model);await b.start()
        scope='10000:private:20000:shared'
        try:
            # A previous worker has exited after recording its interrupted inbox.
            with b.store.connect() as db:
                db.execute("insert into inbox(event_key,scope,payload,created,state,error) values('orphan',?,'{}',?,'interrupted','shutdown')",(scope,time.time()))
            t=b.task_registry.register('orphan',scope,b.controls()['revision']);b.task_registry.claim(t['id'],scope)
            submit(b,'new message',2);await asyncio.wait_for(b.queue.join(),2)
            assert b.task_registry.get(t['id'],scope)['state']=='interrupted'
            assert any('queue released' in x[1] for x in b.client.sent)
        finally:await b.stop()
    asyncio.run(run())


def test_drain_can_cancel_waiting_messages_but_waits_for_live_generation(tmp_path):
    async def run():
        import httpx
        from fastapi import FastAPI
        from saya_app.routers import qq as routes
        entered,release=asyncio.Event(),asyncio.Event();called=[]
        async def model(message,*args):
            called.append(message.text);entered.set();await release.wait();return 'active settled'
        b=make(tmp_path,model,admin_token='fixture');await b.start();app=FastAPI();app.include_router(routes.router)
        try:
            submit(b,'active',1);await entered.wait();submit(b,'waiting',2)
            with patch.object(routes,'qq_bridge',b):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                    path='/api/qq/lifecycle/cancel-pending';body={'confirmed':True}
                    assert (await c.post(path,json=body)).status_code==401
                    c.headers['Authorization']='Bearer fixture'
                    assert (await c.post(path,json=body)).status_code==409
                    b.draining=True
                    response=await c.post(path,json=body);assert response.status_code==200
                    assert b.status()['counts']['active_tasks']==1
                    release.set();await asyncio.wait_for(b.queue.join(),2);await until(lambda:not b._task_workers)
                    assert called==['active']
        finally:release.set();await b.stop()
    asyncio.run(run())


def test_provider_context_keeps_certificate_and_hostname_checks_with_CA_paths(monkeypatch,tmp_path):
    from saya_app.services.api_tls import provider_ssl_context
    context=provider_ssl_context();assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
    assert context.cert_store_stats()['x509_ca']>0
    monkeypatch.setenv('SSL_CERT_FILE',str(tmp_path/'absent-ca.pem'))
    context=provider_ssl_context();assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname


def test_actual_API_client_factory_uses_verified_context(server):
    async def run():
        client=server._get_api_httpx_client()
        try:
            context=client._transport._pool._ssl_context
            assert context.verify_mode==ssl.CERT_REQUIRED and context.check_hostname
        finally:
            await client.aclose();server._api_httpx_client=None;server._api_httpx_client_loop=None
    asyncio.run(run())


def test_cancel_waits_for_real_commit_and_preserves_its_confirmation(tmp_path):
    async def run():
        import threading
        from saya_app.services.qq_tasks import guarded_memory_write
        entered,release=threading.Event(),threading.Event();threads=[];calls=[]
        @guarded_memory_write
        def commit():entered.set();release.wait(4);return True
        async def model(message,*args):
            calls.append(message.text)
            if message.text=='first':
                threads.append(asyncio.create_task(asyncio.to_thread(commit)))
                while not entered.is_set():await asyncio.sleep(.01)
                raise asyncio.CancelledError()
            return 'second committed response'
        b=make(tmp_path,model);await b.start();scope='10000:private:20000:shared'
        try:
            submit(b,'first',1);await until(lambda:entered.is_set())
            await until(lambda:b.task_registry.list_scope(scope)[0]['state']=='cancel_requested')
            first=b.task_registry.list_scope(scope)[0]
            assert any(e['outcome']=='in_flight' for e in first['effects'].values())
            submit(b,'second',2);await asyncio.sleep(.05);assert calls==['first']
            release.set();await asyncio.wait_for(b.queue.join(),2);await until(lambda:not b._task_workers)
            task=b.task_registry.get(first['id'],scope)
            assert task['state']=='cancelled' and any(e['kind']=='memory' and e['outcome']=='committed' for e in task['effects'].values())
            assert calls==['first','second']
        finally:release.set();await asyncio.gather(*threads,return_exceptions=True);await b.stop()
    asyncio.run(run())


def test_orphan_reconciliation_refuses_live_effect_and_keeps_confirmed_result(tmp_path):
    async def run():
        b=make(tmp_path,None);await b.start();scope='10000:private:20000:shared'
        try:
            with b.store.connect() as db:
                db.execute("insert into inbox(event_key,scope,payload,created,state,error) values('guarded-orphan',?,'{}',?,'interrupted','shutdown')",(scope,time.time()))
            task=b.task_registry.register('guarded-orphan',scope,b.controls()['revision']);b.task_registry.claim(task['id'],scope)
            effect=b.task_registry.begin_effect(task['id'],scope,'memory')
            assert not b.task_registry.reconcile_terminal_inbox(task['id'],scope)
            assert b.task_registry.get(task['id'],scope)['effects'][effect]['outcome']=='in_flight'
            b.task_registry.end_effect(task['id'],scope,effect,'committed')
            assert b.task_registry.reconcile_terminal_inbox(task['id'],scope)
            assert b.task_registry.get(task['id'],scope)['effects'][effect]['outcome']=='committed'
        finally:await b.stop()
    asyncio.run(run())


def test_wsl_inspection_does_not_decode_native_stderr_as_utf8(tmp_path,monkeypatch):
    import sys
    from pathlib import Path
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
    import saya_qq_control as control
    monkeypatch.setattr(control,'ROOT',tmp_path);(tmp_path/'.runtime/qq-service').mkdir(parents=True)
    (tmp_path/'.runtime/qwen-omni-installed.json').write_text('{}')
    state={'processes':[],'uncertain':[],'tokens':[]}
    responses=[SimpleNamespace(returncode=0,stdout='Ubuntu'.encode('utf-16-le')),
        SimpleNamespace(returncode=0,stdout=json.dumps(state).encode(),stderr=b'\xc0\xee')]
    with patch.object(control.subprocess,'run',side_effect=responses) as run:
        assert control.voice_inventory()==state
        assert isinstance(run.call_args.kwargs['input'],bytes) and not run.call_args.kwargs.get('text',False)
