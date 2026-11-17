import asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from saya_app.routers import qq as routes
from test_qq_person_events import lab


def test_episode_policy_read_and_write_require_management_owner_confirmation_and_revision(tmp_path,monkeypatch):
    async def run():
        async with lab(tmp_path,enabled=False) as (b,s,now,msg,choice,prompts):
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                assert client.get('/api/qq/episode-policy').status_code==401
                headers={'Authorization':'Bearer admin'}
                current=client.get('/api/qq/episode-policy',headers=headers).json();before=s.policy()
                request={'owner_id':'20000','revision':current['revision'],'values':current['values']|{'enabled':True},'confirmed':False}
                assert client.put('/api/qq/episode-policy',headers=headers,json=request).status_code==403
                assert client.put('/api/qq/episode-policy',headers=headers,json=request|{'confirmed':True,'owner_id':'30000'}).status_code==403
                assert client.put('/api/qq/episode-policy',headers=headers,json=request|{'confirmed':True,'revision':-1}).status_code in {409,422}
                assert s.policy()==before
    asyncio.run(run())


def test_topic_dependency_failure_preserves_policy_and_success_preserves_other_values(tmp_path,monkeypatch):
    async def run():
        async with lab(tmp_path,enabled=False) as (b,s,now,msg,choice,prompts):
            before=s.policy();s.configure('20000',before['revision'],before['values']|{'max_active':2,'max_total':9})
            topic=b.context_salience.state();b.context_salience.configure('20000',topic['revision'],topic['values']|{'enabled':False})
            monkeypatch.setattr(routes,'qq_bridge',b);app=FastAPI();app.include_router(routes.router)
            with TestClient(app) as client:
                headers={'Authorization':'Bearer admin'};current=client.get('/api/qq/episode-policy',headers=headers).json()
                request={'owner_id':'20000','revision':current['revision'],'values':current['values']|{'enabled':True},'confirmed':True}
                assert client.put('/api/qq/episode-policy',headers=headers,json=request).status_code==409 and s.policy()==current
                topic=b.context_salience.state();b.context_salience.configure('20000',topic['revision'],topic['values']|{'enabled':True})
                response=client.put('/api/qq/episode-policy',headers=headers,json=request)
                assert response.status_code==200 and response.json()['values']==current['values']|{'enabled':True}
                assert client.put('/api/qq/episode-policy',headers=headers,json=request).status_code==409
    asyncio.run(run())
