"""Versioned, reversible archival of two copies of exactly the same QQ evidence."""
from dataclasses import replace
import json
import re

from .memory_health import native_version
from .maintenance_review import review_candidates, source_preview
from .memory_versions import record_version
from .qq_tasks import guarded_memory_write


def archive_preview(kb, links, access, keep, archive, *, keep_version, archive_version):
    members = (keep, archive)
    versions = (keep_version, archive_version)
    if (any(not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}', key) for key in members)
            or keep == archive or any(not isinstance(version, str) or not re.fullmatch(r'[a-f0-9]{64}', version)
                                     for version in versions)):
        raise ValueError('invalid_maintenance_members')

    with links.lock:
        links.authorize(True, members)
        store = kb.memory_v4_store
        if store is None or not kb.settings.get('memory_v4_write_enabled'):
            return {'status': 'unavailable'}
        fence = links.fence()
        review = review_candidates(keep, [archive], links.load)
        preview = source_preview(review, *versions, source_limit=links.source_limit())
        if preview['status'] != 'ready':
            return preview
        if preview['relation'] != 'duplicate_evidence':
            return {'status': 'separate_review_required', 'relation': preview['relation']}

        legacy = [kb._find_memory(key) for key in members]
        native = [store.get_memory(key) for key in members]
        evidence = [store.list_evidence(key) for key in members]
        if not all(_eligible(kb, access, links.scope[1], item, row) for item, row in zip(native, legacy)):
            return {'status': 'unavailable'}

        # A visible assertion is insufficient if either record has other evidence.
        # Compare the full stored evidence as well as the authorized source view.
        records = (review['anchor'], review['candidates'][0]['record'])
        if any(native_version(item, False) != record.revision for item, record in zip(native, records)):
            return {'status': 'snapshot_changed'}
        if any({row.id for row in rows} != {source.id for source in record.sources}
               for rows, record in zip(evidence, records)):
            return {'status': 'separate_review_required', 'reason': 'incomplete_evidence_projection'}
        if (_legacy_payload(legacy[0]) != _legacy_payload(legacy[1]) or
                _native_payload(native[0]) != _native_payload(native[1]) or
                _evidence_payload(evidence[0]) != _evidence_payload(evidence[1])):
            return {'status': 'separate_review_required', 'reason': 'different_member_payload'}
        links.authorize(True, members)
        if fence != links.fence():
            return {'status': 'snapshot_changed'}

        legacy_versions = {row['id']: record_version(row) for row in legacy}
        plan_version = record_version({'scope': links.scope, 'topic': links.topic, 'fence': fence,
                                       'keep': keep, 'archive': archive, 'members': preview['members'],
                                       'legacy_versions': legacy_versions,
                                       'evidence': _evidence_payload(evidence[0])})
        return {**preview, 'keep': keep, 'archive': archive, 'legacy_versions': legacy_versions,
                'plan_version': plan_version}


def _eligible(kb, access, bot, item, legacy):
    return (item is not None and legacy is not None and item.lifecycle == 'active' and
            item.canonical_text == legacy.get('content') and kb.memory_allowed_for_use(legacy) and
            not (item.pinned or item.locked or item.supersedes_id or legacy.get('is_locked') or
                 legacy.get('is_important') or legacy.get('importance_locked') or legacy.get('managed_slot') or
                 legacy.get('supersedes') or legacy.get('superseded_by') or legacy.get('conflicts_with')) and
            legacy.get('status', 'active') in {'active', 'valid'} and kb._memory_layer(legacy) != 'core' and
            legacy.get('retention_policy') != 'permanent' and not kb._is_derived_memory(legacy) and
            access.get(bot, item.id) is None)


def _legacy_payload(record):
    result = {key: value for key, value in record.items()
              if key not in {'id', 'access_count', 'last_accessed'}}
    detail = result.get('source_detail')
    if isinstance(detail, dict) and detail.get('memory_id') == record['id']:
        result['source_detail'] = {key: value for key, value in detail.items() if key != 'memory_id'}
    return result


def _native_payload(item):
    result = {key: value for key, value in item.to_dict().items() if key not in {
        'id', 'revision', 'created_at', 'updated_at', 'used_count', 'candidate_shown_count', 'last_used_at'}}
    metadata = result['metadata']
    if metadata.get('legacy_id') == item.id:
        del metadata['legacy_id']
    detail = metadata.get('legacy_source_detail')
    if isinstance(detail, dict) and detail.get('memory_id') == item.id:
        del detail['memory_id']
    return result


def _evidence_payload(rows):
    return sorted(record_version({key: value for key, value in row.to_dict().items()
                                  if key not in {'id', 'memory_id'}}) for row in rows)


_LIFECYCLE_FIELDS = {'is_archived', 'status', 'memory_layer', 'archived_from_layer'}
_UNKNOWN = {'archiving', 'restoring'}


class MaintenanceArchive:
    def __init__(self, kb, links, access):
        self.kb, self.links, self.access = kb, links, access

    def prepare(self, operation, keep, target, *, check_preview, preview_channel='qq'):
        if preview_channel not in {'qq','native'}:raise ValueError('invalid_preview_channel')
        self.links._require_operation(operation)
        with self.links.lock:
            self.links.authorize(True, (keep, target))
            current = self._row(operation)
            if current:
                if (current['keep_id'], current['target_id'], current['topic']) != (keep, target, self.links.topic):
                    return {'status': 'unavailable'}
                return self._pending(current) if current['state'] == 'pending' else self._result(current)
            first, second = self.links.load(keep), self.links.load(target)
            if not first or not second:
                return {'status': 'snapshot_changed'}
            plan = {'keep_version': first.version, 'archive_version': second.version}
            preview = self._preview(keep, target, plan)
            if preview['status'] != 'ready':
                return preview
            result = {**preview, 'status': 'pending', 'id': operation}
            check_preview(result)
            plan.update(plan_version=preview['plan_version'], keep_legacy=preview['legacy_versions'][keep])
            with self.links.store.connect() as db:
                db.execute('''create table if not exists qq_maintenance_archives(
                    id text primary key, namespace text not null, topic text not null,
                    keep_id text not null, target_id text not null, expires real not null,
                    state text not null, delivered integer not null, plan text not null)''')
                now = self.links.clock()
                db.execute("delete from qq_maintenance_archives where state='pending' and expires<?", (now - 86400,))
                if db.execute('select count(*) from qq_maintenance_archives').fetchone()[0] >= 4096:
                    return {'status': 'operation_capacity_exceeded'}
                db.execute('insert into qq_maintenance_archives values(?,?,?,?,?,?,\'pending\',?,?)',
                           (operation, self.links.namespace, self.links.topic, keep, target, now + 600,2 if preview_channel=='native' else 0,json.dumps(plan)))
            return result

    def pending(self, operation):
        with self.links.lock:
            self.links.authorize(True, ())
            row = self._row(operation)
            if row and row['topic'] == self.links.topic and row['state'] == 'pending':
                return self._pending(row)
            return {'status': 'unavailable'}

    def mark_delivered(self, operation):
        with self.links.lock:
            if self.pending(operation)['status'] != 'pending':
                return False
            if self._row(operation)['delivered']==2:return False
            with self.links.store.connect() as db:
                return db.execute("update qq_maintenance_archives set delivered=1 where id=? and state='pending'",
                                  (operation,)).rowcount == 1

    def confirm(self, operation):
        with self.links.lock:
            self.links.authorize(True, ())
            row = self._row(operation)
            if not row or row['topic'] != self.links.topic:
                return {'status': 'unavailable'}
            if row['state'] != 'pending':
                return self._result(row)
            if row['delivered']==2:
                if not (self.links.native_consent and self.links.native_consent(operation)):
                    return self._status(row,'preview_not_delivered')
            elif row['delivered']!=1:
                return self._status(row, 'preview_not_delivered')
            pending = self._pending(row)
            if pending['status'] != 'pending':
                return pending
            if self._rows_for(row['target_id']) or self._rows_for(row['keep_id']):
                return self._status(row, 'member_already_linked')
            return self._transition(row, json.loads(row['plan']), archive=True)

    def undo(self, operation):
        with self.links.lock:
            self.links.authorize(True, ())
            row = self._row(operation)
            if not row:
                return {'status': 'unavailable'}
            result = self._result(row)
            if result['status'] != 'archived':
                return result
            row = self._row(operation)
            plan = json.loads(row['plan'])
            if not self._visible(row, plan):
                return self._status(row, 'invalidated')
            return self._transition(row, plan, archive=False)

    def result(self, operation):
        with self.links.lock:
            self.links.authorize(False, ())
            row = self._row(operation)
            if not row or (row['state'] == 'pending' and row['topic'] != self.links.topic):
                return {'status': 'unavailable'}
            return self._result(row)

    def for_member(self, member):
        with self.links.lock:
            self.links.authorize(False, ())
            for row in self._rows_for(member):
                if row['state'] == 'archived':
                    kept = self._visible(row, json.loads(row['plan']))
                    if kept:
                        return self._status(row, 'archived') | {
                            'keep': kept.id, 'archive': row['target_id'], 'version': kept.version,
                            'sources': kept.sources}
            return {'status': 'unavailable'}

    def _preview(self, keep, target, plan):
        return archive_preview(self.kb, self.links, self.access, keep, target,
                               keep_version=plan['keep_version'], archive_version=plan['archive_version'])

    def _pending(self, row):
        if row['expires'] <= self.links.clock():
            return self._status(row, 'expired')
        plan = json.loads(row['plan'])
        preview = self._preview(row['keep_id'], row['target_id'], plan)
        if preview.get('plan_version') != plan['plan_version']:
            return self._status(row, 'snapshot_changed')
        return {**preview, **self._status(row, 'pending')}

    def _row(self, operation):
        self.links._require_operation(operation)
        with self.links.store.connect() as db:
            if not self._has_table(db):
                return None
            return db.execute('select * from qq_maintenance_archives where id=? and namespace=?',
                              (operation, self.links.namespace)).fetchone()

    def _rows_for(self, member):
        with self.links.store.connect() as db:
            if not self._has_table(db):
                return []
            return db.execute("select * from qq_maintenance_archives where namespace=? and (keep_id=? or target_id=?) "
                              "and state in ('archiving','archived','restoring')",
                              (self.links.namespace, member, member)).fetchall()

    @staticmethod
    def _has_table(db):
        return db.execute("select 1 from sqlite_master where name='qq_maintenance_archives'").fetchone()

    @staticmethod
    def _status(row, state):
        return {'status': state, 'id': row['id']}

    def _save(self, row, state, plan):
        with self.links.store.connect() as db:
            db.execute('update qq_maintenance_archives set state=?,plan=? where id=?',
                       (state, json.dumps(plan), row['id']))

    def _result(self, row):
        state = row['state']
        if state in _UNKNOWN:
            plan = json.loads(row['plan'])
            if not self._matches_write(row, plan):
                return self._status(row, 'unknown')
            state = 'archived' if state == 'archiving' else 'undone'
            self._save(row, state, plan)
        if state == 'pending' and row['expires'] <= self.links.clock():
            state = 'expired'
        return self._status(row, state)

    def _matches_write(self, row, plan):
        target = row['target_id']
        legacy = self.kb._find_memory(target)
        store = self.kb.memory_v4_store
        compat = self.kb.sqlite_store
        if not legacy or not store or not compat or record_version(legacy) != plan['after_legacy']:
            return False
        item = store.get_memory(target)
        if not item or record_version(_native_payload(item)) != plan['after_native']:
            return False
        if _evidence_payload(store.list_evidence(target)) != plan['target_evidence']:
            return False
        latest = store.list_versions(target, limit=1)
        if not latest or (latest[0].revision, latest[0].reason) != (plan['after_revision'], plan['marker']):
            return False
        with compat._lock, compat._connection() as db:
            persisted = db.execute('select payload,is_archived from memories where id=? and is_deleted=0 '
                                   'and not exists(select 1 from memory_tombstones where id=?)', (target, target)).fetchone()
            return bool(persisted and bool(persisted['is_archived']) == bool(legacy.get('is_archived'))
                        and record_version(json.loads(persisted['payload'])) == plan['after_legacy'])

    def _visible(self, row, plan):
        target = self.kb._find_memory(row['target_id'])
        if (not target or self.links.fence() != plan['epoch'] or not self._matches_write(row, plan)
                or not self.kb.memory_allowed_for_use(target, _dependency=True)
                or self.access.get(self.links.scope[1], row['target_id']) is not None
                or self.access.get(self.links.scope[1], row['keep_id']) is not None):
            return None
        keep_id = row['keep_id']
        kept = self.links.load(keep_id)
        legacy = self.kb._find_memory(keep_id)
        if not kept or not legacy or record_version(legacy) != plan['keep_legacy']:
            return None
        store = self.kb.memory_v4_store
        item = store.get_memory(keep_id)
        if (item and record_version(_native_payload(item)) == plan['keep_native']
                and _evidence_payload(store.list_evidence(keep_id)) == plan['target_evidence']):
            return kept
        return None

    def _transition(self, row, plan, *, archive):
        self.links.authorize(True, ())
        kb = self.kb
        target = row['target_id']
        capacity = kb._memory_capacity_snapshot()
        destination = 'archived' if archive else 'active'
        limit = 'archive_hard_limit' if archive else 'active_hard_limit'
        if capacity[destination] >= capacity[limit]:
            return self._status(row, 'archive_capacity_exceeded')
        store = kb.memory_v4_store
        if store is None:
            return self._status(row, 'snapshot_changed')
        with store._lock:
            # Repeat the relevant eligibility check under the native write lock.
            valid = self._pending(row)['status'] == 'pending' if archive else self._visible(row, plan)
            if not valid or not kb.settings.get('memory_v4_write_enabled'):
                return self._status(row, 'snapshot_changed')
            original = kb._find_memory(target)
            native = store.get_memory(target)
            if archive:
                plan['original_lifecycle'] = {key: original[key] for key in _LIFECYCLE_FIELDS if key in original}
                plan['original_native_layer'] = native.legacy_layer
                plan['keep_native'] = record_version(_native_payload(store.get_memory(row['keep_id'])))
                fields = {'is_archived': True, 'status': 'archived', 'memory_layer': 'archive',
                          'archived_from_layer': kb._memory_layer(original)}
            else:
                fields = plan['original_lifecycle']
            replacement = {key: value for key, value in original.items() if key not in _LIFECYCLE_FIELDS} | fields
            expected = replace(native, lifecycle=destination,
                               legacy_layer='archive' if archive else plan['original_native_layer'])
            plan.update(after_legacy=record_version(replacement), after_native=record_version(_native_payload(expected)),
                        after_revision=native.revision + 1, target_evidence=_evidence_payload(store.list_evidence(target)),
                        marker=f'qq_archive:{row["id"]}:{destination}')
            self._write(row, plan, store, original, replacement, expected, archive=archive)
            return self._result(self._row(row['id']))

    @guarded_memory_write
    def _write(self, row, plan, store, original, replacement, expected, *, archive):
        plan['epoch'] = self.kb.memory_use.advance()
        self._save(row, 'archiving' if archive else 'restoring', plan)

        def update_native():
            store.update_memory(original['id'], {'lifecycle': expected.lifecycle, 'legacy_layer': expected.legacy_layer},
                                operation='archive_duplicate' if archive else 'undo_archive_duplicate',
                                reason=plan['marker'], actor=self.links.actor)

        # On a partial write, the compatibility read remains archived until
        # both projections of a restore are ready.
        if not archive:
            update_native()
        if not self.kb._replace_memory_lifecycle_projection(original, replacement):
            return None
        if archive:
            update_native()
        return True if self._matches_write(row, plan) else None
