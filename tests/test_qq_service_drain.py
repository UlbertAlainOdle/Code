import asyncio
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import httpx
import pytest

from saya_app.routers import qq as routes
from saya_app.services.qq_bridge import QQBridge, QQSettings, QQStore
from saya_app.services.service_activity import ServiceActivity, ServiceActivityMiddleware


def test_maintenance_waits_for_web_stream_body_and_rejects_new_writes(tmp_path):
    async def run():
        bridge=QQBridge(QQSettings(admin_token='fake-admin'),QQStore(tmp_path/'unused.sqlite3'))
        started=asyncio.Event(); release=asyncio.Event()
        app=FastAPI();app.state.service_activity=ServiceActivity()
        app.add_middleware(ServiceActivityMiddleware,activity=app.state.service_activity,is_draining=lambda:bridge.draining)
        app.include_router(routes.router)
        @app.post('/chat_stream')
        async def chat():
            async def body():
                yield 'data: started\n\n'
                started.set()
                await release.wait()
                yield 'data: completed\n\n'
            return StreamingResponse(body(),media_type='text/event-stream')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000',
                                     headers={'Authorization':'Bearer fake-admin'}) as client:
            with patch.object(routes,'qq_bridge',bridge):
                pending=asyncio.create_task(client.post('/chat_stream'))
                await asyncio.wait_for(started.wait(),2)
                status=(await client.post('/api/qq/lifecycle/drain',json={'draining':True})).json()
                assert status['active_web_requests']==1
                assert (await client.post('/chat_stream')).status_code==503
                assert app.state.service_activity.active_web_requests==1
                release.set()
                answer=await pending
                assert answer.status_code==200 and 'completed' in answer.text
                assert (await client.post('/api/qq/lifecycle/drain',json={'draining':True})).json()['active_web_requests']==0
                await client.post('/api/qq/lifecycle/drain',json={'draining':False})
                assert (await client.post('/chat_stream')).status_code==200
    asyncio.run(run())


@pytest.mark.parametrize('target', [
    '/api/qq/memory-console/policy', '/api/qq/memory-console/decision',
    '/api/qq/memory-console/operation-preview', '/api/qq/memory-console/operation-confirm',
    '/api/qq/memory-console/save-select', '/api/qq/memory-console/save-decision',
    '/api/qq/memory-console/candidate-preview', '/api/qq/memory-console/revision-preview',
    '/api/qq/memory-console/revision-confirm',
    '/api/qq/memory-console/action-preview', '/api/qq/memory-console/action-decision',
    '/api/qq/memory-console/event-decision',
    '/api/qq/memory-console/command',
    '/api/qq/memory-console/library-scan', '/api/qq/memory-console/library-prepare',
    '/api/qq/memory-console/library-overlap', '/api/qq/memory-console/daily-overlap',
    '/api/qq/memory-console/daily-overlap-cancel',
    '/api/qq/memory-console/decision/',
])
def test_memory_console_writes_join_drain_without_blocking_lifecycle_controls(tmp_path,target):
    async def run():
        bridge=QQBridge(QQSettings(admin_token='fixture-admin'),QQStore(tmp_path/'unused.sqlite3'))
        started,release=asyncio.Event(),asyncio.Event()
        app=FastAPI();activity=ServiceActivity();app.state.service_activity=activity
        app.add_middleware(ServiceActivityMiddleware,activity=activity,is_draining=lambda:bridge.draining)
        async def write():
            started.set();await release.wait();return {'committed':True}
        app.add_api_route(target,write,methods=['POST'])
        app.include_router(routes.router)
        with patch.object(routes,'qq_bridge',bridge):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000',
                                        headers={'Authorization':'Bearer fixture-admin'}) as client:
                pending=asyncio.create_task(client.post(target))
                await asyncio.wait_for(started.wait(),2)
                state=(await client.post('/api/qq/lifecycle/drain',json={'draining':True})).json()
                assert state['active_web_requests']==1 and activity.active_web_chats==0
                response=await client.post(target)
                assert response.status_code==503 and response.headers['retry-after']=='5'
                assert activity.active_web_requests==1
                # Read-only endpoints and the ability to release maintenance
                # stay available. An admitted write completes exactly once.
                assert (await client.get('/api/qq/status')).status_code==200
                release.set();assert (await pending).json()=={'committed':True}
                assert activity.active_web_requests==0
                assert (await client.post('/api/qq/lifecycle/drain',json={'draining':False})).status_code==200
    asyncio.run(run())


def test_console_failed_write_releases_drain_counter():
    async def run():
        app=FastAPI();activity=ServiceActivity()
        app.add_middleware(ServiceActivityMiddleware,activity=activity,is_draining=lambda:False)
        @app.post('/api/qq/memory-console/decision')
        async def fail():
            assert activity.active_web_requests==1
            raise OSError('isolated disk failure')
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,raise_app_exceptions=False),base_url='http://127.0.0.1:8000') as client:
            assert (await client.post('/api/qq/memory-console/decision')).status_code==500
        assert activity.active_web_requests==0
    asyncio.run(run())
