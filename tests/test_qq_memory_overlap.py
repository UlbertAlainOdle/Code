"""Synthetic overlap plans prove retention and consent boundaries, not accuracy."""
import asyncio,json,sqlite3
from copy import deepcopy
from unittest.mock import patch
import httpx,pytest
from fastapi import FastAPI
from types import SimpleNamespace

from test_qq_daily_memory_lookup import lab
from test_qq_daily_memory import lab as base_lab
from test_qq_daily_memory import accept,msg
from test_qq_memory_library import responder
from saya_app.services.qq_memory_library import QQMemoryLibrary,CHECKS
from saya_app.services.qq_console_actions import QQConsoleActions
from saya_app.services.qq_memory_overlap import QQMemoryOverlap,units,residual
from saya_app.services.qq_rollback import memory_rollback_requirements
from saya_app.services.qq_daily_memory import QQDailyMemory
from saya_app.routers import qq as routes


@pytest.fixture
def overlap(lab):
    b,k,s,now=lab;m=b.memory_management
    b.store.save_conversation('10000:private:20000:shared',[],'overlap-topic')
    m.actions=QQConsoleActions(m);m.library=QQMemoryLibrary(m);m.overlap=QQMemoryOverlap(m)
    important=accept(lab,'这是虚构材料，星湾档案长期使用青色标签。',level='important')[0]
    p=s.preview('20000',important['id']);s.decide('20000',important['id'],p['preview_version'],'approve')
    anchor=k.memories[0]
    daily=next(r for r in accept(lab,'这是虚构材料，星湾档案长期使用青色标签；今天把纸船放进木盒。',2) if r['kind']=='daily')
    return lab,m,daily,anchor


def model(anchor,mutate=None):
    async def decide(prompt,**kw):
        assert kw=={'purpose':'qq_memory_overlap','output_tokens':1536}
        data=json.loads(prompt.split('DATA:\n',1)[1]);assert '今天把纸船放进木盒' in data['daily']['body']
        value={'matches':[{'unit':0,'anchor':anchor['id'],'important_quote':'星湾档案长期使用青色标签',
            'checks':dict.fromkeys(CHECKS,True)}]}
        if mutate:mutate(value)
        return value
    return decide


def test_one_owner_cannot_start_duplicate_paid_overlap_and_cancel_releases_budget(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id']);calls=[]
    async def run():
        entered=asyncio.Event();release=asyncio.Event()
        async def blocked(*args,**kwargs):
            calls.append(1);entered.set();await release.wait();return await model(anchor)(*args,**kwargs)
        with patch.object(b.expressions,'decide',blocked):
            task=asyncio.create_task(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
            await entered.wait()
            with pytest.raises(ValueError,match='owner_review_busy'):
                await m.overlap.prepare_daily('20000',row['id'],p['preview_version'])
            with pytest.raises(ValueError,match='owner_review_busy'):
                await m.overlap.prepare_saved('20000','not-yet-read','old')
            assert calls==[1]
            task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
        assert not m.overlap._budget_owners
        with b.store.connect() as db:assert db.execute('select overlap_plan from qq_daily_entries where id=?',(row['id'],)).fetchone()[0]=='{}'
        with patch.object(b.expressions,'decide',model(anchor)):
            fresh=await m.overlap.prepare_daily('20000',row['id'],p['preview_version'])
        assert '纸船' in fresh['body'] and not m.overlap._budget_owners
    asyncio.run(run())


def test_total_review_wait_budget_releases_slot_without_a_partial_plan(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id']);original_timeout=asyncio.timeout
    async def blocked(*args,**kwargs):await asyncio.sleep(10)
    with patch.object(b.expressions,'decide',blocked),patch('saya_app.services.qq_memory_overlap.asyncio.timeout',lambda _:original_timeout(0)):
        with pytest.raises(ValueError,match='review_time_budget'):
            asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert not m.overlap._budget_owners
    with b.store.connect() as db:assert db.execute('select overlap_plan from qq_daily_entries where id=?',(row['id'],)).fetchone()[0]=='{}'


def saved(overlap):
    lab,m,daily,anchor=overlap;b,k,s,now=lab;now[0]+=60
    p=s.preview('20000',daily['id']);s.decide('20000',daily['id'],p['preview_version'],'approve')
    state=m.library.status('20000')
    with patch.object(b.expressions,'decide',responder('complementary')):state=asyncio.run(m.library.scan('20000',state['snapshot'],0))
    review=m.library.preview('20000',state['entries'][0]['id']);assert review['overlap_available']
    target=next(r for r in k.memories if r['source']=='qq_daily')
    return review,target


def test_pending_original_intact_until_owner_approves_residual(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id']);original=p['body']
    with patch.object(b.expressions,'decide',model(anchor)):view=asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert view['previous_body']==original and view['body']=='这是虚构材料，今天把纸船放进木盒。'
    assert view['overlap_anchors'][0]['body']==anchor['content'] and len(k.memories)==1
    with b.store.connect() as db:
        raw=dict(db.execute('select * from qq_daily_entries where id=?',(row['id'],)).fetchone())
    assert raw['body']==original and original not in raw['overlap_plan'] and anchor['content'] not in raw['overlap_plan']
    with pytest.raises(ValueError):s.decide('20000',row['id'],view['preview_version'],'approve')
    now[0]+=60;view=s.preview('20000',row['id']);result=s.decide('20000',row['id'],view['preview_version'],'approve')
    assert result['state']=='saved' and len(k.memories)==2
    daily=next(r for r in k.memories if r['source']=='qq_daily')
    assert daily['content']=='这是虚构材料，今天把纸船放进木盒。' and daily['origin']['verified_subject']=='20000'
    assert k._find_memory(anchor['id'])['content']==anchor['content']
    assert s.decide('20000',row['id'],view['preview_version'],'approve')['state']=='saved' and len(k.memories)==2


def test_saved_original_id_revision_only_after_full_native_confirmation(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;review,target=saved(overlap);before=deepcopy(target);a=deepcopy(k._find_memory(anchor['id']))
    with patch.object(b.expressions,'decide',model(anchor)):p=asyncio.run(m.overlap.prepare_saved('20000',review['id'],review['preview_version']))
    assert p['status']=='pending' and p['view']['old']==before['content']
    assert p['view']['new']=='这是虚构材料，今天把纸船放进木盒。'
    from saya_qq_action_panel import action_text
    assert a['content'] in action_text(p) and before['content'] in action_text(p)
    assert k._find_memory(target['id'])['content']==before['content']
    result=m.actions.decide('20000',p['id'],p['preview_version'],'confirm');assert result['status']=='applied'
    current=k._find_memory(target['id']);assert current['content']==p['view']['new'] and len(k.memories)==2
    for field in ['id','date','source','origin','qq_daily_entry']:assert current[field]==before[field]
    assert k.memory_allowed_for_use(current) and k.memory_allowed_for_use(k._find_memory(anchor['id']))
    assert m.actions.decide('20000',p['id'],p['preview_version'],'confirm')['status']=='applied'
    terminal=m.actions.preview('20000',p['id']);assert 'overlap_anchors' not in terminal['view']
    assert before['content'] not in json.dumps(terminal,ensure_ascii=False)


def test_cross_day_stable_fact_is_reviewable_but_never_whole_record_duplicate(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab
    anchor=k._find_memory(anchor['id']);anchor['date']='2026-11-13';anchor['qq_daily_entry']['day']='2026-11-13'
    review,target=saved(overlap)
    assert review['guard']=='different_daily_period' and review['overlap_available']
    with patch.object(b.expressions,'decide',model(anchor)):p=asyncio.run(m.overlap.prepare_saved('20000',review['id'],review['preview_version']))
    assert '今天把纸船放进木盒' in p['view']['new']
    with pytest.raises(ValueError):m.library.prepare('20000',review['id'],review['preview_version'],'first')


@pytest.mark.parametrize('mutate',[
    lambda v:v['matches'][0].update(unit=88),
    lambda v:v['matches'][0].update(important_quote='编造的引用'),
    lambda v:v['matches'][0].update(anchor='foreign'),
    lambda v:v['matches'][0]['checks'].update(same_period=False),
    lambda v:v.update(new_body='偷偷改写其他内容'),
    lambda v:v['matches'].append(v['matches'][0]),
    lambda v:v.update(matches=[]),
])
def test_bad_plan_never_changes_pending_original_or_saved_memories(overlap,mutate):
    lab,m,row,anchor=overlap;b,k,s,now=lab;before=deepcopy(k.memories);p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',model(anchor,mutate)),pytest.raises(ValueError):
        asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert s.preview('20000',row['id'])['body']==row['body'] and k.memories==before


@pytest.mark.parametrize('kind',['pending','saved'])
def test_anchor_change_invalidates_full_consent_and_pending_raw_draft_remains(overlap,kind):
    lab,m,row,anchor=overlap;b,k,s,now=lab
    if kind=='saved':review,target=saved(overlap)
    else:review=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',model(anchor)):
        p=asyncio.run(m.overlap.prepare_saved('20000',review['id'],review['preview_version'])) if kind=='saved' else asyncio.run(m.overlap.prepare_daily('20000',review['id'],review['preview_version']))
    k.update_memory(anchor['id'],{'content':'这是虚构材料，星湾档案现在改用白色标签。'})
    if kind=='saved':
        with pytest.raises(ValueError):m.actions.decide('20000',p['id'],p['preview_version'],'confirm')
        assert '青色' in k._find_memory(target['id'])['content']
    else:
        now[0]+=60;current=s.preview('20000',row['id']);assert current['overlap_stale'] and current['body']==row['body']
        with pytest.raises(ValueError):s.decide('20000',row['id'],current['preview_version'],'approve')


def test_draft_change_during_model_wait_cannot_remove_new_facts(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id'])
    def change(_):accept(lab,'这是虚构材料，木盒又加了一艘红纸船。',10,body='这是虚构材料，星湾档案长期使用青色标签；木盒新增红纸船。')
    with patch.object(b.expressions,'decide',model(anchor,change)),pytest.raises(ValueError,match='daily_changed'):
        asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert '红纸船' in s.preview('20000',row['id'])['body']


def test_one_unit_containing_extra_fact_cannot_be_partially_rewritten(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab
    body='这是虚构材料，星湾档案长期使用青色标签，而且今天把纸船放进木盒。'
    daily=m.overlap.draft_record(dict(row)|{'body':body,'bot':b.settings.bot_id})
    # Removing this whole compound unit would leave no substantive residual.
    with patch.object(b.expressions,'decide',model(anchor)),pytest.raises(ValueError,match='no_residual'):
        asyncio.run(m.overlap.propose('20000',daily,[anchor]))
    assert residual('这是虚构材料，甲句内容；乙句内容。',[[r['start'],r['end']] for r in units('这是虚构材料，甲句内容；乙句内容。')[:1]])=='这是虚构材料，乙句内容。'


def test_pending_plan_restarts_expires_and_never_changes_three_day_policy(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',model(anchor)):asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    b.daily_memory=s=QQDailyMemory(b,k,clock=lambda:now[0]);now[0]+=60
    assert s.preview('20000',row['id'])['overlap_anchors']
    now[0]+=3*86400;p=s.preview('20000',row['id']);assert p['state']=='expired' and not p['body'] and not p['sources']
    with b.store.connect() as db:assert db.execute('select overlap_plan from qq_daily_entries where id=?',(row['id'],)).fetchone()[0]=='{}'
    assert len(k.memories)==1


def test_partial_event_coverage_does_not_erase_other_daily_facts(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab
    source=s.preview('20000',row['id'])['sources'][0]['event']
    important=next(r for r in accept(lab,'这是虚构材料，星湾档案的青色标签是长期规则。',8,level='important',covered=[source]) if r['kind']=='important' and r['state']=='pending')
    now[0]+=60;p=s.preview('20000',row['id']);assert important['id'] in p['waiting_on']
    p=s.preview('20000',important['id']);s.decide('20000',important['id'],p['preview_version'],'approve')
    p=s.preview('20000',row['id']);assert p['body']==row['body'] and p['sources'] and not p['waiting_on']
    assert '纸船' in p['body']


@pytest.mark.parametrize('change',['person','bot','project','unapproved','fiction','locked','stopped'])
def test_anchor_identity_and_authority_do_not_follow_similarity(overlap,change):
    lab,m,row,anchor=overlap;b,k,s,now=lab;anchor=k._find_memory(anchor['id'])
    if change=='person':anchor['origin']['verified_subject']='30000'
    elif change=='bot':anchor['origin']['bot_id']='10001'
    elif change=='project':anchor['project_id']='different'
    elif change=='unapproved':anchor['user_confirmed']=False
    elif change=='fiction':anchor['content']='星湾档案长期使用青色标签。'
    elif change=='locked':anchor['is_locked']=True
    else:k.memory_use.set_block(anchor['id'],True)
    p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide') as provider,pytest.raises(ValueError):
        asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    provider.assert_not_called();assert len(k.memories)==1


def test_plan_binds_entire_original_not_only_unit_number(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',model(anchor)):p=asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    with b.store.connect() as db:db.execute('update qq_daily_entries set body=? where id=?',('这是虚构材料，不同的独立事实；今天把纸船放进木盒。',row['id']))
    p=s.preview('20000',row['id']);assert p['overlap_stale'] and '不同的独立事实' in p['body']


def test_cancelled_saved_trim_never_modifies_original(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;review,target=saved(overlap);before=deepcopy(k.memories)
    with patch.object(b.expressions,'decide',model(anchor)):p=asyncio.run(m.overlap.prepare_saved('20000',review['id'],review['preview_version']))
    assert m.actions.decide('20000',p['id'],p['preview_version'],'cancel')['status']=='cancelled' and k.memories==before


def test_daily_cancel_restores_original_and_replay_cannot_erase_new_plan(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab;p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',model(anchor)):p=asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    old_version=p['preview_version'];restored=m.overlap.cancel_daily('20000',p['id'],old_version)
    assert restored['body']==row['body'] and 'previous_body' not in restored and not restored.get('overlap_anchors')
    assert m.overlap.cancel_daily('20000',p['id'],old_version)['body']==row['body']
    with patch.object(b.expressions,'decide',model(anchor)):new=asyncio.run(m.overlap.prepare_daily('20000',restored['id'],restored['preview_version']))
    with pytest.raises(ValueError):m.overlap.cancel_daily('20000',new['id'],old_version)
    assert s.preview('20000',new['id'])['overlap_anchors']


def test_unknown_important_result_blocks_daily_double_save(overlap):
    lab,m,row,anchor=overlap;b,k,s,now=lab
    source=s.preview('20000',row['id'])['sources'][0]['event']
    important=next(r for r in accept(lab,'这是虚构材料，星湾档案的青色标签是长期规则。',8,level='important',covered=[source]) if r['kind']=='important' and r['state']=='pending')
    with b.store.connect() as db:db.execute("update qq_daily_entries set state='unknown',sources='[]',result=? where id=?",(json.dumps({'source_events':[source]}),important['id']))
    now[0]+=60;p=s.preview('20000',row['id']);assert important['id'] in p['waiting_on']
    with pytest.raises(ValueError):s.decide('20000',row['id'],p['preview_version'],'approve')


def test_overlap_routes_auth_origin_schema_and_rollback(overlap,tmp_path):
    lab,m,row,anchor=overlap;b,k,s,now=lab;b.task=SimpleNamespace(done=lambda:False)
    b.settings=b.settings.model_copy(update={'admin_token':'fixture-admin'});app=FastAPI();app.include_router(routes.router)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1',headers={'Authorization':'Bearer fixture-admin'}) as c:
            p=s.preview('20000',row['id']);values={'owner_id':'20000','id':p['id'],'preview_version':p['preview_version']};path='/api/qq/memory-console/daily-overlap'
            assert (await c.post(path,json=values)).status_code==403
            assert (await c.post(path,json=values|{'subject':'30000'},headers={'Origin':'http://127.0.0.1'})).status_code==422
            assert (await c.post(path,json=values|{'owner_id':'30000'},headers={'Origin':'http://127.0.0.1'})).status_code==403
            response=await c.post(path,json=values,headers={'Origin':'http://127.0.0.1'})
            assert response.status_code==200,response.text
            assert response.headers['cache-control']=='no-store' and response.json()['previous_body']==row['body']
    with patch.object(routes,'qq_bridge',b),patch.object(b.expressions,'decide',model(anchor)):asyncio.run(run())
    target=tmp_path/'rollback/knowledge_base/qq/bridge.sqlite3';target.parent.mkdir(parents=True)
    with b.store.connect() as src,sqlite3.connect(target) as dst:src.backup(dst)
    assert 'qq_memory_overlap_v1' in memory_rollback_requirements(target.parent.parent.parent)
