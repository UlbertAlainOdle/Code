"""Atomic confirmation journal for reversible, requester-scoped source views.

This store holds references and fingerprints only. The original records remain
the authority for content, access, lifecycle, and retention.
"""
import json
import re

from .maintenance_review import combine_sources, review_candidates, source_preview
from .memory_write_operations import fingerprint
from .qq_tasks import checkpoint_current, effect_current


_PREVIEW_SECONDS = 600
_ROW_LIMIT = 1024


class MaintenanceLinks:
    def __init__(self, store, *, scope, actor, topic, clock, retention, lock,
                 authorize, load, fence, source_limit, native_consent=None):
        self.store, self.scope, self.actor, self.topic = store, scope, actor, topic
        self.clock, self.retention, self.lock = clock, retention, lock
        self.authorize, self.load, self.fence, self.source_limit = authorize, load, fence, source_limit
        self.namespace = json.dumps(scope, separators=(',', ':'))
        self.native_consent = native_consent

    def prepare(self, operation, first, second, *, anchor_version, candidate_version, proposal=None,
                comparison=False, check_preview=None, preview_channel='qq'):
        if preview_channel not in {'qq','native'}:raise ValueError('invalid_preview_channel')
        self._require_operation(operation)
        members = (first, second)
        versions = (anchor_version, candidate_version)
        if (any(not isinstance(key, str) or not 1 <= len(key) <= 128 for key in members)
                or first == second
                or any(not isinstance(key, str) or not re.fullmatch('[a-f0-9]{64}', key) for key in versions)):
            raise ValueError('invalid_maintenance_members')

        def change(db):
            self.authorize(True, members)
            row = self._row(db, operation)
            if row:
                if (row['topic'], row['first'], row['second'], row['first_version'], row['second_version']) != (
                    self.topic, *members, *versions
                ):
                    return {'status': 'unavailable'}
                return self._result(row)
            fence = self._fence()
            review = review_candidates(first, [second], self.load, proposals={second: proposal})
            preview = source_preview(review, *versions, source_limit=self.source_limit(), comparison=comparison)
            if preview['status'] != 'ready':
                return preview
            if review['anchor'].scope != self.scope or fence != self._fence():
                return {'status': 'snapshot_changed'}
            pending = {**preview, 'status': 'pending', 'id': operation}
            if check_preview is not None:
                check_preview(pending)
            db.execute('''create table if not exists qq_maintenance_links(
                id text primary key, namespace text not null, actor text not null, topic text not null,
                first text not null, second text not null, first_version text not null, second_version text not null,
                relation text not null, fence text not null, state text not null, changed real not null,
                preview_state text not null default 'internal')''')
            if 'preview_state' not in {col[1] for col in db.execute('pragma table_info(qq_maintenance_links)')}:
                db.execute("alter table qq_maintenance_links add column preview_state text not null default 'internal'")
            now = self.clock()
            db.execute("delete from qq_maintenance_links where state!='active' and changed<=?",
                       (now - self.retention(),))
            if db.execute('select count(*) from qq_maintenance_links').fetchone()[0] >= _ROW_LIMIT:
                return {'status': 'operation_capacity_exceeded'}
            # A foreign use of the same host operation identity must not be disclosed.
            self.authorize(True, members)
            checkpoint_current()
            inserted = db.execute('''insert or ignore into qq_maintenance_links
                values(?,?,?,?,?,?,?,?,?,?,'pending',?,?)''',
                (operation, self.namespace, self.actor, self.topic, *members, *versions,
                 preview['relation'], fence, now, 'native_waiting' if preview_channel=='native' else 'waiting' if check_preview is not None else 'internal')).rowcount
            return pending if inserted else {'status': 'unavailable'}

        return self._write(change)

    def confirm(self, operation):
        self._require_operation(operation)

        def change(db):
            self.authorize(True, ())
            row = self._row(db, operation)
            if not row or row['topic'] != self.topic:
                return {'status': 'unavailable'}
            if row['state'] != 'pending':
                return self._result(row)
            if self.clock() >= row['changed'] + _PREVIEW_SECONDS:
                return self._set_state(db, operation, 'expired')
            if 'preview_state' in row.keys() and row['preview_state'] == 'waiting':
                return {'status': 'preview_not_delivered', 'id': operation}
            if 'preview_state' in row.keys() and row['preview_state']=='native_waiting' and not (self.native_consent and self.native_consent(operation)):
                return {'status':'preview_not_delivered','id':operation}
            self.authorize(True, (row['first'], row['second']))
            preview = self._view(row)
            if preview['status'] != 'ready':
                return self._set_state(db, operation, 'invalidated')
            overlaps = db.execute('''select * from qq_maintenance_links
                where namespace=? and actor=? and state='active'
                and (first in (?,?) or second in (?,?))''',
                (self.namespace, self.actor, row['first'], row['second'], row['first'], row['second'])).fetchall()
            if any(self._view(other)['status'] == 'ready' for other in overlaps):
                return self._set_state(db, operation, 'member_already_linked')
            if self.clock() >= row['changed'] + _PREVIEW_SECONDS:
                return self._set_state(db, operation, 'expired')
            self.authorize(True, (row['first'], row['second']))
            if row['fence'] != self._fence():
                return self._set_state(db, operation, 'invalidated')
            for other in overlaps:
                self._set_state(db, other['id'], 'invalidated')
            self._set_state(db, operation, 'active')
            return {**preview, 'status': 'active', 'id': operation}

        return self._write(change)

    def mark_preview_delivered(self, operation):
        """Called by QQ only after the final preview part has a transport receipt."""
        self._require_operation(operation)

        def change(db):
            self.authorize(True, ())
            row = self._row(db, operation)
            if (not row or row['topic'] != self.topic or row['state'] != 'pending' or
                    'preview_state' not in row.keys() or row['preview_state'] != 'waiting' or
                    self.clock() >= row['changed'] + _PREVIEW_SECONDS or self._view(row)['status'] != 'ready'):
                return False
            checkpoint_current()
            db.execute("update qq_maintenance_links set preview_state='delivered' where id=?", (operation,))
            return True

        return self._write(change)

    def result(self, operation):
        self._require_operation(operation)
        with self.lock, self.store.connect() as db:
            checkpoint_current()
            self.authorize(False, ())
            return self._result(self._row(db, operation))

    def for_member(self, member):
        """Resolve either original ID to its current joint view in this scope."""
        if not isinstance(member, str) or not 1 <= len(member) <= 128:
            raise ValueError('invalid_maintenance_member')
        with self.lock, self.store.connect() as db:
            checkpoint_current()
            self.authorize(False, ())
            if not self._has_table(db):
                return {'status': 'unavailable'}
            for row in db.execute('''select * from qq_maintenance_links
                    where namespace=? and actor=? and state='active' and (first=? or second=?)
                    order by changed desc''', (self.namespace, self.actor, member, member)):
                result = self._result(row)
                if result['status'] == 'active':
                    return result
        return {'status': 'unavailable'}

    def undo(self, operation):
        self._require_operation(operation)

        def change(db):
            self.authorize(True, ())
            row = self._row(db, operation)
            if not row:
                return {'status': 'unavailable'}
            if row['state'] == 'pending':
                if row['topic'] != self.topic:
                    return {'status': 'unavailable'}
                state = 'expired' if self.clock() >= row['changed'] + _PREVIEW_SECONDS else 'cancelled'
                return self._set_state(db, operation, state)
            if row['state'] not in {'active', 'invalidated'}:
                return self._result(row)
            # The journal row belongs to this actor. Undo removes only their
            # derived view, even if an original source is now unavailable.
            return self._set_state(db, operation, 'undone')

        return self._write(change)

    def _write(self, change):
        with self.lock, effect_current('memory') as effect:
            with self.store.connect() as db:
                db.execute('begin immediate')
                checkpoint_current()
                before = db.total_changes
                result = change(db)
                changed = db.total_changes != before
            if changed:
                effect.committed()
            else:
                effect.failed()
        return result

    @staticmethod
    def _require_operation(operation):
        if not isinstance(operation, str) or not re.fullmatch('[a-f0-9]{32}', operation):
            raise ValueError('invalid_maintenance_operation')

    @staticmethod
    def _has_table(db):
        return db.execute("select 1 from sqlite_master where name='qq_maintenance_links'").fetchone()

    def _row(self, db, operation):
        if self._has_table(db):
            return db.execute('select * from qq_maintenance_links where id=? and namespace=? and actor=?',
                              (operation, self.namespace, self.actor)).fetchone()
        return None

    def _set_state(self, db, operation, state):
        checkpoint_current()
        db.execute('update qq_maintenance_links set state=?,changed=? where id=?',
                   (state, self.clock(), operation))
        return {'status': state, 'id': operation}

    def _fence(self):
        return fingerprint(self.fence())

    def _view(self, row):
        if row['fence'] != self._fence():
            return {'status': 'snapshot_changed'}
        first, second = self.load(row['first']), self.load(row['second'])
        if (not first or not second or first.scope != self.scope or second.scope != self.scope or
                (first.version, second.version) != (row['first_version'], row['second_version']) or
                row['fence'] != self._fence()):
            return {'status': 'snapshot_changed'}
        return combine_sources(first, second, row['relation'], source_limit=self.source_limit())

    def _result(self, row):
        if row is None:
            return {'status': 'unavailable'}
        state = row['state']
        if state == 'pending' and self.clock() >= row['changed'] + _PREVIEW_SECONDS:
            state = 'expired'
        if state in {'active', 'pending'}:
            preview = self._view(row)
            if preview['status'] == 'ready':
                self.authorize(False, ())
                checkpoint_current()
                return {**preview, 'status': state, 'id': row['id']}
            state = 'invalidated'
        return {'status': state, 'id': row['id']}
