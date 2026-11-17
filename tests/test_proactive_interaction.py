import asyncio,json,time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import httpx,pytest
from fastapi import FastAPI
from core_services import KnowledgeBase,MemoryManager,ProjectManager
from saya_app.services.proactive import ProactiveStore,ProactivePolicy,quiet
from saya_app.services.proactive_runtime import ProactiveRuntime,generate_draft
from saya_app.services.service_activity import ServiceActivity
from test_qq_task_scheduler import make
from test_qq_delivery import Client,event
from test_qq_chat_runtime import server


async def lab(tmp_path,send=True):
    now=[time.time()];kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));manager.legacy_sessions_file=str(tmp_path/'no_legacy.json')
    projects=ProjectManager(str(tmp_path/'kb'));calls=[]
    async def generate(data):calls.append(data);return {'send':send,'candidate_id':data['candidates'][0]['key'],'text':'刚才那个话题，我又想到一点。'}
    async def normal(*a):return '普通答复。'
    b=make(tmp_path/'qq',normal,client=Client());await b.start();b.settings=b.settings.model_copy(update={'shared_memory':False})
    runtime=ProactiveRuntime(tmp_path/'kb',kb,manager,projects,b,generate,ServiceActivity(),lambda:now[0]);b.proactive=runtime
    scope='10000:private:20000:isolated';b.store.save_conversation(scope,['Doctor: 后来聊聊这个项目吧','Saya: 好呀。'],'session_qq_fixture.jsonl')
    session=manager.create_session(make_current=False);manager.save_message('user','最近在画画。',session_id=session)
    p=ProactivePolicy(enabled=True,owner_id='20000',quiet_start=0,quiet_end=0,min_idle_minutes=1,min_gap_minutes=0)
    runtime.configure('20000',0,p.model_dump());now[0]=time.time()+120
    return runtime,b,kb,manager,session,now,calls


def enable_web(kb):kb.settings.update(echo_enabled=True,echo_source_context=True,echo_quiet_start=0,echo_quiet_end=0,echo_min_idle_minutes=1,echo_min_gap_minutes=0)


def test_web_proactive_new_preferences_persist_through_real_settings_store_reload(tmp_path):
    kb=KnowledgeBase(str(tmp_path/'kb'))
    kb.update_settings({'echo_source_context':True,'echo_min_idle_minutes':7,'echo_timezone':'America/New_York','echo_min_gap_minutes':0,'echo_daily_limit':0})
    restored=KnowledgeBase(str(tmp_path/'kb'))
    assert restored.settings['echo_source_context'] is True and restored.settings['echo_min_idle_minutes']==7
    assert restored.settings['echo_timezone']=='America/New_York' and restored.settings['echo_min_gap_minutes']==0 and restored.settings['echo_daily_limit']==0


def test_qq_can_initiate_without_forging_user_history_and_only_once(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path)
        try:
            await rt.tick();await b.queue.join()
            assert len(calls)==1 and len(b.client.sent)==1
            assert b.client.sent[0][1]=='刚才那个话题，我又想到一点。'
            history,_=b.store.conversation('10000:private:20000:isolated')
            assert len(history)==3 and history[-1].startswith('Saya: ') and not any('主动互动机会' in x for x in history)
            with rt.store.connect() as db:assert db.execute("select state,body from initiative_jobs").fetchone()[:]==('sent','')
            now[0]+=3600;await rt.tick();assert len(calls)==1 and len(b.client.sent)==1 and rt.last_reason=='awaiting_reply'
        finally:await b.stop()
    asyncio.run(run())


def test_shared_qq_initiative_persists_only_bot_turn_and_keeps_web_pointer(tmp_path):
    async def run():
        from saya_app.services.chat_time_context import QQTurnTimes
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);b.settings=b.settings.model_copy(update={'shared_memory':True})
        b.turn_times=QQTurnTimes(b.store)
        sid='session_qq_fixture.jsonl';b.store.save_conversation('10000:private:20000:shared',['Doctor: 画画后续','Saya: 好呀。'],sid)
        manager.save_message('user','画画后续',session_id=sid);manager.save_message('ai','好呀。',session_id=sid);pointer=manager.current_session_file
        try:
            await rt.tick();await b.queue.join()
            rows=manager.load_session_full(sid,make_current=False)
            assert [x['role'] for x in rows]==['user','ai','ai'] and rows[-1]['proactive']['id']
            assert manager.current_session_file==pointer and len(manager.load_session_full(session,make_current=False))==1
            assert len(b.client.sent)==1
        finally:await b.stop()
    asyncio.run(run())


def test_disabled_qq_is_inert_and_silence_never_spends_delivery_quota(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path,send=False)
        try:
            p=rt.state();rt.configure('20000',p['revision'],p['values']|{'enabled':False});now[0]+=61
            await rt.tick();assert not calls and not b.client.sent
            p=rt.state();rt.configure('20000',p['revision'],p['values']|{'enabled':True});now[0]+=61
            await rt.tick();await rt.tick();assert len(calls)==1 and not b.client.sent
            assert rt.store.summary()==[{'channel':'qq','state':'silent','count':1}]
        finally:await b.stop()
    asyncio.run(run())


def test_web_prepare_commit_render_ack_and_refresh_have_one_history_message(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);client='a'*32
        try:
            data=await rt.poll_web(client,session,True,False);assert data['message'] is None and data['offer_id']
            assert len(manager.load_session_full(session,make_current=False))==1
            args=(data['offer_id'],data['token'],client,session)
            committed=rt.commit_web(*args);assert committed['message']
            assert rt.store.get(data['offer_id'])['state']=='committed'
            assert rt.commit_web(*args)==committed
            assert len(manager.load_session_full(session,make_current=False))==2
            assert rt.ack_web(*args)['acknowledged'] and rt.ack_web(*args)['acknowledged']
            assert rt.store.get(data['offer_id'])['state']=='sent'
            assert not (await rt.poll_web(client,session,True,False)).get('offer_id')
            now[0]+=61;rt.presence=None;await rt.tick();assert not b.client.sent
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',['activity','permission','settings','session','hidden','other_client'])
def test_web_pending_draft_cannot_cross_page_or_revocation(tmp_path,change):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);client='a'*32
        try:
            data=await rt.poll_web(client,session,True,False);args=(data['offer_id'],data['token'],client,session)
            if change=='activity':rt.note_web()
            elif change=='permission':kb.memory_use.set_block('fixture',True)
            elif change=='settings':kb.settings['echo_enabled']=False
            elif change=='session':
                other=manager.create_session(make_current=False);manager.save_message('user','另一个话题。',session_id=other);rt.heartbeat(client,other,True,False)
            elif change=='hidden':rt.heartbeat(client,session,False,False)
            else:
                with pytest.raises(PermissionError):rt.heartbeat('b'*32,session,True,False)
                with pytest.raises(PermissionError):rt.commit_web(data['offer_id'],data['token'],'b'*32,session)
                return
            with pytest.raises((ValueError,PermissionError)):rt.commit_web(*args)
            assert len(manager.load_session_full(session,make_current=False))==1
        finally:await b.stop()
    asyncio.run(run())


def test_new_user_message_during_generation_prevents_initiative(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);entered=asyncio.Event();release=asyncio.Event()
        async def generate(data):entered.set();await release.wait();return {'send':True,'candidate_id':data['candidates'][0]['key'],'text':'不该发出。'}
        rt.generate=generate
        try:
            task=asyncio.create_task(rt.tick());await entered.wait();now[0]+=1;rt.note_web();release.set();await task
            assert not b.client.sent
            assert all(x['state']!='sent' for x in rt.store.summary())
        finally:await b.stop()
    asyncio.run(run())


def test_unknown_receipt_is_not_replayed_and_counts_as_waiting_not_sent(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path)
        async def uncertain(*a,**kw):raise TimeoutError('unknown')
        b.client.send_part=uncertain
        try:
            await rt.tick();await b.queue.join();assert rt.store.summary()[0]['state']=='unknown'
            now[0]+=3600;await rt.tick();assert len(calls)==1 and rt.last_reason=='awaiting_reply'
        finally:await b.stop()
    asyncio.run(run())


def test_cross_channel_shared_quota_and_topic_pending_are_not_double_consumed(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);client='a'*32
        try:
            data=await rt.poll_web(client,session,True,False)
            rt.commit_web(data['offer_id'],data['token'],client,session);rt.ack_web(data['offer_id'],data['token'],client,session)
            now[0]+=100;rt.presence=None;rt.note_web();now[0]+=61;await rt.tick();await b.queue.join()
            assert len(calls)==1 and not b.client.sent and rt.last_reason=='daily_limit'
        finally:await b.stop()
    asyncio.run(run())


def test_restart_does_not_replay_committed_queued_or_sending(tmp_path):
    now=[time.time()];store=ProactiveStore(tmp_path/'state.db',lambda:now[0]);p=ProactivePolicy(enabled=True,quiet_start=0,quiet_end=0,min_idle_minutes=1)
    for index,state in enumerate(['committed','queued','sending','ready']):
        job=store.reserve(str(index),'web','session','topic',p,{})
        store.transition(job['id'],'generating',state,'secret draft')
    restored=ProactiveStore(tmp_path/'state.db',lambda:now[0])
    with restored.connect() as db:
        rows=db.execute('select state,body from initiative_jobs').fetchall()
    assert [x['state'] for x in rows].count('unknown')==3 and all(not x['body'] for x in rows)


def test_midnight_daily_budget_resets_without_legacy_quota_reset(tmp_path):
    now=[datetime.fromisoformat('2026-11-01T12:00:00-05:00').timestamp()];store=ProactiveStore(tmp_path/'s.db',lambda:now[0]);p=ProactivePolicy(enabled=True,quiet_start=0,quiet_end=0,min_idle_minutes=1,min_gap_minutes=0)
    store.activity('owner',now[0]-1000)
    with store.connect() as db:db.execute("insert into initiative_meta values('legacy_echo',?)",(json.dumps({'day':'2026-11-01','sent_today':1}),))
    assert store.eligible('owner',p)=='daily_limit';now[0]+=86400;assert store.eligible('owner',p)=='eligible'


def test_unknown_delivery_reserves_budget_without_claiming_it_was_sent(tmp_path):
    now=[time.time()];store=ProactiveStore(tmp_path/'s.db',lambda:now[0]);p=ProactivePolicy(enabled=True,quiet_start=0,quiet_end=0,min_idle_minutes=1,min_gap_minutes=0)
    job=store.reserve('owner','qq','session','topic',p,{})
    store.transition(job['id'],'generating','unknown','');now[0]+=1;store.activity('owner');now[0]+=120
    assert store.eligible('owner',p)=='delivery_uncertain'
    assert store.summary()==[{'channel':'qq','state':'unknown','count':1}]


def test_policy_version_timezone_and_nonowner_controls(tmp_path):
    async def run():
        rt,b,*rest=await lab(tmp_path)
        try:
            with pytest.raises(PermissionError):rt.configure('30000',1,rt.state()['values'])
            with pytest.raises(ValueError):rt.configure('20000',0,rt.state()['values'])
            with pytest.raises(ValueError):ProactivePolicy(timezone='invalid-zone')
            from dataclasses import replace
            from saya_app.services.qq_bridge import normalize_event
            message=normalize_event(event('/proactive off'),b.settings)[0]
            assert '关闭' in rt.command(message) and not rt.state()['values']['enabled']
            assert '主人' in rt.command(replace(message,user_id='30000'))
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('block',['busy','draining','quiet','removed_owner','legacy'])
def test_runtime_fences_prevent_generation_before_network_calls(tmp_path,block):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path)
        try:
            if block=='busy':rt.activity.active_web_chats=1
            elif block=='draining':b.draining=True
            elif block=='quiet':
                p=rt.state();hour=datetime.fromtimestamp(now[0],__import__('zoneinfo').ZoneInfo(p['values']['timezone'])).hour
                rt.configure('20000',p['revision'],p['values']|{'quiet_start':hour,'quiet_end':(hour+1)%24});now[0]+=61
            elif block=='removed_owner':b.settings=b.settings.model_copy(update={'owner_ids':frozenset()})
            else:b.settings=b.settings.model_copy(update={'delivery_mode':'legacy'})
            await rt.tick();assert not calls and not b.client.sent
        finally:await b.stop()
    asyncio.run(run())


def test_current_history_projection_excludes_revoked_web_facts(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb)
        try:
            manager.save_message('ai','PRIVATE_REVOKED_CANARY',session_id=session)
            kb.memory_use.set_block('private-fixture',True)
            result=await rt.poll_web('a'*32,session,True,False)
            assert not calls and not result.get('offer_id')
        finally:await b.stop()
    asyncio.run(run())


def test_same_project_is_not_repeated_on_qq_after_web_even_with_larger_daily_cap(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);kb.settings.update(echo_source_context=False,echo_source_projects=True)
        rt.projects.add_project('shared project');rt.projects.projects[0]['updated_at']='2026-01-01 00:00:00';rt.projects.save_projects()
        b.settings=b.settings.model_copy(update={'shared_memory':True})
        b.store.save_conversation('10000:private:20000:shared',['Doctor: 项目情况','Saya: 好。'],'session_qq_fixture.jsonl')
        p=rt.state();rt.configure('20000',p['revision'],p['values']|{'source_context':False,'source_projects':True,'daily_limit':2});now[0]+=61
        try:
            data=await rt.poll_web('a'*32,session,True,False);rt.commit_web(data['offer_id'],data['token'],'a'*32,session);rt.ack_web(data['offer_id'],data['token'],'a'*32,session)
            rt.presence=None;now[0]+=1;rt.note_web();now[0]+=61;await rt.tick();await b.queue.join()
            assert not b.client.sent and rt.last_reason=='topic_already_sent'
        finally:await b.stop()
    asyncio.run(run())


def test_project_update_invalidates_draft_before_web_publication(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);kb.settings.update(echo_source_context=False,echo_source_projects=True)
        project=rt.projects.add_project('old project');rt.projects.projects[0]['updated_at']='2026-01-01 00:00:00';rt.projects.save_projects()
        try:
            data=await rt.poll_web('a'*32,session,True,False);assert data.get('offer_id')
            rt.projects.projects[0]['name']='changed project';rt.projects.save_projects()
            with pytest.raises(ValueError):rt.commit_web(data['offer_id'],data['token'],'a'*32,session)
        finally:await b.stop()
    asyncio.run(run())


def test_assistant_or_private_channel_session_is_not_a_web_target(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb)
        try:
            rt.session_allowed=lambda sid:False
            with pytest.raises(ValueError):await rt.poll_web('a'*32,session,True,False)
            with pytest.raises(ValueError):await rt.poll_web('a'*32,'session_qq_fixture.jsonl',True,False)
            assert not calls
        finally:await b.stop()
    asyncio.run(run())


def test_http_protocol_rejects_cross_origin_and_requires_commit_before_ack(tmp_path):
    from saya_app.routers import companion
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);enable_web(kb);app=FastAPI();app.include_router(companion.router)
        headers={'Origin':'http://127.0.0.1','X-Saya-Proactive':'1'};body={'client_id':'a'*32,'session_id':session}
        try:
            with patch.object(companion,'proactive',rt):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1234)),base_url='http://127.0.0.1') as web:
                    assert (await web.post('/api/companion/echo/poll',json=body)).status_code==403
                    assert (await web.post('/api/companion/echo/poll',json=body,headers=headers|{'Origin':'http://evil.invalid'})).status_code==403
                    offer=(await web.post('/api/companion/echo/poll',json=body,headers=headers)).json();assert offer['offer_id']
                    receipt=body|{'offer_id':offer['offer_id'],'token':offer['token']}
                    assert (await web.post('/api/companion/echo/ack',json=receipt,headers=headers)).status_code==409
                    assert (await web.post('/api/companion/echo/commit',json=receipt|{'token':'wrong'},headers=headers)).status_code==403
                    assert (await web.post('/api/companion/echo/commit',json=receipt,headers=headers)).status_code==200
                    assert (await web.post('/api/companion/echo/ack',json=receipt,headers=headers)).json()['acknowledged']
        finally:await b.stop()
    asyncio.run(run())


def test_quiet_hours_uses_selected_zone_and_wraps_midnight():
    stamp=datetime.fromisoformat('2026-11-01T04:30:00+00:00').timestamp()
    assert quiet(stamp,ProactivePolicy(timezone='America/New_York',quiet_start=23,quiet_end=8))
    assert not quiet(stamp,ProactivePolicy(timezone='Asia/Shanghai',quiet_start=23,quiet_end=8))


def test_global_policy_source_disabled_never_uses_other_private_sources(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path)
        try:
            p=rt.state();rt.configure('20000',p['revision'],p['values']|{'source_context':False,'source_dates':True});now[0]+=61
            kb.add_memory('生日是11月1日 PRIVATE_SOURCE_CANARY')
            await rt.tick();assert not calls and not b.client.sent
        finally:await b.stop()
    asyncio.run(run())


def test_backend_stop_cancels_generation_and_releases_drain_accounting(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path);entered=asyncio.Event()
        async def wait(data):entered.set();await asyncio.Event().wait()
        async def loop():await rt.tick()
        rt.generate=wait;rt.run=loop
        try:
            await rt.start();await entered.wait();assert rt.activity.active_web_requests==1
            await rt.stop();assert rt.task is None and rt.activity.active_web_requests==0 and not b.client.sent
            assert all(x['state']!='sent' for x in rt.store.summary())
        finally:await b.stop()
    asyncio.run(run())


def test_typing_in_visible_web_suppresses_both_channels_but_hidden_page_does_not(tmp_path):
    async def run():
        rt,b,kb,manager,session,now,calls=await lab(tmp_path)
        try:
            rt.heartbeat('a'*32,session,True,True);await rt.tick();assert not calls
            rt.heartbeat('a'*32,session,False,False);await rt.tick();await b.queue.join();assert len(b.client.sent)==1
        finally:await b.stop()
    asyncio.run(run())


def test_retained_proactive_intents_block_old_reader_without_declared_support(tmp_path):
    from saya_app.services.qq_rollback import check_memory_rollback
    base=tmp_path/'root';store=ProactiveStore(base/'knowledge_base/proactive.sqlite3')
    store.reserve('owner','qq','session','topic',ProactivePolicy(enabled=True),{})
    result=check_memory_rollback(base,{'memory_compatibility':[]})
    assert result['status']=='blocked' and result['missing']==['proactive_interaction_v1']
    assert check_memory_rollback(base,{'memory_compatibility':['proactive_interaction_v1']})['status']=='compatible_declared'


def test_actual_generator_draft_uses_persona_but_no_tools_memory_or_history_writes(tmp_path,server):
    kb=KnowledgeBase(str(tmp_path/'kb'));manager=MemoryManager(str(tmp_path/'chats'));manager.legacy_sessions_file=str(tmp_path/'no_legacy.json');payload=[]
    data={'mood':{'curiosity':60},'candidates':[{'key':'fixture','topic':'SYNTHETIC_TOPIC'}]}
    def model(request):
        payload.append(json.loads(request.content));delta={'choices':[{'delta':{'content':json.dumps({'send':False})},'finish_reason':'stop'}]}
        return httpx.Response(200,text='data: '+json.dumps(delta)+'\n\ndata: [DONE]\n\n')
    async def run():
        from saya_app.services.conversation import conversation_service
        async with httpx.AsyncClient(transport=httpx.MockTransport(model)) as api:
            with patch.object(server,'kb_system',kb),patch.object(server,'memory_manager',manager),patch.object(server,'_get_api_httpx_client',lambda:api),patch.object(conversation_service,'save_credentials',lambda **kw:None),patch.object(server,'refresh_daily_activity_memory',lambda:None):
                result=await generate_draft(data,server.ChatWithMotion,server._chat_stream_single,lambda:{'configured':True,'api_key':'fixture','api_url':'http://mock/v1','model':'fixture'},'api')
        assert result=={'send':False} and not manager.get_session_list()
        assert not payload[0].get('tools')
        system='\n'.join(x['content'] for x in payload[0]['messages'] if x['role']=='system')
        assert 'self-initiated draft, not a new user message' in system and 'gentle otherworldly girl companion' not in system
    asyncio.run(run())
