import asyncio
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from core_services import KnowledgeBase
from saya_app.routers import qq as routes
from saya_app.services.qq_memory_access import MemoryActor, MemoryAccessDenied, MemoryAccessConflict
from saya_app.services.qq_memory_management import QQMemoryManagement
from test_qq_task_scheduler import make


@pytest.fixture
def lab(tmp_path):
    bridge = make(tmp_path / 'bridge', None)
    bridge.store.initialize(7)
    kb = KnowledgeBase(str(tmp_path / 'kb'))
    memory = kb.add_memory('喜欢茉莉茶。', title='茶的偏好', source='manual')
    bridge.store.set_group('10000', '90000', True)
    service = QQMemoryManagement(bridge, kb)
    bridge.memory_management = service
    owner = MemoryActor('10000', '20000', True, True)
    return bridge, kb, service, owner, memory


def test_separate_saved_and_pending_catalogs_and_stale_page(lab):
    _, kb, service, owner, memory = lab
    kb.add_memory_candidate('想去一处公园。', title='出行候选')
    saved = service.browse(owner)
    pending = service.browse(owner, kind='candidates')
    assert saved['items'][0]['id'] == memory['id']
    assert pending['items'][0]['title'] == '出行候选'
    assert saved['snapshot'] != pending['snapshot']
    kb.update_memory(memory['id'], {'content': '改为喜欢桂花茶。'})
    with pytest.raises(MemoryAccessConflict):
        service.browse(owner, snapshot=saved['snapshot'])


def test_catalog_stable_order_and_pages(lab):
    _, kb, service, owner, _ = lab
    for i in range(15):
        kb.add_memory('测试条目 ' + str(i), title=str(i), source='manual')
    first = service.browse(owner, page=1)
    second = service.browse(owner, page=2, snapshot=first['snapshot'])
    assert len(first['items']) == 10 and len(second['items']) == 6
    assert not {x['id'] for x in first['items']} & {x['id'] for x in second['items']}
    assert service.browse(owner, query='没有这种文字')['total'] == 0


@pytest.mark.parametrize('change', [dict(verified=False), dict(is_owner=False),
    dict(user_id='30000'), dict(bot_id='10001'), dict(group_id='90000', group_authorized=True)])
def test_identity_is_checked_against_live_configuration(lab, change):
    _, _, service, owner, _ = lab
    with pytest.raises(MemoryAccessDenied):
        service.browse(replace(owner, **change))


def test_publish_is_explicit_versioned_and_does_not_modify_memory(lab):
    bridge, kb, service, owner, memory = lab
    before = dict(kb._find_memory(memory['id']))
    detail = service.detail(owner, memory['id'])
    kwargs = dict(content_version=detail['content_version'], expected_revision=0,
        subject_id='20000', visibility='groups', groups=['90000'], requesters=['30000'], writers=[])
    with pytest.raises(MemoryAccessDenied):
        service.set_access(owner, memory['id'], confirmed=False, **kwargs)
    state = service.set_access(owner, memory['id'], confirmed=True, **kwargs)
    assert state['policy']['revision'] == 1 and state['policy_current']
    assert kb._find_memory(memory['id']) == before
    assert not state['group_recall_enabled']
    with pytest.raises(MemoryAccessConflict):
        service.set_access(owner, memory['id'], confirmed=True, **kwargs)
    kb.update_memory(memory['id'], {'content': '喜欢另一种茶。'})
    assert not service.detail(owner, memory['id'])['policy_current']
    with pytest.raises(MemoryAccessConflict):
        service.set_access(owner, memory['id'], confirmed=True, **(kwargs | {'expected_revision': 1}))


def test_revocation_uses_fresh_version_and_persists_on_reopen(lab):
    bridge, _, service, owner, memory = lab
    detail = service.detail(owner, memory['id'])
    kw = dict(content_version=detail['content_version'], subject_id='20000', confirmed=True)
    service.set_access(owner, memory['id'], expected_revision=0, visibility='public', requesters=['*'], **kw)
    service.set_access(owner, memory['id'], expected_revision=1, visibility='private', **kw)
    reopened = QQMemoryManagement(bridge, service.kb)
    policy = reopened.detail(owner, memory['id'])['policy']
    assert policy['revision'] == 2 and policy['visibility'] == 'private' and not policy['requesters']


@pytest.mark.parametrize('extra', [dict(groups=['90001']), dict(requesters=['Doctor']),
    dict(subject_id='Doctor'), dict(expected_revision=True), dict(groups=['90000'] * 129)])
def test_invalid_or_unauthorized_policy_never_saves(lab, extra):
    _, _, service, owner, memory = lab
    detail = service.detail(owner, memory['id'])
    kw = dict(content_version=detail['content_version'], expected_revision=0, subject_id='20000',
        visibility='groups', groups=['90000'], requesters=[], writers=[], confirmed=True)
    with pytest.raises((ValueError, MemoryAccessDenied)):
        service.set_access(owner, memory['id'], **(kw | extra))
    assert service.detail(owner, memory['id'])['policy'] is None


@pytest.mark.parametrize('updates', [{'content': 'password=fixture-only'},
    {'sensitivity': 'unknown'}, {'is_archived': True}])
def test_secret_unknown_and_archived_records_cannot_be_published(lab, updates):
    _, kb, service, owner, memory = lab
    with kb._memory_write_lock:
        memory.update(updates)
    d = service.detail(owner, memory['id'])
    with pytest.raises(MemoryAccessDenied):
        service.set_access(owner, memory['id'], content_version=d['content_version'],
            expected_revision=0, subject_id='20000', visibility='public', confirmed=True)


def test_owner_removal_and_group_revocation_invalidate_old_management(lab):
    bridge, _, service, owner, memory = lab
    d = service.detail(owner, memory['id'])
    bridge.store.set_group('10000', '90000', False)
    with pytest.raises(MemoryAccessDenied):
        service.set_access(owner, memory['id'], content_version=d['content_version'],
            expected_revision=0, subject_id='20000', visibility='groups', groups=['90000'], confirmed=True)
    bridge.settings = bridge.settings.model_copy(update={'owner_ids': frozenset({'40000'})})
    with pytest.raises(MemoryAccessDenied):
        service.detail(owner, memory['id'])


def test_storage_failure_does_not_report_success_or_touch_body(lab):
    _, kb, service, owner, memory = lab
    before = dict(kb._find_memory(memory['id']))
    d = service.detail(owner, memory['id'])
    with patch.object(service.access, 'set', side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError):
            service.set_access(owner, memory['id'], content_version=d['content_version'],
                expected_revision=0, subject_id='20000', visibility='public', confirmed=True)
    assert kb._find_memory(memory['id']) == before
    assert service.detail(owner, memory['id'])['policy'] is None


def test_real_bridge_lists_and_reads_without_model_or_candidate_changes(lab):
    async def run():
        from types import SimpleNamespace
        from saya_app.services.qq_chat import QQChatResponder
        from test_qq_task_scheduler import submit
        bridge, kb, service, _, memory = lab
        async def forbidden(*args, **kw):
            raise AssertionError('management must not invoke model')
        responder = QQChatResponder(bridge.settings, lambda **kw: SimpleNamespace(**kw), forbidden,
            lambda: 'api', lambda: {})
        responder.memory_management = service
        bridge.responder = responder
        await bridge.start()
        try:
            submit(bridge, '/memory list'); await bridge.queue.join()
            assert any(memory['id'] in item[1] for item in bridge.client.sent)
            submit(bridge, '/memory show ' + memory['id'], 2); await bridge.queue.join()
            assert any('喜欢茉莉茶' in item[1] for item in bridge.client.sent)
            assert not kb.memory_candidates
        finally:
            await bridge.stop()
    asyncio.run(run())


def test_recall_counters_do_not_invalidate_reviewed_content(lab):
    _, kb, service, owner, memory = lab
    detail = service.detail(owner, memory['id'])
    page = service.browse(owner)
    with kb._memory_write_lock:
        memory['access_count'] = 99
        memory['last_accessed'] = '2026-10-31 12:00:00'
        memory['_score'] = 0.7
    assert service.detail(owner, memory['id'])['content_version'] == detail['content_version']
    assert service.browse(owner, snapshot=page['snapshot'])['snapshot'] == page['snapshot']


def test_real_sqlite_abort_rolls_back_acl_without_altering_memory(lab):
    bridge, kb, service, owner, memory = lab
    before = dict(kb._find_memory(memory['id']))
    d = service.detail(owner, memory['id'])
    with bridge.store.connect() as db:
        db.execute("create trigger fail_acl before insert on qq_memory_access begin select raise(abort,'fixture failure'); end")
    with pytest.raises(sqlite3.IntegrityError):
        service.set_access(owner, memory['id'], content_version=d['content_version'],
            expected_revision=0, subject_id='20000', visibility='public', confirmed=True)
    assert service.detail(owner, memory['id'])['policy'] is None
    assert kb._find_memory(memory['id']) == before


def test_concurrent_reviewers_cannot_overwrite_each_other(lab):
    _, _, service, owner, memory = lab
    d = service.detail(owner, memory['id'])
    def review():
        try:
            service.set_access(owner, memory['id'], content_version=d['content_version'],
                expected_revision=0, subject_id='20000', visibility='private', confirmed=True)
            return 'saved'
        except MemoryAccessConflict:
            return 'conflict'
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(lambda _: review(), range(2))) == ['conflict', 'saved']


def test_personal_sensitivity_still_needs_requester_and_audience_grants(lab):
    from saya_app.services.qq_memory_access import can_disclose
    _, _, service, owner, memory = lab
    d = service.detail(owner, memory['id'])
    service.set_access(owner, memory['id'], content_version=d['content_version'],
        expected_revision=0, subject_id='20000', visibility='groups', groups=['90000'], confirmed=True)
    p = service.access.get(owner.bot_id, memory['id'])
    group = replace(owner, group_id='90000', group_authorized=True)
    assert can_disclose(group, p, memory['id'], p.memory_revision, sensitivity='personal')
    assert not can_disclose(replace(group, user_id='30000', is_owner=False), p,
        memory['id'], p.memory_revision, sensitivity='personal')


def test_api_auth_origin_owner_content_and_policy_cas(lab):
    async def run():
        bridge, _, _, _, memory = lab
        await bridge.start()
        app = FastAPI(); app.include_router(routes.router)
        try:
            with patch.object(routes, 'qq_bridge', bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://127.0.0.1') as c:
                    url = '/api/qq/memory-management'
                    assert (await c.get(url)).status_code == 401
                    headers = {'Origin': 'http://127.0.0.1', 'X-Saya-QQ-UI': '1'}
                    assert (await c.post('/api/qq/ui-session', headers=headers)).status_code == 200
                    listing = (await c.get(url)).json()
                    assert listing['owner_id'] == '20000' and listing['items'][0]['id'] == memory['id']
                    assert (await c.get(url, params={'owner_id': '30000'})).status_code == 403
                    d = (await c.get(url + '/item', params={'memory_id': memory['id']})).json()
                    body = dict(owner_id='20000', memory_id=memory['id'], content_version=d['content_version'],
                        expected_revision=0, subject_id='20000', visibility='groups', groups=['90000'],
                        requesters=[], writers=[], confirmed=True, revision=listing['revision'])
                    assert (await c.put(url + '/access', json=body, headers={'Origin': 'http://evil.invalid'})).status_code == 401
                    assert (await c.put(url + '/access', json=body | {'revision': 'old'}, headers=headers)).status_code == 409
                    assert (await c.put(url + '/access', json=body | {'confirmed': False}, headers=headers)).status_code == 403
                    assert (await c.put(url + '/access', json=body, headers=headers)).status_code == 200
                    assert (await c.put(url + '/access', json=body, headers=headers)).status_code == 409
        finally:
            await bridge.stop()
    asyncio.run(run())
