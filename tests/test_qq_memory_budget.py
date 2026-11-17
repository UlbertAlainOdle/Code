"""Bounded complete locator coverage; full originals remain the only evidence."""
import asyncio,json
from copy import deepcopy
from unittest.mock import patch
import pytest
from saya_app.services.qq_memory_budget import select_catalog,MAX_INPUT,MAX_CALLS,PAGE_INPUT
from test_qq_memory_overlap import overlap,model
from test_qq_daily_memory_lookup import lab
from test_qq_daily_memory import lab as base_lab


def rows(count):return [{'id':'original-'+str(i),'content':'完整原始事实'+str(i)+'文'*1500,'recorded_day':'2026-11-15','kind':'daily'} for i in range(count)]


def test_large_catalogue_visits_every_entry_and_retains_full_last_original():
    original=rows(1200);visited=[];calls=[]
    async def decide(request,**kw):
        calls.append(request);data=json.loads(request.split('DATA:\n',1)[1]);slots=[r[0] for r in data['index']];visited.extend(slots)
        return {'slots':[1199] if 1199 in slots else []}
    result=asyncio.run(select_catalog(decide,'同义改述定位',original))
    assert result==[original[-1]] and len(result[0]['content'])>1500
    assert set(visited)==set(range(1200)) and len(calls)<=MAX_CALLS
    assert sum(map(len,calls))<=MAX_INPUT and all(len(c)<=PAGE_INPUT for c in calls)


def test_global_reduction_rechecks_only_leaf_candidates():
    original=rows(700);calls=[]
    async def decide(request,**kw):
        index=json.loads(request.split('DATA:\n',1)[1])['index'];calls.append(index)
        return {'slots':[r[0] for r in index[-5:]]}
    result=asyncio.run(select_catalog(decide,'定位',original))
    assert len(result)==5 and len(calls)>1 and len(calls[-1])<=5*(len(calls)-1)
    assert {r['id'] for r in result}<={original[i]['id'] for i in [r[0] for r in calls[-1]]}


@pytest.mark.parametrize('reply',[{'slots':[True]},{'slots':[-1]},{'slots':[0,0]},{'slots':['0']},{'ids':[0]}])
def test_bad_locator_slots_never_return_a_partial_catalogue(reply):
    async def decide(*a,**kw):return reply
    with pytest.raises(ValueError):asyncio.run(select_catalog(decide,'x',rows(10)))


def test_preflight_and_permission_change_bound_cost_without_fallback():
    calls=[];alive=[True]
    async def decide(*a,**kw):calls.append(1);alive[0]=False;return {'slots':[]}
    with pytest.raises(ValueError):asyncio.run(select_catalog(decide,'x',rows(6000)))
    assert not calls
    with pytest.raises(ValueError):asyncio.run(select_catalog(decide,'x',rows(800),current=lambda:alive[0]))
    assert calls==[1]


def test_many_important_anchors_are_located_then_read_in_full(overlap):
    fixture,m,row,anchor=overlap;b,k,s,now=fixture
    for n in range(30):k.memories.append(deepcopy(anchor)|{'id':'anchor-'+str(n)})
    seen=[]
    async def decide(request,**kw):
        seen.append(kw['purpose'])
        if kw['purpose']=='qq_memory_lookup':
            index=json.loads(request.split('DATA:\n',1)[1])['index'];return {'slots':[index[-1][0]]}
        return await model(k._find_memory('anchor-29'))(request,**kw)
    p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',decide):p=asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert seen==['qq_memory_lookup','qq_memory_overlap'] and '纸船' in p['body']
    assert p['overlap_anchors'][0]['id']=='anchor-29' and len(k.memories)==31


def test_long_full_anchors_use_bounded_comparison_batches(overlap):
    fixture,m,row,anchor=overlap;b,k,s,now=fixture
    for n in range(8):
        text=(anchor['content']+'独立附注'*4000)[:12000]
        k.memories.append(deepcopy(anchor)|{'id':'long-'+str(n),'content':text})
    counts=[]
    async def decide(request,**kw):
        data=json.loads(request.split('DATA:\n',1)[1])
        if kw['purpose']=='qq_memory_lookup':return {'slots':[r[0] for r in data['index'] if r[3].startswith('这是')][-8:]}
        counts.append(len(data['important']));record=data['important'][0]
        assert all(len(r['body'])==12000 for r in data['important'])
        return {'matches':[{'unit':0,'anchor':record['id'],'important_quote':'星湾档案长期使用青色标签',
            'checks':{k:True for k in ['same_subject','same_period','same_conditions','same_modality','same_fiction','both_complete']}}]}
    p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',decide):p=asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    assert counts==[2,2,2,2] and '纸船' in p['body']


def test_large_overlap_source_change_during_lookup_does_not_persist_plan(overlap):
    fixture,m,row,anchor=overlap;b,k,s,now=fixture
    for n in range(20):k.memories.append(deepcopy(anchor)|{'id':'anchor-'+str(n)})
    calls=[]
    async def decide(request,**kw):
        calls.append(kw['purpose']);k.memory_use.set_block(anchor['id'],True)
        return {'slots':[0]}
    p=s.preview('20000',row['id'])
    with patch.object(b.expressions,'decide',decide),pytest.raises(ValueError):
        asyncio.run(m.overlap.prepare_daily('20000',row['id'],p['preview_version']))
    with b.store.connect() as db:
        stored=dict(db.execute('select * from qq_daily_entries where id=?',(row['id'],)).fetchone())
    assert stored['overlap_plan']=='{}' and stored['body']==row['body'] and calls==['qq_memory_lookup']
