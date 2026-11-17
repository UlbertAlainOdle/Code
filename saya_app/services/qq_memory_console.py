"""Local owner consent for source-bound QQ saves, without fake QQ delivery."""
from types import SimpleNamespace
from uuid import uuid4
import json,re
import time

from .memory_versions import record_version
from .memory_write_operations import fingerprint
from .memory_v4.models import _contains_secret
from .qq_memory_access import MemoryAccessDenied
from .qq_responses import ConsoleNotice


TABLE = 'qq_memory_console_saves'


class QQMemoryConsole:
    def __init__(self, management):
        self.m = management
        self.b = management.bridge
        self.kb = management.kb
        self.r = management.references
        with self.b.store.connect() as db:
            db.execute(f'''create table if not exists {TABLE}(
                id text primary key,scope text not null,session text not null,
                source_key text not null,channel text not null,revision integer not null,
                state text not null,created real not null,expires real not null,
                settings_revision text not null,epoch text not null,
                message text not null,payload text not null,result text not null)''')

    def recover(self):
        # Called under the original backend/consumer startup ownership. A new
        # process requires a new complete preview, not a replay of old consent.
        with self.b.store.connect() as db:
            db.execute(f"update {TABLE} set state='unknown',message='{{}}',payload='{{}}' where state='saving'")
            db.execute(f"update {TABLE} set revision=revision+1 where state in ('pending','select')")

    def actor(self, owner):
        from .qq_memory_access import MemoryActor
        actor = MemoryActor(self.b.settings.bot_id, owner, True, True)
        self.m.require_owner(actor)
        return actor

    def context(self, owner):
        actor = self.actor(owner)
        scope = f'{actor.bot_id}:private:{owner}:shared'
        session = self.b.store.conversation(scope)[1]
        self.m.authorize_operation(actor, scope, session)
        return actor, scope, session

    @staticmethod
    def message_data(message):
        return {k: getattr(message, k) for k in ('key', 'scope', 'bot_id', 'user_id', 'group_id', 'message_id', 'text', 'shared')}

    def admitted(self, message):
        with self.b.store.connect() as db:
            row = db.execute('select scope,payload from inbox where event_key=?', (message.key,)).fetchone()
        if not row or row['scope'] != message.scope:
            return False
        raw = json.loads(row['payload'])
        # Enqueue while processing the authenticated original ingress. A
        # content-free terminal dedupe key cannot mint a new save request.
        return bool(raw and all(raw.get(k) == getattr(message, k) for k in ('bot_id', 'user_id', 'message_id', 'text', 'shared')))

    def valid(self, row, payload=None, only_plan=None):
        try:
            message = SimpleNamespace(**json.loads(row['message']))
            actor, scope, session = self.context(message.user_id)
            if (scope != row['scope'] or session != row['session'] or
                    row['epoch'] != self.kb.memory_use.snapshot() or
                    row['settings_revision'] != self.b.controls()['revision'] or row['expires'] <= time.time()):
                return False
            payload = payload if payload is not None else json.loads(row['payload'])
            plans = payload.get('plans', [])
            if payload.get('options') is not None:
                plans = [self.r.plan(message, session, option) for option in payload['options']]
            if only_plan is not None:
                plans = [only_plan]
            if not plans:
                return False
            if payload.get('requested_action') is not None and (payload['requested_action']!='ignore' or len(plans)!=1
                    or not plans[0]['sources'][0].get('candidate') or plans[0].get('literal') or plans[0].get('fragment')):return False
            if row['channel'] == 'qq':
                with self.b.store.connect() as db:
                    if not db.execute('select 1 from inbox where event_key=? and scope=?', (message.key, message.scope)).fetchone():
                        return False
            elif row['channel'] != 'local_candidate' or any(not p['sources'][0].get('candidate') or p.get('literal') or p.get('fragment') for p in plans):
                return False
            for plan in plans:
                for source in plan['sources']:
                    if source.get('candidate') and not self.r.candidate_option(message, session, source['candidate'], source['version'], expires=source['expires'], external=True):
                        return False
            return all((p['epoch'] == row['epoch'] and p['expires'] > time.time() and p['session'] == session)
                       if p.get('literal') else self.r.valid(message, p) for p in plans)
        except (ValueError, KeyError, TypeError, MemoryAccessDenied, PermissionError):
            return False

    def sweep(self):
        with self.kb._memory_write_lock:
            with self.b.store.connect() as db:
                rows = [dict(r) for r in db.execute(f"select * from {TABLE} where state in ('pending','select')")]
            for row in rows:
                state = 'expired' if row['expires'] <= time.time() else 'invalid' if not self.valid(row) else None
                if state:
                    with self.b.store.connect() as db:
                        db.execute(f"update {TABLE} set state=?,revision=revision+1,message='{{}}',payload='{{}}' where id=? and state in ('pending','select')", (state, row['id']))
            with self.b.store.connect() as db:
                # Uncertain outcomes remain content-free dedupe barriers.
                db.execute(f"delete from {TABLE} where expires<? and state not in ('unknown','saving')", (time.time()-self.b.settings.retention_days*86400,))

    def enqueue(self, message, session, kind, payload, *, channel='qq', source_key=''):
        actor, scope, current = self.context(message.user_id)
        if current != session or message.scope+':shared' != scope or message.group_id or not message.shared:
            raise MemoryAccessDenied('console_save_scope_changed')
        if channel == 'qq' and not self.admitted(message):
            raise MemoryAccessDenied('console_save_source_not_admitted')
        if kind == 'select_reference':
            options = payload['options']
            if not 1 <= len(options) <= 4:
                raise ValueError('console_selection_capacity')
            data = {'options': options}
            plans = [self.r.plan(message, session, o) for o in options]
            state = 'select'
        else:
            plans = payload['plans'] if kind == 'save_reference_set' else [payload['plan']]
            data = {'plans': plans}
            state = 'pending'
        if kind=='ignore_candidate':data['requested_action']='ignore'
        if not 1 <= len(plans) <= 4 or any(len(p['content']) > 12000 for p in plans) or len({p['content'] for p in plans}) != len(plans):
            raise ValueError('console_save_capacity_or_duplicates')
        if _contains_secret(message.text) or any(_contains_secret(p['content']) for p in plans):
            raise ValueError('console_sensitive_content')
        now = time.time()
        row = dict(id=fingerprint(['console_save', message.key, kind])[:32] if channel == 'qq' else uuid4().hex,
                   scope=scope, session=session, source_key=source_key or message.key, channel=channel,
                   revision=1, state=state, created=now, expires=min(now+600, *[p['expires'] for p in plans]),
                   settings_revision=self.b.controls()['revision'], epoch=self.kb.memory_use.snapshot(),
                   message=json.dumps(self.message_data(message), ensure_ascii=False),
                   payload=json.dumps(data, ensure_ascii=False), result='{}')
        with self.kb._memory_write_lock:
            self.sweep()
            if not self.valid(row, data):
                raise ValueError('console_save_source_changed')
            with self.b.store.connect() as db:
                db.execute('begin immediate')
                if channel == 'local_candidate':
                    old = db.execute(f"select id from {TABLE} where scope=? and source_key=? and state not in ('expired','invalid','rejected') order by created desc limit 1", (scope, row['source_key'])).fetchone()
                else:
                    old = db.execute(f'select id from {TABLE} where id=?', (row['id'],)).fetchone()
                if old:
                    return old['id']
                if db.execute(f'select count(*) from {TABLE}').fetchone()[0] >= 2048:
                    raise ValueError('console_save_queue_full')
                keys = list(row)
                db.execute(f"insert into {TABLE}({','.join(keys)}) values({','.join('?' for _ in keys)})", tuple(row.values()))
            if self.b.action_proposals:
                self.b.action_proposals.supersede(scope, session)
        return row['id']

    def offer(self, message, session, kind, payload):
        self.enqueue(message, session, kind, payload)
        return ConsoleNotice('我把这次要保存的内容放到记忆操作台了，你在那里核对后再决定。')

    def offer_ignore(self,message,session):
        s=self.b.settings
        if not (s.enabled and s.shared_memory and message.bot_id==s.bot_id and message.user_id in s.owner_ids
                and message.shared and not message.group_id and message.kind=='message'
                and not message.reply_to and not message.forward_present and not message.media_types and not getattr(message,'quote_context',None)):
            return None
        if (message.text.split() or [''])[0]!='/memory-ignore':return None
        match=re.fullmatch(r'/memory-ignore\s+([A-Za-z0-9_.:-]{1,128})',message.text.strip())
        if not match:return ConsoleNotice('请在后台操作台的旧候选页核对具体条目后选择忽略；这里没有修改。')
        if not self.admitted(message):raise MemoryAccessDenied('console_ignore_original_ingress_required')
        with self.kb._memory_write_lock:
            option=self.r.candidate_option(message,session,match[1],external=True)
            if not option:return ConsoleNotice('这条候选已处理或来源不可用，请在后台重新读取；没有修改。')
            self.enqueue(message,session,'ignore_candidate',{'plan':self.r.plan(message,session,option)})
        return ConsoleNotice('忽略这条候选的请求已放进后台操作台，请在那里核对原文后决定；现在还没有忽略。')

    def hint(self, message, session):
        if message.text.strip().rstrip('。！!') not in {'记住', '记住吧', '保存吧', '确认保存', '同意保存', '记下来'}:
            return None
        self.actor(message.user_id)
        with self.b.store.connect() as db:
            row = db.execute(f'select state,payload from {TABLE} where scope=? and session=? order by created desc limit 1', (message.scope+':shared', session)).fetchone()
        if not row:
            return None
        if row['state']=='ignored':return ConsoleNotice('这条候选已经按原请求忽略，可以在操作台核对；没有新增长期记忆。')
        if json.loads(row['payload']).get('requested_action')=='ignore':
            return ConsoleNotice('这次待办是忽略旧候选，请在后台核对后选择忽略或取消；这里不会将它改为保存。')
        if row['state'] in {'done', 'partial', 'unknown', 'saving'}:
            return ConsoleNotice('这次保存已经处理或正在核对，可以在记忆操作台查看原结果，不会再次新增。')
        return ConsoleNotice('请在记忆操作台核对这次的完整内容后确认，这里先不重复保存。')

    def literal(self, message, session, body, epoch, revision, *, contextual=True):
        if epoch != self.kb.memory_use.snapshot() or revision != self.b.controls()['revision']:
            raise ValueError('console_literal_changed')
        content = '用户明确选择保存的陈述：「'+body+'」' if contextual else body
        pin = not contextual and any(w in message.text for w in self.kb._EXPLICIT_SAVE_PIN_WORDS)
        plan = {'literal': True, 'scope': message.scope+':shared', 'session': session, 'epoch': epoch,
                'expires': time.time()+600, 'content': content, 'author': '用户', 'pin': pin,
                'sources': [{'request_event': message.key, 'text': message.text, 'expires': time.time()+600}]}
        return self.offer(message, session, 'literal', {'plan': plan})

    def covered_events(self, row, plans):
        """Exact complete user-source coverage only; no semantic matching."""
        with self.b.store.connect() as db:
            if not db.execute("select 1 from sqlite_master where name='qq_daily_entries'").fetchone():return [[] for _ in plans]
            daily = [s for r in db.execute("select sources from qq_daily_entries where bot=? and subject=? and kind='daily' and state in ('open','pending')",
                    (self.b.settings.bot_id, row['scope'].split(':')[2])) for s in json.loads(r['sources'])]
        from .qq_memory_reference_dialogue import fingerprint as history_hash
        raw = {history_hash(r):r for r in self.r.raw(row['session']) if r.get('role')=='user'}
        covered=[]
        for plan in plans:
            events=set()
            if not plan.get('fragment'):
                for source in plan['sources']:
                    original=raw.get(source.get('hash')) if plan.get('author')=='用户' else None
                    if original:
                        events.update(s['event'] for s in daily if s['session']==row['session'] and s['text']==original.get('content'))
                    if source.get('candidate'):
                        candidate=self.kb._find_memory_candidate(source['candidate']) or {}
                        origin=candidate.get('origin') or {}
                        if origin.get('channel')=='qq' and origin.get('bot_id')==self.b.settings.bot_id:
                            events.update(s['event'] for s in daily if s['subject']==origin.get('verified_subject') and s['message_id']==origin.get('message_id')
                                          and s['text']==candidate.get('content'))
            covered.append(sorted(events))
        return covered

    def pending_covering(self, subject, events):
        scope=f'{self.b.settings.bot_id}:private:{subject}:shared'
        with self.b.store.connect() as db:
            rows=[dict(r) for r in db.execute(f"select * from {TABLE} where scope=? and state in ('pending','select','saving','unknown')",(scope,))]
        waiting=[]
        for row in rows:
            if json.loads(row['payload']).get('requested_action')=='ignore':continue
            result=json.loads(row['result'])
            if row['state'] in {'saving','unknown'}:
                covered=result.get('uncertain_events',sum(result.get('coverage_by_item',[]),[]))
            elif row['expires']>time.time() and self.valid(row):
                payload=json.loads(row['payload']);plans=payload.get('plans')
                if plans is None:
                    message=SimpleNamespace(**json.loads(row['message']))
                    plans=[self.r.plan(message,row['session'],o) for o in payload.get('options',[])]
                covered=sum(self.covered_events(row,plans),[])
            else:covered=[]
            if events.intersection(covered):waiting.append(row['id'])
        return waiting

    def listing(self, owner):
        self.actor(owner); self.sweep()
        scope = f'{self.b.settings.bot_id}:private:{owner}:shared'
        with self.b.store.connect() as db:
            rows = [dict(r) for r in db.execute(f'select * from {TABLE} where scope=? order by created desc limit 150', (scope,))]
        return {'entries': [self.public(r) for r in rows]}

    @staticmethod
    def public(row):
        payload = json.loads(row['payload'])
        plans, options = payload.get('plans', []), payload.get('options', [])
        title = (plans[0]['content'] if plans else options[0]['text'] if options else '')[:120]
        if payload.get('requested_action')=='ignore':title='忽略候选 · '+title
        return {k: row[k] for k in ('id', 'state', 'created', 'expires', 'channel')} | {'title': title, 'count': len(plans or options), 'result': json.loads(row['result']),
            'requested_action':payload.get('requested_action','save')}

    def row(self, owner, identifier):
        self.actor(owner)
        scope = f'{self.b.settings.bot_id}:private:{owner}:shared'
        with self.b.store.connect() as db:
            row = db.execute(f'select * from {TABLE} where id=? and scope=?', (identifier, scope)).fetchone()
        if not row:
            raise LookupError('console_save_unavailable')
        return dict(row)

    def preview(self, owner, identifier):
        self.sweep()
        row = self.row(owner, identifier)
        payload = json.loads(row['payload'])
        return self.public(row) | {'preview_version': fingerprint(row), 'plans': payload.get('plans', []),
                                  'options': payload.get('options', []), 'subject': owner}

    def select(self, owner, identifier, version, indices):
        if not isinstance(indices, list) or not 1 <= len(indices) <= 4 or any(type(i) is not int for i in indices) or len(set(indices)) != len(indices):
            raise ValueError('console_selection_invalid')
        with self.kb._memory_write_lock:
            self.sweep(); row = self.row(owner, identifier)
            if row['state'] != 'select' or fingerprint(row) != version or not self.valid(row):
                raise ValueError('console_selection_changed')
            options = json.loads(row['payload'])['options']
            if any(i < 0 or i >= len(options) for i in indices):
                raise ValueError('console_selection_invalid')
            message = SimpleNamespace(**json.loads(row['message']))
            plans = [self.r.plan(message, row['session'], options[i]) for i in indices]
            if len({p['content'] for p in plans}) != len(plans):
                raise ValueError('console_duplicate_content')
            with self.b.store.connect() as db:
                db.execute(f"update {TABLE} set payload=?,state='pending',revision=revision+1 where id=?", (json.dumps({'plans': plans}, ensure_ascii=False), identifier))
            return self.preview(owner, identifier)

    def candidate(self, owner, candidate_id, version):
        actor, scope, session = self.context(owner)
        # Local actor envelope only. It is never ingested as a QQ message or
        # used to create memory origin; the original candidate writer keeps it.
        message = SimpleNamespace(key='', scope=scope.removesuffix(':shared'), bot_id=actor.bot_id,
                                  user_id=owner, group_id='', message_id='', text='本机主人选择候选', shared=True)
        option = self.r.candidate_option(message, session, candidate_id, version, external=True)
        if not option:
            raise ValueError('console_candidate_unavailable')
        plan = self.r.plan(message, session, option)
        identifier = self.enqueue(message, session, 'save_reference', {'plan': plan}, channel='local_candidate',
                                  source_key=fingerprint([candidate_id, version]))
        return self.preview(owner, identifier)

    def decide(self, owner, identifier, version, action):
        if action not in {'approve', 'reject', 'ignore'}:
            raise ValueError('console_save_action')
        with self.kb._memory_write_lock:
            self.sweep(); row = self.row(owner, identifier)
            if row['state'] in {'done', 'partial', 'unknown', 'saving', 'ignored'}:
                return self.public(row)
            if row['state'] not in {'pending', 'select'} or fingerprint(row) != version or not self.valid(row):
                raise ValueError('console_save_preview_changed')
            if action == 'reject':
                with self.b.store.connect() as db:
                    db.execute(f"update {TABLE} set state='rejected',message='{{}}',payload='{{}}',revision=revision+1 where id=?", (identifier,))
                return {'id': identifier, 'state': 'rejected'}
            if row['state'] != 'pending':
                raise ValueError('console_save_selection_required')
            message = SimpleNamespace(**json.loads(row['message']))
            payload=json.loads(row['payload']);plans=payload['plans']
            if payload.get('requested_action')=='ignore' and action=='approve':raise ValueError('console_ignore_only')
            if action == 'ignore' and (row['channel'] != 'local_candidate' and payload.get('requested_action')!='ignore' or len(plans) != 1 or not plans[0]['sources'][0].get('candidate')):
                raise ValueError('console_ignore_requires_candidate')
            coverage=[[] for _ in plans] if action=='ignore' else self.covered_events(row,plans)
            with self.b.store.connect() as db:
                db.execute(f"update {TABLE} set state='saving',result=? where id=?", (json.dumps({'operation_id':identifier,'coverage_by_item':coverage}),identifier))
            def verify(db):
                current = db.execute(f'select state,payload from {TABLE} where id=? and scope=?', (identifier, row['scope'])).fetchone()
                return bool(current and current['state'] == 'saving' and current['payload'] == row['payload'])
            def write(plan):
                if action == 'ignore':
                    source = plan['sources'][0]
                    def authorize():
                        if not self.valid(row, only_plan=plan):raise MemoryAccessDenied('console_candidate_changed')
                    ignored = self.kb.ignore_memory_candidate(source['candidate'], expected_version=source['version'], write_check=authorize)
                    return {'state':'not_saved', 'candidate_id':source['candidate'], 'candidate_ignored':bool(ignored)}
                return self.r.capture(message, row['session'], plan['content'], pin=plan.get('pin', False)) if plan.get('literal') else self.r._write_reference(message, plan)
            try:
                result = self.r.saves.run(identifier, row['scope'], row['session'], 'console_save', plans,
                    verify=verify, valid=lambda plan: self.valid(row, only_plan=plan),
                    write=write,
                    retention=self.b.settings.retention_days*86400)
                outcome = {'operation_id': result.id, 'items': result.items}
                committed_events=[event for events,item in zip(coverage,result.items) if item['state']=='committed' for event in events]
                outcome['uncertain_events']=[event for events,item in zip(coverage,result.items) if item['state']=='unknown' for event in events]
                daily=getattr(self.b,'daily_memory',None)
                if committed_events and daily:daily.exclude_confirmed_events(committed_events)
                # Maintain the original recently-saved index, without sending
                # a QQ receipt or changing an already committed journal result.
                try:self.r.receipt(self.actor(owner), row['session'], result)
                except Exception:outcome['index_pending'] = True
                state = 'done' if result.committed else 'unknown' if any(i['state'] in {'unknown', 'pending'} for i in result.items) else 'partial'
                if action == 'ignore' and result.items[0].get('candidate_ignored'):state = 'ignored'
            except BaseException:
                with self.b.store.connect() as db:
                    db.execute(f"update {TABLE} set state='unknown',message='{{}}',payload='{{}}' where id=?", (identifier,))
                raise
            with self.b.store.connect() as db:
                db.execute(f"update {TABLE} set state=?,result=?,message='{{}}',payload='{{}}',revision=revision+1 where id=?", (state, json.dumps(outcome), identifier))
            return {'id': identifier, 'state': state, 'result': outcome}
