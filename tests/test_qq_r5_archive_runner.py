"""Test the fixed archival scenario through the dedicated webhook and real stores."""
import asyncio
from contextlib import closing
from pathlib import Path
import sqlite3
import subprocess
import sys

import httpx
import pytest

from core_services import KnowledgeBase
from saya_app.services.qq_bridge import QQStore
from scripts.qq_test_archive import ArchiveEvidence, DUPLICATE, FACT, KEEP, STEPS, execute, preview, seed
from scripts.qq_test_instance import create_app, prepare
from scripts.qq_test_sender import TestTarget
from test_qq_delivery import Client, event
from test_qq_r5_test_instance import post_signed
from test_qq_task_scheduler import until


@pytest.fixture
def lab(tmp_path):
    target = TestTarget('fixture-archive', '22000', '33000', 'http://127.0.0.1:3010', 'http://127.0.0.1:3011')
    path = prepare('archive-fixture', tmp_path)
    return target, path, KnowledgeBase(str(path / 'knowledge_base'))


class Receiver(Client):
    def __init__(self):
        super().__init__()
        self.history = []

    async def call(self, action, payload):
        if action == 'get_login_info':
            return {'user_id': 33000}
        assert action == 'get_friend_msg_history'
        return {'messages': self.history[-40:]}


def test_seed_keeps_two_projections_of_one_source_across_restart(lab):
    target, _, kb = lab
    seed(kb, target)
    assert {row['id'] for row in kb.memories} == {KEEP, DUPLICATE}
    before = kb.memory_v4_store.export_state()
    assert len(before['evidence']) == 2
    reopened = KnowledgeBase(kb.base_dir)
    seed(reopened, target)
    assert len(reopened.memories) == 2
    assert reopened.memory_v4_store.export_state()['evidence'] == before['evidence']


def test_existing_unrelated_memory_blocks_fixture_seed(lab):
    target, _, kb = lab
    assert kb.add_memory('Unrelated', source='manual')
    with pytest.raises(ValueError, match='archive_fixture_requires_empty_dedicated_kb'):
        seed(kb, target)


def test_fresh_cli_bootstrap_redirects_default_kb(tmp_path):
    root = Path(__file__).resolve().parents[1]
    code = """
import ast, sys
from pathlib import Path
from scripts.qq_test_archive import isolated_kb, seed
from scripts.qq_test_sender import TestTarget, _configured_production_accounts
root, fixture = Path.cwd(), Path(sys.argv[1])
tree = ast.parse((root / 'saya_app/services/qq_rollback.py').read_text(encoding='utf-8'))
guard = next(ast.literal_eval(statement.value) for statement in tree.body
             if isinstance(statement, ast.Assign)
             and any(isinstance(target, ast.Name) and target.id == 'NETWORK_GUARD' for target in statement.targets))
exec(guard, {})
assert _configured_production_accounts(fixture) == {'10000', '20000'}
kb = isolated_kb(fixture)
assert Path(kb.base_dir) == fixture / 'knowledge_base'
seed(kb, TestTarget('isolated-bootstrap', '22000', '33000', 'http://127.0.0.1:3010', 'http://127.0.0.1:3011'))
assert len(kb.memories) == 2
"""
    (tmp_path / '.env.qq').write_text('SAYA_QQ_BOT_ID=10000\nSAYA_QQ_OWNER_IDS=20000\n', encoding='utf-8')
    result = subprocess.run([sys.executable, '-B', '-X', 'utf8', '-c', code, str(tmp_path)],
                            cwd=root, capture_output=True, text=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr


def test_archive_case_with_real_webhook_and_storage(lab, monkeypatch):
    original_admit = QQStore.admit
    monkeypatch.setattr(QQStore, 'admit', lambda self, message, settings:
                        original_admit(self, message, settings.model_copy(update={'cooldown_seconds': 0})))

    async def run():
        target, path, kb = lab
        receiver = Receiver()
        app = create_app(target, path, 'test-outbound', 'test-inbound', client=receiver, archive=kb)
        evidence = ArchiveEvidence(path / 'knowledge_base' / 'qq' / 'bridge.sqlite3', target)
        async with app.router.lifespan_context(app), httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url='http://test') as http:
            assert (await http.get('/health')).json()['mode'] == 'r5-a-archive-fixture'
            endpoint = '/api/qq/webhook'
            other = event(f'/memory archive {KEEP} memory:other', 1, user_id=22000) | {'self_id': 33000}
            assert (await post_signed(http, endpoint, other)).json()['status'] == 'ignored'

            class Sender:
                count = 0

                async def call(self, action, body):
                    if action == 'get_login_info':
                        return {'user_id': 22000}
                    assert action == 'send_private_msg' and body['user_id'] == 33000
                    self.count += 1
                    inbound = 1000 + self.count
                    text = body['message'][0]['data']['text']
                    receiver.history.append({'message_id': inbound, 'user_id': 22000, 'message': body['message']})
                    offset = len(receiver.sent)
                    payload = event(text, inbound, user_id=22000) | {'self_id': 33000}
                    response = await post_signed(http, endpoint, payload)
                    assert response.json()['status'] == 'queued'
                    await until(lambda: evidence.admission(str(inbound)) == 'sent')
                    for index, (message, content, reference, _) in enumerate(receiver.sent[offset:], offset + 501):
                        assert message.message_id == str(inbound)
                        prefix = f'[CQ:reply,id={inbound}]' if reference else ''
                        receiver.history.append({'message_id': index, 'user_id': 33000, 'raw_message': prefix + content})
                    return {'message_id': 7000 + self.count}

            sender = Sender()
            ledger = path / 'runner.sqlite3'
            approval = preview(target, 'archive-fixture')['plan_id']
            result = await execute(target, 'archive-fixture', approval, sender, receiver, ledger, evidence,
                                   forbidden={'10000', '20000'}, wait_seconds=0, pause_seconds=0)
            assert result['status'] == 'verified', (result, [row[1] for row in receiver.sent])
            assert len(STEPS) == sender.count == 9
            evidence.check_operation(result['operation'], 'undone')
            assert all(kb.memory_allowed_for_use(row) for row in kb.memories)
            assert await execute(target, 'archive-fixture', approval, sender, receiver, ledger, evidence,
                                 forbidden={'10000', '20000'}, wait_seconds=0, pause_seconds=0) == result
            assert sender.count == 9
            with closing(sqlite3.connect(ledger)) as db:
                assert FACT not in str(db.execute('select * from personal_steps').fetchall())
    asyncio.run(run())
