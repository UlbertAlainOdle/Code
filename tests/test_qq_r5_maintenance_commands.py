"""Exercise R5 through authenticated QQ ingress and the actual delivery loop."""
import asyncio
import json
import re
import sqlite3
import time
from types import SimpleNamespace

import pytest

from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.qq_maintenance_commands import command
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_memory_reference_dialogue import fingerprint as history_fingerprint
from saya_app.services.qq_responses import is_control_reference
from test_qq_delivery import event
from test_qq_memory_management import lab
from test_qq_memory_reference_dialogue import approve, lab as reference_lab
from test_qq_r5_maintenance_links import memory_links
from test_qq_task_scheduler import submit, until
from test_qq_topic_routing import create, item, topics


async def run_command(bridge, text, mid):
    start = len(bridge.client.sent)
    assert submit(bridge, text, mid)[0] == 'queued'
    await asyncio.wait_for(bridge.queue.join(), 5)
    await until(lambda: not bridge._task_workers)
    return bridge.client.sent[start:]


def bind_memory_responder(bridge, service):
    async def forbidden(*args, **kwargs):
        pytest.fail('R5 management must not invoke a model')
    responder = QQChatResponder(bridge.settings, lambda **kw: SimpleNamespace(**kw),
                                forbidden, lambda: 'api', lambda: {})
    responder.memory_management = service
    bridge.bind_responder(responder)


def test_owner_qq_preview_confirm_queries_and_undo_preserve_originals(memory_links):
    async def run():
        bridge, _, service, _, _, store = memory_links
        bind_memory_responder(bridge, service)
        originals = store.export_state()
        await bridge.start()
        try:
            preview = await run_command(bridge, '/memory link first second', 1)
            shown = '\n'.join(x[1] for x in preview)
            assert '并列核对' in shown and '偏爱浅烘咖啡' in shown and '喜欢浅烘咖啡' in shown
            operation = re.search(r'/memory link-confirm ([a-f0-9]{32})', shown)[1]
            with bridge.store.connect() as db:
                assert db.execute('select preview_state from qq_maintenance_links where id=?',
                                  (operation,)).fetchone()[0] == 'delivered'
                assert db.execute('select count(*) from qq_deliveries').fetchone()[0] == 0
                receipts = [r[0] for r in db.execute('select receipt from qq_control_receipt_ids')]
            assert len(receipts) == len(preview) and all(
                is_control_reference(bridge.store, preview[0][0], receipt) for receipt in receipts)
            for mid in (2, 3):
                rows = await run_command(bridge, '/memory link-confirm ' + operation, mid)
                assert '联合视图已确认' in rows[-1][1]
            for mid, member in [(4, 'first'), (5, 'second')]:
                rows = await run_command(bridge, '/memory link-show ' + member, mid)
                assert '原编号：first、second' in '\n'.join(x[1] for x in rows)
            rows = await run_command(bridge, '/memory link-result ' + operation, 6)
            assert '联合视图已确认' in rows[-1][1]
            rows = await run_command(bridge, '/memory link-undo ' + operation, 7)
            assert '已撤销' in rows[-1][1]
            rows = await run_command(bridge, '/memory link-show first', 8)
            assert '没有可用' in rows[-1][1]
            assert store.export_state() == originals
        finally:
            await bridge.stop()
    asyncio.run(run())


def test_source_revocation_between_parts_blocks_footer_and_confirmation(memory_links):
    async def run():
        bridge, kb, service, _, _, _ = memory_links
        bridge.settings = bridge.settings.model_copy(update={
            'delivery_mode': 'legacy', 'chunk_chars': 95, 'reply_max_messages': 5,
        })
        bind_memory_responder(bridge, service)
        original_send = bridge.client.send_part

        async def revoke_after_send(*args, **kwargs):
            result = await original_send(*args, **kwargs)
            if len(bridge.client.sent) == 1:
                kb.memory_use.set_block('second', True)
            return result

        bridge.client.send_part = revoke_after_send
        await bridge.start()
        try:
            rows = await run_command(bridge, '/memory link first second', 1)
            shown = '\n'.join(x[1] for x in rows)
            assert '/memory link-confirm' not in shown and '来源或权限已变化' in shown
            with bridge.store.connect() as db:
                operation, preview_state = db.execute('select id,preview_state from qq_maintenance_links').fetchone()
                assert preview_state == 'waiting'
                assert db.execute('select count(*) from qq_deliveries').fetchone()[0] == 0
            rows = await run_command(bridge, '/memory link-confirm ' + operation, 2)
            assert '完整预览未核实送达' in rows[-1][1]
        finally:
            await bridge.stop()
    asyncio.run(run())


def test_preview_that_does_not_fit_saves_no_confirmation(memory_links):
    async def run():
        bridge, _, service, _, _, _ = memory_links
        bridge.settings = bridge.settings.model_copy(update={
            'delivery_mode': 'legacy', 'chunk_chars': 95, 'reply_max_messages': 1,
        })
        bind_memory_responder(bridge, service)
        await bridge.start()
        try:
            rows = await run_command(bridge, '/memory link first second', 1)
            assert '不能完整显示' in rows[-1][1]
            with bridge.store.connect() as db:
                assert not db.execute("select 1 from sqlite_master where name='qq_maintenance_links'").fetchone()
        finally:
            await bridge.stop()
    asyncio.run(run())


def test_storage_failure_points_to_original_result_instead_of_generation(memory_links):
    bridge, _, _, _, _, _ = memory_links
    operation = 'a' * 32
    text = '/memory link-confirm ' + operation
    message, _ = normalize_event(event(text), bridge.settings)
    def fail(_):
        raise sqlite3.OperationalError('fixture database interruption')
    reply = command(SimpleNamespace(confirm=fail, result=None, undo=None), message, text.split()[1:],
                    prefix='/memory', settings=bridge.settings)
    assert '操作结果待核对' in reply and '/memory link-result ' + operation in reply


def test_real_reference_batch_recovers_user_words_from_matching_committed_plan(tmp_path):
    async def run():
        async with reference_lab(tmp_path) as (bridge, _, kb, service, history, msg, scope, session, tag):
            kb.update_settings({'memory_v4_read_enabled': True, 'memory_v4_write_enabled': True,
                                'embedding_enabled': False})
            facts = ['虚构温室在白天使用蓝色标记。', '虚构温室在夜间使用橙色标记。']
            for text in facts:
                history.save_message('user', text, session_id=session, metadata={
                    'qq_event_dependencies': tag, 'memory_use_epoch': kb.memory_use.snapshot(),
                    'memory_history_floor': kb.memory_use.history_floor(),
                })
            source_rows = [row for row in history.load_session_full(session, make_current=False)
                           if row['role'] == 'user' and row['content'] in facts]
            assert len(source_rows) == 2
            now = time.time()
            options = [{'id': f'h{index}', 'author': '用户', 'text': row['content'], 'source': {
                'hash': history_fingerprint(row), 'tag': tag, 'sent_at': now, 'expires': now + 600,
            }} for index, row in enumerate(source_rows, start=1)]
            preview = service.references.sets.preview(msg, session, options)
            decision = approve(bridge, scope, session, preview)
            reply = service.references.sets.commit(msg, decision['id'], decision['payload'])
            assert '已确认并保存全部2条' in reply
            bridge.action_proposals.finish(decision['id'], True)
            operation = service.references.saves.lookup(scope, session, decision['id'])
            members = [entry['memory_id'] for entry in operation.items]
            for member in members:
                assert any(row.quote.startswith('记住：用户选择保存本次用户的表述')
                           for row in kb.memory_v4_store.list_evidence(member))
            actor = service.references.actor(msg)
            review = service.maintenance_review(actor, members[0], members[1:])
            assert {review['anchor'].sources[0].text,
                    review['candidates'][0]['record'].sources[0].text} == set(facts)
            for member in members:
                kb.normalize_memory(kb._find_memory(member))
            assert kb.save_memories()
            assert all(kb.memory_v4_store.get_memory(member).revision > 1 for member in members)
            normalized = service.maintenance_review(actor, members[0], members[1:])
            assert normalized['candidates'][0]['record'].sources[0].text == review['candidates'][0]['record'].sources[0].text
            current = kb.memory_v4_store.get_memory(members[1]).canonical_text
            kb.memory_v4_store.update_memory(members[1], {'canonical_text': '试验改为紫色。'}, operation='correct')
            kb.memory_v4_store.update_memory(members[1], {'canonical_text': current}, operation='restore')
            assert service.maintenance_review(actor, members[0], members[1:])['candidates'] == []
            with bridge.store.connect() as db:
                db.execute('update qq_chat_save_operations set fingerprint=? where id=?',
                           ('fixture-mismatch', decision['id']))
            assert service.maintenance_review(actor, members[0], members[1:])['status'] == 'anchor_unavailable'
    asyncio.run(run())


def test_personal_event_command_shows_both_originals_and_undoes(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, choice, _):
            facts = ('云苇展台负责登记。', '云苇展台负责归还。')
            incoming = message(''.join(facts))
            choice[0] = {'items': [item(fact, personal=create(), mode='progress') for fact in facts]}
            await service.observe(incoming, [])
            before = service.people._canonical('20000')
            assert len(before) == 2
            ids = [card['id'] for card in before]
            rows = await run_command(bridge, '/events link ' + ' '.join(ids), 201)
            preview = '\n'.join(row[1] for row in rows)
            assert all(fact in preview for fact in facts)
            operation = re.search(r'/events link-confirm ([a-f0-9]{32})', preview)[1]
            for mid in (202, 203):
                rows = await run_command(bridge, '/events link-confirm ' + operation, mid)
                assert '联合视图已确认' in rows[-1][1]
            for mid, card in enumerate(ids, 204):
                rows = await run_command(bridge, '/events link-show ' + card, mid)
                assert all(fact in '\n'.join(row[1] for row in rows) for fact in facts)
            rows = await run_command(bridge, '/events link-undo ' + operation, 206)
            assert '已撤销' in rows[-1][1]
            rows = await run_command(bridge, '/events link-result ' + operation, 207)
            assert '已撤销' in rows[-1][1]
            assert service.people._canonical('20000') == before
    asyncio.run(run())


def test_group_command_uses_existing_coordinator_and_rejects_private_replay(tmp_path):
    async def run():
        async with topics(tmp_path) as (bridge, service, _, message, _, _):
            unified = service.unified
            ids = []
            for text in ('星桥书会负责登记。', '星桥书会负责归还。'):
                row = unified.commit(message(text, group=90000), '90000', {'title': '星桥书会'})
                unified.command(message(f'/events group coordinator {row} 20000', group=90000))
                ids.append(row)

            async def group_command(text, mid, actor=20000):
                start = len(bridge.client.sent)
                body = json.dumps(event(text, mid, actor, group_id=90000)).encode()
                assert bridge.submit_signed(body, 'Bearer inbound')[0] == 'queued'
                await asyncio.wait_for(bridge.queue.join(), 5)
                await until(lambda: not bridge._task_workers)
                return '\n'.join(x[1] for x in bridge.client.sent[start:])

            request = '/events group link ' + ' '.join(ids)
            assert '不允许' in await group_command(request, 101, 30000)
            preview = await group_command(request, 102)
            assert '负责登记' in preview and '负责归还' in preview
            operation = re.search(r'/events group link-confirm ([a-f0-9]{32})', preview)[1]
            rows = await run_command(bridge, '/events group link-confirm ' + operation, 103)
            assert '请在原群' in rows[-1][1]
            assert '联合视图已确认' in await group_command('/events group link-confirm ' + operation, 104)
            assert '负责登记' in await group_command('/events group link-show ' + ids[1], 105)
            assert '已撤销' in await group_command('/events group link-undo ' + operation, 106)
    asyncio.run(run())
