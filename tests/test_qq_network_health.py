import asyncio
import json

import httpx
import pytest

from saya_app.services.qq_network_health import PublicNetworkProbe
from saya_app.services.qq_health import inspect_health
from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.qq_observation import ObservationLedger


def test_public_heads_are_credential_free_cached_and_recover_after_expiry():
    now = [10.0]
    down = [True]
    calls = []
    def respond(request):
        calls.append(request)
        assert request.method == 'HEAD' and str(request.url) in PublicNetworkProbe.URLS
        assert 'authorization' not in request.headers and 'cookie' not in request.headers
        if down[0]:
            raise httpx.ConnectError('private diagnostic must not escape')
        return httpx.Response(403)
    async def run():
        probe = PublicNetworkProbe(httpx.MockTransport(respond), clock=lambda: now[0])
        assert await probe.probe() == 'unreachable'
        down[0] = False
        assert await probe.probe() == 'unreachable' and len(calls) == 2
        now[0] += 6
        assert await probe.probe() == 'reachable' and len(calls) == 4
    asyncio.run(run())


def test_one_public_response_is_sufficient_without_following_redirects():
    def respond(request):
        if request.url.host == 'www.qq.com':
            raise httpx.ConnectError('blocked site')
        return httpx.Response(302, headers={'Location': 'https://other.invalid/private'})
    assert asyncio.run(PublicNetworkProbe(httpx.MockTransport(respond)).probe()) == 'reachable'


def test_simultaneous_health_requests_share_a_single_bounded_probe():
    calls = []
    async def respond(request):
        calls.append(request)
        await asyncio.sleep(.01)
        return httpx.Response(404)
    async def run():
        probe = PublicNetworkProbe(httpx.MockTransport(respond))
        assert await asyncio.gather(*(probe.probe() for _ in range(8))) == ['reachable'] * 8
    asyncio.run(run())
    assert len(calls) == 2


def test_probe_timeout_is_unknown_and_does_not_disclose_transport_errors(monkeypatch):
    async def timeout():
        raise asyncio.TimeoutError('private detail')
    probe = PublicNetworkProbe()
    monkeypatch.setattr(probe, '_check', timeout)
    assert asyncio.run(probe.probe()) == 'unknown'


@pytest.mark.parametrize('network', ['unreachable', 'unknown', 'reachable'])
def test_cached_login_match_does_not_hide_failed_connectivity(tmp_path, network):
    class Client:
        async def call(self, action, payload):
            return {'online': True} if action == 'get_status' else {'user_id': '10000'}
        async def close(self):
            pass
    async def probe():
        return network
    bridge = QQBridge(QQSettings(bot_id='10000'), QQStore(tmp_path / 'unused.sqlite3'))
    data = asyncio.run(inspect_health(bridge, {}, lambda _: Client(), network_probe=probe))
    assert data['qq_login'] == 'matched' and data['network'] == network
    assert any('外网连通性' in item for item in data['requires_action']) is (network != 'reachable')
    assert not any('扫码' in item for item in data['requires_action'])
    assert 'private' not in json.dumps(data)


def test_network_outage_cannot_add_healthy_time_and_legacy_ledger_is_preserved(tmp_path):
    ledger = ObservationLedger(tmp_path / 'availability.json')
    state = {'saya': 'ready', 'consumer': 'running', 'napcat': 'reachable', 'onebot_auth': 'accepted', 'qq_login': 'matched'}
    ledger.record(state, 100)
    ledger.record(state, 160)
    original = ledger.load()
    ledger.record(state | {'network': 'unreachable'}, 220)
    ledger.record(state | {'network': 'reachable'}, 280)
    result = ledger.record(state | {'network': 'reachable'}, 340)
    assert result['started'] == original['started'] and result['healthy_seconds'] == 120
    assert result['unhealthy_seconds'] == 120 and result['measured_seconds'] == 240
    assert result['last_state']['network'] == 'reachable'
