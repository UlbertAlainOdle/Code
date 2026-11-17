import asyncio
import json
from unittest.mock import patch

from fastapi import FastAPI
import httpx
import pytest

from saya_app.services.qq_bridge import QQBridge, QQStore
from saya_app.services.qq_tasks import current_guard, TaskCancelled
from saya_app.services.qq_bridge import QQSettings, normalize_event
from saya_app.services.qq_tasks import QQTaskRegistry
from saya_app.services.qq_controls import QQControlsBusy
from saya_app.services import qq_delivery as delivery
from saya_app.routers import qq as routes
from test_qq_delivery import config, event, Client


async def until(predicate):
    async def poll():
        while not predicate():
            await asyncio.sleep(.01)
    await asyncio.wait_for(poll(),3)


@pytest.mark.parametrize('change',['source','cancel','revoked','notice_unknown'])
def test_owner_private_source_change_has_one_host_notice_without_retry(tmp_path,change):
    async def run():
        entered=asyncio.Event();calls=[]
        async def model(message,*args):
            entered.set();calls.append(message.key);guard=current_guard()
            if change=='cancel':guard.registry.cancel(guard.task_id,guard.scope)
            if change=='revoked':bridge.settings=bridge.settings.model_copy(update={'enabled':False})
            guard.permission_check=lambda:False
            return 'STALE_MODEL_SOURCE_MUST_NOT_SEND'
        from test_qq_delivery import Client
        client=Client(fail_at=1 if change=='notice_unknown' else None)
        bridge=make(tmp_path,model,client=client);await bridge.start()
        try:
            raw=event('虚构来源的当前条件是什么？',93001,20000)
            assert bridge.submit(raw)[0]=='queued'
            await until(entered.is_set);await until(lambda:not bridge.task_registry.list_active())
            text=' '.join(r[1] for r in client.sent)
            assert 'STALE_MODEL' not in text and len(calls)==1
            assert len(client.sent)==(1 if change in {'source','notice_unknown'} else 0)
            if client.sent:assert '可用上下文已变化' in text and '请发送新问题' in text
            if change=='notice_unknown':assert bridge.last_error=='context_notice_delivery_uncertain'
            with bridge.store.connect() as db:
                assert db.execute('select count(*) from qq_deliveries').fetchone()[0]==0
                assert db.execute('select count(*) from qq_control_receipt_ids').fetchone()[0]==int(change=='source')
            if change=='revoked':bridge.settings=bridge.settings.model_copy(update={'enabled':True})
            assert bridge.submit(raw)[0]=='duplicate' and len(calls)==1
        finally:await bridge.stop()
    asyncio.run(run())


def submit(bridge,text,mid=1,user=20000):
    return bridge.submit_signed(json.dumps(event(text,mid,user)).encode(),'Bearer inbound')


def make(tmp_path,responder,client=None,**changes):
    return QQBridge(settings=config(task_control_enabled=True,shared_memory=True,**changes),
        store=QQStore(tmp_path/'bridge.sqlite3'),client=client or Client(),responder=responder)


def test_real_bridge_control_status_and_cancel_bypass_blocked_generation(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(message,history,session):
            assert current_guard() is not None
            entered.set();await release.wait()
            return 'late answer must not be sent'
        client=Client();bridge=make(tmp_path,model,client)
        await bridge.start()
        try:
            assert submit(bridge,'slow')[0]=='queued'
            await entered.wait()
            assert submit(bridge,'/status',2)[0]=='queued'
            await asyncio.wait_for(bridge.task_controls.join(),1)
            assert '运行中' in client.sent[0][1]
            bridge.task_controls.clock=lambda:__import__('time').time()+1
            assert submit(bridge,'/cancel',3)[0]=='queued'
            await asyncio.wait_for(bridge.task_controls.join(),1)
            assert any('请求取消' in x[1] for x in client.sent)
            release.set();await asyncio.wait_for(bridge.queue.join(),2)
            await until(lambda:not bridge._task_workers)
            assert not any('late answer' in x[1] for x in client.sent)
            task=bridge.task_registry.list_scope('10000:private:20000:shared')[0]
            assert task['state']=='cancelled'
            assert not bridge.store.conversation(task['scope'])[0]
        finally:
            release.set();await bridge.stop()
    asyncio.run(run())


def test_cross_scope_parallelism_and_same_scope_fifo(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event();calls=[]
        async def model(message,history,session):
            calls.append(message.text)
            if message.text=='slow':entered.set();await release.wait()
            return 'answer '+message.text
        client=Client();bridge=make(tmp_path,model,client)
        await bridge.start()
        try:
            submit(bridge,'slow',1);await entered.wait()
            submit(bridge,'second',2);submit(bridge,'other',3,user=30000)
            await until(lambda:'other' in calls)
            assert 'second' not in calls
            release.set();await asyncio.wait_for(bridge.queue.join(),3)
            assert calls.index('second')>calls.index('slow')
            assert len(calls)==3
        finally:
            release.set();await bridge.stop()
    asyncio.run(run())


def test_queued_cancel_is_projected_and_never_runs_model(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event();calls=[]
        async def model(message,history,session):
            calls.append(message.text);entered.set();await release.wait();return 'answer'
        bridge=make(tmp_path,model)
        await bridge.start()
        try:
            submit(bridge,'slow');await entered.wait();submit(bridge,'queued',2)
            task=[t for t in bridge.task_registry.list_scope('10000:private:20000:shared') if t['state']=='queued'][0]
            submit(bridge,'/cancel '+task['id'],3)
            await bridge.task_controls.join()
            with bridge.store.connect() as db:
                row=db.execute('select state,payload from inbox where event_key=?',(task['event_key'],)).fetchone()
                assert tuple(row)==('cancelled','{}')
            release.set();await bridge.queue.join()
            assert calls==['slow']
        finally:
            release.set();await bridge.stop()
    asyncio.run(run())


def test_first_sent_part_cancel_freezes_cache_and_explicit_continue_cannot_replay(tmp_path):
    async def run():
        holder={}
        class CancellingClient(Client):
            async def send_part(self,message,text,**kwargs):
                receipt=await super().send_part(message,text,**kwargs)
                if len(self.sent)==1:
                    bridge=holder['bridge'];task=bridge.task_registry.list_active()[0]
                    bridge.task_registry.cancel(task['id'],task['scope'])
                return receipt
        async def model(*args):return ('完整段落说明。'*30+'\n\n')*6
        client=CancellingClient();bridge=make(tmp_path,model,client);holder['bridge']=bridge
        await bridge.start()
        try:
            submit(bridge,'long');await bridge.queue.join();await until(lambda:not bridge._task_workers)
            scope='10000:private:20000:shared'
            record,parts=delivery.lookup(bridge.store,scope)
            assert parts[0]['state']=='sent'
            assert all(p['state']=='cancelled' for p in parts[1:])
            assert delivery.topics(bridge.store,scope)=='目前没有没说完的话题。'
            submit(bridge,'/continue '+record['id'],2)
            await bridge.queue.join();await until(lambda:not bridge._task_workers)
            assert len(client.sent)==2 and '已取消' in client.sent[1][1]
            assert delivery.lookup(bridge.store,scope,record['id'])[1][0]['receipt']==parts[0]['receipt']
        finally:await bridge.stop()
    asyncio.run(run())


def test_setting_default_and_old_http_form_cas_preserve_new_switch(tmp_path):
    async def run():
        async def model(*args):return 'answer'
        bridge=make(tmp_path,model)
        await bridge.start()
        app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000',headers={'Authorization':'Bearer admin-secret'}) as web:
                    # Use the configured admin token, never a production secret.
                    bridge.settings=bridge.settings.model_copy(update={'admin_token':'admin-secret'})
                    payload=(await web.get('/api/qq/settings')).json()
                    values={k:v for k,v in payload['settings'].items() if k!='task_control_enabled'}
                    saved=await web.put('/api/qq/settings',json=values|{'revision':payload['revision'],'cooldown_seconds':4})
                    assert saved.status_code==200 and saved.json()['settings']['task_control_enabled'] is True
                    stale=await web.put('/api/qq/settings',json=values|{'revision':payload['revision']})
                    assert stale.status_code==409
        finally:await bridge.stop()
        assert config().task_control_enabled is False
    asyncio.run(run())


def test_ten_long_jobs_do_not_block_100_accepted_control_acknowledgements(tmp_path):
    async def run():
        import time
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*args):entered.set();await release.wait();return 'answer'
        bridge=make(tmp_path,model);await bridge.start();latencies=[]
        try:
            for mid in range(1,11):assert submit(bridge,'slow',mid)[0]=='queued'
            await entered.wait()
            now=[time.time()+2];bridge.task_controls.clock=lambda:now[0]
            for index in range(100):
                now[0]+=1
                start=time.monotonic()
                assert submit(bridge,'/status' if index%2==0 else '/cancel',100+index)[0]=='queued'
                await asyncio.wait_for(bridge.task_controls.join(),2)
                latencies.append(time.monotonic()-start)
            assert sorted(latencies)[94]<=2
            (tmp_path/'latencies.json').write_text(json.dumps({'accepted_controls':100,'registered_jobs':10,
                'running_jobs':1,'queued_same_scope':9,'p95_seconds':sorted(latencies)[94],
                'max_seconds':max(latencies),'mock_transport':True,'rate_window_clock_advanced':True}),encoding='utf-8')
            assert len(bridge._task_workers)==1  # nine are ordered behind the same scope
        finally:release.set();await bridge.queue.join();await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('state',['queued','generating','sending'])
def test_disabled_restart_never_requeues_registry_running_terminal_projection(tmp_path,state):
    async def run():
        calls=[]
        async def model(*args):calls.append(True);return 'must not run'
        settings=config(task_control_enabled=False,shared_memory=True)
        store=QQStore(tmp_path/'bridge.sqlite3');store.initialize(7)
        message,_=normalize_event(event('original'),settings)
        _,row_id=store.admit(message,settings);store.set_state(row_id,state)
        registry=QQTaskRegistry(store);scope=message.scope+':shared'
        task=registry.register(message.key,scope,'a'*64);registry.claim(task['id'],scope)
        bridge=QQBridge(settings=settings,store=store,client=Client(),responder=model)
        await bridge.start()
        try:
            await bridge.queue.join()
            assert not calls and not store.queued()
            assert bridge.task_registry.get(task['id'],scope)['state']=='interrupted'
            with store.connect() as db:
                assert db.execute('select state from inbox where id=?',(row_id,)).fetchone()[0]==('delivery_unknown' if state=='sending' else 'interrupted')
        finally:await bridge.stop()
    asyncio.run(run())


def test_switch_off_restores_fifo_and_unavailable_cancel_does_not_invoke_model(tmp_path):
    async def run():
        entered,release,other_entered=asyncio.Event(),asyncio.Event(),asyncio.Event();calls=[]
        async def model(message,*args):
            calls.append(message.text)
            if message.text=='slow':entered.set();await release.wait()
            else:other_entered.set()
            return 'answer'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            with patch('saya_app.services.qq_bridge.OneBotClient',return_value=Client()):
                payload=bridge.controls()
                await bridge.update_controls(payload['settings']|{'task_control_enabled':False},payload['revision'])
            assert bridge.task_controls is None and bridge.settings.task_control_enabled is False
            submit(bridge,'slow',1);await entered.wait();submit(bridge,'other',2,user=30000)
            with pytest.raises(asyncio.TimeoutError):await asyncio.wait_for(other_entered.wait(),.05)
            release.set();await bridge.queue.join()
            submit(bridge,'/cancel',3);await bridge.queue.join()
            assert calls==['slow','other'] and '尚未开启' in bridge.client.sent[-1][1]
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_group_revocation_cancels_task_before_applying_new_permission(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*args):entered.set();await release.wait();return 'must not be sent'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            bridge.submit_signed(json.dumps(event('slow',group_id=90000)).encode(),'Bearer inbound')
            await entered.wait();task=bridge.task_registry.list_active()[0]
            bridge.set_group_access('90000',False)
            assert bridge.task_registry.get(task['id'],task['scope'])['state']=='cancel_requested'
            assert '90000' not in bridge.effective_groups()
            release.set();await bridge.queue.join();await until(lambda:not bridge._task_workers)
            assert not any('must not' in x[1] for x in bridge.client.sent)
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_group_permission_change_waits_for_reserved_effect_to_settle(tmp_path):
    async def run():
        bridge=make(tmp_path,lambda *args:None);await bridge.start()
        scope='10000:group:90000:user:20000:isolated'
        try:
            task=bridge.task_registry.register('synthetic',scope,'a'*64,is_group=True)
            bridge.task_registry.claim(task['id'],scope)
            effect=bridge.task_registry.begin_effect(task['id'],scope,'send')
            with pytest.raises(QQControlsBusy):bridge.set_group_access('90000',False)
            assert '90000' in bridge.effective_groups()
            bridge.task_registry.end_effect(task['id'],scope,effect,'unknown')
            bridge.set_group_access('90000',False)
            assert '90000' not in bridge.effective_groups()
        finally:await bridge.stop()
    asyncio.run(run())


def test_same_control_event_stays_deduplicated_after_switching_back_to_fifo(tmp_path):
    async def run():
        async def model(*args):return 'answer'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            assert submit(bridge,'/status',91)[0]=='queued'
            await bridge.task_controls.join()
            with patch('saya_app.services.qq_bridge.OneBotClient',return_value=Client()):
                payload=bridge.controls()
                await bridge.update_controls(payload['settings']|{'task_control_enabled':False},payload['revision'])
            assert submit(bridge,'/status',91)[0]=='duplicate'
            await bridge.queue.join()
            assert bridge.client.sent==[]
        finally:await bridge.stop()
    asyncio.run(run())
