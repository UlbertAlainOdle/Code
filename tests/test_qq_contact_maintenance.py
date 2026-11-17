"""Contacts propose only their own old daily revisions; owner alone approves."""
import asyncio,json
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import pytest

from test_qq_daily_memory_lookup import lab
from test_qq_daily_memory import lab as base_lab
from test_qq_daily_memory import msg,accept
from saya_app.services.memory_versions import record_version
from saya_app.services.qq_memory_access import MemoryAccessDenied


def saved_contact(lab,user=30000):
    b,k,s,now=lab
    row=accept(lab,'这是虚构材料，星浦资料柜使用青色标签。',user=user,level='important')[0]
    assert row['kind']=='daily';now[0]+=60
    p=s.preview('20000',row['id']);s.decide('20000',row['id'],p['preview_version'],'approve')
    return next(r for r in k.memories if (r.get('origin') or {}).get('verified_subject')==str(user))


def test_large_contact_catalogue_uses_one_current_policy_snapshot(lab):
    b,k,s,now=lab;first=saved_contact(lab)
    for n in range(100):k.memories.append(deepcopy(first)|{'id':'contact-copy-'+str(n)})
    k.memory_use.set_block('contact-copy-99',True)
    message=msg(b,now,'继续我本人的资料柜安排。',20,30000)
    with patch.object(k.memory_use,'read_state',wraps=k.memory_use.read_state) as read:catalog=s._catalog(message)
    assert read.call_count==1 and len(catalog)==100 and all(r['maintainable'] for r in catalog)
    assert 'contact-copy-99' not in {r['id'] for r in catalog}
    k.memory_use.set_block('contact-copy-98',True)
    assert 'contact-copy-98' not in {r['id'] for r in s._catalog(message)}


def test_contact_two_continuous_revisions_preserve_person_namespace_and_creation(lab):
    b,k,s,now=lab;original=deepcopy(saved_contact(lab));identifier=original['id'];receipts=[]
    for mid,color in [(10,'白'),(11,'绿')]:
        text='这是虚构材料，星浦资料柜现在改用'+color+'色标签。'
        async def decide(prompt,**kw):
            data=json.loads(prompt.split('DATA:\n',1)[1]);assert data['speaker_is_owner'] is False
            assert data['memories'][0]['maintainable'] is True and data['memories'][0]['id']==identifier
            return {'value':'important','memory_id':identifier,'pending_id':None,'content':text}
        with patch.object(b.expressions,'decide',decide):asyncio.run(s.observe(msg(b,now,text,mid,30000),'session_qq_contact.jsonl'))
        row=next(r for r in s.listing('20000')['entries'] if r['kind']=='revision' and r['state']=='pending')
        p=s.preview('20000',row['id']);assert p['contact_maintenance'] and p['subject']=='30000'
        assert k._find_memory(identifier)['content']!=text
        with pytest.raises(MemoryAccessDenied):s.decide('30000',row['id'],p['preview_version'],'approve')
        result=s.decide('20000',row['id'],p['preview_version'],'approve');assert result['state']=='saved'
        receipts.append((row,p));now[0]+=3600
        current=k._find_memory(identifier);assert current['content']==text and not current['is_important']
        for field in ['id','date','source','origin','project_id','qq_daily_entry']:assert current[field]==original[field]
    assert len(k.memories)==1
    for row,p in receipts:assert s.decide('20000',row['id'],p['preview_version'],'approve')['state']=='saved'
    current=k._find_memory(identifier);assert '绿色' in current['content']
    assert current['source_detail']['qq_confirmed_revision']['contact_maintenance'] is True
    own=s.personal_context(msg(b,now,'你记得我的星浦资料柜吗？',20,30000));assert own and '绿色' in json.dumps(own,ensure_ascii=False)
    other=deepcopy(msg(b,now,'你记得我的星浦资料柜吗？',21,30000));other.user_id='40000'
    assert not s.personal_context(other)
    assert identifier not in {r['id'] for r in k.query_memory_v4('星浦资料柜',bypass_need_planner=True)['items']}


@pytest.mark.parametrize('change',['other_subject','other_bot','unverified','not_approved','project','important','blocked','locked'])
def test_contact_target_requires_original_identity_approval_and_protection(lab,change):
    b,k,s,now=lab;record=saved_contact(lab)
    if change=='other_subject':record['origin']['verified_subject']='40000'
    elif change=='other_bot':record['origin']['bot_id']='10001'
    elif change=='unverified':record['origin']['verification']='unknown'
    elif change=='not_approved':record['user_confirmed']=False
    elif change=='project':record['project_id']='different'
    elif change=='important':record['is_important']=True
    elif change=='blocked':k.memory_use.set_block(record['id'],True)
    else:record['is_locked']=True
    m=msg(b,now,'这是虚构材料，星浦资料柜现在改用白色标签。',10,30000)
    # Even an injected plan/catalog cannot bypass the host's contact boundary.
    with pytest.raises(ValueError):s.accept(m,'contact-topic',{'value':'daily','pending_id':None,'memory_id':record['id'],'content':m.text},
        s._rows('30000'),[{'id':record['id'],'version':record_version(record)}],s.policy()['revision'],k.memory_use.history_floor())
    assert not any(r['kind']=='revision' for r in s.listing('20000')['entries'])


def test_contact_question_cannot_rewrite_and_changed_target_invalidates_approval(lab):
    b,k,s,now=lab;record=saved_contact(lab)
    with pytest.raises(ValueError,match='direct_evidence'):
        accept(lab,'星浦资料柜也许改用白色标签？',10,30000,target=record['id'])
    row=next(r for r in accept(lab,'这是虚构材料，星浦资料柜现在改用白色标签。',11,30000,target=record['id']) if r['kind']=='revision')
    p=s.preview('20000',row['id']);k.update_memory(record['id'],{'content':'这是虚构材料，星浦资料柜现在改用绿色标签。'})
    with pytest.raises(ValueError):s.decide('20000',row['id'],p['preview_version'],'approve')
    assert '绿色' in k._find_memory(record['id'])['content'] and len(k.memories)==1


def test_concurrent_contact_approval_is_one_original_revision(lab):
    b,k,s,now=lab;record=saved_contact(lab)
    row=next(r for r in accept(lab,'这是虚构材料，星浦资料柜现在改用白色标签。',10,30000,target=record['id']) if r['kind']=='revision')
    p=s.preview('20000',row['id']);before=len(k.memory_v4_store.list_versions(record['id']))
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(lambda _:s.decide('20000',row['id'],p['preview_version'],'approve'),range(2)))
    assert all(r['state']=='saved' for r in results) and len(k.memories)==1
    assert len(k.memory_v4_store.list_versions(record['id']))==before+1


def test_contact_capability_survives_daily_queue_cleanup(lab,tmp_path):
    from pathlib import Path
    from saya_app.services.qq_rollback import memory_rollback_requirements
    b,k,s,now=lab;record=saved_contact(lab)
    row=next(r for r in accept(lab,'这是虚构材料，星浦资料柜现在改用白色标签。',10,30000,target=record['id']) if r['kind']=='revision')
    p=s.preview('20000',row['id']);s.decide('20000',row['id'],p['preview_version'],'approve')
    target=tmp_path/'rollback/knowledge_base';target.mkdir(parents=True)
    (target/'knowledge.json').write_text(json.dumps(k.memories,ensure_ascii=False),encoding='utf-8')
    assert 'qq_contact_maintenance_v1' in memory_rollback_requirements(target.parent)


def test_contact_revocation_fences_proposal_approval_and_cached_recall(lab):
    b,k,s,now=lab;record=saved_contact(lab);query=msg(b,now,'你记得我的星浦资料柜吗？',9,30000)
    snapshot=s.personal_context(query);assert snapshot
    row=next(r for r in accept(lab,'这是虚构材料，星浦资料柜现在改用白色标签。',10,30000,target=record['id']) if r['kind']=='revision')
    p=s.preview('20000',row['id']);b.settings=b.settings.model_copy(update={'allowed_users':frozenset()})
    assert not s.managed(query) and not s.personal_context(query) and not s.valid_personal_context(query,snapshot)
    with pytest.raises(ValueError):s.decide('20000',row['id'],p['preview_version'],'approve')
    assert k._find_memory(record['id'])['content']==record['content']
