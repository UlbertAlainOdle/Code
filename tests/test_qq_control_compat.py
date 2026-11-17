import asyncio
import time

import pytest

from saya_app.services.qq_bridge import QQBridge,QQStore
from test_qq_task_scheduler import make,submit
from test_qq_delivery import config,Client


@pytest.mark.parametrize('old_state',['queued','sending','sent','unknown'])
def test_old_control_records_get_terminal_projection_before_disabled_fifo_starts(tmp_path,old_state):
    async def run():
        store=QQStore(tmp_path/'bridge.sqlite3');store.initialize(7)
        key='10000:private:20000:message:77'
        with store.connect() as db:
            db.execute('CREATE TABLE qq_control_events(event_key TEXT PRIMARY KEY,scope TEXT,created REAL,state TEXT)')
            db.execute('INSERT INTO qq_control_events VALUES(?,?,?,?)',(key,'10000:private:20000:shared',time.time(),old_state))
        calls=[]
        async def model(*args):calls.append(True);return 'answer'
        client=Client();bridge=QQBridge(settings=config(shared_memory=True),store=store,client=client,responder=model)
        await bridge.start()
        try:
            assert submit(bridge,'/status',77)[0]=='duplicate'
            await bridge.queue.join()
            assert not calls and not client.sent
            with store.connect() as db:
                row=db.execute('SELECT state,payload,scope FROM inbox WHERE event_key=?',(key,)).fetchone()
                assert tuple(row)==('control','{}','10000:private:20000')
        finally:await bridge.stop()
    asyncio.run(run())


def test_control_tombstones_do_not_extend_normal_cooldown(tmp_path):
    async def run():
        calls=[]
        async def model(message,*args):calls.append(message.text);return 'answer'
        bridge=make(tmp_path,model,cooldown_seconds=3600);await bridge.start()
        try:
            assert submit(bridge,'/status',1)[0]=='queued'
            await bridge.task_controls.join()
            assert submit(bridge,'ordinary',2)[0]=='queued'
            await bridge.queue.join()
            assert submit(bridge,'second ordinary',3)[0]=='rate_limited'
            assert calls==['ordinary']
        finally:await bridge.stop()
    asyncio.run(run())


def test_data_event_cannot_be_reclassified_as_a_fresh_control_event(tmp_path):
    async def run():
        calls=[]
        async def model(message,*args):calls.append(message.text);return 'answer'
        bridge=make(tmp_path,model);await bridge.start()
        try:
            submit(bridge,'ordinary',1);await bridge.queue.join()
            sent=len(bridge.client.sent)
            assert submit(bridge,'/cancel',1)[0]=='duplicate'
            await bridge.task_controls.join()
            assert len(bridge.client.sent)==sent and calls==['ordinary']
        finally:await bridge.stop()
    asyncio.run(run())
