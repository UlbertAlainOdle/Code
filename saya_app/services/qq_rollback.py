"""Prepare an isolated historical-code rehearsal without restoring live data."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from .qq_controls import QQControls, QQControlsStore, DELIVERY_FIELDS
from .shared_chat_config import SharedChatConfigStore
from .credentials_storage import read_credentials
from .state_storage import atomic_save_json

BASELINE_TESTS=['tests/test_qq_bridge.py','tests/test_qq_groups.py','tests/test_qq_controls.py',
    'tests/test_qq_capabilities.py','tests/test_qq_chat_runtime.py','tests/test_memory_default_runtime.py',
    'tests/test_memory_v4_agent_adapter.py','tests/test_memory_v4_phase7.py','tests/test_web_research.py','tests/test_chat_agents.py']

NETWORK_GUARD='''"""Historical-code rehearsal: deny non-loopback DNS and sockets."""
import socket, ipaddress, asyncio
_resolve=socket.getaddrinfo
_connect=socket.socket.connect
_connect_ex=socket.socket.connect_ex
_name=socket.gethostbyname
_names=socket.gethostbyname_ex
_open=asyncio.BaseEventLoop.create_connection
def _local(host):
    if isinstance(host,bytes): host=host.decode('ascii')
    if host in (None,'','localhost'): return True
    try:return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:return False
def guarded_resolve(host,*args,**kwargs):
    if not _local(host):raise OSError('external network denied in rollback rehearsal')
    return _resolve(host,*args,**kwargs)
def guarded_connect(self,address):
    if self.family in (socket.AF_INET,socket.AF_INET6) and not _local(address[0]):
        raise OSError('external network denied in rollback rehearsal')
    return _connect(self,address)
def guarded_connect_ex(self,address):
    if self.family in (socket.AF_INET,socket.AF_INET6) and not _local(address[0]):
        raise OSError('external network denied in rollback rehearsal')
    return _connect_ex(self,address)
def guarded_name(host):
    if not _local(host):raise OSError('external DNS denied in rollback rehearsal')
    return _name(host)
def guarded_names(host):
    if not _local(host):raise OSError('external DNS denied in rollback rehearsal')
    return _names(host)
async def guarded_open(self,protocol_factory,host=None,port=None,*args,**kwargs):
    if not _local(host):raise OSError('external async network denied in rollback rehearsal')
    return await _open(self,protocol_factory,host,port,*args,**kwargs)
socket.getaddrinfo=guarded_resolve
socket.socket.connect=guarded_connect
socket.socket.connect_ex=guarded_connect_ex
socket.gethostbyname=guarded_name
socket.gethostbyname_ex=guarded_names
asyncio.BaseEventLoop.create_connection=guarded_open
'''


def _snapshot_sqlite(source, target):
    target.parent.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri()+'?mode=ro',uri=True) as original, sqlite3.connect(target) as copy:
        original.backup(copy)
        integrity=copy.execute('PRAGMA integrity_check').fetchone()[0]
        if integrity!='ok':raise ValueError('invalid SQLite copy')
        tables=[row[0] for row in copy.execute("select name from sqlite_master where type='table'")]
        counts={table:copy.execute('SELECT COUNT(*) FROM "'+table.replace('"','""')+'"').fetchone()[0] for table in tables}
    return {'integrity':integrity,'tables':len(tables),'row_counts':counts}


def memory_rollback_requirements(source_root):
    """Content-free minimum reader protections required by retained data."""
    base=Path(source_root).resolve()/'knowledge_base'
    requirements=set()
    def read(name):
        path=base/name
        if not path.exists():return None
        if path.stat().st_size>32*1024*1024:raise ValueError('memory state too large for rollback preflight')
        try:return json.loads(path.read_text(encoding='utf-8-sig'))
        except (ValueError,OSError):raise ValueError('memory state unreadable; rollback denied') from None
    use=read('memory_use_state.json')
    importance=read('memory_importance_policy.json')
    if importance is not None:
        if not isinstance(importance,dict) or importance.get('schema_version')!=1:raise ValueError('invalid importance policy')
        if importance.get('enabled'):requirements.add('memory_importance_v1')
    # Older readers can silently discard provenance on edit/reprojection.
    for name in ('knowledge.json', 'memory_candidates.json'):
        records=read(name)
        if isinstance(records,dict):records=records.get('memories',records.get('candidates',[]))
        if isinstance(records,list) and any(isinstance(row,dict) and isinstance(row.get('origin'),dict)
                and row['origin'].get('origin_version')==1 for row in records):
            requirements.add('memory_origin_context_v1')
        if isinstance(records,list) and any(isinstance(row,dict) and row.get('source') in {'qq_daily','qq_important'} for row in records):
            requirements.add('qq_daily_admission_v1')
        if isinstance(records,list) and any(isinstance(row,dict) and ((row.get('source_detail') or {}).get('qq_confirmed_revision') or {}).get('contact_maintenance') is True for row in records):
            requirements.add('qq_contact_maintenance_v1')
        if isinstance(records,list) and any(isinstance(row,dict) and str(row.get('project_id') or '').startswith('qq_person_') for row in records):
            requirements.add('qq_person_memory_v1')
        if isinstance(records,list) and any(isinstance(row,dict) and row.get('importance_policy_version')==1 for row in records):
            requirements.add('memory_importance_v1')
        if isinstance(records,list) and any(isinstance(row,dict) and isinstance(row.get('source_detail'),dict)
                and row['source_detail'].get('candidate_confirmation_plan') for row in records):
            requirements.add('candidate_confirmation_plan_v1')
    v4=base/'memory4.sqlite3'
    if v4.exists():
        with sqlite3.connect(v4.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("select 1 from sqlite_master where type='table' and name='memory_items'").fetchone():
                if db.execute("select 1 from memory_items where json_extract(metadata_json,'$.origin.origin_version')=1 limit 1").fetchone():
                    requirements.add('memory_origin_context_v1')
                if db.execute("select 1 from memory_items where json_extract(metadata_json,'$.importance_policy_version')=1 limit 1").fetchone():
                    requirements.add('memory_importance_v1')
    if use is not None:
        if not isinstance(use,dict) or use.get('version') not in {1,2}:raise ValueError('unsupported memory use state')
        requirements.add('memory_use_v'+str(use['version']))
        if use.get('dependencies'):requirements.add('source_dependencies')
        if use.get('policies'):requirements.add('per_memory_use_policy')
        if use.get('history_floor'):requirements.add('history_floor')
    deletion=read('memory_deletions.json')
    legacy=base/'memory3.sqlite3'
    if legacy.exists():
        with sqlite3.connect(legacy.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("select 1 from sqlite_master where type='table' and name='memory_write_operations'").fetchone():
                if db.execute('select 1 from memory_write_operations limit 1').fetchone():requirements.add('memory_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind='archive' limit 1").fetchone():
                    requirements.add('archive_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind='listener' limit 1").fetchone():
                    requirements.add('listener_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind='manual' limit 1").fetchone():
                    requirements.add('manual_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind='game_archive' limit 1").fetchone():
                    requirements.add('game_archive_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind='game_task' limit 1").fetchone():
                    requirements.add('game_task_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind in ('dream_theme','dream_diary') limit 1").fetchone():
                    requirements.add('dream_write_operations_v1')
                if db.execute("select 1 from memory_write_operations where kind in ('learner_batch','learner_item') limit 1").fetchone():
                    requirements.add('learner_write_operations_v1')
                if db.execute("select 1 from memory_write_operations op, json_each(op.result) item "
                              "where json_type(item.value,'$.target_versions')='object' "
                              "and json_extract(item.value,'$.target_versions')!='{}' limit 1").fetchone():
                    requirements.add('native_write_plan_versions_v1')
                if 'kind' in {r[1] for r in db.execute('pragma table_info(memory_write_operations)')} and db.execute(
                        "select 1 from memory_write_operations where kind='candidate' limit 1").fetchone():
                    requirements.add('candidate_confirmation_plan_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='memory_revision_previews'").fetchone():
                if db.execute('select 1 from memory_revision_previews limit 1').fetchone():requirements.add('memory_revisions_v1')
    if deletion is not None:
        if not isinstance(deletion,dict) or not isinstance(deletion.get('deleted'),dict):raise ValueError('invalid deletion ledger')
        if deletion['deleted']:requirements.add('deletion_ledger')
    projection=read('memory_v4_projection_pending.json')
    if projection is not None:
        if not isinstance(projection,dict) or projection.get('schema_version')!=1 or not isinstance(projection.get('ids'),list):
            raise ValueError('invalid projection ledger')
        if projection['ids']:requirements.add('v4_projection_reconciliation')
    conflicts=read('memory_conflicts.json')
    if conflicts is not None:
        if not isinstance(conflicts,dict) or not isinstance(conflicts.get('conflicts'),list):raise ValueError('invalid conflict ledger')
        if any(c.get('status') in {'pending','applying'} for c in conflicts['conflicts']):requirements.add('conflict_cas_intent')
    bridge=base/'qq/bridge.sqlite3'
    if bridge.exists():
        with sqlite3.connect(bridge.resolve().as_uri()+'?mode=ro',uri=True) as db:
            for table,feature in [('qq_bot_media_sources','qq_bot_media_sources_v1'),('qq_delivery_part_kinds','qq_control_receipts_v1'),('qq_control_receipt_ids','qq_control_receipts_v1'),
                ('qq_context_policy','qq_context_salience_v1'),('qq_context_turn_sources','qq_episode_context_v1'),
                ('qq_forward_input_policy','qq_forward_input_v1'),('qq_forward_input_results','qq_forward_input_v1'),
                ('qq_relation_policy','qq_relationships_v1'),('qq_relation_configs','qq_relationships_v1'),('qq_relation_affinity','qq_relationships_v1'),
                ('qq_episode_policy','qq_episode_context_v1'),('qq_episode_cards','qq_episode_context_v1'),('qq_episode_result_sources','qq_episode_context_v1'),
                ('qq_person_event_schema','qq_person_events_v2'),('qq_person_event_migrations','qq_person_events_v2'),('qq_person_event_deletions','qq_person_events_v2')]:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add(feature)
            for table in ['qq_unified_event_policy','qq_shared_events']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_unified_events_v1')
            for table in ['qq_topic_routes','qq_topic_evidence','qq_topic_history_guards']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_topic_routing_v1')
            for table in ['qq_event_source_masks','qq_event_preview_guards','qq_event_operation_sessions','qq_event_operation_results','qq_event_operation_attempts']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_event_operations_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_event_operation_sessions'").fetchone() and db.execute(
                "select 1 from qq_event_operation_sessions where json_extract(data,'$.preview_channel')='native' limit 1").fetchone():
                requirements.add('qq_event_console_v1')
            for table in ['qq_event_operation_sessions','qq_event_operation_results']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute(
                    'select 1 from '+table+" where json_type(data,'$.native_control') is not null limit 1").fetchone():
                    requirements.add('qq_native_event_controls_v1')
            if db.execute("select 1 from sqlite_master where name='qq_event_provenance_schema'").fetchone() and db.execute(
                'select 1 from qq_event_provenance_schema limit 1').fetchone():requirements.add('qq_event_provenance_v1')
            if db.execute("select 1 from sqlite_master where name='qq_action_proposals'").fetchone() and db.execute(
                "select 1 from qq_action_proposals where state='clarifying' limit 1").fetchone():requirements.add('qq_memory_reference_v1')
            if db.execute("select 1 from sqlite_master where name='qq_action_proposals'").fetchone() and db.execute(
                "select 1 from qq_action_proposals where kind in ('save_reference','select_reference') limit 1").fetchone():requirements.add('qq_memory_reference_dialogue_v1')
            if db.execute("select 1 from sqlite_master where name='qq_action_proposals'").fetchone():
                columns={x[1] for x in db.execute('pragma table_info(qq_action_proposals)')}
                if 'payload' in columns and db.execute("select 1 from qq_action_proposals where kind='save_reference_set' or json_extract(payload,'$.plan.fragment')=1 or (kind='select_reference' and json_type(payload,'$.epoch') is not null) limit 1").fetchone():requirements.add('qq_memory_reference_selection_v1')
            if db.execute("select 1 from sqlite_master where name='qq_memory_reference_batches'").fetchone() and db.execute('select 1 from qq_memory_reference_batches limit 1').fetchone():requirements.add('qq_memory_reference_selection_v1')
            if db.execute("select 1 from sqlite_master where name='qq_chat_save_operations'").fetchone() and db.execute('select 1 from qq_chat_save_operations limit 1').fetchone():requirements.add('qq_chat_save_operations_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_maintenance_archives'").fetchone() and db.execute(
                "select 1 from qq_maintenance_archives where state!='pending' limit 1").fetchone():
                requirements.add('qq_maintenance_archive_v1')
            if db.execute("select 1 from sqlite_master where name='qq_topic_dialogue_schema'").fetchone() and db.execute('select 1 from qq_topic_dialogue_schema where enabled=1 limit 1').fetchone():requirements.add('qq_topic_dialogue_v1')
            if db.execute("select 1 from sqlite_master where name='qq_topic_dialogue_units'").fetchone() and db.execute('select 1 from qq_topic_dialogue_units limit 1').fetchone():requirements.add('qq_topic_dialogue_v1')
            for table in ['qq_topic_aliases','qq_topic_binding_schema']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_topic_bindings_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_topic_split_previews'").fetchone() and db.execute('select 1 from qq_topic_split_previews limit 1').fetchone():requirements.add('qq_topic_split_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_shared_events'").fetchone() and db.execute(
                "select 1 from qq_shared_events where json_type(data,'$.split_from') is not null or json_type(data,'$.split_children') is not null limit 1").fetchone():requirements.add('qq_topic_split_v1')
            for table,field in [('qq_episode_cards','facts'),('qq_shared_events','contributions')]:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute(
                    "select 1 from "+table+" c,json_each(c.data,'$."+field+"') x where json_extract(x.value,'$.topic_revision_version')=1 or json_type(x.value,'$.superseded_by_evidence') is not null limit 1").fetchone():
                    requirements.add('qq_topic_revisions_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_topic_evidence'").fetchone() and db.execute(
                "select 1 from qq_topic_evidence where json_extract(data,'$.revision_version')=1 limit 1").fetchone():requirements.add('qq_topic_revisions_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_topic_lifecycle_policy'").fetchone() and db.execute(
                "select 1 from qq_topic_lifecycle_policy where json_extract(data,'$.enabled')=1 limit 1").fetchone():requirements.add('qq_topic_lifecycle_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_shared_events'").fetchone() and db.execute(
                "select 1 from qq_shared_events where json_type(data,'$.attention') is not null limit 1").fetchone():requirements.add('qq_topic_lifecycle_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_unified_event_policy'").fetchone() and db.execute(
                "select 1 from qq_unified_event_policy where json_type(data,'$.topic_routing_enabled') is not null limit 1").fetchone():requirements.add('qq_topic_routing_v1')
            for table in ['qq_person_event_source_guards','qq_person_event_history_schema']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_person_event_history_v1')
            for table in ['qq_event_control_schema','qq_event_control_versions','qq_event_control_audit']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():requirements.add('qq_event_controls_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_episode_cards'").fetchone() and db.execute(
                "select 1 from qq_episode_cards where json_type(data,'$.timing') is not null or json_type(data,'$.importance_rule_version') is not null limit 1").fetchone():requirements.add('qq_event_priority_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_experience_policy'").fetchone():
                if db.execute("select 1 from qq_experience_policy where json_extract(policy,'$.enabled')=1 limit 1").fetchone():requirements.add('qq_dialogue_experience_v1')
                if db.execute("select 1 from qq_experience_policy where json_type(policy,'$.concise_replies') is not null or json_type(policy,'$.short_voice') is not null limit 1").fetchone():requirements.add('qq_dialogue_experience_components_v2')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_expression_deliveries'").fetchone():
                if db.execute("select 1 from qq_expression_deliveries where json_extract(plan,'$.speech_span') is not null and expires>? limit 1",(time.time(),)).fetchone():requirements.add('qq_dialogue_experience_v1')
                if db.execute("select 1 from qq_expression_deliveries where json_extract(plan,'$.speech_source')='labelled_segment' and expires>? limit 1",(time.time(),)).fetchone():requirements.add('qq_speech_section_v1')
            for table,feature in [('qq_memory_access','qq_memory_acl'),('qq_memory_operations','qq_memory_operations')]:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    if db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:requirements.add(feature)
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_memory_console_saves'").fetchone() and db.execute(
                "select 1 from qq_memory_console_saves where json_extract(payload,'$.requested_action')='ignore' limit 1").fetchone():
                requirements.add('qq_console_candidate_ignore_v1')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_media_policy'").fetchone():
                for row in db.execute('select policy from qq_media_policy'):
                    policy=json.loads(row[0])
                    if not isinstance(policy,dict):raise ValueError('invalid media policy')
                    if 'documents_enabled' in policy or 'quote_documents_enabled' in policy:requirements.add('qq_media_documents')
                    if 'voice_reply_enabled' in policy:requirements.add('qq_voice_reply')
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_media_events'").fetchone():
                for row in db.execute('select snapshot from qq_media_events'):
                    snapshot=json.loads(row[0])
                    if not isinstance(snapshot,dict):raise ValueError('invalid media provenance')
                    if 'documents' in snapshot.get('capabilities',[]):requirements.add('qq_media_documents')
            for table,feature in [('qq_group_policies','qq_group_context'),('qq_group_results','qq_group_context'),
                    ('qq_group_events','qq_group_context'),('qq_group_facts','qq_group_context'),
                    ('qq_group_privacy_guard','qq_group_context'),('qq_friend_requests','qq_contact_review'),
                    ('qq_media_policy','qq_media_images'),('qq_media_events','qq_media_images'),('qq_voice_deliveries','qq_voice_reply'),
                    ('qq_forward_results','qq_self_merged_forward'),('qq_forward_sources','qq_self_merged_forward'),
                    ('qq_favorite_catalogue','qq_curated_favorites'),('qq_official_pack_catalogue','qq_official_pack_catalogue'),
                    ('qq_expression_policy','qq_expressions_v1'),('qq_expression_deliveries','qq_expressions_v1'),
                    ('qq_affect','qq_social_v1'),('qq_poke_events','qq_social_v1'),
                    ('qq_mood_observations','qq_mood_metadata_v1'),('qq_daily_policy','qq_daily_admission_v1'),
                    ('qq_daily_entries','qq_daily_admission_v1'),('qq_daily_seen','qq_daily_admission_v1'),
                    ('qq_memory_console_saves','qq_console_saves_v1'),
                    ('qq_memory_console_reads','qq_console_reads_v1'),
                    ('qq_memory_console_actions','qq_console_actions_v1')]:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone():
                    if db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0]:requirements.add(feature)
            for table in ['qq_memory_library_scans','qq_memory_library_reviews']:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone() and db.execute('select 1 from '+table+' limit 1').fetchone():
                    requirements.add('qq_semantic_library_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_memory_console_actions'").fetchone() and db.execute(
                "select 1 from qq_memory_console_actions where json_type(refs,'$.library_review') is not null limit 1").fetchone():requirements.add('qq_semantic_library_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_memory_console_actions'").fetchone() and db.execute(
                "select 1 from qq_memory_console_actions where json_type(refs,'$.overlap_plan') is not null limit 1").fetchone():requirements.add('qq_memory_overlap_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_daily_entries'").fetchone():
                columns={r[1] for r in db.execute('pragma table_info(qq_daily_entries)')}
                if 'overlap_plan' in columns and db.execute("select 1 from qq_daily_entries where overlap_plan!='{}' limit 1").fetchone():requirements.add('qq_memory_overlap_v1')
                if 'contact_maintenance' in columns and db.execute('select 1 from qq_daily_entries where contact_maintenance=1 limit 1').fetchone():requirements.add('qq_contact_maintenance_v1')
            # Old archive readers treat any nonzero delivered value as QQ
            # delivery. A native preview must require the new consent reader,
            # even if its console reference was lost in a partial write.
            for table,column,value in [('qq_maintenance_archives','delivered',2),('qq_maintenance_links','preview_state','native_waiting')]:
                if db.execute("select 1 from sqlite_master where type='table' and name=?",(table,)).fetchone():
                    columns={r[1] for r in db.execute('pragma table_info('+table+')')}
                    if column in columns and db.execute('select 1 from '+table+' where '+column+'=? limit 1',(value,)).fetchone():
                        requirements.add('qq_console_actions_v1')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_expression_deliveries'").fetchone():
                if db.execute("select 1 from qq_expression_deliveries where json_extract(plan,'$.inline')=1 or json_extract(plan,'$.voice')=1 limit 1").fetchone():requirements.add('qq_expression_composition_v2')
            if db.execute("select 1 from sqlite_master where type='table' and name='qq_memory_scope_epochs'").fetchone():
                if db.execute("select 1 from qq_memory_scope_epochs where scope like '%:isolated' limit 1").fetchone():requirements.add('qq_person_memory_v1')
    initiative=base/'proactive.sqlite3'
    if initiative.exists():
        with sqlite3.connect(initiative.resolve().as_uri()+'?mode=ro',uri=True) as db:
            if db.execute("select 1 from sqlite_master where type='table' and name='initiative_jobs'").fetchone():
                if db.execute('select 1 from initiative_jobs limit 1').fetchone():requirements.add('proactive_interaction_v1')
    return sorted(requirements)


def check_memory_rollback(source_root, target_manifest):
    required=memory_rollback_requirements(source_root)
    supported=target_manifest.get('memory_compatibility',[])
    if not isinstance(supported,list) or any(not isinstance(x,str) for x in supported):raise ValueError('invalid compatibility declaration')
    missing=sorted(set(required)-set(supported))
    return {'status':'blocked' if missing else 'compatible_declared', 'required':required,'missing':missing,
        'execution_authorized':False,'note':'Preflight does not replace a full isolated rehearsal.'}


def prepare_data(source_root, target):
    source_root=Path(source_root).resolve();target=Path(target).resolve()
    if target.exists():raise FileExistsError('rollback target already exists')
    target.mkdir(parents=True)
    source_qq=source_root/'knowledge_base/qq'
    document=QQControlsStore(source_qq/'settings.json').load_document()
    shared=SharedChatConfigStore(source_qq/'shared_settings.json').load()
    values=dict(document['settings'])
    for field in document['inherit_fields']:
        if field in shared['values']:values[field]=shared['values'][field]
    values=QQControls.model_validate(values).model_dump()
    destination=target/'knowledge_base/qq';destination.mkdir(parents=True)
    # Historical readers reject unknown keys. Preserve current choices in an
    # ignored private sidecar, and export the pre-QQ03 compatibility projection.
    atomic_save_json(destination/'settings-current.json',values)
    atomic_save_json(destination/'settings.json',{key:value for key,value in values.items() if key not in DELIVERY_FIELDS})
    for name in ('settings.json','shared_settings.json'):
        source=source_qq/name
        if source.exists():
            backup=target/'rollback-origin/qq'/name;backup.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,backup)
    # This is an explicit private downgrade export, never a second live store or sync timer.
    credential_source=source_root/'knowledge_base/api_credentials.json'
    credentials=read_credentials(credential_source)
    if credential_source.exists():shutil.copy2(credential_source,target/'knowledge_base/api_credentials.json')
    legacy=source_qq/'search_credentials.json'
    key=credentials.get('brave_search_api_key')
    if 'brave_search_api_key' not in credentials and legacy.exists():
        raw=json.loads(legacy.read_text(encoding='utf-8'))
        if not isinstance(raw,dict):raise ValueError('invalid legacy search file')
        key=raw.get('api_key','')
    if key is not None:
        if not isinstance(key,str) or len(key)>512 or any(c in key for c in '\r\n\0'):
            raise ValueError('invalid search credentials')
        atomic_save_json(destination/'search_credentials.json',{'api_key':key})
    databases=[]
    for relative in ('knowledge_base/qq/bridge.sqlite3','knowledge_base/memory3.sqlite3','knowledge_base/memory4.sqlite3'):
        source=source_root/relative
        if source.exists():databases.append({'path':relative,**_snapshot_sqlite(source,target/relative)})
    # Old QQMessage constructors cannot accept the added reply_to key. Only
    # rewrite queued payloads in the isolated downgrade copy; preserve the full
    # current payload in a private sidecar. Live databases are never modified.
    bridge_copy=target/'knowledge_base/qq/bridge.sqlite3'
    quoted_queue=[]
    if bridge_copy.exists():
        with sqlite3.connect(bridge_copy) as db:
            rows=db.execute("SELECT id,payload FROM inbox WHERE state='queued'").fetchall() if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox'").fetchone() else []
            for row_id,payload in rows:
                raw=json.loads(payload)
                if 'reply_to' in raw:
                    quoted_queue.append({'id':row_id,'payload':raw})
                    projected=dict(raw);projected.pop('reply_to',None)
                    db.execute('UPDATE inbox SET payload=? WHERE id=?',(json.dumps(projected,ensure_ascii=False),row_id))
    if quoted_queue:
        atomic_save_json(destination/'quoted-queue-current.json',quoted_queue)
    preserved_memory_files=[]
    for name in ('knowledge.json','memory_settings.json','memory_candidates.json','memory_deletions.json',
            'memory_use_state.json','memory_graph.json','memory_maintenance.json','memory_cold_archive.jsonl',
            'memory_conflicts.json','memory_v4_projection_pending.json'):
        source=source_root/'knowledge_base'/name
        if source.exists():
            shutil.copy2(source,target/'knowledge_base'/name)
            preserved_memory_files.append(name)
    report={'schema_version':1,'mode':'private_data_export_only','live_sources_modified':False,
            'preserved_memory_files':preserved_memory_files,'execution_authorized':False,
            'memory_compatibility_required':memory_rollback_requirements(source_root),
            'frozen_inherit_fields':document['inherit_fields'],'search_exported':key is not None,'databases':databases,
            'note':'Per-database consistent snapshots; no cross-store global transaction. No live restore performed.'}
    atomic_save_json(target/'data-report.json',report)
    return report


def build_source_bundle(root,snapshot,manifest,target,*,data_root=None):
    root=Path(root).resolve();snapshot=Path(snapshot).resolve();target=Path(target).resolve()
    allowed=root/'.runtime/qq-upgrade/rollback-rehearsals'
    if not target.is_relative_to(allowed) or target==allowed:raise ValueError('isolated target required')
    if target.exists():raise FileExistsError('source target already exists')
    if data_root is not None:
        gate=check_memory_rollback(data_root,manifest)
        if gate['missing']:raise ValueError('memory rollback protections missing: '+','.join(gate['missing']))
    for row in manifest['files']:
        relative=Path(row['path'])
        if relative.is_absolute() or relative.drive or '..' in relative.parts:
            raise ValueError('snapshot paths must be relative')
        source=(snapshot/row['path']).resolve()
        if not source.is_relative_to(snapshot) or not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha256']:
            raise ValueError('historical snapshot failed validation')
    target.mkdir(parents=True)
    copied=0
    for package in ('saya_app','tests'):
        directory=root/package
        if not directory.exists():continue
        for source in directory.rglob('*.py'):
            if '__pycache__' in source.parts:continue
            if not source.resolve().is_relative_to(root):raise ValueError('source symlink escapes workspace')
            dest=target/source.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest);copied+=1
    for name in ('core_services.py','server5-22B----.py'):
        if (root/name).exists():shutil.copy2(root/name,target/name);copied+=1
    static_sources=0
    static=root/'static'
    if static.exists():
        for source in static.rglob('*'):
            if not source.is_file() or source.suffix.lower() not in {'.js','.css','.html','.svg','.glsl','.woff','.woff2','.ttf'}:
                continue
            if not source.resolve().is_relative_to(root):raise ValueError('static source symlink escapes workspace')
            dest=target/source.relative_to(root);dest.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dest);static_sources+=1
    for row in manifest['files']:
        dest=target/row['path'];dest.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(snapshot/row['path'],dest)
    (target/'static').mkdir(exist_ok=True)
    guard=target/'rehearsal_guard';guard.mkdir()
    (guard/'sitecustomize.py').write_text(NETWORK_GUARD,encoding='utf-8')
    report={'schema_version':1,'copied_python_sources':copied,'copied_static_sources':static_sources,'overlaid_files':len(manifest['files']),
            'live_data_included':False,'external_network':'denied','mode':'historical_source_rehearsal'}
    atomic_save_json(target/'source-report.json',report)
    return report


def run_baseline(target,python=None):
    target=Path(target).resolve()
    if not (target/'source-report.json').exists() or not (target/'rehearsal_guard/sitecustomize.py').exists():
        raise ValueError('verified source bundle required')
    (target/'.runtime').mkdir(exist_ok=True)
    (target/'fixture-dear/MMD').mkdir(parents=True,exist_ok=True)
    environment={key:value for key,value in os.environ.items() if not key.startswith(('API_','SAYA_','BRAVE_','PROJECT_ROOT','DEAR_ROOT'))}
    environment.update(PROJECT_ROOT=str(target),DEAR_ROOT=str(target/'fixture-dear'),SAYA_STARTUP_MODEL='api',
        SAYA_CHAT_MEMORY_DIR=str(target/'fixture-chats'),SAYA_API_CREDENTIALS_FILE=str(target/'fixture-credentials.json'),
        PYTHONPATH=str(target/'rehearsal_guard')+os.pathsep+str(target),PYTEST_DISABLE_PLUGIN_AUTOLOAD='1')
    command=[python or sys.executable,'-m','pytest',*BASELINE_TESTS,'-q','--basetemp='+str(target/'.runtime/test-data'),
             '--junitxml='+str(target/'baseline-junit.xml')]
    with (target/'baseline-output.log').open('w',encoding='utf-8') as output:
        result=subprocess.run(command,cwd=target,env=environment,stdout=output,stderr=subprocess.STDOUT,timeout=240)
    return {'exit_code':result.returncode,'evidence':'baseline-junit.xml','network_guard':'non_loopback_denied'}
