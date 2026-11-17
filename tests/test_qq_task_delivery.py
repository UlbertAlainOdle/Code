import asyncio

import pytest

from saya_app.services.qq_bridge import QQMessage, QQStore
from saya_app.services import qq_delivery as delivery
from saya_app.services.qq_tasks import QQTaskRegistry, QQTaskGuard, TaskCancelled


def fixture(tmp_path):
    store = QQStore(tmp_path / 'bridge.sqlite3')
    store.initialize(7)
    delivery.initialize(store)
    registry = QQTaskRegistry(store)
    scope = 'bot:private:user:shared'
    task = registry.register('job', scope, 'a'*64)
    registry.claim_next()
    guard = QQTaskGuard(registry, task['id'], scope)
    message = QQMessage('job','bot:private:user','10000','20000','','1','question',True)
    result = delivery.create(store,message,scope,('第一段的完整说明。'*20+'\n\n')*5,100)
    return store,registry,scope,task,guard,message,result


@pytest.mark.parametrize('cancel_at', ['before', 'first_confirmed', 'uncertain'])
def test_real_send_batch_respects_guard_and_preserves_receipts(tmp_path, cancel_at):
    async def run():
        store, registry, scope, task, guard, message, result = fixture(tmp_path)
        calls = []
        class Client:
            async def send_part(self, message, text, **kwargs):
                calls.append(text)
                registry.cancel(task['id'], scope)
                if cancel_at == 'uncertain':
                    raise TimeoutError('synthetic transport failure')
                return 'receipt1'
        if cancel_at == 'before':
            registry.cancel(task['id'],scope)
        with guard.bind():
            with pytest.raises(TimeoutError if cancel_at == 'uncertain' else TaskCancelled):
                await delivery.send_batch(store,Client(),message,scope,result,3,True)
        _, parts = delivery.lookup(store,scope,result)
        if cancel_at == 'before':
            assert not calls
            assert all(p['state']=='cancelled' for p in parts)
        else:
            assert len(calls)==1
            assert parts[0]['state']==('unknown' if cancel_at=='uncertain' else 'sent')
            assert parts[0]['receipt']==('' if cancel_at=='uncertain' else 'receipt1')
            assert all(p['state']=='cancelled' for p in parts[1:])
        effects = registry.get(task['id'],scope)['effects']
        assert [e['outcome'] for e in effects.values()] == ([] if cancel_at=='before' else ['unknown' if cancel_at=='uncertain' else 'committed'])
    asyncio.run(run())


def test_wrong_scope_guard_cannot_send_to_another_result(tmp_path):
    async def run():
        store, registry, scope, task, guard, message, result = fixture(tmp_path)
        other = registry.register('other', 'other_scope', 'a'*64)
        registry.claim_next(max_active=2)
        wrong_guard = QQTaskGuard(registry,other['id'],'other_scope')
        class Client:
            async def send_part(self, *args, **kwargs):
                pytest.fail('must not transmit')
        with wrong_guard.bind(), pytest.raises(KeyError):
            await delivery.send_batch(store,Client(),message,scope,result,3,True)
        assert all(p['state']=='pending' for p in delivery.lookup(store,scope,result)[1])
    asyncio.run(run())
