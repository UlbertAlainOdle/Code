"""Lookup only locates full originals; original approval/CAS still owns writes."""
import asyncio,json
from copy import deepcopy
from unittest.mock import patch
import pytest

from test_qq_daily_memory import lab as base_lab,msg,accept
from saya_app.services.qq_expressions import QQExpressions


@pytest.fixture
def lab(base_lab):
    b=base_lab[0];b.expressions=QQExpressions(b,b.store.path.parent)
    return base_lab


def seed(lab,kind='important'):
    b,k,s,now=lab
    row=accept(lab,'这是虚构材料，槭湾目录计划用蓝色标签。',level=kind)[0]
    if kind=='daily':now[0]+=60
    p=s.preview('20000',row['id']);s.decide('20000',row['id'],p['preview_version'],'approve')
    target=k.memories[0]['id']
    for n in range(6):k.add_memory('其他主题'+chr(0x4e00+n),source='manual')
    return target


@pytest.mark.parametrize('kind',['daily','important'])
def test_semantic_lookup_then_two_original_id_updates(lab,kind):
    b,k,s,now=lab;target=seed(lab,kind);original=deepcopy(k._find_memory(target));calls=[]
    async def decide(prompt,**kw):
        calls.append(kw['purpose']);data=json.loads(prompt.split('DATA:\n',1)[1])
        if kw['purpose']=='qq_memory_lookup':
            assert kw['output_tokens']==512 and len(data['index'])==7
            assert {'id','recorded_day','kind','locator_excerpt'}==set(data['index'][0])
            return {'ids':[target]}
        assert len(data['memories'])==1 and data['memories'][0]['content']==k._find_memory(target)['content']
        return {'value':'important','memory_id':target,'pending_id':None,'content':current[0]}
    for mid,body in [(10,'这是虚构材料，槭湾目录计划现在改用白色标签。'),(11,'这是虚构材料，槭湾目录计划现在改用绿色标签。')]:
        current=[body];m=msg(b,now,body,mid)
        with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(m,'session_qq_test.jsonl'))
        row=next(r for r in s.listing('20000')['entries'] if r['kind']=='revision' and r['state']=='pending')
        p=s.preview('20000',row['id']);assert p['previous_body']==k._find_memory(target)['content']
        assert s.decide('20000',row['id'],p['preview_version'],'approve')['state']=='saved'
        assert k._find_memory(target)['content']==body
        now[0]+=60
    assert calls==['qq_memory_lookup','qq_daily_memory']*2 and len(k.memories)==7
    for field in ['id','date','source','origin','qq_daily_entry']:assert k._find_memory(target)[field]==original[field]


@pytest.mark.parametrize('reply',[None,{'ids':['invented']},{'ids':['same','same']},{'ids':[],'extra':1},{'ids':[True]}])
def test_failed_lookup_never_falls_back_to_new_memory(lab,reply):
    b,k,s,now=lab;seed(lab);before=deepcopy(k.memories);calls=[]
    async def decide(prompt,**kw):calls.append(kw['purpose']);return reply
    with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(msg(b,now,'这是虚构材料，改成另一种包装。',5),'session_qq_test.jsonl'))
    assert calls==['qq_memory_lookup'] and k.memories==before
    assert not any(r['state'] in {'open','pending'} for r in s.listing('20000')['entries'])
    assert s.last_error=='existing_memory_lookup_unavailable'


def test_small_catalog_uses_full_bodies_without_extra_request(lab):
    b,k,s,now=lab;item=k.add_memory('这是一条用于避免重复的完整原文。',source='manual');calls=[]
    async def decide(prompt,**kw):
        calls.append(kw['purpose']);data=json.loads(prompt.split('DATA:\n',1)[1]);assert data['memories'][0]['content']==item['content']
        return {'value':'none','pending_id':None,'memory_id':None,'content':''}
    with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(msg(b,now,'再次提到已经保存过的同一件事。'),'session_qq_test.jsonl'))
    assert calls==['qq_daily_memory'] and len(k.memories)==1 and not s.listing('20000')['entries']


@pytest.mark.parametrize('phase',['lookup','classification'])
def test_catalog_change_during_model_await_prevents_stale_capture(lab,phase):
    b,k,s,now=lab;target=seed(lab)
    async def decide(prompt,**kw):
        if (phase=='lookup')==(kw['purpose']=='qq_memory_lookup'):k.update_memory(target,{'content':'刚被主人编辑的不同版本。'})
        if kw['purpose']=='qq_memory_lookup':return {'ids':[target]}
        return {'value':'important','memory_id':target,'pending_id':None,'content':'这是虚构材料，槭湾目录计划改为封口盒。'}
    with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(msg(b,now,'这是虚构材料，槭湾目录计划改为封口盒。',8),'session_qq_test.jsonl'))
    assert k._find_memory(target)['content']=='刚被主人编辑的不同版本。'
    assert not any(r['state'] in {'open','pending'} for r in s.listing('20000')['entries'])


def test_index_budget_fails_closed_and_never_sends_truncated_facts(lab):
    b,k,s,now=lab
    for n in range(6000):k.memories.append({'id':str(n),'content':'完整事实'*100,'source':'manual'})
    with patch.object(b.expressions,'decide') as model:
        asyncio.run(s.observe(msg(b,now,'这条新消息需要先定位已有条目。'),'session_qq_test.jsonl'))
    model.assert_not_called();assert s.last_error=='existing_memory_lookup_unavailable'


def test_locator_excludes_other_subject_project_protected_expired_and_long_records(lab):
    b,k,s,now=lab;m=msg(b,now,'这是用于定位的测试消息。')
    for n,fields in enumerate([
        {'origin':{'verified_subject':'30000','bot_id':b.settings.bot_id}},
        {'project_id':'foreign'},{'is_locked':True},{'is_archived':True},{'content':'文'*2001},
        {'expires_at':'2001-01-01T00:00:00+00:00'},
    ]):k.memories.append({'id':str(n),'content':'不可供此人修改的内容','source':'manual'}|fields)
    good=k.add_memory('可以定位的完整内容。',source='manual')
    assert [r['id'] for r in s._catalog(m)]==[good['id']]


def test_locator_can_select_zero_overlap_but_only_full_original_is_evidence(lab):
    from saya_app.services.memory_rules import fact_terms
    b,k,s,now=lab;target=seed(lab)
    text='资料编排已弃用天青贴纸，换成象牙色便签。'
    assert not fact_terms(text)&fact_terms(k._find_memory(target)['content'])
    async def decide(prompt,**kw):return {'ids':[target]}
    with patch.object(b.expressions,'decide',decide):selected,_=asyncio.run(s._select_relevant(msg(b,now,text,10)))
    assert selected[0]['content']==k._find_memory(target)['content'] and selected[0]['id']==target
    assert len(k.memories)==7 and not any(r['state']=='pending' for r in s.listing('20000')['entries'])


@pytest.mark.parametrize('kind',['daily','important'])
def test_newer_draft_during_await_cannot_be_overwritten(lab,kind):
    b,k,s,now=lab
    original=accept(lab,'这是虚构材料，今天整理蓝色纸船。',level=kind)[0]
    newer='这是虚构材料，今天整理蓝色纸船并装进白盒。'
    async def decide(prompt,**kw):
        accept(lab,newer,5,level=kind,pending=original['id'] if kind=='important' else None)
        return {'value':kind,'pending_id':original['id'] if kind=='important' else None,
                'memory_id':None,'content':'这是虚构材料，今天只整理蓝色纸船。'}
    with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(msg(b,now,'这是虚构材料，今天只整理蓝色纸船。',9),'session_qq_test.jsonl'))
    current=next(r for r in s.listing('20000')['entries'] if r['id']==original['id'])
    assert current['body']==newer and current['source_count']==2
