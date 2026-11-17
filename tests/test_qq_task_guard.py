import asyncio
import threading

import pytest

from saya_app.services.qq_tasks import QQTaskRegistry, QQTaskGuard, TaskCancelled, current_guard, checkpoint_current
from test_qq_tasks import Store


def fixture(tmp_path):
    registry = QQTaskRegistry(Store(tmp_path / 'guard.sqlite3'))
    a = registry.register('a', 'bot:user:a', 'a'*64)
    b = registry.register('b', 'bot:user:b', 'a'*64)
    registry.claim_next(max_active=2)
    registry.claim_next(max_active=2)
    return registry, a, b


def test_unbound_web_and_other_request_are_not_cancelled(tmp_path):
    registry, a, b = fixture(tmp_path)
    guard_a = QQTaskGuard(registry, a['id'], a['scope'])
    guard_b = QQTaskGuard(registry, b['id'], b['scope'])
    registry.cancel(a['id'], a['scope'])
    assert current_guard() is None
    checkpoint_current()
    with guard_a.bind():
        with pytest.raises(TaskCancelled):
            checkpoint_current()
        with guard_b.bind():
            checkpoint_current()
        assert current_guard() is guard_a
    assert current_guard() is None
    assert registry.get(b['id'], b['scope'])['state'] == 'running'


def test_to_thread_late_work_cannot_start_memory_commit(tmp_path):
    async def run():
        registry, a, _ = fixture(tmp_path)
        guard = QQTaskGuard(registry, a['id'], a['scope'])
        started, release = threading.Event(), threading.Event()
        committed = []
        def late_worker():
            started.set()
            assert release.wait(3)
            with current_guard().effect('memory') as effect:
                committed.append(True)
                effect.committed()
        with guard.bind():
            work = asyncio.create_task(asyncio.to_thread(late_worker))
        try:
            while not started.is_set():
                await asyncio.sleep(0)
            registry.cancel(a['id'], a['scope'])
        finally:
            release.set()
        with pytest.raises(TaskCancelled):
            await work
        assert not committed
        assert registry.get(a['id'], a['scope'])['effects'] == {}
        assert current_guard() is None
    asyncio.run(run())


def test_commit_in_flight_keeps_confirmation_after_cancel(tmp_path):
    registry, a, _ = fixture(tmp_path)
    guard = QQTaskGuard(registry, a['id'], a['scope'])
    with guard.effect('memory') as effect:
        assert registry.cancel(a['id'], a['scope'])['state'] == 'cancel_requested'
        effect.committed()
    final = registry.finish(a['id'], a['scope'], 'completed')
    assert final['state'] == 'cancelled'
    assert [e['outcome'] for e in final['effects'].values()] == ['committed']


@pytest.mark.parametrize('confirmed', [False, True])
def test_exception_never_reverses_confirmed_or_assumes_unconfirmed_send(tmp_path, confirmed):
    registry, a, _ = fixture(tmp_path)
    guard = QQTaskGuard(registry, a['id'], a['scope'])
    with pytest.raises(RuntimeError):
        with guard.effect('send') as effect:
            if confirmed:
                effect.committed()
            raise RuntimeError('synthetic send failure')
    outcomes = [e['outcome'] for e in registry.get(a['id'], a['scope'])['effects'].values()]
    assert outcomes == ['committed' if confirmed else 'unknown']


def test_exception_restores_context_and_cancel_prevents_followup_effect(tmp_path):
    registry, a, _ = fixture(tmp_path)
    guard = QQTaskGuard(registry, a['id'], a['scope'])
    with pytest.raises(RuntimeError):
        with guard.bind():
            raise RuntimeError('synthetic')
    assert current_guard() is None
    registry.cancel(a['id'], a['scope'])
    with pytest.raises(TaskCancelled):
        with guard.effect('read'):
            pytest.fail('read must not start')
