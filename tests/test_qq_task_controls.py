"""Independent signed QQ control ingress, with fake transport and isolated DB."""
import asyncio
import hashlib
import hmac
import json
import time

import pytest

from saya_app.services.qq_bridge import QQBridge, QQStore, QQSettings
from saya_app.services.qq_tasks import QQTaskRegistry
from saya_app.services.qq_task_controls import QQTaskControls


def fixture(tmp_path):
    settings = QQSettings(enabled=True, bot_id='10000', webhook_secret='test-secret', access_token='fake-onebot',
        owner_ids={'20000'}, allowed_users={'20000'}, allowed_groups={'90000'},
        shared_memory=True, cooldown_seconds=3600)
    bridge = QQBridge(settings=settings, store=QQStore(tmp_path / 'bridge.sqlite3'))
    bridge.store.initialize(7)
    registry = QQTaskRegistry(bridge.store)
    sent = []
    async def send(message, text):
        sent.append(text)
        return str(500 + len(sent))
    now = [100.0]
    controls = QQTaskControls(bridge, registry, send, clock=lambda: now[0])
    return bridge, registry, controls, sent, now


def signed(text='/status', *, user=20000, bot=10000, group=None, mid=1, stale=False):
    event = dict(time=time.time() - (10000 if stale else 0), self_id=bot,
        post_type='message', message_type='private', sub_type='friend',
        user_id=user, message_id=mid, message=[{'type':'text','data':{'text':text}}])
    if group:
        event.update(message_type='group', group_id=group,
            message=[{'type':'at','data':{'qq':'10000'}}, *event['message']])
    body = json.dumps(event).encode()
    signature = 'sha1=' + hmac.new(b'test-secret', body, hashlib.sha1).hexdigest()
    return body, signature


def submit(controls, text='/status', **kwargs):
    body, signature = signed(text, **kwargs)
    return controls.submit(body, signature=signature)


def test_status_and_help_do_not_wait_for_blocked_main_queue(tmp_path):
    async def run():
        bridge, registry, controls, sent, now = fixture(tmp_path)
        main_wait = asyncio.Event()
        main = asyncio.create_task(main_wait.wait())
        controls.start()
        try:
            assert submit(controls) == ('queued', None)
            await asyncio.wait_for(controls.join(), .5)
            now[0] += 1
            assert submit(controls, '/help', mid=2) == ('queued', None)
            await asyncio.wait_for(controls.join(), .5)
            assert len(sent) == 2 and '/cancel' in sent[1]
            with bridge.store.connect() as db:
                assert [tuple(row) for row in db.execute(
                    'select receipt,scope from qq_control_receipt_ids order by receipt')] == [
                        ('501', '10000:private:20000:shared'), ('502', '10000:private:20000:shared')]
                assert [row[0] for row in db.execute('select state from qq_control_events')] == ['sent', 'sent']
            assert not main.done()
            assert not registry.list_scope('10000:private:20000:shared')
        finally:
            main.cancel()
            await asyncio.gather(main, return_exceptions=True)
            await controls.stop()
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['missing_receipt', 'ledger'])
def test_control_failure_keeps_unknown_and_does_not_repeat_send(tmp_path, caplog, failure):
    async def run():
        bridge, _, controls, sent, _ = fixture(tmp_path)
        if failure == 'missing_receipt':
            async def missing(message, text):
                sent.append(text)
                return None
            controls.send = missing
        else:
            with bridge.store.connect() as db:
                db.execute("""create trigger fail_control_commit before update on qq_control_events
                              when new.state='sent' begin select raise(abort, 'fixture commit failure'); end""")
        controls.start()
        try:
            assert submit(controls) == ('queued', None)
            await controls.join()
            assert submit(controls)[0] == 'duplicate'
            with bridge.store.connect() as db:
                assert db.execute('select state from qq_control_events').fetchone()[0] == 'unknown'
                assert db.execute('select count(*) from qq_control_receipt_ids').fetchone()[0] == 0
            assert len(sent) == 1
            assert f'qq_control_delivery_unknown stage={failure}' in caplog.text
        finally:
            await controls.stop()
    asyncio.run(run())


@pytest.mark.parametrize('kwargs', [dict(user=30000), dict(bot=10001),
    dict(group=90001), dict(stale=True)])
def test_invalid_identity_scope_or_age_is_rejected_before_control_admission(tmp_path, kwargs):
    async def run():
        _, _, controls, sent, _ = fixture(tmp_path)
        controls.start()
        try:
            assert submit(controls, **kwargs)[0] == 'ignored'
            await controls.join()
            assert not sent
        finally:
            await controls.stop()
    asyncio.run(run())


def test_bad_signature_cannot_cancel_a_task(tmp_path):
    async def run():
        _, registry, controls, sent, _ = fixture(tmp_path)
        task = registry.register('job', 'scope', 'a'*64)
        controls.start()
        try:
            body, _ = signed('/cancel ' + task['id'])
            assert controls.submit(body, signature='sha1=wrong')[0] == 'unauthorized'
            assert registry.get(task['id'], 'scope')['state'] == 'queued'
            assert not sent
        finally:
            await controls.stop()
    asyncio.run(run())


def test_cross_scope_task_id_is_not_disclosed_or_cancelled(tmp_path):
    async def run():
        _, registry, controls, sent, _ = fixture(tmp_path)
        task = registry.register('other', 'other:scope', 'a'*64)
        controls.start()
        try:
            submit(controls, '/cancel ' + task['id'])
            await controls.join()
            assert registry.get(task['id'], 'other:scope')['state'] == 'queued'
            assert task['id'] not in sent[0]
            assert '不存在或不属于' in sent[0]
        finally:
            await controls.stop()
    asyncio.run(run())


def test_signed_control_dedup_and_separate_rate_limit(tmp_path):
    async def run():
        _, _, controls, sent, now = fixture(tmp_path)
        controls.start()
        try:
            assert submit(controls)[0] == 'queued'
            assert submit(controls)[0] == 'duplicate'
            assert submit(controls, '/help', mid=2)[0] == 'rate_limited'
            await controls.join()
            assert len(sent) == 1
            now[0] += 1
            assert submit(controls, '/help', mid=2)[0] == 'queued'
            await controls.join()
            assert len(sent) == 2
        finally:
            await controls.stop()
    asyncio.run(run())


def test_group_mention_gate_and_normal_question_do_not_trigger_control(tmp_path):
    async def run():
        _, _, controls, sent, _ = fixture(tmp_path)
        controls.start()
        try:
            assert submit(controls, '普通问题')[0] == 'not_control'
            body, _ = signed('/status', group=90000)
            event = json.loads(body);event['message'] = event['message'][1:]
            body = json.dumps(event).encode()
            signature = 'sha1='+hmac.new(b'test-secret', body, hashlib.sha1).hexdigest()
            assert controls.submit(body, signature=signature)[0] == 'ignored'
            assert not sent
        finally:
            await controls.stop()
    asyncio.run(run())


def test_cancel_current_and_committed_effect_receipt(tmp_path):
    async def run():
        _, registry, controls, sent, _ = fixture(tmp_path)
        body, _ = signed();from saya_app.services.qq_bridge import normalize_event
        message, _ = normalize_event(json.loads(body), controls.bridge.settings)
        scope = controls.scope(message)
        task = registry.register('work', scope, 'a'*64)
        registry.claim_next()
        effect = registry.begin_effect(task['id'], scope, 'memory')
        registry.end_effect(task['id'], scope, effect, 'committed')
        controls.start()
        try:
            submit(controls, '/cancel')
            await controls.join()
            assert registry.get(task['id'], scope)['state'] == 'cancel_requested'
            assert '请求取消' in sent[0] and '已确认提交' in sent[0]
        finally:
            await controls.stop()
    asyncio.run(run())


def test_send_unknown_is_not_replayed_after_control_restart(tmp_path):
    async def run():
        bridge, registry, controls, _, now = fixture(tmp_path)
        calls = []
        async def broken(message, text):
            calls.append(True)
            raise TimeoutError('private provider secret')
        controls.send = broken
        controls.start()
        submit(controls)
        await controls.join()
        await controls.stop()
        controls.start()
        assert submit(controls)[0] == 'duplicate'
        await controls.join()
        await controls.stop()
        assert len(calls) == 1
        with bridge.store.connect() as db:
            row = dict(db.execute('select * from qq_control_events').fetchone())
            assert row['state'] == 'unknown'
            assert not any('private provider secret' in str(v) for v in row.values())
            assert 'payload' not in row
    asyncio.run(run())


def test_control_capacity_is_bounded_and_stop_never_closes_shared_transport(tmp_path):
    async def run():
        bridge, _, controls, sent, now = fixture(tmp_path)
        controls.capacity = 1
        shared_client = object()
        bridge.client = shared_client
        controls.start()
        try:
            assert submit(controls)[0] == 'queued'
            now[0] += 1
            assert submit(controls, '/help', mid=2)[0] == 'busy'
            await controls.join()
        finally:
            await controls.stop()
        assert len(sent) == 1
        assert bridge.client is shared_client
    asyncio.run(run())
