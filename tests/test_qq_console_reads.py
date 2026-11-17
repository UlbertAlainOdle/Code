import asyncio,json,sqlite3
from copy import deepcopy
from unittest.mock import patch
from types import SimpleNamespace
import httpx,pytest
from fastapi import FastAPI
from saya_app.services.qq_console_reads import QQConsoleReads,readonly_query,TABLE
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_responses import ConsoleNotice,dialogue_body
from saya_app.services.qq_event_console import QQEventConsole
from saya_app.services.qq_rollback import memory_rollback_requirements
from saya_app.routers import qq as routes
from test_qq_memory_management import lab
from test_qq_delivery import event
from test_qq_topic_routing import topics
from test_qq_event_operations import seed
from test_qq_r5_maintenance_links import memory_links,prepare_pair
from test_qq_r5_maintenance_archive import archive_lab
from test_qq_r5_maintenance_commands import bind_memory_responder,run_command
from dataclasses import replace


@pytest.fixture
def reads(lab):
    b,k,m,a,record=lab;m.reads=QQConsoleReads(m)
    b.store.save_conversation('10000:private:20000:shared',[],'read-topic')
    return b,k,m,a,record


def incoming(b,text,mid=1):
    m=normalize_event(event(text,mid,20000),b.settings)[0];assert m
    b.store.admit(m,b.settings);return m


def test_source_bound_query_no_QQ_body_and_full_local_current_result(reads):
    b,k,m,a,record=reads;long=k.add_memory('完整事实'*600+'末尾必须出现。',source='manual')
    before=deepcopy(k.memories);msg=incoming(b,'/memory show '+long['id'])
    notice=m.reads.offer(msg,'read-topic');assert isinstance(notice,ConsoleNotice) and '【操作回执】' not in notice and not dialogue_body(notice)
    assert long['content'] not in notice and long['id'] not in notice
    listing=m.reads.listing(a.user_id);assert len(listing['entries'])==1
    result=m.reads.read(a.user_id,listing['entries'][0]['id']);assert long['content'] in result['text'] and result['read_only']
    assert k.memories==before
    m.reads.offer(msg,'read-topic');assert len(m.reads.listing(a.user_id)['entries'])==1
    with b.store.connect() as db:
        row=dict(db.execute('select * from '+TABLE).fetchone());assert long['content'] not in json.dumps(row,ensure_ascii=False)
    k.update_memory(long['id'],{'content':'当前新版本。'})
    result=m.reads.read(a.user_id,listing['entries'][0]['id']);assert '当前新版本' in result['text'] and '末尾必须出现' not in result['text']


@pytest.mark.parametrize('change',['body','delete_source','owner','sharing','expiry'])
def test_query_source_and_current_permissions_are_rechecked(reads,change):
    b,k,m,a,record=reads;msg=incoming(b,'/memory list');m.reads.offer(msg,'read-topic');identifier=m.reads.listing(a.user_id)['entries'][0]['id']
    with b.store.connect() as db:
        if change=='body':
            raw=json.loads(db.execute('select payload from inbox where event_key=?',(msg.key,)).fetchone()[0]);raw['text']='/memory show different'
            db.execute('update inbox set payload=? where event_key=?',(json.dumps(raw),msg.key))
        elif change=='delete_source':db.execute('delete from inbox where event_key=?',(msg.key,))
        elif change=='expiry':db.execute('update '+TABLE+' set expires=0')
    if change=='owner':b.settings=b.settings.model_copy(update={'owner_ids':frozenset({'30000'})})
    elif change=='sharing':b.settings=b.settings.model_copy(update={'shared_memory':False})
    with pytest.raises((ValueError,LookupError,MemoryAccessDenied)):m.reads.read(a.user_id,identifier)


@pytest.mark.parametrize('text',['/memory delete memory:a','/memory confirm abc','/memory correct a b text','/events stop abc','/events group show abc','/events on','/events user 30000','/memory show a\n/memory delete b'])
def test_read_namespace_cannot_execute_writes_or_widen_group_person_scope(reads,text):
    b,k,m,a,_=reads;assert not readonly_query(text)
    with pytest.raises(ValueError):m.reads.local(a.user_id,text)
    assert not m.reads.listing(a.user_id)['entries']


def test_owner_http_read_and_local_command_keep_auth_and_no_store(reads):
    b,k,m,a,record=reads;m.reads.offer(incoming(b,'/memory list'),'read-topic')
    b.task=SimpleNamespace(done=lambda:False);b.settings=b.settings.model_copy(update={'admin_token':'fixture-admin'})
    app=FastAPI();app.include_router(routes.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1',headers={'Authorization':'Bearer fixture-admin'}) as c:
            path='/api/qq/memory-console/reads'
            listing=await c.get(path+'?owner_id=20000');assert listing.status_code==200 and listing.headers['cache-control']=='no-store'
            key=listing.json()['entries'][0]['id'];assert (await c.get(path+'/'+key+'?owner_id=30000')).status_code==403
            result=await c.get(path+'/'+key+'?owner_id=20000');assert result.status_code==200 and result.json()['read_only']
    with patch.object(routes,'qq_bridge',b):asyncio.run(run())


def test_read_queue_requires_rollback_capability(reads,tmp_path):
    b,k,m,a,_=reads;m.reads.offer(incoming(b,'/memory list'),'read-topic')
    target=tmp_path/'rollback/knowledge_base/qq/bridge.sqlite3';target.parent.mkdir(parents=True)
    with b.store.connect() as src,sqlite3.connect(target) as dst:src.backup(dst)
    assert 'qq_console_reads_v1' in memory_rollback_requirements(target.parent.parent.parent)


def test_explicit_owner_event_stop_requires_original_native_preview_not_QQ_yes(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);console=s.operations.console=QQEventConsole(s.operations)
            request=msg('/events stop '+cards[0]['id']);b.store.admit(request,b.settings)
            notice=console.command(request);assert isinstance(notice,ConsoleNotice)
            assert all(c.get('tracking')!='suppressed' for c in s.people._canonical('20000'))
            listing=console.listing('20000');p=console.preview('20000',listing['entries'][0]['id'])
            assert p['state']=='native_prepared' and len(p['targets'])==1
            result=await s.operations.handle(msg('对的'));assert not result or '已' not in str(result)
            assert all(c.get('tracking')!='suppressed' for c in s.people._canonical('20000'))
            result=console.decide('20000',p['id'],p['preview_version'],'confirm');assert result['state']=='done'
            assert sum(c.get('tracking')=='suppressed' for c in s.people._canonical('20000'))==1
    asyncio.run(run())


def test_authenticated_bridge_routes_memory_queries_without_model_or_fact_write(reads):
    async def run():
        b,k,m,a,record=reads;before=deepcopy(k.memories);bind_memory_responder(b,m)
        await b.start()
        try:
            sent=await run_command(b,'/memory show '+record['id'],9001)
            assert len(sent)==1 and '查询与回执' in sent[0][1] and '【操作回执】' not in sent[0][1]
            assert record['content'] not in sent[0][1]
            entries=m.reads.listing(a.user_id)['entries'];assert len(entries)==1
            assert record['content'] in m.reads.read(a.user_id,entries[0]['id'])['text']
            assert k.memories==before
            with patch.object(m.reads,'offer',side_effect=ValueError('native_read_queue_full')):
                failed=await run_command(b,'/memory list',9002)
            assert '没有执行保存或修改' in failed[0][1] and '模型配置' not in failed[0][1]
        finally:await b.stop()
    asyncio.run(run())


@pytest.mark.parametrize('change',[{'group_id':'90000'},{'user_id':'30000'},{'bot_id':'11000'},{'reply_to':'old'},{'forward_present':True},{'media_types':['image']}])
def test_read_migration_does_not_capture_other_audiences_or_quoted_inputs(reads,change):
    b,k,m,a,_=reads;msg=replace(incoming(b,'/memory list'),**change)
    assert m.reads.offer(msg,'read-topic') is None
    assert not m.reads.listing(a.user_id)['entries']


def test_authenticated_event_commands_only_prepare_and_local_query_is_read_only(tmp_path):
    from core_services import KnowledgeBase
    from saya_app.services.qq_memory_management import QQMemoryManagement
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            cards=await seed(s,msg,choice);console=s.operations.console=QQEventConsole(s.operations)
            m=b.memory_management=QQMemoryManagement(b,KnowledgeBase(str(tmp_path/'memory')));m.reads=QQConsoleReads(m)
            bind_memory_responder(b,m)
            query=await run_command(b,'/events self',9001)
            assert len(query)==1 and '查询与回执' in query[0][1] and '【操作回执】' not in query[0][1]
            entry=m.reads.listing('20000')['entries'][0];current=m.reads.read('20000',entry['id'])
            assert cards[0]['id'] in current['text']
            before=deepcopy(s.people._canonical('20000'))
            assert m.reads.local('20000','/events self')['read_only']
            assert s.people._canonical('20000')==before
            operation=await run_command(b,'/events stop '+cards[0]['id'],9002)
            assert len(operation)==1 and '后台' in operation[0][1] and '【操作回执】' not in operation[0][1]
            assert s.people._canonical('20000')==before
            preview=console.preview('20000',console.listing('20000')['entries'][0]['id'])
            assert len(preview['targets'])==1 and preview['targets'][0]['id']==cards[0]['id']
            console.decide('20000',preview['id'],preview['preview_version'],'reject')
            assert s.people._canonical('20000')==before
    asyncio.run(run())


def test_native_joint_reads_preserve_pending_and_current_source_permissions(memory_links):
    b,k,m,a,links,store=memory_links;m.reads=QQConsoleReads(m);op='a'*32
    assert prepare_pair(links,op)['status']=='pending'
    before=store.export_state();pending=m.reads.local(a.user_id,'/memory link-result '+op)
    assert 'pending' in pending['text'] and links.result(op)['status']=='pending'
    assert links.confirm(op)['status']=='active'
    full=m.reads.local(a.user_id,'/memory link-show first')
    assert '偏爱浅烘咖啡' in full['text'] and '喜欢浅烘咖啡' in full['text']
    k.memory_use.set_block('second',True)
    stale=m.reads.local(a.user_id,'/memory link-show first')
    assert '偏爱浅烘咖啡' not in stale['text'] and store.export_state()==before
    for text in ['/memory link-confirm '+op,'/memory link-undo '+op]:
        with pytest.raises(ValueError):m.reads.local(a.user_id,text)


def test_native_archive_reads_never_confirm_pending_or_restore_originals(archive_lab):
    b,k,m,a,_=archive_lab;m.reads=QQConsoleReads(m);archive=m.maintenance_archive(a,'test-topic');op='b'*32
    assert archive.prepare(op,'first','second',check_preview=lambda preview:None)['status']=='pending'
    before=deepcopy(k.memories)
    pending=m.reads.local(a.user_id,'/memory archive-result '+op)
    assert 'pending' in pending['text'] and archive.result(op)['status']=='pending' and k.memories==before
    for text in ['/memory archive-confirm '+op,'/memory archive-undo '+op]:
        with pytest.raises(ValueError):m.reads.local(a.user_id,text)


def test_old_candidate_list_is_a_current_native_read_without_candidate_approval(reads):
    b,k,m,a,record=reads
    k.add_memory_candidate('这是隔离候选，杉桥资料拟使用青色标签。')
    before=deepcopy(k.memory_candidates);request=incoming(b,'/memories')
    notice=m.reads.offer(request,'read-topic');assert isinstance(notice,ConsoleNotice) and '【操作回执】' not in notice
    key=m.reads.listing(a.user_id)['entries'][0]['id'];result=m.reads.read(a.user_id,key)
    assert '杉桥资料' in result['text'] and result['read_only'] and k.memory_candidates==before
    assert m.reads.local(a.user_id,'/memories 1')['read_only']
