import asyncio
from dataclasses import replace
import pytest
from saya_app.services.qq_bridge import normalize_event
from test_qq_task_scheduler import make,submit,until
from test_qq_delivery import event


def test_owner_identity_override_lock_and_scope_isolation(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.relationships
        owner,_=normalize_event(event('问候'),bridge.settings)
        stranger,_=normalize_event(event('问候',2,30000),bridge.settings)
        try:
            service.configure_policy('20000',0,{'enabled':True})
            with pytest.raises(ValueError):service.configure('20000','20000','private',0,{'level':'distant'})
            assert service.view(owner)['level']=='owner'
            service.configure('20000','30000','group:90000',0,{'level':'important','locked':True})
            assert service.view(replace(stranger,group_id='90000'))['level']=='important'
            assert service.view(stranger)['level']=='ordinary'
            with pytest.raises(PermissionError):service.configure('30000','20000','private',0,{})
        finally:await bridge.stop()
    asyncio.run(run())


def test_quiet_is_terminal_without_messages_but_status_still_answers(tmp_path):
    async def run():
        async def model(*args):raise AssertionError('quiet must not generate')
        bridge=make(tmp_path,model);await bridge.start();service=bridge.relationships
        try:
            service.configure_policy('20000',0,{'enabled':True})
            service.configure('20000','30000','private',0,{'reply_policy':'quiet'})
            submit(bridge,'闲聊',user=30000);await bridge.queue.join()
            await until(lambda:bridge.task_registry.list_scope('10000:private:30000:isolated')[0]['state']=='suppressed')
            assert not bridge.client.sent and not bridge.store.conversation('10000:private:30000:isolated')[0]
            submit(bridge,'/status',2,user=30000);await bridge.task_controls.join()
            assert bridge.client.sent and '【操作回执】' in bridge.client.sent[-1][1]
        finally:await bridge.stop()
    asyncio.run(run())


def test_semantic_learning_is_bounded_and_does_not_reduce_for_disagreement(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.relationships
        service.configure_policy('20000',0,{'enabled':True})
        async def positive(*a,**kw):return {'action':'silent','delta':2,'reason':'friendly','confidence':.95}
        service.decide=positive
        try:
            for i in range(6):
                msg,_=normalize_event(event('友好交流',i+1,30000),bridge.settings)
                choice=await service.consider(msg,[])
                assert choice['action']=='reply'  # always_answer overrides optional silence
                service.commit(msg,choice,'sent');service.commit(msg,choice,'sent')
            view=service.view(msg)
            assert view['score']<=6 and not view['is_owner']
            async def disagreement(*a,**kw):return {'action':'reply','delta':-2,'reason':'uncertain','confidence':.99}
            service.decide=disagreement
            assert (await service.consider(msg,[]))['delta']==0
        finally:await bridge.stop()
    asyncio.run(run())


def test_lock_freezes_effective_relationship_without_restoring_old_score(tmp_path):
    async def run():
        bridge=make(tmp_path,None);await bridge.start();service=bridge.relationships
        now=[__import__('time').time()];service.clock=lambda:now[0]
        msg,_=normalize_event(event('测试',1,30000),bridge.settings)
        try:
            with bridge.store.connect() as db:db.execute('insert into qq_relation_affinity values(?,?,?,?,?,?)',('10000','30000','private',9.,5,now[0]-30*86400))
            before=service.view(msg)['score']
            service.configure('20000','30000','private',0,{'locked':True})
            assert service.view(msg)['score']==before
            now[0]+=30*86400
            assert service.view(msg)['score']==before
            service.configure('20000','30000','private',1,{'locked':False})
            assert service.view(msg)['score']==before
        finally:await bridge.stop()
    asyncio.run(run())
