"""Bounded independent QQ-04 control adapter.

The owner of the existing bridge consumer lease owns start/stop here too.
Only normalized signed messages enter the queue. No shared client is closed.
"""
import asyncio
import json
import logging
import re
import time
from .qq_responses import ControlReceipt

_LOG = logging.getLogger(__name__)


COMMANDS = frozenset({'/status', '/help', '/tasks', '/cancel','/expression','/表情模式','/proactive','/主动'})
LABELS = {'queued': '排队', 'running': '运行中', 'cancel_requested': '请求取消',
          'suppressed':'本次选择安静',
          'cancelled': '已取消', 'completed': '已完成', 'failed': '失败',
          'interrupted': '已中断', 'received': '已收到', 'memory': '检索记忆',
          'search': '搜索', 'sources': '读取来源', 'generating': '生成', 'sending': '发送'}
ERROR_LABELS={'model_timeout':'模型等待超时','model_unconfigured':'模型尚未配置','search_unconfigured':'搜索尚未配置',
              'login_required':'QQ需要登录','generation_failed':'生成失败','delivery_unknown':'发送结果不明',
              'authorization_changed':'权限已变更','restart_interrupted':'服务重启中断'}
ERROR_LABELS['task_expired']='排队超时，未执行'


class QQTaskControls:
    def __init__(self, bridge, registry, send, *, clock=time.time):
        self.bridge, self.registry, self.send, self.clock = bridge, registry, send, clock
        self.capacity = 16
        self.worker = None
        self.queue = None
        with bridge.store.connect() as db:
            db.execute('''CREATE TABLE IF NOT EXISTS qq_control_events (
                event_key TEXT PRIMARY KEY, scope TEXT NOT NULL,
                created REAL NOT NULL, state TEXT NOT NULL)''')
            db.execute('CREATE INDEX IF NOT EXISTS qq_control_scope ON qq_control_events(scope,created)')

    @staticmethod
    def scope(message):
        return message.scope + (':shared' if message.shared else ':isolated')

    def start(self):
        if self.worker and not self.worker.done():
            return
        with self.bridge.store.connect() as db:
            # Missing send acknowledgements never authorize replay after restart.
            db.execute("UPDATE qq_control_events SET state='unknown' WHERE state IN ('queued','sending')")
        self.queue = asyncio.Queue(maxsize=self.capacity)
        self.worker = asyncio.create_task(self._run(), name='qq-task-controls')

    async def join(self):
        await self.queue.join()

    async def stop(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
            self.worker = None
        if self.queue:
            while not self.queue.empty():
                message = self.queue.get_nowait()
                self._state(message.key, 'unknown')
                self.queue.task_done()

    def _state(self, key, state):
        with self.bridge.store.connect() as db:
            db.execute('UPDATE qq_control_events SET state=? WHERE event_key=?', (state, key))

    def counts(self):
        with self.bridge.store.connect() as db:
            return {r[0]:r[1] for r in db.execute("SELECT state,count(*) FROM qq_control_events WHERE state IN ('queued','sending') GROUP BY state")}

    def submit(self, body, *, authorization='', signature=''):
        if not isinstance(body, bytes) or len(body) > 65536:
            return 'ignored', 'invalid_body'
        if not self.bridge.authenticate(body, authorization, signature):
            return 'unauthorized', None
        settings = self.bridge.settings
        if (not settings or not settings.enabled or self.bridge.draining
                or not self.worker or self.worker.done()):
            return 'unavailable', None
        try:
            event = json.loads(body)
            if not isinstance(event, dict):
                return 'ignored', 'invalid_event'
            from .qq_bridge import normalize_event
            active = settings.model_copy(update={'allowed_groups': self.bridge.effective_groups()})
            unified=getattr(getattr(self.bridge,'episodes',None),'unified',None)
            if unified and unified.event_admitted(event):active=active.model_copy(update={'allowed_users':active.allowed_users|{str(event['user_id'])}})
            message, reason = normalize_event(event, active)
        except (ValueError, TypeError, KeyError):
            return 'ignored', 'invalid_event'
        if message is None:
            return 'ignored', reason
        from .qq_natural_actions import control_alias
        alias=control_alias(message.text)
        if alias:message.text=alias
        command = message.text.split()[0].lower()
        if command not in COMMANDS:
            return 'not_control', None
        now, scope = self.clock(), self.scope(message)
        with self.bridge.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute('SELECT 1 FROM qq_control_events WHERE event_key=?', (message.key,)).fetchone():
                return 'duplicate', None
            if db.execute('SELECT 1 FROM inbox WHERE event_key=?',(message.key,)).fetchone():
                return 'duplicate',None
            if db.execute("SELECT count(*) FROM qq_control_events WHERE state IN ('queued','sending')").fetchone()[0] >= self.capacity:
                return 'busy', None
            latest = db.execute('SELECT max(created) FROM qq_control_events WHERE scope=?', (scope,)).fetchone()[0]
            global_latest = db.execute('SELECT max(created) FROM qq_control_events').fetchone()[0]
            if ((latest is not None and now - latest < .75)
                    or (global_latest is not None and now - global_latest < .05)):
                return 'rate_limited', None
            db.execute('INSERT INTO qq_control_events VALUES(?,?,?,?)', (message.key, scope, now, 'queued'))
            # A content-free terminal projection also protects the old FIFO
            # (and a previous source build) after this feature is switched off.
            db.execute("INSERT INTO inbox(event_key,scope,payload,created,state,error) VALUES(?,?,'{}',?,'control','')",
                (message.key,message.scope,now))
            db.execute("DELETE FROM qq_control_events WHERE created<? AND state NOT IN ('queued','sending')", (now - settings.max_event_age - 120,))
        self.queue.put_nowait(message)
        return 'queued', None

    def _authorized(self, message):
        settings = self.bridge.settings
        allowed = message.group_id in self.bridge.effective_groups() if message.group_id else message.user_id in settings.allowed_users | settings.owner_ids
        if not allowed and not message.group_id and self.bridge.episodes:allowed=self.bridge.episodes.unified.admitted(message.user_id,message.text)
        shared = bool(not message.group_id and settings.shared_memory and message.user_id in settings.owner_ids)
        return (settings.enabled and not self.bridge.config_error and message.bot_id == settings.bot_id
                and allowed and bool(message.shared) == shared)

    def answer(self, message):
        args = message.text.split()
        command, scope = args[0].lower(), self.scope(message)
        if command in {'/expression','/表情模式'}:return self.bridge.expressions.command(message)
        if command in {'/proactive','/主动'}:return self.bridge.proactive.command(message) if self.bridge.proactive else '主动互动服务暂不可用。'
        if command == '/help':
            from .qq_help import command_help
            return command_help(self.bridge,message)
        tasks = self.registry.list_scope(scope)
        if command in {'/status', '/tasks'}:
            if len(args) != 1:
                return '用法：/status 或 /tasks。'
            return ('当前会话任务：\n' + '\n'.join(
                f"{t['id']} · {LABELS[t['state']]} · {ERROR_LABELS.get(t['error'],LABELS.get(t['phase'], '处理中'))}" for t in tasks)
                if tasks else 'QQ接入运行中；当前会话没有登记任务。')
        if len(args) > 2 or (len(args) == 2 and not re.fullmatch(r'[0-9a-f]{32}', args[1])):
            return '用法：/cancel 或 /cancel 任务编号。'
        if len(args) == 1:
            tasks = [t for t in tasks if t['state'] in {'running', 'queued', 'cancel_requested'}]
            if not tasks:
                return '当前会话没有可取消的任务。'
            task_id = tasks[0]['id']
        else:
            task_id = args[1]
        try:
            task = self.registry.cancel(task_id, scope)
        except KeyError:
            return '任务不存在或不属于当前会话。'
        text = LABELS[task['state']] + '。'
        effects = list(task['effects'].values())
        commits = sum(e['kind'] == 'memory' and e['outcome'] == 'committed' for e in effects)
        if commits:
            text += f'有{commits}项记忆操作已确认提交，保留原保存结果。'
        sends=sum(e['kind']=='send' and e['outcome']=='committed' for e in effects)
        if sends:
            text += f'已有{sends}次发送得到确认，已发送内容保留。'
        if any(e['outcome'] in {'in_flight', 'unknown'} for e in effects):
            text += '存在正在提交或结果未确认的操作，需要对账；不能保证撤回已发送内容。'
        return text

    async def _run(self):
        while True:
            message = await self.queue.get()
            failure_stage = 'prepare'
            try:
                if not self._authorized(message):
                    self._state(message.key, 'ignored')
                    continue
                answer = self.answer(message)
                self._state(message.key, 'sending')
                failure_stage = 'transport'
                receipt = str(await asyncio.wait_for(self.send(message, ControlReceipt(answer)), timeout=5) or '')
                if not re.fullmatch(r'-?\d{1,20}', receipt):
                    failure_stage = 'missing_receipt'
                    raise ValueError('missing_control_delivery_receipt')
                failure_stage = 'ledger'
                now = self.clock()
                with self.bridge.store.connect() as db:
                    db.execute('insert or ignore into qq_control_receipt_ids values(?,?,?)',
                               (receipt, self.scope(message), now))
                    db.execute('delete from qq_control_receipt_ids where created<?', (now - 86400,))
                    db.execute('delete from qq_control_receipt_ids where rowid in (select rowid from qq_control_receipt_ids order by created desc limit -1 offset 2048)')
                    db.execute("update qq_control_events set state='sent' where event_key=?", (message.key,))
            except asyncio.CancelledError:
                self._state(message.key, 'unknown')
                raise
            except Exception as error:
                # Transport exception strings may include credentials and URLs.
                _LOG.warning('qq_control_delivery_unknown stage=%s timeout=%s',
                             failure_stage, isinstance(error, TimeoutError))
                self._state(message.key, 'unknown')
            finally:
                self.queue.task_done()
