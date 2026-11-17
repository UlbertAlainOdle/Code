"""QQ-04 isolated registry contracts; no provider or OneBot calls."""
from concurrent.futures import ThreadPoolExecutor
import sqlite3
import threading

import pytest

from saya_app.services.qq_tasks import QQTaskRegistry, TaskCancelled, TaskCapacity


class Store:
    def __init__(self, path):
        self.path = path

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db


@pytest.fixture
def registry(tmp_path):
    return QQTaskRegistry(Store(tmp_path / 'tasks.sqlite3'))


def add(registry, event='event', scope='bot:private:user', **kwargs):
    return registry.register(event, scope, 'a' * 64, **kwargs)


def test_dedup_and_config_snapshot_do_not_overwrite_original(registry):
    task = add(registry)
    duplicate = registry.register('event', 'bot:private:user', 'b' * 64)
    assert duplicate == task
    assert duplicate['config_revision'] == 'a' * 64
    with pytest.raises(KeyError):
        registry.register('event', 'another_scope', 'b' * 64)


@pytest.mark.parametrize('scope', ['other_bot:private:user', 'bot:private:other',
                                  'bot:group:one:user', 'bot:group:two:user'])
def test_scope_is_required_for_read_cancel_and_effects(registry, scope):
    task = add(registry)
    for action in [lambda: registry.get(task['id'], scope),
                   lambda: registry.cancel(task['id'], scope),
                   lambda: registry.begin_effect(task['id'], scope, 'memory')]:
        with pytest.raises(KeyError):
            action()
    assert registry.get(task['id'], task['scope'])['state'] == 'queued'


def test_queue_cancel_is_terminal_and_cannot_be_claimed(registry):
    task = add(registry)
    assert registry.cancel(task['id'], task['scope'])['state'] == 'cancelled'
    assert registry.claim_next() is None
    with pytest.raises(TaskCancelled):
        registry.checkpoint(task['id'], task['scope'])
    assert registry.finish(task['id'], task['scope'], 'completed')['state'] == 'cancelled'


def test_cancel_before_commit_blocks_new_side_effect_and_late_result(registry):
    task = add(registry)
    registry.claim_next()
    assert registry.cancel(task['id'], task['scope'])['state'] == 'cancel_requested'
    for action in [lambda: registry.begin_effect(task['id'], task['scope'], 'memory'),
                   lambda: registry.checkpoint(task['id'], task['scope'])]:
        with pytest.raises(TaskCancelled):
            action()
    assert registry.finish(task['id'], task['scope'], 'completed')['state'] == 'cancelled'


@pytest.mark.parametrize('outcome', ['committed', 'unknown', 'failed'])
def test_cancel_during_commit_is_immediate_but_does_not_lie_about_effect(registry, outcome):
    task = add(registry)
    registry.claim_next()
    operation = registry.begin_effect(task['id'], task['scope'], 'send')
    requested = registry.cancel(task['id'], task['scope'])
    assert requested['state'] == 'cancel_requested'
    assert requested['effects'][operation] == {'kind': 'send', 'outcome': 'in_flight'}
    with pytest.raises(RuntimeError):
        registry.finish(task['id'], task['scope'], 'completed')
    registry.end_effect(task['id'], task['scope'], operation, outcome)
    final = registry.finish(task['id'], task['scope'], 'completed')
    assert final['state'] == 'cancelled'
    assert final['effects'][operation]['outcome'] == outcome
    assert registry.end_effect(task['id'], task['scope'], operation, outcome) == final
    with pytest.raises(ValueError):
        registry.end_effect(task['id'], task['scope'], operation, 'in_flight')


def test_cancel_after_committed_memory_reports_commit_without_rollback(registry):
    task = add(registry)
    registry.claim_next()
    op = registry.begin_effect(task['id'], task['scope'], 'memory')
    registry.end_effect(task['id'], task['scope'], op, 'committed')
    registry.cancel(task['id'], task['scope'])
    final = registry.finish(task['id'], task['scope'], 'completed')
    assert final['effects'][op]['outcome'] == 'committed'
    assert final['state'] == 'cancelled'


def test_same_scope_order_and_bounded_cross_scope_claims(registry):
    a = add(registry, 'a')
    b = add(registry, 'b')
    c = add(registry, 'c', 'bot:private:other')
    assert registry.claim_next(max_active=2)['id'] == a['id']
    assert registry.claim_next(max_active=2)['id'] == c['id']
    assert registry.claim_next(max_active=2) is None
    registry.finish(a['id'], a['scope'], 'completed')
    assert registry.claim_next(max_active=2)['id'] == b['id']


def test_cancel_pending_still_holds_its_scope_until_effects_settle(registry):
    a, b = add(registry, 'a'), add(registry, 'b')
    registry.claim_next()
    registry.cancel(a['id'], a['scope'])
    assert registry.claim_next(max_active=2) is None
    registry.finish(a['id'], a['scope'], 'cancelled')
    assert registry.claim_next(max_active=2)['id'] == b['id']


def test_restart_never_requeues_running_or_unknown_effect(registry):
    a, b = add(registry, 'a'), add(registry, 'b', 'bot:private:other')
    registry.claim_next()
    op = registry.begin_effect(a['id'], a['scope'], 'send')
    registry.recover()
    interrupted = registry.get(a['id'], a['scope'])
    assert interrupted['state'] == 'interrupted'
    assert interrupted['effects'][op]['outcome'] == 'unknown'
    assert registry.claim_next()['id'] == b['id']
    registry.recover()
    assert registry.get(a['id'], a['scope']) == interrupted


@pytest.mark.parametrize('group,interval', [(False, 2), (True, 10)])
def test_progress_is_throttled_per_task_without_raw_text(registry, group, interval):
    task = add(registry, is_group=group)
    registry.claim_next()
    assert registry.progress(task['id'], task['scope'], 'memory', now=100)
    assert not registry.progress(task['id'], task['scope'], 'memory', now=101)
    assert not registry.progress(task['id'], task['scope'], 'search', now=101)
    assert registry.get(task['id'], task['scope'])['phase'] == 'search'
    assert registry.progress(task['id'], task['scope'], 'sources', now=100 + interval)
    with pytest.raises(ValueError):
        registry.progress(task['id'], task['scope'], 'secret provider reasoning', now=200)


def test_capacity_and_terminal_idempotence(registry):
    task = add(registry, max_pending=1)
    with pytest.raises(TaskCapacity):
        add(registry, 'b', max_pending=1)
    registry.claim_next()
    final = registry.finish(task['id'], task['scope'], 'failed', error='model_timeout')
    assert registry.cancel(task['id'], task['scope']) == final
    assert registry.finish(task['id'], task['scope'], 'completed') == final
    add(registry, 'b', max_pending=1)


def test_repeated_running_cancellation_is_idempotent(registry):
    task = add(registry)
    registry.claim_next()
    first = registry.cancel(task['id'], task['scope'])
    assert registry.cancel(task['id'], task['scope']) == first
    assert not registry.progress(task['id'], task['scope'], 'sending')


def test_queue_cannot_be_reported_success_without_running(registry):
    task = add(registry)
    with pytest.raises(ValueError):
        registry.finish(task['id'], task['scope'], 'completed')
    assert registry.get(task['id'], task['scope'])['state'] == 'queued'


def test_cancel_complete_race_has_one_stable_terminal_100_times(registry):
    with ThreadPoolExecutor(max_workers=2) as workers:
        for index in range(100):
            task = add(registry, str(index))
            registry.claim_next()
            barrier = threading.Barrier(2)
            def finish():
                barrier.wait()
                return registry.finish(task['id'], task['scope'], 'completed')
            def cancel():
                barrier.wait()
                return registry.cancel(task['id'], task['scope'])
            first, second = workers.submit(finish), workers.submit(cancel)
            done, request = first.result(), second.result()
            assert done['state'] in {'completed', 'cancelled'}
            assert registry.finish(task['id'], task['scope'], 'completed') == done
            assert registry.cancel(task['id'], task['scope']) == done
            if done['state'] == 'completed':
                assert request['state'] == 'completed'


def test_schema_is_additive_and_never_mutates_old_inbox(tmp_path):
    store = Store(tmp_path / 'legacy.sqlite3')
    with store.connect() as db:
        db.execute('create table inbox(id integer,state text,payload text)')
        db.execute("insert into inbox values(1,'delivery_unknown','{}')")
    registry = QQTaskRegistry(store)
    task = add(registry)
    registry.cancel(task['id'], task['scope'])
    registry.recover()
    with store.connect() as db:
        assert tuple(db.execute('select * from inbox').fetchone()) == (1, 'delivery_unknown', '{}')
