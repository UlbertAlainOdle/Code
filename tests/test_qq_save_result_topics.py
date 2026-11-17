"""Explicit operation queries survive new topics without relaxing write scope."""
import asyncio
from dataclasses import replace
import json
import time
from unittest.mock import patch

import pytest

from saya_app.services.qq_chat_saves import QQChatSaves
from saya_app.services.qq_context_links import QQContextLinks, web_history_source
from saya_app.services.qq_memory_access import MemoryAccessDenied
from test_qq_chat_save_operations import operation_id
from test_qq_memory_candidate_quotes import responder, send
from test_qq_memory_reference_dialogue import approve, lab


def operation_rows(b):
    with b.store.connect() as db:
        return [tuple(x) for x in db.execute('select * from qq_chat_save_operations order by id')]


def query(m, actor, session, operation):
    return m.command(actor, '/memory result ' + operation, session)


def test_real_batch_save_query_after_context_new_and_reader_reload_is_readonly(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            plans = [{'content': f'虚构验收箱第{i}号使用琥珀色指示灯。'} for i in range(3)]
            first = m.references.saves.run('a' * 32, scope, sid, 'set', plans,
                verify=lambda db: True, valid=lambda plan: True,
                write=lambda plan: m.references.capture(q, sid, plan['content']), retention=86400)
            assert first.committed and len(k.memories) == 3
            ids = {item['memory_id'] for item in first.items}
            b.context_links = QQContextLinks(b.store, web_history_source(manager))
            responder(b, m)
            await send(b, '/context new', 701)
            current = b.store.conversation(scope)[1]
            assert current != sid
            before = operation_rows(b)
            with patch.object(k, 'capture_explicit_memory', side_effect=AssertionError('query must not write')):
                for mid in [702, 703]:
                    m.references.saves = QQChatSaves(b.store, k._memory_write_lock)
                    await send(b, '/memory result ' + first.id, mid)
                    text = b.client.sent[-1][1]
                    assert '全部3条' in text and all(memory_id in text for memory_id in ids)
            assert operation_rows(b) == before and len(k.memories) == 3
            # The mutating/replay reader remains bound to the original session.
            assert m.references.saves.lookup(scope, sid, first.id).committed
            assert m.references.saves.lookup(scope, current, first.id) is None
    asyncio.run(run())


@pytest.mark.parametrize('state', ['pending', 'unknown', 'partial'])
def test_cross_topic_queries_preserve_unsettled_results_without_retry(tmp_path, state):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            items = ([{'state': 'committed', 'memory_id': 'original', 'version': 'v1'},
                      {'state': 'unknown'}, {'state': 'pending'}]
                     if state == 'partial' else [{'state': state}])
            op = 'b' * 32
            with b.store.connect() as db:
                db.execute('insert into qq_chat_save_operations values(?,?,?,?,?,?,?)',
                    (op, scope, sid, 'set', 'fixture', time.time() + 600, json.dumps(items)))
            current = 'session_qq_query_new.jsonl'
            b.store.save_conversation(scope, [], current)
            before = operation_rows(b)
            with patch.object(k, 'capture_explicit_memory', side_effect=AssertionError('no replay')):
                answer = query(m, m.references.actor(q), current, op)
            assert '没有可核对' not in answer and not answer.committed
            assert m.references.saves.lookup(scope, sid, op).items == items
            assert operation_rows(b) == before and not k.memories
    asyncio.run(run())


def test_cross_topic_query_keeps_actor_current_topic_expiry_and_exact_id_checks(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            q.text = '记住：虚构实验中的茶杯颜色为蓝色。'
            saved = await m.references.resolve(q, sid)
            op = operation_id(saved)
            current = 'session_qq_query_new.jsonl'
            b.store.save_conversation(scope, [], current)
            actor = m.references.actor(q)
            assert op in query(m, actor, current, op)
            assert '没有可核对' in m.references.result(actor, current, op)
            assert '没有可核对' in query(m, actor, sid, op)
            assert '没有可核对' in query(m, actor, current, op[:-1])
            for denied in [replace(actor, verified=False), replace(actor, is_owner=False),
                           replace(actor, bot_id='99999'), replace(actor, group_id='90000', group_authorized=True)]:
                with pytest.raises(MemoryAccessDenied):
                    query(m, denied, current, op)
            b.settings = b.settings.model_copy(update={'owner_ids': b.settings.owner_ids | {'30000'}})
            other = replace(actor, user_id='30000')
            b.store.save_conversation('10000:private:30000:shared', [], current)
            assert '没有可核对' in query(m, other, current, op)
            with b.store.connect() as db:
                db.execute('update qq_chat_save_operations set expires=? where id=?', (time.time() - 1, op))
            assert '没有可核对' in query(m, actor, current, op)
    asyncio.run(run())


def test_legacy_batch_query_after_new_topic_keeps_original_schema_and_scope(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            op = 'c' * 32
            items = [{'state': 'committed', 'memory_id': 'legacy-original', 'version': 'v1'}]
            with b.store.connect() as db:
                db.execute('insert into qq_memory_reference_batches values(?,?,?,?,?)',
                    (op, scope, sid, 'fixture', json.dumps(items)))
            current = 'session_qq_query_new.jsonl'
            b.store.save_conversation(scope, [], current)
            actor = m.references.actor(q)
            assert 'legacy-original' in query(m, actor, current, op)
            b.settings = b.settings.model_copy(update={'owner_ids': b.settings.owner_ids | {'30000'}})
            b.store.save_conversation('10000:private:30000:shared', [], current)
            assert '没有可核对' in query(m, replace(actor, user_id='30000'), current, op)
            with b.store.connect() as db:
                assert len(db.execute('pragma table_info(qq_memory_reference_batches)').fetchall()) == 5
            assert not k.memories
    asyncio.run(run())


@pytest.mark.parametrize('change', ['edited', 'deleted'])
def test_cross_topic_result_keeps_historical_id_without_reviving_old_body(tmp_path, change):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            q.text = '记住：虚构的旧茶杯颜色是蓝色。'
            saved = await m.references.resolve(q, sid)
            op = operation_id(saved)
            memory_id = k.memories[0]['id']
            if change == 'edited':
                assert k.update_memory(memory_id, {'content': '虚构的新茶杯颜色是绿色。'})
            else:
                assert k.delete_memory(memory_id)
            current = 'session_qq_query_new.jsonl'
            b.store.save_conversation(scope, [], current)
            before = operation_rows(b)
            ids = [item['id'] for item in k.memories]
            with patch.object(k, 'capture_explicit_memory', side_effect=AssertionError('do not restore old memory')):
                answer = query(m, m.references.actor(q), current, op)
            assert memory_id in answer and '旧茶杯颜色是蓝色' not in answer
            assert [item['id'] for item in k.memories] == ids and operation_rows(b) == before
    asyncio.run(run())


def test_new_topic_still_cannot_confirm_old_delivered_save_preview(tmp_path):
    async def run():
        async with lab(tmp_path) as (b, s, k, m, manager, q, scope, sid, tag):
            preview = await m.references.resolve(q, sid)
            decision = approve(b, scope, sid, preview)
            assert decision['status'] == 'approved'
            b.store.save_conversation(scope, [], 'session_qq_query_new.jsonl')
            result = m.references.commit(q, decision['payload']['plan'], decision['id'])
            assert not result.committed and not k.memories
    asyncio.run(run())
