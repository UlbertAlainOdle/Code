import asyncio
from contextvars import copy_context
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from core_services import KnowledgeBase
from saya_app.services.memory_v4 import MemoryV4Store, WritePipeline
from saya_app.services.qq_tasks import QQTaskRegistry, QQTaskGuard, TaskCancelled, guarded_memory_write
from test_qq_tasks import Store


def fixture(tmp_path):
    registry = QQTaskRegistry(Store(tmp_path/'tasks.sqlite3'))
    task = registry.register('event','scope','a'*64)
    registry.claim_next()
    guard = QQTaskGuard(registry, task['id'],task['scope'])
    store = MemoryV4Store(tmp_path/'memory4.sqlite3')
    return registry,task,guard,store


def outcomes(registry,task):
    return [e['outcome'] for e in registry.get(task['id'],task['scope'])['effects'].values()]


def test_real_unchanged_upsert_does_not_claim_a_memory_commit(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    WritePipeline(store).process('请记住：我喜欢清淡的绿茶。',turn_id='seed')
    item=store.list_memories()[0]
    with store._connection() as db:
        before=db.execute('select count(*) from memory_versions').fetchone()[0]
    with guard.bind():
        result=store.upsert_memory(item)
    assert result['status']=='unchanged'
    with store._connection() as db:
        assert db.execute('select count(*) from memory_versions').fetchone()[0]==before
    assert outcomes(registry,task)==['failed']  # Confirmed no mutation, not unknown/committed.


@pytest.mark.parametrize('result,expected',[
    ({'status':'unchanged','memory':{'id':'existing'}},'failed'),
    ({'status':'unchanged','memory':{'id':'existing'},'evidence_added':1},'committed'),
    ({'status':'updated','memory':{'id':'existing'}},'committed'),
    ({'status':'error'},'unknown'),
])
def test_commit_receipt_distinguishes_unchanged_from_real_evidence_or_write(tmp_path,result,expected):
    registry,task,guard,_=fixture(tmp_path)
    @guarded_memory_write
    def write():return result
    with guard.bind():write()
    assert outcomes(registry,task)==[expected]


def test_cancel_before_actual_pipeline_writes_nothing(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    registry.cancel(task['id'],task['scope'])
    with guard.bind(),pytest.raises(TaskCancelled):
        WritePipeline(store).process('请记住：我喜欢清淡的绿茶。',turn_id='qq-test')
    assert not store.list_memories()
    assert not store.list_candidates()
    assert outcomes(registry,task)==[]


def test_reserved_real_commit_finishes_and_counts_once_after_cancel(tmp_path,monkeypatch):
    registry,task,guard,store=fixture(tmp_path)
    original=store._connection
    @contextmanager
    def commit():
        with original() as db:
            state=registry.get(task['id'],task['scope'])
            if any(e['outcome']=='in_flight' for e in state['effects'].values()):
                registry.cancel(task['id'],task['scope'])
            yield db
    monkeypatch.setattr(store,'_connection',commit)
    with guard.bind():
        result=WritePipeline(store).process('请记住：我喜欢清淡的绿茶。',turn_id='qq-test')
    assert result['committed'] and len(store.list_memories())==1
    assert outcomes(registry,task)==['committed']
    with guard.bind(),pytest.raises(TaskCancelled):
        WritePipeline(store).process('请记住：我喜欢安静的房间。',turn_id='qq-test')
    assert len(store.list_memories())==1


def test_cancel_while_waiting_for_real_store_lock_prevents_commit(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    entered=threading.Event()
    def worker():
        with guard.bind():
            entered.set()
            return WritePipeline(store).process('请记住：我喜欢清淡的绿茶。')
    with ThreadPoolExecutor(max_workers=1) as pool:
        with store._lock:
            future=pool.submit(worker)
            assert entered.wait(3)
            registry.cancel(task['id'],task['scope'])
        with pytest.raises(TaskCancelled):future.result(timeout=3)
    assert not store.list_memories()
    assert outcomes(registry,task)==[]


def test_multi_fact_write_stops_after_first_commit_and_returns_partial_receipt(tmp_path,monkeypatch):
    registry,task,guard,store=fixture(tmp_path)
    pipeline=WritePipeline(store)
    extract=pipeline.extractor.extract
    def two(*args,**kwargs):
        proposals=extract(*args,**kwargs)
        return proposals+proposals
    monkeypatch.setattr(pipeline.extractor,'extract',two)
    original=store.upsert_memory
    calls=[]
    def first(*args,**kwargs):
        result=original(*args,**kwargs)
        calls.append(True)
        registry.cancel(task['id'],task['scope'])
        return result
    monkeypatch.setattr(store,'upsert_memory',first)
    with guard.bind():
        result=pipeline.process('请记住：我喜欢清淡的绿茶。')
    assert result['status']=='cancelled' and len(result['committed'])==1
    assert len(calls)==1 and len(store.list_memories())==1
    assert outcomes(registry,task)==['committed']


@pytest.mark.parametrize('v4',[False,True])
def test_actual_knowledgebase_rejects_late_save_before_disk_or_ram_change(tmp_path,v4):
    registry,task,guard,_=fixture(tmp_path)
    kb=KnowledgeBase(base_dir=str(tmp_path/'kb'))
    kb.sqlite_store=None;kb.embedding_provider=None
    kb.settings['memory_v4_write_enabled']=v4
    if v4:kb.initialize_memory_v4_store()
    before=[dict(m) for m in kb.memories]
    registry.cancel(task['id'],task['scope'])
    with guard.bind(),pytest.raises(TaskCancelled):
        kb.capture_explicit_memory('请记住：我喜欢清淡的绿茶。',session_id='qq-test')
    assert kb.memories==before
    if v4:assert not kb.memory_v4_store.list_memories()


def test_v4_commit_and_legacy_projection_settle_together_when_cancelled(tmp_path,monkeypatch):
    registry,task,guard,_=fixture(tmp_path)
    kb=KnowledgeBase(base_dir=str(tmp_path/'kb'))
    kb.sqlite_store=None;kb.embedding_provider=None
    kb.settings['memory_v4_write_enabled']=True
    kb.initialize_memory_v4_store()
    original=kb._mirror_v4_write_to_legacy
    def mirror(result):
        registry.cancel(task['id'],task['scope'])
        return original(result)
    monkeypatch.setattr(kb,'_mirror_v4_write_to_legacy',mirror)
    with guard.bind():
        saved=kb.capture_explicit_memory('请记住：我喜欢清淡的绿茶。',session_id='qq-test')
    assert saved and len(kb.memory_v4_store.list_memories())==1
    assert any(m['id']==saved['id'] for m in kb.memories)
    assert outcomes(registry,task)==['committed']


def test_nested_commit_confirmation_survives_later_projection_failure(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    @guarded_memory_write
    def outer():
        result=WritePipeline(store).process('请记住：我喜欢清淡的绿茶。')
        assert result['committed']
        raise RuntimeError('synthetic projection failure')
    with guard.bind(),pytest.raises(RuntimeError):outer()
    assert len(store.list_memories())==1
    assert outcomes(registry,task)==['committed']


def test_copied_commit_context_cannot_authorize_later_background_save(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    captured=[]
    @guarded_memory_write
    def reserve():
        captured.append(copy_context())
        return False
    with guard.bind():reserve()
    registry.cancel(task['id'],task['scope'])
    with pytest.raises(TaskCancelled):
        captured[0].run(WritePipeline(store).process,'请记住：我喜欢清淡的绿茶。')
    assert not store.list_memories()


def test_cancelled_qq_guard_does_not_block_unbound_web_write(tmp_path):
    registry,task,guard,store=fixture(tmp_path)
    registry.cancel(task['id'],task['scope'])
    result=WritePipeline(store).process('请记住：我喜欢清淡的绿茶。',turn_id='web-test')
    assert result['committed'] and len(store.list_memories())==1
    assert outcomes(registry,task)==[]
