import asyncio,json
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import patch
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core_services import KnowledgeBase
from saya_app.services.qq_daily_memory import QQDailyMemory,boundary
from saya_app.services.qq_bridge import normalize_event
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_memory_access import MemoryAccessDenied
from saya_app.services.memory_versions import record_version
from saya_app.routers import qq as routes
from test_qq_task_scheduler import make
from test_qq_delivery import event
from test_qq_chat_runtime import server


@pytest.fixture
def lab(tmp_path):
    now=[datetime(2026,11,14,15,59,tzinfo=timezone.utc).timestamp()]
    b=make(tmp_path/'bridge',None,memory_management_enabled=True);b.store.initialize(7)
    kb=KnowledgeBase(str(tmp_path/'kb'));b.memory_management=QQMemoryManagement(b,kb)
    s=QQDailyMemory(b,kb,clock=lambda:now[0]);b.daily_memory=s;s.configure('20000',0,True)
    return b,kb,s,now


def msg(b,now,text,mid=1,user=20000):
    value=normalize_event(event(text,mid,user),b.settings)[0];value.sent_at=now[0]
    b.store.admit(value,b.settings)
    return value


def accept(lab,text,mid=1,user=20000,level='daily',pending=None,target=None,body=None,covered=None):
    b,k,s,now=lab;m=msg(b,now,text,mid,user);rows=s._rows(m.user_id);mem=s._relevant(m)
    plan={'value':level,'pending_id':pending,'memory_id':target,'content':body or text}
    if covered is not None:plan['covered_daily_events']=covered
    s.accept(m,'session_qq_test.jsonl',plan,rows,mem,s.policy()['revision'],k.memory_use.history_floor())
    return s.listing('20000')['entries']


def test_midnight_uses_saved_QQ_timezone_not_host_or_old_four_am_boundary():
    at=datetime(2026,11,14,15,59,tzinfo=timezone.utc).timestamp()
    day,end=boundary(at,'Asia/Shanghai')
    assert day=='2026-11-14' and end-at==60
    assert boundary(end,'Asia/Shanghai')[0]=='2026-11-15'
    # A short DST day is still closed at local midnight, not after 24 hours.
    at=datetime(2026,3,8,5,tzinfo=timezone.utc).timestamp()
    assert boundary(at,'America/New_York')[1]-at==23*3600


def test_daytime_revisions_one_draft_no_LT_write_and_day_end_one_approval(lab):
    b,k,s,now=lab
    row=accept(lab,'这是虚构材料，周末计划看展。')[0]
    accept(lab,'这是虚构材料，周末改为整理书，看展暂缓。',2,body='这是虚构材料，周末整理书；看展暂缓。')
    rows=s.listing('20000')['entries'];assert len(rows)==1
    assert rows[0]['body']=='这是虚构材料，周末整理书；看展暂缓。' and rows[0]['source_count']==2
    assert not k.memories and rows[0]['state']=='open'
    preview=s.preview('20000',row['id'])
    with pytest.raises(ValueError):s.decide('20000',row['id'],preview['preview_version'],'approve')
    now[0]+=60
    preview=s.preview('20000',row['id']);assert preview['state']=='pending'
    result=s.decide('20000',row['id'],preview['preview_version'],'approve')
    assert result['state']=='saved' and len(k.memories)==1
    memory=k.memories[0];assert memory['content']==rows[0]['body'] and memory['origin']['verified_subject']=='20000'
    assert memory['date']=='2026-11-14'
    native=k.memory_v4_store.get_memory(memory['id']);assert native.authority=='user_confirmed'
    assert len(native.metadata['evidence_refs'])==2
    assert s.decide('20000',row['id'],preview['preview_version'],'approve')['state']=='saved' and len(k.memories)==1
    assert s.preview('20000',row['id'])['sources']==[]


def test_offline_queue_survives_restart_and_expires_after_three_pending_days(lab):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，今日讨论紫色纸船。')[0]
    now[0]+=60;s=QQDailyMemory(b,k,clock=lambda:now[0])
    assert s.preview('20000',row['id'])['state']=='pending'
    now[0]+=3*86400-1;assert s.preview('20000',row['id'])['body']
    now[0]+=1;data=s.preview('20000',row['id'])
    assert data['state']=='expired' and not data['body'] and not data['sources'] and not k.memories


def test_owner_approves_other_person_only_daily_no_group_or_high_value_extension(lab):
    b,k,s,now=lab
    rows=accept(lab,'这是虚构材料，丙的整理箱用蓝色。',user=30000,level='important')
    assert rows[0]['kind']=='daily' and rows[0]['subject']=='30000'
    with pytest.raises(MemoryAccessDenied):s.listing('30000')
    group=normalize_event(event('这是群里的讨论',2,20000,90000),b.settings)[0]
    assert not s.managed(group)
    now[0]+=60;preview=s.preview('20000',rows[0]['id']);s.decide('20000',rows[0]['id'],preview['preview_version'],'approve')
    assert len(k.memories)==1 and k.memories[0]['origin']['verified_subject']=='30000'
    record=k.memories[0]
    assert record['project_scoped'] and not k._memory_allowed_for_project(record)
    assert k.memory_v4_store.get_memory(record['id']).scope_type=='project'
    assert record['id'] not in {x['id'] for x in k.query_memory_v4('整理箱蓝色',bypass_need_planner=True)['items']}


def test_approved_contact_context_is_only_the_same_person_and_revocation_is_fenced(lab):
    b,k,s,now=lab
    row=accept(lab,'我的虚构偏好是纸船代号CONTACT_CANARY。',user=30000)[0]
    now[0]+=60;preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    k.add_memory('主人的隐私偏好OWNER_PRIVATE_CANARY。',title='主人的记录')
    person=msg(b,now,'你记得我的虚构偏好是什么？',2,30000)
    snapshot=s.personal_context(person)
    assert snapshot and 'CONTACT_CANARY' in json.dumps(snapshot) and 'OWNER_PRIVATE_CANARY' not in json.dumps(snapshot)
    assert s.valid_personal_context(person,snapshot)
    owner=msg(b,now,'你记得我的偏好是什么？',3,20000)
    assert s.personal_context(owner) is None
    group=normalize_event(event('记得我的偏好吗？',4,30000,90000),b.settings)[0]
    assert s.personal_context(group) is None and not s.valid_personal_context(owner,snapshot)
    record=k._find_memory(snapshot['items'][0]['id']);k.set_memory_use_policy(record['id'],True,expected_version=record_version(record))
    assert not s.valid_personal_context(person,snapshot) and s.personal_context(person) is None


def test_disabling_workflow_fences_contact_history_and_cached_continuation(lab):
    from saya_app.services import qq_delivery
    from test_qq_delivery import Client
    b,k,s,now=lab;m=msg(b,now,'查询我的偏好。',2,30000);scope=m.scope+':isolated'
    epoch=k.memory_use.snapshot();b.memory_management.record_scope(scope,epoch)
    identifier=qq_delivery.create(b.store,m,scope,'CONTACT_PRIVATE_CANARY。',100,memory_epoch=epoch)
    s.configure('20000',1,False)
    assert s.fences_scope(m,scope) and not b.memory_management.valid_delivery(scope,identifier,k.memory_use.snapshot())
    history,_=b.memory_management.prepare_scope(scope,['Doctor: 我之前说过。','Saya: CONTACT_PRIVATE_CANARY。'])
    assert history==['Doctor: 我之前说过。']
    c=Client()
    asyncio.run(qq_delivery.send_batch(b.store,c,m,scope,identifier,3,False,
        memory_check=lambda:b.memory_management.valid_delivery(scope,identifier,k.memory_use.snapshot())))
    assert not c.sent


def test_pending_important_changes_in_place_and_old_preview_cannot_confirm(lab):
    b,k,s,now=lab
    row=accept(lab,'这是虚构偏好，我一直用蓝色标签。',level='important')[0]
    preview=s.preview('20000',row['id'])
    rows=accept(lab,'这是虚构偏好，现在改用白色标签。',2,level='important',pending=row['id'],body='这是虚构偏好，现在使用白色标签。')
    assert len(rows)==1 and rows[0]['id']==row['id'] and rows[0]['source_count']==2
    with pytest.raises(ValueError):s.decide('20000',row['id'],preview['preview_version'],'approve')
    preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    assert len(k.memories)==1 and '白色' in k.memories[0]['content']


def test_legacy_saved_memory_gets_CAS_proposal_not_duplicate_or_silent_overwrite(lab):
    b,k,s,now=lab;old=k.add_memory('虚构的槭湾目录一直用蓝色标签。',title='槭湾目录')
    version=record_version(old)
    row=accept(lab,'虚构的槭湾目录改用白色标签。',target=old['id'],body='虚构的槭湾目录现在用白色标签。')[0]
    assert row['kind']=='revision' and len(k.memories)==1 and record_version(k.memories[0])==version
    preview=s.preview('20000',row['id']);result=s.decide('20000',row['id'],preview['preview_version'],'approve')
    assert result['state']=='saved' and len(k.memories)==1 and k.memories[0]['id']==old['id']
    assert '白色' in k.memories[0]['content'] and record_version(k.memories[0])!=version
    row2=next(x for x in accept(lab,'虚构的槭湾目录现在改用绿色标签。',3,target=old['id']) if x['state']=='pending')
    assert row2['id']!=row['id']
    preview=s.preview('20000',row2['id']);s.decide('20000',row2['id'],preview['preview_version'],'approve')
    assert len(k.memories)==1 and '绿色' in k.memories[0]['content']


def test_changed_or_locked_saved_target_blocks_confirmation(lab):
    b,k,s,now=lab;old=k.add_memory('虚构的槭湾目录一直用蓝色标签。')
    row=accept(lab,'虚构的槭湾目录改用白色标签。',target=old['id'])[0]
    preview=s.preview('20000',row['id']);k.update_memory(old['id'],{'content':'虚构的槭湾目录改用黄色标签。'})
    with pytest.raises(ValueError):s.decide('20000',row['id'],preview['preview_version'],'approve')
    assert len(k.memories)==1 and '黄色' in k.memories[0]['content']


def test_reject_and_history_forget_clear_draft_body_and_evidence(lab):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，我喜欢薄荷气味。',level='important')[0]
    preview=s.preview('20000',row['id']);assert s.decide('20000',row['id'],preview['preview_version'],'reject')['state']=='rejected'
    assert not s.preview('20000',row['id'])['sources'] and not k.memories
    row=accept(lab,'这是虚构材料，我喜欢松木气味。',2)[0]
    with k._memory_write_lock:
        state=k.memory_use.read_state();state['history_floor']='a'*32;k.memory_use._save(state)
    data=s.preview('20000',row['id']);assert data['state']=='invalid' and not data['body'] and not data['sources']


def test_uncertain_save_is_not_retried_as_a_new_memory(lab,monkeypatch):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，我喜欢纸模型。',level='important')[0]
    preview=s.preview('20000',row['id']);original=k.add_memory;calls=[]
    def lost_reply(*a,**kw):calls.append(1);original(*a,**kw);raise OSError('after_commit')
    monkeypatch.setattr(k,'add_memory',lost_reply)
    assert s.decide('20000',row['id'],preview['preview_version'],'approve')['state']=='unknown'
    assert s.decide('20000',row['id'],preview['preview_version'],'approve')['state']=='unknown'
    assert len(k.memories)==len(calls)==1


def test_plan_cannot_invent_target_source_or_promote_across_person(lab):
    b,k,s,now=lab;m=msg(b,now,'这是虚构材料，我喜欢纸模型。')
    with pytest.raises(ValueError):s.accept(m,'s',{'value':'daily','pending_id':None,'memory_id':'fake','content':'fake'},[],[],1,k.memory_use.history_floor())
    assert not s.listing('20000')['entries'] and not k.memories


def test_observation_uses_only_admitted_user_text_and_opt_out_makes_no_model_call(lab):
    b,k,s,now=lab;calls=[]
    async def classify(prompt,**kw):
        calls.append((prompt,kw));return {'value':'daily','pending_id':None,'memory_id':None,'content':'这是虚构材料，我喜欢纸模型。'}
    b.expressions=SimpleNamespace(decide=classify)
    async def run():
        m=msg(b,now,'这是虚构材料，我喜欢纸模型。');await s.observe(m,'session_qq_t.jsonl');await s.observe(m,'session_qq_t.jsonl')
        m=msg(b,now,'不用记录到记忆，这只是虚构材料。',2);await s.observe(m,'session_qq_t.jsonl')
    asyncio.run(run());assert len(calls)==1 and calls[0][1]['purpose']=='qq_daily_memory'
    assert 'assistant' not in json.loads(calls[0][0].split('DATA:\n')[1])
    assert len(s.listing('20000')['entries'])==1 and not k.memories


def test_unsupported_quote_or_attachment_does_not_reenable_old_automatic_candidates(lab):
    b,k,s,now=lab;m=msg(b,now,'这张图里的话是什么意思？')
    m.reply_to='123';assert s.managed(m)
    async def forbidden(*a,**kw):raise AssertionError('must_not_classify_external_quote')
    b.expressions=SimpleNamespace(decide=forbidden)
    asyncio.run(s.observe(m,'s'))
    assert not s.listing('20000')['entries'] and not k.memories


@pytest.mark.parametrize('text',['这件事别记，我今天只是在聊聊。','不要记住这个，我今天吃了面条。',
    "Don't store this, I had noodles today.",'Do not remember this: I like paper boats.'])
def test_explicit_opt_out_never_reaches_new_retention_classifier(lab,text):
    b,k,s,now=lab;m=msg(b,now,text)
    async def forbidden(*a,**kw):raise AssertionError('opt_out_must_not_be_classified')
    b.expressions=SimpleNamespace(decide=forbidden)
    assert s.managed(m)
    asyncio.run(s.observe(m,'s'))
    assert not s.listing('20000')['entries'] and not k.memories
    assert not s.managed(msg(b,now,'忘掉那条旧记忆。',2))


def test_arrangement_nonuse_is_not_mistaken_for_forgetting_a_memory(lab):
    b,k,s,now=lab;calls=[]
    text='虚构的槐汀晚餐计划不再使用蒸锅，改用烤箱。'
    async def classify(*a,**kw):calls.append(1);return {'value':'daily','pending_id':None,'memory_id':None,'content':text}
    b.expressions=SimpleNamespace(decide=classify)
    asyncio.run(s.observe(msg(b,now,text),'session_qq_test.jsonl'))
    assert len(calls)==1 and len(s.listing('20000')['entries'])==1
    assert not s.managed(msg(b,now,'请不再使用那条记忆。',2))


def test_console_requires_local_admin_and_full_current_preview(lab):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，我喜欢纸模型。',level='important')[0]
    app=FastAPI();app.include_router(routes.router)
    b.task=SimpleNamespace(done=lambda:False)
    with patch.object(routes,'qq_bridge',b),TestClient(app) as client:
        assert client.get('/api/qq/memory-console').status_code==401
        headers={'Authorization':'Bearer admin','Origin':'http://testserver'}
        # Use the bridge's configured admin token; a bearer caller still needs
        # a configured owner for all sensitive queue/approval operations.
        b.settings=b.settings.model_copy(update={'admin_token':'admin'})
        assert client.get('/api/qq/memory-console?owner_id=30000',headers=headers).status_code==403
        preview=client.get('/api/qq/memory-console/preview/'+row['id']+'?owner_id=20000',headers=headers)
        assert preview.status_code==200
        payload={'owner_id':'20000','id':row['id'],'preview_version':preview.json()['preview_version'],'action':'approve'}
        assert client.post('/api/qq/memory-console/decision',json=payload,headers=headers).json()['state']=='saved'
        assert len(k.memories)==1


def test_source_capacity_does_not_discard_earlier_sources_to_hide_overflow(lab):
    b,k,s,now=lab
    for n in range(32):accept(lab,'虚构的小箱计划第'+str(n)+'次补充。',n+1,body='虚构的小箱计划持续补充。')
    before=s.listing('20000')['entries'][0]
    with pytest.raises(ValueError):accept(lab,'虚构的小箱计划额外补充。',40)
    after=s.listing('20000')['entries'][0]
    assert after==before and after['source_count']==32
    now[0]+=60;preview=s.preview('20000',after['id']);result=s.decide('20000',after['id'],preview['preview_version'],'approve')
    identifier=result['result']['items'][0]['memory_id']
    assert len(k.memory_v4_store.list_evidence(identifier))==32


@pytest.mark.parametrize('important_action',['approve','reject'])
def test_overlapping_important_must_be_decided_before_daily_no_double_save(lab,important_action):
    b,k,s,now=lab
    daily=accept(lab,'这是虚构材料，我一直喜欢蓝色纸船。')[0]
    source=s.preview('20000',daily['id'])['sources'][0]['event']
    important=next(x for x in accept(lab,'这是虚构材料，蓝色纸船是我的长期爱好。',2,level='important',covered=[source],
        body='这是虚构材料，我一直喜欢蓝色纸船。') if x['kind']=='important')
    now[0]+=60
    preview=s.preview('20000',daily['id']);assert preview['waiting_on']==[important['id']]
    with pytest.raises(ValueError):s.decide('20000',daily['id'],preview['preview_version'],'approve')
    preview=s.preview('20000',important['id']);s.decide('20000',important['id'],preview['preview_version'],important_action)
    preview=s.preview('20000',daily['id'])
    if important_action=='approve':
        assert preview['state']=='superseded' and not preview['body'] and len(k.memories)==1
    else:
        assert not preview['waiting_on'];s.decide('20000',daily['id'],preview['preview_version'],'approve')
        assert len(k.memories)==1


def test_invalid_event_dependencies_cannot_be_saved_from_a_daily_draft(lab):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，今天讨论纸船。')[0]
    with b.store.connect() as db:
        sources=json.loads(db.execute('select sources from qq_daily_entries where id=?',(row['id'],)).fetchone()[0])
        sources[0]['dependencies']={'version':2,'refs':['STOPPED_EVENT_CANARY']}
        db.execute('update qq_daily_entries set sources=? where id=?',(json.dumps(sources),row['id']))
    b.episodes=SimpleNamespace(provenance=SimpleNamespace(valid=lambda m,tag:False))
    result=s.preview('20000',row['id'])
    assert result['state']=='invalid' and not result['body'] and not result['sources'] and not k.memories


def test_original_user_sources_survive_unrelated_edit_but_explicit_stop_revokes(lab):
    b,k,s,now=lab;barrier={'person:20000':0}
    b.episodes=SimpleNamespace(provenance=SimpleNamespace(barriers=lambda m:dict(barrier),valid=lambda m,tag:False))
    row=accept(lab,'这是虚构材料，今日整理阳台的薄荷。')[0]
    other=k.add_memory('虚构的另一个目录使用蓝色标签。')
    k.update_memory(other['id'],{'content':'虚构的另一个目录使用白色标签。'})
    assert s.preview('20000',row['id'])['body']
    barrier['person:20000']=1
    assert s.preview('20000',row['id'])['state']=='invalid'


def test_existing_shared_memory_off_choice_blocks_capture_and_approval(lab):
    b,k,s,now=lab;row=accept(lab,'这是虚构材料，我一直喜欢纸模型。',level='important')[0]
    preview=s.preview('20000',row['id']);b.settings=b.settings.model_copy(update={'shared_memory':False})
    assert not s.managed(msg(b,now,'今天整理了纸模型。',2))
    with pytest.raises(MemoryAccessDenied):s.decide('20000',row['id'],preview['preview_version'],'approve')
    assert not k.memories


def test_new_important_memory_maintains_history_only_for_true_arrangement_change(lab):
    b,k,s,now=lab
    old='虚构的槐汀晚餐计划使用蒸锅，因为炉灶要留给汤锅。'
    new='虚构的槐汀晚餐计划不再用蒸锅，改用烤箱，因为蒸锅坏了。'
    row=accept(lab,old,level='important')[0];preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    identifier=k.memories[0]['id']
    row=next(x for x in accept(lab,new,2,target=identifier,body=new) if x['kind']=='revision')
    preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    from saya_app.services.memory_temporal_history import selected_history
    native=k.memory_v4_store.get_memory(identifier)
    result=selected_history(k,'槐汀晚餐计划在改用烤箱之前，为什么选蒸锅？',[native.to_dict()])
    assert result[identifier]['text']==old and k._find_memory(identifier)['content']==new


def test_paraphrased_change_does_not_override_actual_source_factual_correction(lab):
    b,k,s,now=lab
    old='虚构的槐汀晚餐计划使用蒸锅，因为炉灶要留给汤锅。'
    row=accept(lab,old,level='important')[0];preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    identifier=k.memories[0]['id']
    source='原记录写错了，虚构的槐汀晚餐计划实际一直使用烤箱，从未用过蒸锅。'
    misleading='虚构的槐汀晚餐计划不再用蒸锅，改用烤箱。'
    row=next(x for x in accept(lab,source,2,target=identifier,body=misleading) if x['state']=='pending')
    preview=s.preview('20000',row['id']);s.decide('20000',row['id'],preview['preview_version'],'approve')
    native=k.memory_v4_store.get_memory(identifier)
    assert 'temporal_predecessor' not in native.metadata


def test_background_capture_requires_successful_actual_bridge_delivery(tmp_path):
    from test_qq_delivery import Client
    from test_qq_task_scheduler import until,submit
    async def run():
        async def responder(*a):return '自然的普通回答。'
        b=make(tmp_path/'bridge',responder,memory_management_enabled=True);await b.start()
        k=KnowledgeBase(str(tmp_path/'kb'));b.memory_management=QQMemoryManagement(b,k)
        s=QQDailyMemory(b,k);b.daily_memory=s;s.configure('20000',0,True)
        async def classify(prompt,**kw):return {'value':'daily','pending_id':None,'memory_id':None,'content':'这是虚构材料，我喜欢纸模型。'}
        b.expressions.decide=classify
        try:
            assert submit(b,'这是虚构材料，我喜欢纸模型。',901)[0]=='queued'
            await until(lambda:b.store.counts().get('sent')==1)
            assert len(s.listing('20000')['entries'])==1 and not k.memories
            assert len(b.client.sent)==1 and '操作回执' not in b.client.sent[0][1]
        finally:await b.stop()
    asyncio.run(run())


def test_managed_chat_disables_old_candidate_pipeline_and_per_message_receipts(server):
    from saya_app.services.qq_chat import QQChatResponder
    from saya_app.services.qq_bridge import QQSettings,QQMessage
    from fastapi.responses import StreamingResponse
    captured=[]
    async def producer(req,channel_turn):
        captured.append((req,channel_turn))
        async def stream():
            yield 'event: answer_complete\ndata: '+json.dumps({'text':'窗边的薄荷看着应该很清爽。'})+'\n\n'
            # Even if an older producer emits a candidate, a managed ordinary
            # turn cannot accidentally turn it into a QQ confirmation bubble.
            yield 'event: memory_candidates\ndata: '+json.dumps({'candidates':[{'id':'old-candidate','title':'薄荷'}]})+'\n\n'
        return StreamingResponse(stream(),media_type='text/event-stream')
    settings=QQSettings(model='api',bot_id='10000',owner_ids=frozenset({'20000'}),shared_memory=True,web_search_mode='off')
    r=QQChatResponder(settings,server.ChatWithMotion,producer,lambda:'api',lambda:{'configured':True,'api_key':'fixture','api_url':'http://fixture.invalid','model':'fixture'})
    r.daily_memory=SimpleNamespace(managed=lambda m:True,personal_context=lambda m:None,valid_personal_context=lambda m,s:True)
    m=QQMessage('test','10000:private:20000','10000','20000','','1','今天把薄荷搬到窗边了。',True)
    answer=asyncio.run(r(m,[],'session_qq_daily.jsonl'))
    assert not captured[0][0].memory_write_enabled and captured[0][1]['daily_memory_managed']
    assert answer.body=='窗边的薄荷看着应该很清爽。' and not answer.notes
