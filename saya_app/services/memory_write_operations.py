"""Shared durable identity and results for host-authorized memory writes."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import sqlite3
import threading
import hashlib
import json
import time

from .qq_tasks import TaskCancelled, checkpoint_current


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


@dataclass(frozen=True)
class SaveResult:
    id: str
    items: list
    fresh: bool = False

    @property
    def committed(self):
        return bool(self.items) and all(item['state'] == 'committed' for item in self.items)


_ACTIVE_OPERATION = ContextVar('active_memory_write_operation', default=None)


def operation_active():
    marker = _ACTIVE_OPERATION.get()
    return bool(marker and marker['active'] and marker['thread'] == threading.get_ident())


class MemoryOperationStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()


class MemoryWriteOperations:
    def __init__(self, store, lock, *, table='memory_write_operations', clock=time.time):
        if table not in {'memory_write_operations', 'qq_chat_save_operations'}:
            raise ValueError('unsupported_operation_table')
        self.store, self.lock, self.clock, self.table = store, lock, clock, table
        with store.connect() as db:
            db.execute(f'''create table if not exists {self.table}(
                id text primary key,scope text not null,session text not null,kind text not null,
                fingerprint text not null,expires real not null,result text not null)''')

    def lookup(self, scope, session, operation, *, kind=None):
        with self.store.connect() as db:
            row = db.execute(f"select result from {self.table} where id=? and scope=? and session=? and (expires>? or kind in ('listener','game_archive','game_task','dream_theme','dream_diary','learner_batch','learner_item')) "
                             'and (? is null or kind=?)',
                             (operation, scope, session, self.clock(),kind,kind)).fetchone()
        return SaveResult(operation, json.loads(row['result'])) if row else None

    def lookup_plan(self, scope, session, kind, plans):
        with self.store.connect() as db:
            row = db.execute(f'select id,result from {self.table} where scope=? and session=? and kind=? '
                             "and fingerprint=? and expires>? and json_extract(result,'$[0].state')!='invalid' order by rowid limit 1",
                             (scope,session,kind,fingerprint(plans),self.clock())).fetchone()
        return SaveResult(row['id'],json.loads(row['result'])) if row else None

    def run(self, operation, scope, session, kind, plans, *, verify, valid, write, retention):
        identity = fingerprint(plans)
        items = [{'state': 'pending'} for _ in plans]
        with self.lock, self.store.connect() as db:
            db.execute('begin immediate')
            db.execute(f"delete from {self.table} where expires<=? and kind not in ('listener','game_archive','game_task','dream_theme','dream_diary','learner_batch','learner_item')", (self.clock(),))
            row = db.execute(f'select * from {self.table} where id=?', (operation,)).fetchone()
            if row:
                if (row['scope'], row['session'], row['kind'], row['fingerprint']) != (scope, session, kind, identity):
                    raise ValueError('save_operation_mismatch')
                return SaveResult(operation, json.loads(row['result']))
            if not verify(db) or db.execute(f'select count(*) from {self.table}').fetchone()[0] >= 8192:
                raise ValueError('save_operation_unavailable')
            db.execute(f'insert into {self.table} values(?,?,?,?,?,?,?)',
                       (operation, scope, session, kind, identity, self.clock() + retention, json.dumps(items)))

        with self.lock:
            if not all(valid(plan) for plan in plans):
                items = [{'state': 'invalid'} for _ in plans]
                self._record(operation, items)
                return SaveResult(operation, items, fresh=True)
            for index, plan in enumerate(plans):
                checkpoint_current()
                if not valid(plan):
                    items[index] = {'state': 'invalid'}
                    self._record(operation, items)
                    break
                # If the process stops before storage returns, this item stays
                # uncertain. No caller is permitted to claim the operation again.
                items[index] = {'state': 'unknown'}
                self._record(operation, items)
                marker = {'active': True, 'thread': threading.get_ident()}
                token = _ACTIVE_OPERATION.set(marker)
                try:
                    outcome = write(plan)
                except TaskCancelled:
                    raise
                except Exception:
                    break
                finally:
                    marker['active'] = False
                    _ACTIVE_OPERATION.reset(token)
                items[index] = outcome
                self._record(operation, items)
                if outcome['state'] != 'committed':
                    break
        return SaveResult(operation, items, fresh=True)

    def _record(self, operation, items):
        with self.store.connect() as db:
            db.execute(f'update {self.table} set result=? where id=?', (json.dumps(items), operation))

    def reconcile(self, scope, session, operation, *, kind, recover):
        """Reconcile a recorded side effect without claiming a new write."""
        with self.lock:
            result=self.lookup(scope,session,operation,kind=kind)
            if result is None:return None
            items=recover(result.items)
            if items!=result.items:self._record(operation,items)
            return SaveResult(operation,items)

    def finish_prepared(self, scope, session, operation, *, kind, valid, write):
        """Only the owner of a fresh prepared operation calls this after work."""
        with self.lock:
            result = self.lookup(scope, session, operation, kind=kind)
            if result is None or result.items != [{'state': 'pending'}]:
                return result
            if not valid():
                items = [{'state': 'invalid'}]
            else:
                checkpoint_current()
                items = [{'state': 'unknown'}]
                self._record(operation, items)
                try:
                    items = [write()]
                except TaskCancelled:
                    raise
                except Exception:
                    return SaveResult(operation, items, fresh=True)
            self._record(operation, items)
            return SaveResult(operation, items, fresh=True)
