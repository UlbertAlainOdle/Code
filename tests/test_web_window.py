from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI,HTTPException
from fastapi.testclient import TestClient
import pytest

from saya_app.services.web_window import WebWindowManager
from saya_app.routers.web_window import router,local_websocket


def test_concurrent_browser_claims_have_one_owner_and_old_disconnect_cannot_release_new_owner():
    manager=WebWindowManager()
    with ThreadPoolExecutor(max_workers=8) as pool:results=list(pool.map(lambda _:manager.claim(),range(20)))
    tokens=[v for v in results if v];assert len(tokens)==1
    assert set(manager.status())=={'occupied','finishing_request'}
    manager.require(tokens[0])
    for invalid in ['', 'stale']:
        with pytest.raises(HTTPException):manager.require(invalid)
    manager.release(tokens[0]);new=manager.claim();manager.release(tokens[0]);manager.require(new)


def test_disconnect_waits_for_previous_stream_to_finish_before_another_window_can_enter():
    activity={'busy':False};manager=WebWindowManager(lambda:activity['busy'])
    token=manager.claim();activity['busy']=True;manager.release(token)
    assert manager.claim() is None
    activity['busy']=False;assert manager.claim()


def test_real_websocket_claim_release_and_second_browser_refusal():
    app=FastAPI();app.include_router(router);app.state.web_window=WebWindowManager()
    with TestClient(app,base_url='http://127.0.0.1:8000',client=('127.0.0.1',1234)) as client:
        with client.websocket_connect('ws://127.0.0.1:8000/api/web-window/connect',headers={'Origin':'http://127.0.0.1:8000'}) as first:
            owned=first.receive_json();assert owned['type']=='accepted'
            with client.websocket_connect('ws://127.0.0.1:8000/api/web-window/connect',headers={'Origin':'http://127.0.0.1:8000'}) as second:
                assert second.receive_json()['type']=='occupied'
            first.send_text('ping');assert first.receive_json()=={'type':'pong'}
        with client.websocket_connect('ws://127.0.0.1:8000/api/web-window/connect',headers={'Origin':'http://127.0.0.1:8000'}) as third:
            assert third.receive_json()['type']=='accepted'
            third.send_text('release');assert third.receive_json()=={'type':'released'}
        assert not app.state.web_window.status()['occupied']


def test_only_chat_stream_activity_blocks_window_transfer():
    import asyncio
    from saya_app.services.service_activity import ServiceActivity,ServiceActivityMiddleware
    async def run():
        for path,chat_count in [('/chat_stream',1),('/api/widget',0)]:
            activity=ServiceActivity()
            async def app(scope,receive,send):
                assert activity.active_web_requests==1 and activity.active_web_chats==chat_count
            middleware=ServiceActivityMiddleware(app,activity,lambda:False)
            await middleware({'type':'http','method':'POST','path':path},None,None)
            assert activity.active_web_requests==activity.active_web_chats==0
    asyncio.run(run())


@pytest.mark.parametrize('origin',['','https://evil.invalid','http://localhost:8000','http://127.0.0.1:8001','http://user@127.0.0.1:8000','http://127.0.0.1:8000/path'])
def test_cross_origin_websocket_cannot_claim_owner(origin):
    from starlette.datastructures import URL
    socket=SimpleNamespace(headers={'origin':origin},url=URL('ws://127.0.0.1:8000/api/web-window/connect'),client=SimpleNamespace(host='127.0.0.1'))
    assert not local_websocket(socket)
