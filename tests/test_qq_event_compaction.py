import asyncio
import json
from copy import deepcopy
import pytest
from test_qq_topic_routing import topics,item,create


def test_expired_group_sources_do_not_block_new_valid_progress(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            m=msg('星桥读书会：我负责整理书单。',group=90000)
            choice[0]={'items':[item('我负责整理书单。',group=create(),mode='commitment')]};await s.observe(m,[])
            card=s.unified.cards(m)[0];base=card['contributions'][0]
            with s.store.connect() as db:
                body=json.loads(db.execute('select data from qq_shared_events where id=?',(card['id'],)).fetchone()[0])
                body['contributions']=[{**deepcopy(base),'id':f'{index:016x}','text':f'旧进度{index}','expires':now[0]-1} for index in range(31)]+[{**base,'expires':now[0]+3600}]
                db.execute('update qq_shared_events set data=? where id=?',(json.dumps(body),card['id']))
            fresh=msg('星桥读书会：我已经整理好第一份书单。',group=90000)
            s.unified.commit(fresh,'90000',{'event_id':card['id'],'kind':'progress'},card['revision'])
            after=s.unified.cards(fresh)[0]
            assert after['id']==card['id'] and len(after['contributions'])==2
            assert any(c['text']==fresh.text for c in after['contributions'])
    asyncio.run(run())


def test_full_valid_window_accepts_unchanged_but_rejects_unresolved_new_source(tmp_path):
    async def run():
        async with topics(tmp_path) as (b,s,now,msg,choice,prompts):
            first=msg('星桥读书会：我负责整理书单。',group=90000)
            mid=s.unified.commit(first,'90000',{'title':'星桥读书会'})
            with s.store.connect() as db:
                body=json.loads(db.execute('select data from qq_shared_events where id=?',(mid,)).fetchone()[0]);base=body['contributions'][0]
                body['contributions']=[{**deepcopy(base),'id':f'{index:016x}','text':f'未决条件{index}','expires':now[0]+3600} for index in range(31)]+[base]
                db.execute('update qq_shared_events set data=? where id=?',(json.dumps(body),mid))
            repeated=msg(first.text,group=90000)
            assert s.unified.commit(repeated,'90000',{'event_id':mid},1)==mid
            assert s.unified.cards(repeated)[0]['revision']==1
            with pytest.raises(ValueError):s.unified.commit(msg('星桥读书会：另一条未解决的条件。',group=90000),'90000',{'event_id':mid},1)
            assert len(s.unified.cards(repeated)[0]['contributions'])==32
    asyncio.run(run())
