"""QQ-04 durable task contract, not yet wired into the live bridge.

Callers must authenticate first and supply the complete bot/user/group scope.
No prompt, provider output, credentials, or transport action is stored here.
Cancellation prevents new work; already reserved effects must be reconciled.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import re
import time
import threading
from functools import wraps
from uuid import uuid4
from .memory_use import checkpoint_use_current


TERMINAL = frozenset({'completed', 'cancelled', 'failed', 'interrupted','suppressed'})
ACTIVE = frozenset({'running', 'cancel_requested'})
PHASES = frozenset({'received', 'queued', 'memory', 'search', 'sources',
                    'generating', 'sending'})
ERRORS = frozenset({'', 'model_timeout', 'search_unconfigured', 'model_unconfigured',
                    'login_required', 'generation_failed', 'delivery_unknown',
                    'authorization_changed', 'restart_interrupted'})
ERRORS = ERRORS | {'task_expired'}
EFFECTS = frozenset({'memory', 'send', 'read', 'history'})


class TaskCancelled(Exception):
    """The caller must discard its result and stop starting side effects."""


class TaskCapacity(Exception):
    pass


_CURRENT_GUARD = ContextVar('qq_request_guard', default=None)
_MEMORY_COMMIT = ContextVar('qq_memory_commit', default=None)


def current_guard():
    return _CURRENT_GUARD.get()


@contextmanager
def bound_guard(guard):
    token = _CURRENT_GUARD.set(guard)
    try:
        yield guard
    finally:
        _CURRENT_GUARD.reset(token)


def phase_current(phase):
    guard = current_guard()
    if guard is not None:
        guard.checkpoint()
        guard.registry.set_phase(guard.task_id,guard.scope,phase)


def guarded_history_write(method):
    @wraps(method)
    def wrapped(*args,**kwargs):
        checkpoint_use_current()
        guard = current_guard()
        if guard is None:
            return method(*args,**kwargs)
        with guard.effect('history') as effect:
            result = method(*args,**kwargs)
            effect.failed() if result is False else effect.committed()
            return result
    return wrapped


def checkpoint_current():
    """Web/unbound requests keep their existing behavior."""
    checkpoint_use_current()
    guard = current_guard()
    if guard is not None:
        guard.checkpoint()


def guarded_memory_write(method):
    """Guard a synchronous logical mutation, including its nested projections.

    A reserved mutation may settle after cancellation; its compatibility writes
    must settle with it. A marker is invalidated on exit so copied contexts can
    never authorize later independent operations.
    """
    @wraps(method)
    def wrapped(*args, **kwargs):
        guard = current_guard()
        parent = _MEMORY_COMMIT.get()
        if not (parent is not None and parent['active'] and parent['thread'] == threading.get_ident()):
            checkpoint_use_current()
        if guard is None:
            return method(*args, **kwargs)
        if (parent is not None and parent['active'] and parent['guard'] is guard
                and parent['thread'] == threading.get_ident()):
            result = method(*args, **kwargs)
            if _memory_write_confirmed(result):
                parent['effect'].committed()
            return result
        with guard.effect('memory') as effect:
            marker = {'guard': guard, 'active': True, 'thread': threading.get_ident(), 'effect': effect}
            token = _MEMORY_COMMIT.set(marker)
            try:
                result = method(*args, **kwargs)
                confirmed = _memory_write_confirmed(result)
                if confirmed:
                    effect.committed()
                elif effect.outcome != 'committed' and (result is False or (isinstance(result, dict) and result.get('status') in {'no_op','unchanged','rejected','disabled'})):
                    effect.failed()
                # An ambiguous/partial failure stays unknown, never rolled back.
                return result
            finally:
                marker['active'] = False
                _MEMORY_COMMIT.reset(token)
    return wrapped


def _memory_write_confirmed(result):
    # V4 returns the existing object even for a confirmed no-op. New evidence
    # can still be committed without changing the item's logical revision.
    if isinstance(result, dict) and result.get('status') == 'unchanged':
        return bool(result.get('evidence_added') or result.get('committed'))
    if isinstance(result,dict) and result.get('status')=='success' and type(result.get('deleted_count')) is int and result['deleted_count']>0:
        return True
    return (result is True or bool(getattr(result, 'id', None))
        or (isinstance(result, dict) and bool(result.get('id') or result.get('memory')
            or result.get('committed') or result.get('candidates') or result.get('candidate'))))


def guarded_store_write(method):
    """Wait for the store lock before reserving the actual mutation."""
    guarded = guarded_memory_write(method)
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if current_guard() is None:
            return method(self, *args, **kwargs)
        with self._lock:
            return guarded(self, *args, **kwargs)
    return wrapped


@contextmanager
def effect_current(kind, *, scope=None):
    guard = current_guard()
    if guard is None:
        yield _EffectResult()
        return
    if scope is not None and scope != guard.scope:
        raise KeyError('effect scope does not match task')
    with guard.effect(kind) as effect:
        yield effect


class QQTaskGuard:
    """A request-local guard copied by asyncio.to_thread, never a global stop."""
    def __init__(self, registry, task_id, scope):
        self.registry, self.task_id, self.scope = registry, task_id, scope
        self.result_ids = set()
        self.permission_check = None
        self.timed_out = False

    @contextmanager
    def bind(self):
        token = _CURRENT_GUARD.set(self)
        try:
            yield self
        finally:
            _CURRENT_GUARD.reset(token)

    def checkpoint(self):
        if self.permission_check is not None and not self.permission_check():
            raise TaskCancelled('task permission changed')
        return self.registry.checkpoint(self.task_id, self.scope)

    @contextmanager
    def effect(self, kind):
        self.checkpoint()
        operation = self.registry.begin_effect(self.task_id, self.scope, kind)
        result = _EffectResult()
        try:
            yield result
        finally:
            # Missing/failed confirmation is unknown, never implicit success.
            self.registry.end_effect(self.task_id, self.scope, operation, result.outcome)


class _EffectResult:
    def __init__(self):
        self.outcome = 'unknown'

    def committed(self):
        if self.outcome == 'failed':
            raise ValueError('effect already failed')
        self.outcome = 'committed'

    def failed(self):
        if self.outcome == 'committed':
            raise ValueError('confirmed effect cannot be reversed')
        self.outcome = 'failed'


class QQTaskRegistry:
    def __init__(self, store):
        self.store = store
        with store.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS qq_tasks (
                    id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    scope TEXT NOT NULL, config_revision TEXT NOT NULL,
                    state TEXT NOT NULL, phase TEXT NOT NULL,
                    created REAL NOT NULL, updated REAL NOT NULL,
                    is_group INTEGER NOT NULL, progress_at REAL,
                    effects TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS qq_tasks_scope ON qq_tasks(scope,state);
                CREATE TABLE IF NOT EXISTS qq_task_results (
                    task_id TEXT NOT NULL,result_id TEXT NOT NULL,
                    PRIMARY KEY(task_id,result_id));
            ''')

    @contextmanager
    def _transaction(self):
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            yield db

    @staticmethod
    def _view(row):
        value = dict(row)
        value['effects'] = json.loads(value['effects'])
        value['is_group'] = bool(value['is_group'])
        return value

    @staticmethod
    def _row(db, task_id, scope):
        row = db.execute('SELECT * FROM qq_tasks WHERE id=? AND scope=?',
                         (task_id, scope)).fetchone()
        if row is None:
            raise KeyError('task unavailable in this scope')
        return row

    def get(self, task_id, scope):
        with self.store.connect() as db:
            return self._view(self._row(db, task_id, scope))

    def claim(self, task_id, scope):
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            if row['state'] != 'queued':
                return None
            if db.execute("SELECT 1 FROM qq_tasks WHERE scope=? AND state IN ('running','cancel_requested')",(scope,)).fetchone():
                return None
            db.execute("UPDATE qq_tasks SET state='running',phase='generating',updated=? WHERE id=?", (time.time(),task_id))
            return self._view(self._row(db,task_id,scope))

    def prune(self, retention_days=7):
        with self._transaction() as db:
            db.execute("DELETE FROM qq_tasks WHERE state IN ('completed','cancelled','failed','interrupted','suppressed') AND updated<?", (time.time()-retention_days*86400,))
            db.execute("DELETE FROM qq_tasks WHERE id IN (SELECT id FROM qq_tasks WHERE state IN ('completed','cancelled','failed','interrupted','suppressed') ORDER BY updated DESC LIMIT -1 OFFSET 2048)")
            db.execute('DELETE FROM qq_task_results WHERE task_id NOT IN (SELECT id FROM qq_tasks)')

    @staticmethod
    def _freeze_results(db,task_id):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_delivery_stops'").fetchone():
            return
        db.execute("INSERT OR IGNORE INTO qq_delivery_stops SELECT result_id,'cancelled' FROM qq_task_results WHERE task_id=?",(task_id,))
        db.execute("UPDATE qq_delivery_parts SET state='cancelled' WHERE state='pending' AND result_id IN (SELECT result_id FROM qq_task_results WHERE task_id=?)",(task_id,))

    def attach_result(self,task_id,scope,result_id):
        with self._transaction() as db:
            row=self._row(db,task_id,scope)
            if not db.execute('SELECT 1 FROM qq_deliveries WHERE id=? AND scope=?',(result_id,scope)).fetchone():
                return
            db.execute('INSERT OR IGNORE INTO qq_task_results VALUES(?,?)',(task_id,result_id))
            if row['state'] in {'cancel_requested','cancelled','interrupted'}:
                self._freeze_results(db,task_id)

    def list_scope(self, scope, *, limit=8):
        if type(limit) is not int or not 1 <= limit <= 32:
            raise ValueError('invalid task list limit')
        with self.store.connect() as db:
            rows = db.execute('''SELECT * FROM qq_tasks WHERE scope=?
                ORDER BY CASE WHEN state IN ('running','cancel_requested') THEN 0
                    WHEN state='queued' THEN 1 ELSE 2 END,
                    CASE WHEN state='queued' THEN rowid ELSE -rowid END LIMIT ?''',
                (scope, limit)).fetchall()
            return [self._view(row) for row in rows]

    def list_active(self):
        with self.store.connect() as db:
            return [self._view(row) for row in db.execute("SELECT * FROM qq_tasks WHERE state IN ('running','cancel_requested')")]

    def reconcile_terminal_inbox(self,task_id,scope):
        """Only the owning scheduler may call this after its worker has exited.

        A terminal inbox and settled effects are both required; unknown effects
        stay unknown, and confirmed writes/sends are never undone or replayed.
        """
        with self._transaction() as db:
            row=self._row(db,task_id,scope)
            if row['state'] not in ACTIVE:return False
            if any(e['outcome']=='in_flight' for e in json.loads(row['effects']).values()):return False
            inbox=db.execute('select state from inbox where event_key=?',(row['event_key'],)).fetchone()
            terminal={'sent':('completed',''),'processed':('completed',''),'suppressed':('suppressed',''),
                'failed':('failed','generation_failed'),'delivery_unknown':('failed','delivery_unknown'),
                'expired':('failed','task_expired'),'interrupted':('interrupted','restart_interrupted'),
                'cancelled':('cancelled','restart_interrupted')}
            if not inbox or inbox[0] not in terminal:return False
            state,error=terminal[inbox[0]]
            if row['state']=='cancel_requested':state='cancelled'
            if state!='completed':self._freeze_results(db,task_id)
            db.execute('update qq_tasks set state=?,error=?,updated=? where id=?',
                (state,error,time.time(),task_id))
            return True

    def list_live(self):
        with self.store.connect() as db:
            return [self._view(row) for row in db.execute("SELECT * FROM qq_tasks WHERE state IN ('queued','running','cancel_requested') ORDER BY rowid")]

    def set_phase(self,task_id,scope,phase):
        if phase not in PHASES:
            raise ValueError('invalid public phase')
        with self._transaction() as db:
            self._row(db,task_id,scope)
            db.execute("UPDATE qq_tasks SET phase=? WHERE id=? AND state='running'",(phase,task_id))

    def register(self, event_key, scope, config_revision, *, is_group=False, max_pending=32):
        if (not isinstance(event_key, str) or not 1 <= len(event_key) <= 512
                or not isinstance(scope, str) or not 1 <= len(scope) <= 512
                or not re.fullmatch(r'[0-9a-f]{64}', config_revision)
                or type(is_group) is not bool or type(max_pending) is not int
                or not 1 <= max_pending <= 256):
            raise ValueError('invalid task registration')
        with self._transaction() as db:
            existing = db.execute('SELECT * FROM qq_tasks WHERE event_key=?', (event_key,)).fetchone()
            if existing is not None:
                if existing['scope'] != scope:
                    raise KeyError('task unavailable in this scope')
                return self._view(existing)
            count = db.execute("SELECT count(*) FROM qq_tasks WHERE state IN ('queued','running','cancel_requested')").fetchone()[0]
            if count >= max_pending:
                raise TaskCapacity('task queue full')
            task_id, now = uuid4().hex, time.time()
            db.execute("INSERT INTO qq_tasks VALUES(?,?,?,?,'queued','queued',?,?,?,NULL,'{}','')",
                       (task_id, event_key, scope, config_revision, now, now, int(is_group)))
            return self._view(self._row(db, task_id, scope))

    def claim_next(self, *, max_active=1):
        if type(max_active) is not int or not 1 <= max_active <= 8:
            raise ValueError('invalid concurrency limit')
        with self._transaction() as db:
            if db.execute("SELECT count(*) FROM qq_tasks WHERE state IN ('running','cancel_requested')").fetchone()[0] >= max_active:
                return None
            row = db.execute('''SELECT q.* FROM qq_tasks q WHERE q.state='queued'
                AND NOT EXISTS (SELECT 1 FROM qq_tasks a WHERE a.scope=q.scope
                    AND a.state IN ('running','cancel_requested')) ORDER BY q.rowid LIMIT 1''').fetchone()
            if row is None:
                return None
            db.execute("UPDATE qq_tasks SET state='running',phase='generating',updated=? WHERE id=?",
                       (time.time(), row['id']))
            return self._view(self._row(db, row['id'], row['scope']))

    def checkpoint(self, task_id, scope):
        value = self.get(task_id, scope)
        if value['state'] != 'running':
            raise TaskCancelled('task no longer running')
        return value

    def cancel(self, task_id, scope):
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            if row['state'] in TERMINAL or row['state'] == 'cancel_requested':
                return self._view(row)
            state = 'cancelled' if row['state'] == 'queued' else 'cancel_requested'
            db.execute('UPDATE qq_tasks SET state=?,updated=? WHERE id=?',
                       (state, time.time(), task_id))
            self._freeze_results(db,task_id)
            if state == 'cancelled':
                columns = {r[1] for r in db.execute('PRAGMA table_info(inbox)')}
                if {'event_key','payload','state'}.issubset(columns):
                    db.execute("UPDATE inbox SET state='cancelled',payload='{}' WHERE event_key=? AND state='queued'", (row['event_key'],))
            return self._view(self._row(db, task_id, scope))

    def begin_effect(self, task_id, scope, kind):
        """Reserve immediately before a local commit/send, never around generation.

        Reservation releases the DB lock before I/O. Cancellation can therefore
        acknowledge immediately, but cannot promise to undo an in-flight effect.
        """
        if kind not in EFFECTS:
            raise ValueError('invalid effect kind')
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            if row['state'] != 'running':
                raise TaskCancelled('task no longer running')
            effects = json.loads(row['effects'])
            operation = uuid4().hex
            effects[operation] = {'kind': kind, 'outcome': 'in_flight'}
            db.execute('UPDATE qq_tasks SET effects=?,updated=? WHERE id=?',
                       (json.dumps(effects), time.time(), task_id))
            return operation

    def end_effect(self, task_id, scope, operation, outcome):
        if outcome not in {'committed', 'unknown', 'failed'}:
            raise ValueError('invalid effect outcome')
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            effects = json.loads(row['effects'])
            effect = effects.get(operation)
            if effect is None:
                raise KeyError('effect unavailable')
            if effect['outcome'] != 'in_flight':
                if effect['outcome'] != outcome:
                    raise ValueError('effect already reconciled')
                return self._view(row)
            effect['outcome'] = outcome
            db.execute('UPDATE qq_tasks SET effects=?,updated=? WHERE id=?',
                       (json.dumps(effects), time.time(), task_id))
            return self._view(self._row(db, task_id, scope))

    def finish(self, task_id, scope, state, *, error=''):
        if state not in TERMINAL or error not in ERRORS:
            raise ValueError('invalid terminal result')
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            if row['state'] in TERMINAL:
                return self._view(row)
            if row['state'] == 'queued' and state == 'completed':
                raise ValueError('queued task has not run')
            if any(e['outcome'] == 'in_flight' for e in json.loads(row['effects']).values()):
                raise RuntimeError('effects require reconciliation')
            if row['state'] == 'cancel_requested':
                state = 'cancelled'
            db.execute('UPDATE qq_tasks SET state=?,error=?,updated=? WHERE id=?',
                       (state, error, time.time(), task_id))
            if state in {'cancelled','interrupted','failed'}:
                columns={r[1] for r in db.execute('PRAGMA table_info(inbox)')}
                if {'event_key','payload','state'}.issubset(columns):
                    db.execute("UPDATE inbox SET state=CASE WHEN state='sending' THEN 'delivery_unknown' ELSE ? END,payload='{}' WHERE event_key=? AND state IN ('queued','generating','sending')",(state,row['event_key']))
            return self._view(self._row(db, task_id, scope))

    def progress(self, task_id, scope, phase, *, now=None):
        if phase not in PHASES:
            raise ValueError('invalid public phase')
        now = time.time() if now is None else now
        with self._transaction() as db:
            row = self._row(db, task_id, scope)
            if row['state'] != 'running':
                return False
            interval = 10 if row['is_group'] else 2
            emit = row['progress_at'] is None or now - row['progress_at'] >= interval
            db.execute('UPDATE qq_tasks SET phase=?,progress_at=?,updated=? WHERE id=?',
                       (phase, now if emit else row['progress_at'], time.time(), task_id))
            return emit

    def recover(self):
        """Explicit startup call, after acquiring the existing consumer lease.

        Never auto-run in __init__: another registry instance may inspect a live
        worker. Interrupted work stays terminal and cannot silently be replayed.
        """
        with self._transaction() as db:
            rows = db.execute("SELECT * FROM qq_tasks WHERE state IN ('running','cancel_requested')").fetchall()
            for row in rows:
                if row['state']=='cancel_requested':
                    self._freeze_results(db,row['id'])
                effects = json.loads(row['effects'])
                for effect in effects.values():
                    if effect['outcome'] == 'in_flight':
                        effect['outcome'] = 'unknown'
                db.execute("UPDATE qq_tasks SET state='interrupted',error='restart_interrupted',effects=?,updated=? WHERE id=?",
                           (json.dumps(effects), time.time(), row['id']))
                columns = {r[1] for r in db.execute('PRAGMA table_info(inbox)')}
                if {'event_key','state','payload'}.issubset(columns):
                    db.execute("UPDATE inbox SET state=CASE WHEN state='sending' THEN 'delivery_unknown' ELSE 'interrupted' END,payload='{}' WHERE event_key=? AND state IN ('queued','generating','sending')",(row['event_key'],))
            return len(rows)
