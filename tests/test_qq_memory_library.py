"""Synthetic model plans prove host safety, not semantic model accuracy."""
import asyncio
from copy import deepcopy
import json
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from saya_app.routers import qq as routes
from saya_app.services.qq_console_actions import QQConsoleActions
from saya_app.services.qq_memory_library import QQMemoryLibrary,CHECKS,duplicate_guard
from saya_app.services.qq_expressions import QQExpressions
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_rollback import memory_rollback_requirements
from test_qq_memory_management import lab


@pytest.fixture
def library(lab):
    b,k,m,a,first=lab
    b.settings=b.settings.model_copy(update={'memory_write_enabled':True,'memory_management_enabled':True})
    b.store.save_conversation('10000:private:20000:shared',[],'library-topic')
    m.actions=QQConsoleActions(m);m.library=lib=QQMemoryLibrary(m)
    b.expressions=QQExpressions(b,b.store.path.parent)
    second=k.add_memory('我偏爱窨花清饮。',title='另一种表述',source='manual')
    return b,k,m,a,lib,first,second


def responder(relation='duplicate',modify=None):
    async def decide(prompt,**kw):
        assert kw=={'purpose':'qq_library_review','output_tokens':1536}
        rows=json.loads(prompt.split('DATA:\n',1)[1]);assert 1<=len(rows)<=4
        value={'pairs':[{'index':r['index'],'relation':relation,'first_quote':r['first']['body'][:100],
                       'second_quote':r['second']['body'][:100],'checks':dict.fromkeys(CHECKS,True)} for r in rows]}
        if modify:modify(value)
        return value
    return decide


def scan(library,relation='duplicate'):
    b,k,m,a,lib,*_=library;state=lib.status(a.user_id)
    with patch.object(b.expressions,'decide',responder(relation)):
        return asyncio.run(lib.scan(a.user_id,state['snapshot'],state['cursor']))


def test_large_catalogue_reads_one_current_policy_snapshot_per_locked_pass(library):
    b,k,m,a,lib,first,second=library
    for n in range(400):k.memories.append(deepcopy(first)|{'id':'catalog-'+str(n)})
    k.memory_use.set_block('catalog-399',True)
    with patch.object(k.memory_use,'read_state',wraps=k.memory_use.read_state) as read:
        rows,snapshot=lib.catalog(a.user_id)
    assert read.call_count==1 and len(rows)==401 and 'catalog-399' not in {r['id'] for r in rows}
    k.memory_use.set_block('catalog-398',True)
    rows,new_snapshot=lib.catalog(a.user_id)
    assert new_snapshot!=snapshot and 'catalog-398' not in {r['id'] for r in rows}


def test_exact_confirmed_source_copy_needs_no_provider_but_still_requires_original_consent(library):
    b,k,m,a,lib,first,second=library
    origin={'verification':'verified','channel':'qq','bot_id':'10000','verified_subject':'20000','message_id':'same-original'}
    for r in [first,second]:r.update(content='这是虚构材料，星港资料只使用蓝色标签。',user_confirmed=True,origin=deepcopy(origin))
    before=deepcopy(k.memories);state=lib.status(a.user_id)
    async def forbidden(*args,**kwargs):pytest.fail('an exact source copy does not require a semantic request')
    with patch.object(b.expressions,'decide',forbidden):state=asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    assert state['cursor']==state['total_pairs']==1 and state['requests']==0 and k.memories==before
    p=lib.preview(a.user_id,state['entries'][0]['id']);assert p['relation']=='duplicate'
    action=lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    assert k.memories==before and all(k.memory_allowed_for_use(r) for r in k.memories)
    assert m.actions.decide(a.user_id,action['id'],action['preview_version'],'cancel')['status']=='cancelled'
    assert k.memories==before


@pytest.mark.parametrize('change',['different_source','unverified','unconfirmed','different_occurrence'])
def test_equal_words_with_different_or_unknown_provenance_still_need_semantic_review(library,change):
    from saya_app.services.qq_memory_library import exact_source_copy
    b,k,m,a,lib,first,second=library
    origin={'verification':'verified','channel':'qq','bot_id':'10000','verified_subject':'20000','message_id':'source'}
    for r in [first,second]:r.update(content='同样一句完整事实。',user_confirmed=True,origin=deepcopy(origin))
    if change=='different_source':second['origin']['message_id']='another-source'
    elif change=='unverified':second['origin']['verification']='unknown'
    elif change=='unconfirmed':second['user_confirmed']=False
    else:
        first['qq_daily_entry']={'kind':'daily','day':'2026-11-14'};second['qq_daily_entry']={'kind':'daily','day':'2026-11-15'}
    assert not exact_source_copy(first,second)


def test_new_catalogue_reuses_unchanged_pair_proofs_including_separate_results(library):
    b,k,m,a,lib,first,second=library;scan(library,'separate')
    old_pair={first['id'],second['id']};before=deepcopy(k.memories);epoch=k.memory_use.snapshot()
    k.add_memory('另一项独立的资料安排。',source='manual')
    assert k.memory_use.snapshot()==epoch
    state=lib.status(a.user_id);seen=[]
    async def decide(prompt,**kw):
        rows=json.loads(prompt.split('DATA:\n',1)[1]);seen.extend(rows)
        assert all({r['first']['id'],r['second']['id']}!=old_pair for r in rows)
        return await responder('separate')(prompt,**kw)
    with patch.object(b.expressions,'decide',decide):state=asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    assert len(seen)==2 and state['cursor']==state['total_pairs']==3
    with b.store.connect() as db:db.execute("update qq_memory_library_scans set snapshot='changed-scope'")
    state=lib.status(a.user_id)
    async def forbidden(*args,**kwargs):pytest.fail('unchanged current pairs should reuse bounded advisory proofs')
    with patch.object(b.expressions,'decide',forbidden):state=asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    assert state['requests']==0 and state['cursor']==3 and k.memories[:2]==before
    for key,value in lib._pair_cache.items():
        assert all(r['content'] not in json.dumps(value,ensure_ascii=False) for r in k.memories)


@pytest.mark.parametrize('change',['epoch','settings','TTL','restart'])
def test_pair_cache_never_survives_privacy_settings_expiry_or_restart(library,change):
    b,k,m,a,lib,*_=library;scan(library,'separate')
    if change=='epoch':k.memory_use.set_block('unrelated',True)
    elif change=='settings':b.settings=b.settings.model_copy(update={'history_turns':b.settings.history_turns+1})
    elif change=='TTL':
        for key,item in list(lib._pair_cache.items()):lib._pair_cache[key]=(0,item[1])
    else:lib=m.library=QQMemoryLibrary(m)
    with b.store.connect() as db:db.execute("update qq_memory_library_scans set snapshot='restart-scope'")
    state=lib.status(a.user_id);calls=[]
    async def decide(*args,**kwargs):calls.append(1);return await responder('separate')(*args,**kwargs)
    with patch.object(b.expressions,'decide',decide):asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    assert calls==[1]


def test_pair_cache_storage_is_bounded_and_does_not_copy_fact_bodies(library):
    b,k,m,a,lib,*_=library
    lib.remember_pairs((str(i),('separate',{'spans':[[0,1],[0,1]]})) for i in range(4100))
    assert len(lib._pair_cache)==4096 and '0' not in lib._pair_cache and '4099' in lib._pair_cache


def test_zero_overlap_suggestions_readonly_then_original_stop_confirm(library):
    b,k,m,a,lib,first,second=library
    before=deepcopy(k.memories);epoch=k.memory_use.snapshot()
    state=scan(library);assert state['cursor']==state['total_pairs']==1 and state['requests']==1
    assert k.memories==before and k.memory_use.snapshot()==epoch
    p=lib.preview(a.user_id,state['entries'][0]['id'])
    from saya_qq_library_panel import library_text
    assert all(r['content'] in library_text(p) for r in before)
    result=lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    from saya_qq_action_panel import action_text
    assert all(r['content'] in action_text(result) for r in before)
    assert '不释放存储容量' in action_text(result)
    assert k.memories==before and k.memory_use.snapshot()==epoch
    keep=p['first']['id'];duplicate=p['second']['id']
    result=m.actions.decide(a.user_id,result['id'],result['preview_version'],'confirm')
    assert result['status']=='success'
    assert k.memory_allowed_for_use(k._find_memory(keep)) and not k.memory_allowed_for_use(k._find_memory(duplicate))
    for original in before:
        record=k._find_memory(original['id'])
        for field in ['id','content','date','origin','source']:
            assert record[field]==original[field]  # Stop-use metadata may change.
    terminal=m.actions.preview(a.user_id,result['id'])
    assert all(r['content'] not in json.dumps(terminal,ensure_ascii=False) for r in before)
    assert m.actions.decide(a.user_id,result['id'],result['preview_version'],'confirm')['status']=='success'
    assert lib.status(a.user_id)['entries']==[]
    restored=m.actions.command_preview(a.user_id,'unblock',[duplicate])
    assert m.actions.decide(a.user_id,restored['id'],restored['preview_version'],'confirm')['status']=='success'
    assert k.memory_allowed_for_use(k._find_memory(duplicate))


def test_every_same_namespace_pair_covered_in_bounded_resumable_batches(library):
    b,k,m,a,lib,*_=library
    for n in range(5):k.add_memory('独立原文'+chr(0x4e00+n),source='manual')
    foreign=k.add_memory('不同项目内容',source='manual');foreign['project_id']='other'
    seen=[]
    async def decide(prompt,**kw):
        rows=json.loads(prompt.split('DATA:\n',1)[1])
        seen.extend((r['first']['id'],r['second']['id']) for r in rows)
        return await responder('separate')(prompt,**kw)
    state=lib.status(a.user_id);assert state['total_pairs']==21
    with patch.object(b.expressions,'decide',decide):
        while state['cursor']<state['total_pairs']:state=asyncio.run(lib.scan(a.user_id,state['snapshot'],state['cursor']))
    assert len(seen)==len(set(seen))==21 and state['requests']==6 and state['state']=='complete'
    assert state['entries']==[]
    rows,_=lib.catalog(a.user_id);all_pairs=list(lib.pairs(rows))
    for start in range(22):assert list(lib.pairs(rows,start))==all_pairs[start:]
    # Progress resumes after service reconstruction, never starts provider work itself.
    assert QQMemoryLibrary(m).status(a.user_id)['cursor']==21


@pytest.mark.parametrize('corrupt',[
    lambda v:v['pairs'][0].update(first_quote='无依据杜撰'),
    lambda v:v['pairs'][0].update(index=99),
    lambda v:v['pairs'].append(v['pairs'][0]),
    lambda v:v['pairs'][0]['checks'].pop('both_complete'),
    lambda v:v['pairs'][0].update(relation=[]),
])
def test_untrusted_model_reply_cannot_advance_or_modify(library,corrupt):
    b,k,m,a,lib,*_=library;state=lib.status(a.user_id);before=deepcopy(k.memories)
    with patch.object(b.expressions,'decide',responder(modify=corrupt)),pytest.raises(ValueError):
        asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    after=lib.status(a.user_id)
    assert after['cursor']==0 and after['requests']==1 and after['error'] and after['entries']==[] and k.memories==before
    assert not lib._pair_cache
    assert scan(library)['cursor']==1


def test_incomplete_checks_downgrade_duplicate_and_no_body_copies_in_review_table(library):
    b,k,m,a,lib,*_=library;state=lib.status(a.user_id)
    with patch.object(b.expressions,'decide',responder(modify=lambda v:v['pairs'][0]['checks'].update(both_complete=False))):
        state=asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    p=lib.preview(a.user_id,state['entries'][0]['id']);assert p['relation']=='unknown'
    with pytest.raises(ValueError):lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    with b.store.connect() as db:
        row=dict(db.execute('select * from qq_memory_library_reviews').fetchone())
    assert all(r['content'] not in json.dumps(row,ensure_ascii=False) for r in k.memories)
    assert 'spans' in json.loads(row['proof'])


@pytest.mark.parametrize('change',['body','epoch','config','restart','expiry'])
def test_preview_version_permissions_and_restart_checked_before_preparing(library,change):
    b,k,m,a,lib,first,second=library;state=scan(library);p=lib.preview(a.user_id,state['entries'][0]['id'])
    if change=='body':k.update_memory(first['id'],{'content':'新正文'})
    elif change=='epoch':k.memory_use.set_block('unrelated',True)
    elif change=='config':b.settings=b.settings.model_copy(update={'memory_write_enabled':False})
    elif change=='restart':lib=m.library=QQMemoryLibrary(m)
    else:
        with b.store.connect() as db:db.execute('update qq_memory_library_reviews set expires=0')
    with pytest.raises((ValueError,LookupError,MemoryAccessDenied)):
        lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    assert all(k.memory_allowed_for_use(r) for r in k.memories)


@pytest.mark.parametrize('change',['keep_body','keep_locked','duplicate_important','topic','epoch'])
def test_final_original_confirmation_revalidates_both_sides(library,change):
    b,k,m,a,lib,*_=library;state=scan(library);p=lib.preview(a.user_id,state['entries'][0]['id'])
    action=lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    if change=='keep_body':k.update_memory(p['first']['id'],{'content':'另一种事实'})
    elif change=='keep_locked':k._find_memory(p['first']['id'])['is_locked']=True
    elif change=='duplicate_important':k._find_memory(p['second']['id'])['is_important']=True
    elif change=='topic':b.store.save_conversation('10000:private:20000:shared',[],'new-topic')
    else:k.memory_use.set_block('unrelated',True)
    with pytest.raises((ValueError,LookupError,MemoryAccessDenied)):
        m.actions.decide(a.user_id,action['id'],action['preview_version'],'confirm')
    assert all(k.memory_allowed_for_use(r) for r in k.memories)


def test_important_and_transitive_keep_dependencies_protected(library):
    b,k,m,a,lib,first,second=library
    first['is_important']=second['is_important']=True
    state=scan(library);p=lib.preview(a.user_id,state['entries'][0]['id'])
    for keep in ['first','second']:
        with pytest.raises(ValueError,match='protected'):lib.prepare(a.user_id,p['id'],p['preview_version'],keep)
    first.pop('is_important');second.pop('is_important')
    state=scan(library);p=lib.preview(a.user_id,state['entries'][0]['id'])
    from saya_app.services.memory_versions import dependency_version
    intermediate={'id':'intermediate','content':'派生节点'};k.memories.append(intermediate)
    dependencies={p['first']['id']:{'target_version':dependency_version(k._find_memory(p['first']['id'])),
                    'sources':{'intermediate':dependency_version(intermediate)}},
                  'intermediate':{'target_version':dependency_version(intermediate),
                    'sources':{p['second']['id']:dependency_version(k._find_memory(p['second']['id']))}}}
    memory_state=k.memory_use.read_state()|{'dependencies':dependencies}
    with patch.object(k.memory_use,'read_state',return_value=memory_state),pytest.raises(ValueError,match='depends'):
        lib.prepare(a.user_id,p['id'],p['preview_version'],'first')


@pytest.mark.parametrize('patch_second,body,guard',[
    ({'project_id':'other'},'喜欢茉莉茶。','different_namespace'),
    ({'qq_daily_entry':{'kind':'daily','day':'2026-11-15'}},'喜欢茉莉茶。','different_daily_period'),
    ({'valid_from':'2026-11-15'},'喜欢茉莉茶。','different_recorded_period'),
    ({},'这是虚构材料，喜欢茉莉茶。','different_fiction'),
    ({},'喜欢茉莉茶2杯。','different_numbers_or_dates'),
    ({'date':'2026-11-16'},'今天喜欢茉莉茶。','different_relative_period'),
    ({},'不喜欢茉莉茶。','different_negation'),
    ({},'也许喜欢茉莉茶。','different_uncertainty'),
])
def test_host_guards_do_not_equate_different_conditions(patch_second,body,guard):
    first={'id':'a','content':'喜欢茉莉茶。','date':'2026-11-15'}
    second=first|{'id':'b','content':body}|patch_second
    assert duplicate_guard(first,second)==guard


def test_changes_and_concurrent_scan_during_model_await_discard_result(library):
    b,k,m,a,lib,first,second=library;state=lib.status(a.user_id)
    async def decide(prompt,**kw):
        with pytest.raises(ValueError,match='busy'):await lib.scan(a.user_id,state['snapshot'],0)
        k.update_memory(first['id'],{'content':'已变动的内容'})
        return await responder()(prompt,**kw)
    with patch.object(b.expressions,'decide',decide),pytest.raises(ValueError,match='changed_during'):
        asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    assert not lib.busy and lib.status(a.user_id)['entries']==[]


def test_cancel_does_not_retry_or_lose_started_request_count(library):
    b,k,m,a,lib,*_=library;state=lib.status(a.user_id)
    async def decide(*args,**kw):raise asyncio.CancelledError
    with patch.object(b.expressions,'decide',decide),pytest.raises(asyncio.CancelledError):
        asyncio.run(lib.scan(a.user_id,state['snapshot'],0))
    after=lib.status(a.user_id);assert after['cursor']==0 and after['requests']==1 and after['state']=='idle' and not lib.busy


def test_pair_body_budget_uses_full_text_and_skips_no_pairs(library):
    b,k,m,a,lib,first,second=library
    for r in k.memories:r['content']='正文'*6000
    third=k.add_memory('文字'*6000,source='manual')
    state=lib.status(a.user_id);assert state['total_pairs']==3
    calls=[]
    async def decide(prompt,**kw):
        rows=json.loads(prompt.split('DATA:\n',1)[1]);assert len(rows)==1
        assert len(rows[0]['first']['body'])==len(rows[0]['second']['body'])==12000
        calls.append(rows[0]['index']);return await responder('separate')(prompt,**kw)
    with patch.object(b.expressions,'decide',decide):
        while state['cursor']<3:state=asyncio.run(lib.scan(a.user_id,state['snapshot'],state['cursor']))
    assert len(calls)==state['requests']==3


def test_stale_suggestions_pruned_and_source_changes_reset_coverage(library):
    b,k,m,a,lib,first,second=library;state=scan(library)
    old=state['entries'][0]['id'];assert len(state['entries'])==1
    k.update_memory(first['id'],{'content':'我喜欢另一种茶饮。'})
    before=lib.status(a.user_id);assert before['cursor']==0 and before['entries']==[]
    after=scan(library)
    with b.store.connect() as db:
        assert not db.execute('select 1 from qq_memory_library_reviews where id=?',(old,)).fetchone()
        assert db.execute('select count(*) from qq_memory_library_reviews').fetchone()[0]==1
    assert after['cursor']==1


def test_disjoint_period_pair_never_calls_provider_or_offers_stop(library):
    b,k,m,a,lib,first,second=library
    first['qq_daily_entry']={'kind':'daily','day':'2026-11-14'}
    second['qq_daily_entry']={'kind':'daily','day':'2026-11-15'}
    with patch.object(b.expressions,'decide') as model:state=asyncio.run(lib.scan(a.user_id,lib.status(a.user_id)['snapshot'],0))
    model.assert_not_called();assert state['cursor']==1 and state['requests']==0 and not state['entries']


@pytest.mark.parametrize('stage',['qq_memory_lookup','qq_library_review','qq_daily_memory','qq_mood_state','qq_expression_select','qq_event_operation','qq_memory_reference'])
def test_internal_stage_costs_and_unknown_failures_remain_accountable(tmp_path,stage):
    from saya_app.services.api_usage import record_turn,load_ledger
    record_turn(tmp_path,'fixture',None,completed=False,stage=stage,elapsed_ms=13)
    record_turn(tmp_path,'fixture',{'prompt_tokens':10,'completion_tokens':2,'total_tokens':12},stage=stage,elapsed_ms=17)
    entry=next(iter(load_ledger(tmp_path)['days'].values()))['fixture'];detail=entry['stages'][stage]
    assert entry['requests']==detail['requests']==2 and entry['total_tokens']==detail['total_tokens']==12
    assert detail['failed']==1 and detail['completed']==1 and detail['usage_unknown']==1 and detail['elapsed_ms']==30


def test_library_routes_owner_origin_schema_and_readonly_http(library):
    b,k,m,a,lib,*_=library;app=FastAPI();app.include_router(routes.router)
    from types import SimpleNamespace
    b.task=SimpleNamespace(done=lambda:False)
    b.settings=b.settings.model_copy(update={'admin_token':'fixture-admin'})
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1',headers={'Authorization':'Bearer fixture-admin'}) as c:
            response=await c.get('/api/qq/memory-console/library',params={'owner_id':a.user_id})
            assert response.status_code==200 and response.headers['cache-control']=='no-store'
            state=response.json();data={'owner_id':a.user_id,'snapshot':state['snapshot'],'cursor':0}
            path='/api/qq/memory-console/library-scan'
            assert (await c.post(path,json=data)).status_code==403
            assert (await c.post(path,json=data|{'cursor':True},headers={'Origin':'http://127.0.0.1'})).status_code==422
            assert (await c.post(path,json=data|{'extra':1},headers={'Origin':'http://127.0.0.1'})).status_code==422
            assert (await c.get('/api/qq/memory-console/library',params={'owner_id':'30000'})).status_code==403
            response=await c.post(path,json=data,headers={'Origin':'http://127.0.0.1'})
            assert response.status_code==200 and response.json()['state']=='complete'
    with patch.object(routes,'qq_bridge',b),patch.object(b.expressions,'decide',responder()):asyncio.run(run())


def test_rollback_requires_new_capability_even_if_review_table_lost(library,tmp_path):
    b,k,m,a,lib,*_=library;state=scan(library);p=lib.preview(a.user_id,state['entries'][0]['id'])
    lib.prepare(a.user_id,p['id'],p['preview_version'],'first')
    from pathlib import Path
    import shutil
    root=tmp_path/'rollback';target=root/'knowledge_base/qq/bridge.sqlite3';target.parent.mkdir(parents=True)
    with b.store.connect() as source:
        import sqlite3
        with sqlite3.connect(target) as destination:source.backup(destination)
    assert 'qq_semantic_library_v1' in memory_rollback_requirements(root)
    with b.store.connect() as db:
        db.execute('drop table qq_memory_library_reviews');db.execute('drop table qq_memory_library_scans')
    with b.store.connect() as source:
        with sqlite3.connect(target) as destination:source.backup(destination)
    assert 'qq_semantic_library_v1' in memory_rollback_requirements(root)
