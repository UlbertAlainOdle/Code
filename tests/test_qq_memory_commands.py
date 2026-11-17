import asyncio
import re
from types import SimpleNamespace
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI

from core_services import KnowledgeBase
from saya_app.routers import qq as routes
from saya_app.services.qq_memory_management import QQMemoryManagement
from saya_app.services.qq_chat import QQChatResponder
from saya_app.services.memory_versions import record_version
from test_qq_task_scheduler import make,submit
from test_qq_delivery import event


async def lab(tmp_path, **settings):
    kb=KnowledgeBase(str(tmp_path/'kb'));memory=kb.add_memory('旧偏好',title='测试偏好',source='manual')
    bridge=make(tmp_path/'bridge',None,**settings);await bridge.start()
    service=QQMemoryManagement(bridge,kb);bridge.memory_management=service
    async def forbidden(*args,**kwargs):raise AssertionError('management must not invoke model')
    responder=QQChatResponder(bridge.settings,lambda **kw:SimpleNamespace(**kw),forbidden,lambda:'api',lambda:{})
    responder.memory_management=service;bridge.responder=responder
    return kb,memory,bridge


@pytest.mark.parametrize('enabled,write',[(False,True),(True,False)])
def test_mutations_are_default_off_and_save_off_is_readonly(tmp_path,enabled,write):
    async def run():
        kb,m,bridge=await lab(tmp_path,memory_management_enabled=enabled,memory_write_enabled=write)
        try:
            submit(bridge,f"/memory edit {m['id']} {record_version(m)} 不可写的新正文");await bridge.queue.join()
            assert kb._find_memory(m['id'])['content']=='旧偏好'
            assert any(('尚未开启' if not enabled else '只读') in x[1] for x in bridge.client.sent)
        finally:await bridge.stop()
    asyncio.run(run())


def test_edit_receipt_survives_own_epoch_change_without_relabelling_old_history(tmp_path):
    async def run():
        kb,m,bridge=await lab(tmp_path,memory_management_enabled=True)
        scope='10000:private:20000:shared';session='session_qq_fixture.jsonl'
        bridge.store.save_conversation(scope,['Doctor: OLD_USER','Saya: OLD_DERIVED'],session)
        try:
            submit(bridge,f"/memory edit {m['id']} {record_version(m)} 新的偏好");await bridge.queue.join()
            assert kb._find_memory(m['id'])['content']=='新的偏好'
            assert any('修改已提交' in x[1] for x in bridge.client.sent)
            submit(bridge,f"/memory show {m['id']}",2);await bridge.queue.join()
            history,_=bridge.store.conversation(scope)
            assert 'OLD_DERIVED' not in str(history) and 'OLD_USER' in str(history)
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('operation',['delete','block','unblock'])
def test_actual_bridge_requires_explicit_target_bound_confirmation(tmp_path,operation):
    async def run():
        kb,m,bridge=await lab(tmp_path,memory_management_enabled=True)
        try:
            submit(bridge,f"/memory {operation} {m['id']}");await bridge.queue.join()
            assert kb._find_memory(m['id']) and kb.memory_allowed_for_use(m)
            text='\n'.join(x[1] for x in bridge.client.sent)
            token=re.search(r'/memory confirm ([A-Za-z0-9_-]+)',text).group(1)
            submit(bridge,'/memory confirm '+token,2);await bridge.queue.join()
            if operation=='delete':assert kb._find_memory(m['id']) is None
            elif operation=='block':assert not kb.memory_allowed_for_use(kb._find_memory(m['id']))
            else:assert kb.memory_allowed_for_use(kb._find_memory(m['id']))
            assert any(('已删除' if operation=='delete' else '已停止' if operation=='block' else '已恢复') in x[1] for x in bridge.client.sent)
            submit(bridge,'/memory confirm '+token,3);await bridge.queue.join()
            assert any('没有再次执行' in x[1] for x in bridge.client.sent)
        finally:await bridge.stop()
    asyncio.run(run())


@pytest.mark.parametrize('group',[False,True])
def test_other_user_and_group_never_reach_private_mutations(tmp_path,group):
    async def run():
        kb,m,bridge=await lab(tmp_path,memory_management_enabled=True)
        try:
            raw=event(f"/memory delete {m['id']}",user_id=20000 if group else 30000,group_id=90000 if group else None)
            import json
            bridge.submit_signed(json.dumps(raw).encode(),'Bearer inbound');await bridge.queue.join()
            assert kb._find_memory(m['id'])
            assert not any('/memory confirm' in x[1] for x in bridge.client.sent)
        finally:await bridge.stop()
    asyncio.run(run())


def test_old_settings_client_cannot_reset_new_management_flag(tmp_path):
    async def run():
        _,_,bridge=await lab(tmp_path,memory_management_enabled=True)
        app=FastAPI();app.include_router(routes.router)
        try:
            with patch.object(routes,'qq_bridge',bridge):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1') as c:
                    h={'Origin':'http://127.0.0.1','X-Saya-QQ-UI':'1'}
                    await c.post('/api/qq/ui-session',headers=h)
                    current=(await c.get('/api/qq/settings')).json()
                    old={k:v for k,v in current['settings'].items() if k!='memory_management_enabled'}
                    result=await c.put('/api/qq/settings',json=old|{'revision':current['revision'],'inherit_fields':current['inherit_fields']},headers=h)
                    assert result.status_code==200
                    assert result.json()['settings']['memory_management_enabled'] is True
        finally:await bridge.stop()
    asyncio.run(run())
