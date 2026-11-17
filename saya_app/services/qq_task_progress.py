"""Content-free, channel-throttled QQ task progress, separate from controls."""
import asyncio
import time

from .qq_tasks import bound_guard


STAGES = {'queued':'排队等待','received':'准备处理','memory':'检索记忆','search':'搜索',
          'sources':'读取来源','generating':'生成回答','sending':'发送回答'}


class QQTaskProgress:
    def __init__(self,bridge,*,clock=time.time):
        self.bridge,self.clock=bridge,clock
        self.automatic=False  # QQ conversation stays quiet; /status is explicit.
        self.pending={};self.paused={};self.last={};self.current=None
        self.done=asyncio.Event();self.done.set()
        self.wake=asyncio.Event();self.stopping=False;self.worker=None
        with bridge.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS qq_progress_events (
                event_key TEXT PRIMARY KEY,created REAL NOT NULL,state TEXT NOT NULL)''')
            db.execute("UPDATE qq_progress_events SET state='unknown' WHERE state='sending'")

    @staticmethod
    def channel(message):
        return (message.bot_id, 'group',message.group_id) if message.group_id else (message.bot_id,'private',message.user_id)

    def start(self):
        with bound_guard(None):
            self.worker=asyncio.create_task(self._run(),name='qq-task-progress')

    def update(self,jobs):
        if not self.automatic:
            self.pending.clear()
            return
        if len(self.last)>2048:
            self.last=dict(sorted(self.last.items(),key=lambda item:item[1],reverse=True)[:2048])
        live={t['id']:t for t in self.bridge.task_registry.list_live()}
        groups={}
        for item,task in jobs:
            task=live.get(task['id'])
            if task is None or task['state']=='cancel_requested':continue
            message=item[1];channel=self.channel(message)
            groups.setdefault(channel,[]).append((message,task))
        pending={}
        for channel,entries in groups.items():
            if self.paused.get(channel) or self.clock()-min(t['created'] for _,t in entries)<1:continue
            running=[e for e in entries if e[1]['state']=='running']
            message,task=(running or entries)[0]
            phase=task['phase'] if running else 'queued'
            if phase not in STAGES:continue
            count=f'本会话有{len(entries)}个任务，' if len(entries)>1 else ''
            pending[channel]=(message,task,phase,f'已收到，{count}正在{STAGES[phase]}。')
        self.pending=pending
        self.wake.set()

    async def pause_and_flush(self,message):
        channel=self.channel(message)
        self.paused[channel]=self.paused.get(channel,0)+1
        self.pending.pop(channel,None)
        if self.current==channel:await self.done.wait()

    def release(self,message):
        channel=self.channel(message)
        self.paused[channel]=max(0,self.paused.get(channel,0)-1)
        if not self.paused[channel]:self.paused.pop(channel,None)

    def busy(self):
        return self.current is not None

    async def stop(self):
        self.stopping=True;self.pending.clear();self.wake.set()
        if self.worker:await self.worker

    async def _run(self):
        global_sent=0.0
        while not self.stopping:
            try:await asyncio.wait_for(self.wake.wait(),timeout=.1)
            except asyncio.TimeoutError:pass
            self.wake.clear()
            for channel,(message,task,phase,text) in list(self.pending.items()):
                now=self.clock();interval=10 if message.group_id else 2
                if self.paused.get(channel) or now-self.last.get(channel,0)<interval or now-global_sent<.1:continue
                if not self.bridge._task_permission(message):continue
                current=self.bridge.task_registry.get(task['id'],task['scope'])
                if current['state'] not in {'queued','running'}:continue
                key=task['id']+':'+phase
                with self.bridge.store.connect() as db:
                    claimed=db.execute("INSERT OR IGNORE INTO qq_progress_events VALUES(?,?,'sending')",(key,now)).rowcount
                    db.execute('DELETE FROM qq_progress_events WHERE created<?',(now-self.bridge.settings.retention_days*86400,))
                    db.execute('DELETE FROM qq_progress_events WHERE event_key IN (SELECT event_key FROM qq_progress_events ORDER BY created DESC LIMIT -1 OFFSET 16384)')
                if not claimed:continue
                self.current=channel;self.done.clear()
                self.last[channel]=now;global_sent=now
                state='unknown'
                try:
                    await asyncio.wait_for(self.bridge.client.send(message,text),timeout=5)
                    state='sent'
                except Exception:
                    self.bridge.last_error='progress_delivery_unknown'
                finally:
                    with self.bridge.store.connect() as db:
                        db.execute('UPDATE qq_progress_events SET state=? WHERE event_key=?',(state,key))
                    self.current=None;self.done.set()
                break
