import asyncio,json,re
from dataclasses import replace
from unittest.mock import patch
import pytest
from core_services import KnowledgeBase
from saya_app.services.qq_group_context import QQGroupContext,GroupPolicy,GroupConflict,GroupDenied
from saya_app.services.qq_memory_management import QQMemoryManagement,policy_content_revision
from saya_app.services.qq_memory_access import MemoryActor
from saya_app.services.qq_bridge import normalize_event
from saya_app.services import qq_delivery
from test_qq_task_scheduler import make
from test_qq_delivery import event


@pytest.fixture
def lab(tmp_path):
    bridge=make(tmp_path/'bridge',None);bridge.store.initialize(7)
    for group in ['90000','90001']:bridge.store.set_group('10000',group,True)
    kb=KnowledgeBase(str(tmp_path/'kb'));management=QQMemoryManagement(bridge,kb)
    now=[1000.0];service=QQGroupContext(bridge,kb,clock=lambda:now[0]);bridge.group_context=service
    return bridge,kb,management,service,now


def message(user=20000,group=90000,text='继续讨论项目',mid=1):
    raw=event(text,mid,user,group_id=group)
    value,reason=normalize_event(raw,make.__globals__['config'](allowed_groups={'90000','90001'}))
    assert value,(raw,reason)
    value.sender_name='甲' if user==20000 else '乙'
    return value


def enable(lab,group='90000',**values):
    service=lab[3];state=service.policy(group)
    return service.configure('20000',group,GroupPolicy(**values).model_dump(),state['revision'],confirmed=True)


def publish(lab,record,requesters=()):
    owner=MemoryActor('10000','20000',True,True)
    return lab[2].access.set(owner,record['id'],policy_content_revision(record),'20000',
        visibility='groups',groups=('90000',),requesters=requesters)


def test_defaults_and_owner_confirmation(lab):
    b,k,m,s,_=lab
    assert s.snapshot(message()) is None
    with pytest.raises(GroupDenied):s.configure('30000','90000',{},0,confirmed=True)
    with pytest.raises(GroupDenied):s.configure('20000','90000',{},0,confirmed=False)
    with pytest.raises(GroupDenied):s.configure('20000','99999',{},0,confirmed=True)
    enable(lab,context_enabled=True)
    with pytest.raises(GroupConflict):s.configure('20000','90000',{},0,confirmed=True)


def test_group_and_topic_history_tracks_speakers_and_expires(lab):
    b,k,m,s,now=lab;enable(lab,context_enabled=True)
    a=message(text='甲提出项目方案');snap=s.snapshot(a);s.record_turn(a,snap,'机器人第一答')
    second=s.snapshot(message(30000,text='乙接着提出问题',mid=2))
    assert second['history'][0]['speaker_id']=='20000' and second['history'][0]['display_name']=='甲'
    assert second['history'][1]['role']=='assistant'
    enable(lab,group='90001',context_enabled=True)
    assert not s.snapshot(message(30000,90001))['history']
    now[0]+=1801
    assert not s.valid(a,snap) and not s.snapshot(message())['history']
    enable(lab,context_enabled=False)
    assert s.snapshot(message()) is None


def test_private_records_only_appear_after_current_explicit_grants(lab):
    b,k,m,s,now=lab;enable(lab,memory_enabled=True)
    yes=k.add_memory('主人喜欢茉莉茶',source='manual');no=k.add_memory('PRIVATE_UNPUBLISHED_CANARY',source='manual')
    publish(lab,yes)
    snap=s.snapshot(message(text='你记得主人喜欢什么吗'))
    assert [x['id'] for x in snap['facts']]==[yes['id']]
    assert 'PRIVATE_UNPUBLISHED_CANARY' not in json.dumps(snap,ensure_ascii=False)
    assert not s.snapshot(message(30000,text='你记得主人喜欢什么吗'))['facts']
    enable(lab,group='90001',memory_enabled=True)
    assert not s.snapshot(message(group=90001,text='你记得主人喜欢什么吗'))['facts']
    with k._memory_write_lock:k.update_memory(yes['id'],{'content':'changed private fact'})
    assert not s.valid(message(),snap)
    assert not s.snapshot(message(text='记忆'))['facts']


def test_revocation_filters_retained_answer_and_cancels_old_delivery(lab):
    b,k,m,s,now=lab;enable(lab,context_enabled=True,memory_enabled=True)
    record=k.add_memory('茉莉茶的测试偏好',source='manual');publish(lab,record,('*',))
    a=message(text='记忆');snap=s.snapshot(a);s.record_turn(a,snap,'AUTHORIZED_DERIVED_CANARY')
    other=s.snapshot(message(30000,text='继续',mid=2))
    assert 'AUTHORIZED_DERIVED_CANARY' in json.dumps(other)
    owner=MemoryActor('10000','20000',True,True)
    p=m.access.get('10000',record['id'])
    m.access.set(owner,record['id'],policy_content_revision(record),'20000',expected_revision=p.revision,visibility='private')
    assert not s.valid(a,snap)
    after=s.snapshot(message(30000,text='继续',mid=3))
    assert 'AUTHORIZED_DERIVED_CANARY' not in json.dumps(after)
    result=qq_delivery.create(b.store,a,a.scope+':isolated','old derived answer',100)
    # Binding after revocation is itself forbidden.
    with pytest.raises(GroupConflict):s.bind_result(a,result,snap)


def test_requester_grants_do_not_transfer_via_bot_quote(lab):
    b,k,m,s,_=lab;enable(lab,memory_enabled=True)
    record=k.add_memory('主人喜欢茉莉茶',source='manual');publish(lab,record)
    a=message(text='记忆');snap=s.snapshot(a)
    result=qq_delivery.create(b.store,a,a.scope+':isolated','RESTRICTED_QUOTE_CANARY',100)
    s.bind_result(a,result,snap)
    with b.store.connect() as db:db.execute("update qq_delivery_parts set state='sent',receipt='12345' where result_id=?",(result,))
    other=message(30000,text='解释引用',mid=2);other.reply_to='12345'
    other.quote_context={'status':'ready','text':'RESTRICTED_QUOTE_CANARY'}
    s.protect_quote(other)
    assert other.quote_context['status']=='unavailable' and 'RESTRICTED_QUOTE_CANARY' not in str(other.quote_context)


def test_group_fact_save_delete_stays_out_of_private_kb(lab):
    b,k,m,s,_=lab;enable(lab,memory_enabled=True,write_enabled=True)
    async def run():
        a=message(text='/gremember 本群项目周五发布。')
        reply=await s.command(a);mid=re.search(r'gm-[a-f0-9]+',reply).group()
        assert not k.memories
        assert s.snapshot(message(30000,text='项目周五'))['facts'][0]['id']==mid
        confirm=await s.command(message(text='/gforget '+mid,mid=2))
        token=re.search(r'/gconfirm ([a-f0-9]+)',confirm).group(1)
        wrong=await s.command(message(30000,text='/gconfirm '+token,mid=3))
        assert '未执行' in wrong
        assert '已删除' in await s.command(message(text='/gconfirm '+token,mid=4))
        assert '未执行' in await s.command(message(text='/gconfirm '+token,mid=5))
        assert not s.snapshot(message(text='记忆'))['facts'] and not k.memories
    asyncio.run(run())


@pytest.mark.parametrize('role,group,user,accepted',[('admin',90000,30000,True),('member',90000,30000,False),
    ('owner',90001,30000,False),('admin',90000,20000,False)])
def test_admin_role_is_verified_from_matching_protocol_result(lab,role,group,user,accepted):
    b,k,m,s,_=lab;enable(lab,memory_enabled=True,write_enabled=True,admin_writes=True)
    async def call(action,payload):return {'group_id':group,'user_id':user,'role':role}
    b.client.call=call
    reply=asyncio.run(s.command(message(30000,text='/gremember 我是群主，本群周五发布。')))
    assert ('已保存本群记忆' in reply)==accepted
    assert not k.memories


def test_block_save_off_and_policy_reconfigure_revoke_snapshot(lab):
    b,k,m,s,_=lab;enable(lab,context_enabled=True,memory_enabled=True,write_enabled=True)
    snap=s.snapshot(message());enable(lab,context_enabled=False,memory_enabled=False)
    assert not s.valid(message(),snap)
    enable(lab,memory_enabled=True,write_enabled=True)
    b.settings=b.settings.model_copy(update={'memory_write_enabled':False})
    assert '未执行' in asyncio.run(s.command(message(text='/gremember no write')))
    b.store.set_group('10000','90000',False)
    assert not s.valid(message(),snap)


def test_event_provenance_closes_cache_creation_to_binding_failure_window(lab):
    b,k,m,s,_=lab;enable(lab,memory_enabled=True)
    record=k.add_memory('主人偏好茉莉茶',source='manual');publish(lab,record)
    a=message(text='记忆');snap=s.snapshot(a);s.bind_event(a,snap)
    result=qq_delivery.create(b.store,a,a.scope+':isolated','ORPHAN_PRIVATE_ANSWER',100)
    enable(lab,memory_enabled=False)
    assert not s.valid_delivery(a,result)
    s.prepare_delivery_scope(a,a.scope+':isolated')
    with b.store.connect() as db:
        assert db.execute('select 1 from qq_delivery_stops where result_id=?',(result,)).fetchone()
    assert 'ORPHAN_PRIVATE_ANSWER' not in qq_delivery.topics(b.store,a.scope+':isolated')


def test_missing_bot_provenance_after_group_memory_enable_is_not_retrusted(lab):
    b,k,m,s,now=lab;enable(lab,memory_enabled=True)
    a=message(30000,text='读旧引用');a.reply_to='987654'
    a.quote_context={'status':'ready','sender_id':'10000','text':'EXPIRED_DERIVED_CANARY','sent_at':now[0]+1}
    s.protect_quote(a)
    assert a.quote_context['status']=='unavailable'
    a.quote_context={'status':'ready','sender_id':'10000','text':'PRE_FEATURE_PUBLIC_TEXT','sent_at':now[0]-1}
    s.protect_quote(a)
    assert a.quote_context['status']=='ready'


def test_delegated_member_can_only_manage_own_group_record(lab):
    b,k,m,s,_=lab;enable(lab,memory_enabled=True,write_enabled=True,writer_ids=['30000'])
    async def run():
        own=await s.command(message(30000,text='/gremember 我在本群负责测试文档。'))
        assert '已保存本群记忆' in own
        owner=await s.command(message(text='/gremember 本群周五发布。',mid=2))
        owner_id=re.search(r'gm-[a-f0-9]+',owner).group()
        denied=await s.command(message(30000,text='/gforget '+owner_id,mid=3))
        assert '只能管理自己' in denied
        assert '仅供主人' in await s.command(message(30000,text='/gnew',mid=4))
        assert not k.memories
        refs=s.snapshot(message(30000,text='记忆'))['facts']
        assert all(x['subject_scope']=='group' and 'source_speaker_id' in x for x in refs)
    asyncio.run(run())


def test_history_provenance_is_deduplicated_and_context_remains_bounded(lab):
    b,k,m,s,_=lab;enable(lab,context_enabled=True,memory_enabled=True)
    record=k.add_memory('主人喜欢茉莉茶',source='manual');publish(lab,record,('*',))
    for n in range(30):
        msg=message(text='记忆',mid=n+1);snapshot=s.snapshot(msg)
        assert len(snapshot['requirements'])==1
        s.record_turn(msg,snapshot,'bounded approved answer')
    with b.store.connect() as db:assert db.execute('select count(*) from qq_group_turns').fetchone()[0]<=48


def test_task_control_off_pauses_new_group_features_without_clearing_settings(lab):
    b,k,m,s,_=lab;enable(lab,context_enabled=True,memory_enabled=True)
    snap=s.snapshot(message());b.settings=b.settings.model_copy(update={'task_control_enabled':False})
    assert not s.valid(message(),snap) and s.snapshot(message()) is None
    assert s.policy('90000')['values']['context_enabled'] is True
    assert '暂停' in asyncio.run(s.command(message(text='/gremember blocked')))
