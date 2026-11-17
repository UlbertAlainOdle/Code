import asyncio
import time

import pytest

from test_qq_task_scheduler import make, submit, until
from test_qq_delivery import Client


@pytest.mark.parametrize('group',[False,True])
def test_long_task_progress_is_public_throttled_and_precedes_final_answer(tmp_path,group):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*args):entered.set();await release.wait();return 'final answer'
        client=Client();bridge=make(tmp_path,model,client)
        await bridge.start();bridge.task_progress.automatic=True
        now=[time.time()];bridge.task_progress.clock=lambda:now[0]
        try:
            if group:
                from test_qq_delivery import event
                import json
                bridge.submit_signed(json.dumps(event('PRIVATE_PROMPT_CANARY',group_id=90000)).encode(),'Bearer inbound')
            else:submit(bridge,'PRIVATE_PROMPT_CANARY')
            await entered.wait()
            task=bridge.task_registry.list_active()[0]
            now[0]=task['created']+2
            await until(lambda:len(client.sent)==1)
            assert '生成回答' in client.sent[0][1] and 'PRIVATE_PROMPT_CANARY' not in client.sent[0][1]
            bridge.task_registry.set_phase(task['id'],task['scope'],'search')
            now[0]+=1
            await asyncio.sleep(.12)
            assert len(client.sent)==1
            now[0]+=10 if group else 2
            await until(lambda:len(client.sent)==2)
            assert '搜索' in client.sent[1][1]
            release.set();await bridge.queue.join();await until(lambda:not bridge._task_workers)
            assert client.sent[-1][1]=='final answer'
            history,_=bridge.store.conversation(task['scope'])
            assert len(history)==2 and '正在搜索' not in history[-1]
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_inflight_progress_settles_before_final_send(tmp_path):
    async def run():
        model_entered,model_release=asyncio.Event(),asyncio.Event()
        progress_entered,progress_release=asyncio.Event(),asyncio.Event()
        class SlowProgress(Client):
            async def send(self,message,text):
                if text.startswith('已收到'):
                    progress_entered.set();await progress_release.wait()
                return await super().send(message,text)
        async def model(*args):model_entered.set();await model_release.wait();return 'final answer'
        client=SlowProgress();bridge=make(tmp_path,model,client)
        await bridge.start();bridge.task_progress.automatic=True
        try:
            submit(bridge,'slow');await model_entered.wait()
            task=bridge.task_registry.list_active()[0]
            bridge.task_progress.clock=lambda:task['created']+2
            await asyncio.wait_for(progress_entered.wait(),2)
            model_release.set();await asyncio.sleep(.03)
            assert not client.sent
            assert bridge.status()['counts'].get('sending',0)>0
            progress_release.set();await bridge.queue.join();await until(lambda:not bridge._task_workers)
            assert client.sent[0][1].startswith('已收到') and client.sent[-1][1]=='final answer'
        finally:
            progress_release.set();model_release.set();await bridge.stop()
    asyncio.run(run())


def test_uncertain_progress_is_never_retried(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event();attempts=[]
        class BrokenProgress(Client):
            async def send(self,message,text):
                if text.startswith('已收到'):
                    attempts.append(True);raise TimeoutError('synthetic transport details')
                return await super().send(message,text)
        async def model(*args):entered.set();await release.wait();return 'answer'
        bridge=make(tmp_path,model,BrokenProgress());await bridge.start();bridge.task_progress.automatic=True
        try:
            submit(bridge,'slow');await entered.wait();task=bridge.task_registry.list_active()[0]
            now=[task['created']+2];bridge.task_progress.clock=lambda:now[0]
            await until(lambda:len(attempts)==1)
            now[0]+=20;await asyncio.sleep(.15)
            assert len(attempts)==1
            with bridge.store.connect() as db:
                assert db.execute('select state from qq_progress_events').fetchone()[0]=='unknown'
        finally:release.set();await bridge.stop()
    asyncio.run(run())


def test_queued_tasks_receive_grouped_feedback_without_prompt_text(tmp_path):
    async def run():
        entered,release=asyncio.Event(),asyncio.Event()
        async def model(*args):entered.set();await release.wait();return 'answer'
        client=Client();bridge=make(tmp_path,model,client);await bridge.start();bridge.task_progress.automatic=True
        try:
            submit(bridge,'PRIVATE_A',1);await entered.wait();submit(bridge,'PRIVATE_B',2)
            first=bridge.task_registry.list_active()[0]
            bridge.task_progress.clock=lambda:first['created']+2
            await until(lambda:bool(client.sent))
            assert '2个任务' in client.sent[0][1]
            assert 'PRIVATE_' not in client.sent[0][1]
        finally:release.set();await bridge.stop()
    asyncio.run(run())
