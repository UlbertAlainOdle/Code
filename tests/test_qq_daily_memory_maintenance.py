"""Approved daily and important entries remain maintainable original records.

These test the host contract with explicit synthetic plans, not model quality.
"""
from copy import deepcopy
import pytest

from test_qq_daily_memory import lab,accept,boundary


@pytest.mark.parametrize('kind',['daily','important'])
def test_approved_entry_accepts_later_updates_without_changing_id_date_or_creation_source(lab,kind):
    b,k,s,now=lab
    initial='这是虚构材料，槭湾目录计划用蓝色标签；是否换盒还没有决定。'
    row=accept(lab,initial,level=kind)[0]
    if kind=='daily':now[0]+=60
    p=s.preview('20000',row['id']);assert s.decide('20000',row['id'],p['preview_version'],'approve')['state']=='saved'
    original=deepcopy(k.memories[0]);identifier=original['id']
    now[0]+=3600
    assert boundary(now[0],'Asia/Shanghai')[0]=='2026-11-15'
    next_text='这是虚构材料，槭湾目录计划现在改用白色标签，因为蓝色标签用完了；是否换盒还没有决定。'
    body=('这是虚构材料，2026-11-14槭湾目录计划用蓝色标签；'
          '2026-11-15现在改用白色标签，因为蓝色标签用完了；是否换盒还没有决定。')
    pending=next(r for r in accept(lab,next_text,2,level='important',target=identifier,body=body) if r['state']=='pending')
    assert pending['kind']=='revision' and pending['target']==identifier and len(k.memories)==1
    assert k.memories[0]['content']==initial
    second=s.preview('20000',pending['id'])
    assert second['previous_body']==initial and second['body']==body
    assert s.decide('20000',pending['id'],second['preview_version'],'approve')['state']=='saved'
    assert k.memories[0]['content']==body
    now[0]+=3600
    third_text='这是虚构材料，槭湾目录计划现在改用绿色标签；是否换盒仍然没有决定。'
    third=next(r for r in accept(lab,third_text,3,level='important',target=identifier) if r['state']=='pending')
    assert third['id']!=pending['id']
    preview=s.preview('20000',third['id'])
    assert preview['previous_body']==body
    assert s.decide('20000',third['id'],preview['preview_version'],'approve')['state']=='saved'
    # Replaying any earlier approval is a receipt lookup, never an overwrite.
    for old,old_preview in [(row,p),(pending,second),(third,preview)]:
        assert s.decide('20000',old['id'],old_preview['preview_version'],'approve')['state']=='saved'
    assert len(k.memories)==1
    current=k.memories[0]
    assert current['id']==identifier and current['content']==third_text
    for field in ['date','source','origin','qq_daily_entry']:
        assert current[field]==original[field]
    assert current['source']=='qq_'+kind
    assert len(k.memory_v4_store.list_versions(identifier))>=3
