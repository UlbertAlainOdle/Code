"""Native consent must preserve original maintenance, CAS and origin rules."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from saya_app.routers import qq as routes
from saya_app.services.qq_console_actions import QQConsoleActions,TABLE
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_responses import ConsoleNotice,ControlReceipt
from saya_app.services.qq_rollback import memory_rollback_requirements
from test_qq_r5_maintenance_archive import archive_lab
from test_qq_memory_management import lab
from test_qq_r5_maintenance_commands import bind_memory_responder,run_command


@pytest.fixture
def native(archive_lab):
    b,k,m,a,_=archive_lab
    m.actions=c=QQConsoleActions(m)
    return b,k,m,a,c


def prepare(native,kind='link'):
    b,k,m,a,c=native
    return c.prepare(a.user_id,kind,'first','second',*[record_version(k._find_memory(i)) for i in ('first','second')])


@pytest.mark.parametrize('kind,done',[('link','active'),('archive','archived')])
def test_native_preview_confirm_repeat_and_undo_use_original_engines(native,kind,done):
    b,k,m,a,c=native;before=json.dumps(k.memories,sort_keys=True)
    p=prepare(native,kind);assert p['status']=='pending'
    assert k._find_memory('first')['content'] in json.dumps(p,ensure_ascii=False)
    refs=json.loads(c.row(a.user_id,p['id'])['refs']);op=refs['operation']
    engine=m.maintenance_archive(a,'test-topic') if kind=='archive' else m.maintenance_links(a,'test-topic')
    assert engine.confirm(op)['status']=='preview_not_delivered'
    assert (engine.mark_delivered(op) if kind=='archive' else engine.mark_preview_delivered(op)) is False
    assert json.dumps(k.memories,sort_keys=True)==before
    result=c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    assert result['status']==done
    from saya_qq_action_panel import action_text
    assert k._find_memory('first')['content'] in action_text(result)
    if kind=='archive':assert '归档原条目：second' in action_text(result)
    assert c.decide(a.user_id,p['id'],p['preview_version'],'confirm')['status']==done
    result=c.decide(a.user_id,p['id'],result['preview_version'],'undo');assert result['status']=='undone'
    assert c.decide(a.user_id,p['id'],result['preview_version'],'confirm')['status']=='undone'
    assert k._find_memory('first')['content']==k._find_memory('second')['content']
    with b.store.connect() as db:
        if kind=='link':assert db.execute('select preview_state from qq_maintenance_links').fetchone()[0]=='native_waiting'
        else:assert db.execute('select delivered from qq_maintenance_archives').fetchone()[0]==2


@pytest.mark.parametrize('change',['legacy','evidence','topic','settings','epoch','restart'])
def test_preview_cannot_authorize_changed_sources_context_or_restart(native,change):
    b,k,m,a,c=native;p=prepare(native)
    if change=='legacy':k.update_memory('first',{'content':'新的不同正文'})
    elif change=='evidence':k.memory_v4_store.update_memory('second',{'locked':True})
    elif change=='topic':b.store.save_conversation('10000:private:20000:shared',[],'different-topic')
    elif change=='settings':asyncio.run(b.update_controls(b.controls()['settings']|{'memory_write_enabled':False},expected_revision=b.controls()['revision']))
    elif change=='epoch':k.memory_use.set_block('unrelated',True)
    elif change=='restart':c=m.actions=QQConsoleActions(m);c.recover()
    with pytest.raises((ValueError,MemoryAccessDenied)):c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    assert m.maintenance_links(a,b.store.conversation('10000:private:20000:shared')[1]).for_member('first')['status']=='unavailable'


def test_parallel_confirmation_executes_once(native):
    _,_,_,a,c=native;p=prepare(native)
    with ThreadPoolExecutor(2) as pool:
        results=list(pool.map(lambda _:c.decide(a.user_id,p['id'],p['preview_version'],'confirm'),range(2)))
    assert [r['status'] for r in results]==['active','active']


def test_unknown_after_committed_write_does_not_retry_or_lose_original_result(native):
    b,k,m,a,c=native;p=prepare(native)
    original=c._engine
    class FailedReply:
        def result(self,op):return original(a,'test-topic',c.row(a.user_id,p['id'])).result(op)
        def confirm(self,op):
            assert original(a,'test-topic',c.row(a.user_id,p['id']),consent=True).confirm(op)['status']=='active'
            raise OSError('reply lost')
    with patch.object(c,'_engine',return_value=FailedReply()):
        with pytest.raises(OSError):c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    assert c.row(a.user_id,p['id'])['phase']=='unknown'
    reread=c.preview(a.user_id,p['id']);assert reread['status']=='active'
    with pytest.raises(ValueError):c.decide(a.user_id,p['id'],reread['preview_version'],'confirm')
    assert c.decide(a.user_id,p['id'],reread['preview_version'],'undo')['status']=='undone'


def test_cancel_and_expiration_never_change_originals_or_copy_source_text(native):
    b,k,m,a,c=native;p=prepare(native)
    assert c.decide(a.user_id,p['id'],p['preview_version'],'cancel')['status']=='cancelled'
    p=prepare(native)
    with b.store.connect() as db:
        db.execute(f'update {TABLE} set expires=0 where id=?',(p['id'],))
        text=str([tuple(row) for row in db.execute(f'select * from {TABLE}')])
    assert k._find_memory('first')['content'] not in text
    assert c.preview(a.user_id,p['id'])['view']=={'status':'expired'}
    with pytest.raises(ValueError):c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    assert m.maintenance_links(a,'test-topic').for_member('first')['status']=='unavailable'


def test_other_owner_cannot_read_or_decide(native):
    _,_,_,a,c=native;p=prepare(native)
    for fn in [lambda:c.preview('30000',p['id']),lambda:c.decide('30000',p['id'],p['preview_version'],'confirm')]:
        with pytest.raises((MemoryAccessDenied,LookupError)):fn()


def test_archive_still_rejects_different_payloads(native):
    _,k,_,_,_=native;k._find_memory('second')['title']='不同资料'
    assert prepare(native,'archive')['status']=='separate_review_required'


def test_rollback_requires_native_reader_even_if_console_reference_is_missing(native,tmp_path):
    b,k,m,a,c=native;prepare(native,'archive')
    with b.store.connect() as db:db.execute(f'delete from {TABLE}')
    # Export only the isolated fixture database under the rollback layout.
    import sqlite3
    target=tmp_path/'rollback/knowledge_base/qq';target.mkdir(parents=True)
    with b.store.connect() as source,sqlite3.connect(target/'bridge.sqlite3') as out:source.backup(out)
    assert 'qq_console_actions_v1' in memory_rollback_requirements(target.parent.parent)


@pytest.fixture
def revision_native(lab):
    b,k,m,a,item=lab
    b.settings=b.settings.model_copy(update={'memory_management_enabled':True,'memory_write_enabled':True})
    b.store.save_conversation('10000:private:20000:shared',[],'test-topic')
    m.actions=c=QQConsoleActions(m)
    return b,k,m,a,item,c


@pytest.mark.parametrize('command',['correct','append','preview-correct','edit'])
def test_qq_modification_waits_for_native_confirmation_and_keeps_original_id(revision_native,command):
    b,k,m,a,item,c=revision_native
    text=f'/memory {command} {item["id"]} {record_version(item)} 喜欢桂花茶。'
    before=item['content'];reply=m.command(a,text,'test-topic')
    assert isinstance(reply,ConsoleNotice) and isinstance(reply,ControlReceipt)
    assert '【操作回执】' not in reply and item['content']==before
    identifier=c.listing(a.user_id)['entries'][0]['id'];p=c.preview(a.user_id,identifier)
    assert p['status']=='pending' and p['view']['old']==before and '桂花茶' in p['view']['new']
    refs=json.loads(c.row(a.user_id,identifier)['refs'])
    assert '没有再次修改' in m.command(a,'/memory revision-confirm '+refs['token'],'test-topic')
    r=c.decide(a.user_id,identifier,p['preview_version'],'confirm');assert r['status']=='applied'
    assert len(k.memories)==1 and k._find_memory(item['id'])['last_modified_channel']=='web'
    assert k._find_memory(item['id'])['origin']['channel']=='unknown'
    assert c.decide(a.user_id,identifier,p['preview_version'],'confirm')['status']=='applied'


@pytest.mark.parametrize('action',['delete','block','unblock'])
def test_qq_operation_waits_for_native_and_revalidates_full_body(revision_native,action):
    b,k,m,a,item,c=revision_native
    reply=m.command(a,f'/memory {action} {item["id"]}','test-topic');assert isinstance(reply,ConsoleNotice)
    identifier=c.listing(a.user_id)['entries'][0]['id'];p=c.preview(a.user_id,identifier)
    assert p['status']=='pending' and p['view']['body']==item['content']
    result=c.decide(a.user_id,identifier,p['preview_version'],'confirm')
    assert result['view']['operation_state']=='done'
    assert c.decide(a.user_id,identifier,p['preview_version'],'confirm')['view']['operation_state']=='done'
    if action=='delete':assert not k.memories


def test_restart_revokes_native_delete_and_hides_unknown_revision_body(revision_native):
    b,k,m,a,item,c=revision_native
    m.command(a,f'/memory delete {item["id"]}','test-topic');p=c.preview(a.user_id,c.listing(a.user_id)['entries'][0]['id'])
    m.operations.recover();c.recover();assert c.preview(a.user_id,p['id'])['status']=='restart_invalidated'
    with pytest.raises(ValueError):c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    m.command(a,f'/memory correct {item["id"]} {record_version(item)} 喜欢桂花茶。','test-topic')
    p=c.preview(a.user_id,c.listing(a.user_id)['entries'][0]['id'])
    with b.store.connect() as db:db.execute(f"update {TABLE} set phase='executing' where id=?",(p['id'],))
    c.recover();assert c.preview(a.user_id,p['id'])['view']=={'status':'unknown'}
    assert item['content']=='喜欢茉莉茶。'


def test_qq_pair_requests_offer_native_notice_without_marking_QQ_preview(native):
    async def run():
        b,k,m,a,c=native;bind_memory_responder(b,m);await b.start()
        try:
            sent=await run_command(b,'/memory link first second',1)
            assert '记忆操作台' in sent[-1][1] and '【操作回执】' not in sent[-1][1]
            with b.store.connect() as db:
                assert db.execute('select preview_state from qq_maintenance_links').fetchone()[0]=='native_waiting'
            assert c.preview(a.user_id,c.listing(a.user_id)['entries'][0]['id'])['status']=='pending'
        finally:await b.stop()
    asyncio.run(run())


def test_actions_http_requires_local_consent_and_returns_complete_revalidated_view(native):
    b,k,m,a,c=native;app=FastAPI();app.include_router(routes.router)
    def service(request,owner_id=''):
        assert owner_id in {'',a.user_id};return m,a,b.settings
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',123)),base_url='http://127.0.0.1:8000') as client:
            values=dict(owner_id=a.user_id,kind='link',first='first',second='second',
                        first_version=record_version(k._find_memory('first')),second_version=record_version(k._find_memory('second')))
            assert (await client.post('/api/qq/memory-console/action-preview',json=values)).status_code==403
            headers={'Origin':'http://127.0.0.1:8000'}
            response=await client.post('/api/qq/memory-console/action-preview',json=values,headers=headers)
            assert response.status_code==200,response.text
            p=response.json();assert response.headers['cache-control']=='no-store'
            assert (await client.get('/api/qq/memory-console/actions?owner_id='+a.user_id)).json()['entries'][0]['id']==p['id']
            decision=dict(owner_id=a.user_id,id=p['id'],preview_version=p['preview_version'],action='confirm')
            assert (await client.post('/api/qq/memory-console/action-decision',json=decision)).status_code==403
            assert (await client.post('/api/qq/memory-console/action-decision',json=decision|{'extra':True},headers=headers)).status_code==422
            result=await client.post('/api/qq/memory-console/action-decision',json=decision,headers=headers)
            assert result.status_code==200 and result.json()['status']=='active',result.text
    with patch.object(routes,'memory_management_service',service):asyncio.run(run())


def test_native_display_preserves_full_text_and_effects():
    from saya_qq_action_panel import action_text
    body='完整原话'*1000
    rendered=action_text(dict(kind='link',status='pending',expires=2000000000,
        view={'sources':[[{'record_id':'old','source':{'author':'test','text':body}}]]}))
    assert body in rendered and '分别保留原条目和条件' in rendered


def test_running_bridge_console_worker_reads_current_config_without_refreshing_it(native):
    async def run():
        b,k,m,a,c=native;await b.start()
        try:
            assert b._qq_values is not None
            revision=b.controls()['revision']
            assert (await asyncio.to_thread(b.controls))['revision']==revision
            p=await asyncio.to_thread(prepare,native)
            assert p['status']=='pending'
            assert (await asyncio.to_thread(c.decide,a.user_id,p['id'],p['preview_version'],'confirm'))['status']=='active'
        finally:await b.stop()
    asyncio.run(run())


def test_committed_revision_keeps_result_if_recent_index_fails(revision_native):
    b,k,m,a,item,c=revision_native
    m.command(a,f'/memory correct {item["id"]} {record_version(item)} 喜欢桂花茶。','test-topic')
    p=c.preview(a.user_id,c.listing(a.user_id)['entries'][0]['id'])
    with patch.object(m,'saved_receipt',side_effect=OSError('index unavailable')):
        result=c.decide(a.user_id,p['id'],p['preview_version'],'confirm')
    assert result['status']=='applied' and result['view']['recent_index']=='unavailable'
    assert c.row(a.user_id,p['id'])['phase']=='finished' and len(k.memories)==1


def test_render_failure_invalidates_consent_and_keeps_polling():
    from queue import Queue
    from types import SimpleNamespace
    from saya_qq_memory_panel import MemoryPanel
    p=MemoryPanel.__new__(MemoryPanel);p.closed=False;p.busy=True;p.events=Queue();invalid=[];scheduled=[];statuses=[]
    def broken(data):raise TypeError('unsupported display')
    p.events.put((broken,{'status':'committed'},None))
    p.refresh_button=p.owners=SimpleNamespace(configure=lambda **kw:None)
    p.status=SimpleNamespace(set=statuses.append);p.invalidate=lambda:invalid.append(True)
    p.window=SimpleNamespace(after=lambda *args:scheduled.append(args))
    p.poll()
    assert invalid==[True] and not p.busy and scheduled[0][1]==p.poll and '不要重复提交' in statuses[-1]


@pytest.mark.parametrize('action',['link','archive','delete','block','unblock'])
def test_native_input_command_creates_full_preview_but_cannot_commit_it(native,action):
    from types import SimpleNamespace
    from saya_app.services.qq_daily_memory import QQDailyMemory
    b,k,m,a,c=native
    b.daily_memory=QQDailyMemory(b,k);b.task=SimpleNamespace(done=lambda:False)
    b.settings=b.settings.model_copy(update={'admin_token':'fixture-admin'})
    app=FastAPI();app.include_router(routes.router)
    members='first second' if action in {'link','archive'} else 'first'
    values=dict(owner_id=a.user_id,text='/memory '+action+' '+members)
    original=json.dumps(k.memories,sort_keys=True)
    async def run():
        with patch.object(routes,'qq_bridge',b):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app,client=('127.0.0.1',1)),base_url='http://127.0.0.1:8000',
                    headers={'Authorization':'Bearer fixture-admin'}) as client:
                path='/api/qq/memory-console/command'
                assert (await client.post(path,json=values)).status_code==403
                headers={'Origin':'http://127.0.0.1:8000'}
                assert (await client.post(path,json=values|{'source_user':'foreign'},headers=headers)).status_code==422
                response=await client.post(path,json=values,headers=headers)
                assert response.status_code==200,response.text
                assert response.json()['kind']=='action_preview' and response.json()['data']['status']=='pending'
                assert response.headers['cache-control']=='no-store'
                assert k._find_memory('first')['content'] in json.dumps(response.json(),ensure_ascii=False)
                assert (await client.post(path,json=values|{'text':'/memory confirm invented'},headers=headers)).status_code==400
    asyncio.run(run())
    assert json.dumps(k.memories,sort_keys=True)==original
    assert c.listing(a.user_id)['entries'][0]['kind']==action


@pytest.mark.parametrize('text',['/memory link first','/memory delete first second','/memory delete first; drop table x','/memory correct first version text'])
def test_native_command_rejects_unknown_arity_or_arbitrary_mutation(native,text):
    _,_,_,a,c=native;words=text.split()
    with pytest.raises((ValueError,LookupError)):c.command_preview(a.user_id,words[1],words[2:])
    assert not c.listing(a.user_id)['entries']
