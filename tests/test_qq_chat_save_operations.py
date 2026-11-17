import asyncio
import re
from unittest.mock import patch

import pytest

from saya_app.services.qq_chat_saves import QQChatSaves
from saya_app.services.qq_rollback import check_memory_rollback
from test_qq_memory_candidate_quotes import candidate, responder, send
from test_qq_memory_reference_dialogue import approve, lab


def operation_id(text):
    return re.search(r'操作编号：([a-f0-9]{32})', text)[1]


@pytest.mark.parametrize('v4', [False, True])
def test_first_owner_message_saves_and_result_command_reads_that_operation(tmp_path, v4):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            k.settings['memory_v4_write_enabled'] = v4
            with b.store.connect() as db:
                db.execute('delete from conversations where scope=?', (scope,))
            responder(b, m)
            await send(b, '/remember 我爱喝清淡的乌龙茶。', 301)
            assert len(k.memories) == 1 and k.memories[0]['content'] == '我爱喝清淡的乌龙茶。'
            operation = operation_id(b.client.sent[-1][1])
            session = b.store.conversation(scope)[1]
            assert m.references.saves.lookup(scope, session, operation).committed
            await send(b, '/memory result ' + operation, 302)
            assert '已确认并保存' in b.client.sent[-1][1] and len(k.memories) == 1
            assert not m.references.saves.lookup(scope, sid, operation)
    asyncio.run(run())


def test_replay_reads_the_committed_result_after_receipt_index_failure(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            q.text = '请务必记住：我晚上喜欢安静读书。'
            with patch.object(k, 'capture_explicit_memory', wraps=k.capture_explicit_memory) as write:
                with patch.object(m, 'saved_receipt', side_effect=OSError('index unavailable')):
                    first = await m.references.resolve(q, sid)
                op = operation_id(first)
                assert first.committed and '回执索引待核对' in first
                assert len(k.memories) == 1 and k.memories[0]['is_important']
                m.references.saves = QQChatSaves(b.store, k._memory_write_lock)
                assert (await m.references.resolve(q, sid)).committed and write.call_count == 1
                assert '我晚上喜欢安静读书' in m.command(m.references.actor(q), '/memory result ' + op, sid)
                missing = m.command(m.references.actor(q), '/memory result ' + op, 'wrong')
                assert '没有可核对' in missing and op not in missing and '我晚上喜欢安静读书' not in missing
                q.text = '记住：改变后不应当使用同一个入站操作。'
                assert '没有再次保存' in await m.references.resolve(q, sid) and write.call_count == 1
    asyncio.run(run())


@pytest.mark.parametrize('change',['delete','correct','block'])
def test_original_result_survives_later_item_changes_without_false_index_failure(tmp_path,change):
    async def run():
        async with lab(tmp_path) as (b,s,k,m,manager,q,scope,sid,tag):
            q.text='记住：虚构的砾港计划使用蓝色标记。'
            first=await m.references.resolve(q,sid)
            operation=operation_id(first)
            saved=k.memories[0]
            original=m.references.saves.lookup(scope,sid,operation).items
            if change=='delete':
                preview=k.preview_memory_deletion([saved['id']],reason='user_delete')
                result=k.delete_memories([saved['id']],reason='user_delete',confirmation_token=preview['confirmation_token'])
                assert result['status']=='success'
            elif change=='correct':assert k.update_memory(saved['id'],{'content':'虚构的砾港计划改用白色标记。'})
            else:assert k.set_memory_use_policy(saved['id'],True)['policy_applied']
            with patch.object(k,'capture_explicit_memory',side_effect=AssertionError('result lookup cannot write')):
                reply=m.references.result(m.references.actor(q),sid,operation)
            assert '原保存操作已提交' in reply and '回执索引待核对' not in reply
            assert '蓝色标记' not in reply and saved['id'] in reply
            assert m.references.saves.lookup(scope,sid,operation).items==original
    asyncio.run(run())


def test_unknown_write_is_queryable_and_never_replayed(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            q.text = '记住：我经常喝清茶。'
            with patch.object(k, 'capture_explicit_memory', side_effect=OSError('private diagnostic')) as write:
                first = await m.references.resolve(q, sid)
                op = operation_id(first)
                m.references.saves = QQChatSaves(b.store, k._memory_write_lock)
                assert (await m.references.resolve(q, sid)) == first and write.call_count == 1
            assert not first.committed and not k.memories and '待核对' in first
            assert 'private diagnostic' not in first
            assert m.references.result(m.references.actor(q), sid, op) == first
    asyncio.run(run())


def test_candidate_status_failure_keeps_actual_commit_and_repeat_confirmation_reads_it(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            c = candidate(s, k, m, q)
            preview = m.references.offer_candidate(q, sid, c['id'])
            b.action_proposals.delivered(scope, sid, str(preview))
            responder(b, m)
            with patch.object(k, 'save_memory_candidates', return_value=False):
                await send(b, '记住', 311)
            first = b.client.sent[-1][1]
            op = operation_id(first)
            assert len(k.memories) == 1 and '候选状态待核对' in first
            assert k._find_memory_candidate(c['id'])['status'] == 'pending'
            with patch.object(k, 'confirm_memory_candidate', side_effect=AssertionError('repeat may not write')):
                await send(b, '记住', 312)
            assert op in b.client.sent[-1][1] and len(k.memories) == 1
            assert m.references.saves.lookup(scope, sid, op).items[0]['candidate_status_pending']
    asyncio.run(run())


def test_single_save_requires_the_delivered_exact_payload_and_uses_proposal_id(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            preview = await m.references.resolve(q, sid)
            with b.store.connect() as db:
                row = db.execute('select id,payload from qq_action_proposals').fetchone()
            import json
            plan = json.loads(row['payload'])['plan']
            assert not m.references.commit(q, plan, row['id']).committed
            decision = approve(b, scope, sid, preview)
            assert not m.references.commit(q, {**plan, 'content': '改写过的正文'}, decision['id']).committed
            first = m.references.commit(q, plan, decision['id'])
            assert first.committed and operation_id(first) == decision['id']
            assert m.references.commit(q, plan, decision['id']) == first and len(k.memories) == 1
    asyncio.run(run())


def test_managed_shared_generator_cannot_repeat_a_save_but_web_keeps_its_path(tmp_path):
    from core_services import KnowledgeBase

    k = KnowledgeBase(str(tmp_path / 'kb'))
    for enabled in (False, True):
        k.settings['memory_v4_write_enabled'] = enabled
        text = f'记住：我喜欢雨天安静读书，试验序号为{int(enabled)}。'
        assert not k.capture_explicit_memory(text, allow_save=False)
        assert k.capture_explicit_memory(text)
    assert len(k.memories) == 2


def test_save_results_require_compatible_rollback_reader(tmp_path):
    import sqlite3

    path = tmp_path / 'knowledge_base/qq/bridge.sqlite3'
    path.parent.mkdir(parents=True)
    with sqlite3.connect(path) as db:
        db.execute('create table qq_chat_save_operations(id text)')
        db.execute('insert into qq_chat_save_operations values(?)', ('operation',))
    assert 'qq_chat_save_operations_v1' in check_memory_rollback(tmp_path, {})['missing']


def test_concurrent_retries_share_one_claim_and_return_the_same_commit(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading
    from test_qq_tasks import Store

    saves = QQChatSaves(Store(tmp_path / 'operations.sqlite3'), threading.RLock())
    start = threading.Barrier(2)
    calls = []
    def write(plan):
        calls.append(plan)
        return {'state': 'committed', 'memory_id': 'stored', 'version': 'v1'}
    def invoke():
        start.wait(timeout=5)
        return saves.run('one', 'scope', 'session', 'literal', [{'content': 'same'}],
                         verify=lambda db: True, valid=lambda plan: True, write=write, retention=86400)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(invoke) for _ in range(2)]
        outcomes = [future.result(timeout=10) for future in futures]
    assert len(calls) == 1
    assert saves.lookup('scope', 'session', 'one').committed
    assert all(result.id == 'one' for result in outcomes)
    assert sum(result.fresh for result in outcomes) == 1


def test_postcommit_storage_error_keeps_unknown_and_never_repeats_the_write(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            q.text = '记住：虚构实验中的茶杯颜色是蓝色。'
            actual = k.capture_explicit_memory
            def committed_then_failed(*args, **kwargs):
                actual(*args, **kwargs)
                raise OSError('postcommit indexing failure')
            with patch.object(k, 'capture_explicit_memory', side_effect=committed_then_failed) as write:
                first = await m.references.resolve(q, sid)
                assert not first.committed and '待核对' in first and len(k.memories) == 1
                assert await m.references.resolve(q, sid) == first and write.call_count == 1
            op = operation_id(first)
            assert m.references.result(m.references.actor(q), sid, op) == first
    asyncio.run(run())
