"""Host plans and durable results over the existing KnowledgeBase writers."""
from collections.abc import Iterable, Mapping
from copy import deepcopy
from pathlib import Path
import json
import re
from uuid import uuid4

from .memory_origin import current_origin
from .memory_versions import record_version
from .memory_write_operations import MemoryOperationStore, MemoryWriteOperations, fingerprint


def journal(kb):
    with kb._memory_write_lock:
        service = getattr(kb, '_chat_write_operations', None)
        if service is None:
            service = MemoryWriteOperations(MemoryOperationStore(Path(kb.base_dir) / 'memory3.sqlite3'),
                                            kb._memory_write_lock)
            kb._chat_write_operations = service
        return service


def identity(kind, session, operation=None):
    origin = (current_origin() or {}).get('origin', {})
    scope = fingerprint([origin.get('channel', 'internal'), origin.get('bot_id'),
                         origin.get('verified_subject'), origin.get('visibility')])
    source = operation or origin.get('message_id') or uuid4().hex
    return fingerprint([scope, session, kind, source])[:32], scope


def capture(kb, text, project_id=None, project_name=None, session=None, *, allow_save=True):
    from .memory_reference import explicit_save_body
    from .memory_v4.write_pipeline import WriteIntentGate

    v4 = kb.settings.get('memory_v4_write_enabled', False)
    decision = WriteIntentGate().detect(text) if v4 else None
    legacy = any(pattern.match(str(text or '').strip()) for pattern in kb._EXPLICIT_SAVE_COMMANDS)
    if not explicit_save_body(text) and not legacy and (not decision or decision.intent == 'none'):
        return None
    if not allow_save and (not decision or decision.intent not in {'explicit_correct', 'explicit_forget'}):
        return None
    if decision and decision.intent in {'explicit_correct', 'explicit_forget'}:
        action = {'scope_type': 'project', 'scope_id': str(project_id)} if project_id else None
        return pipeline(kb, text, action=action, turn_id=session)[0]
    operation, scope = identity('capture', session)
    epoch = kb.memory_use.snapshot()
    plan = {'request': text, 'project_id': project_id, 'project_name': project_name, 'allow_save': allow_save}
    def write(_):
        saved = kb._capture_explicit_memory_impl(text, project_id, project_name, session, allow_save=allow_save)
        return ({'state': 'committed', 'memory_id': saved['id'], 'version': record_version(saved)}
                if saved else {'state': 'not_saved'})
    return journal(kb).run(operation, scope, session or '', 'capture', [plan],
                           verify=lambda db: True,
                           valid=lambda plan: kb.settings.get('auto_save_memories', True) and kb.memory_use.snapshot() == epoch,
                           write=write, retention=30 * 86400)


def saved_memory(kb, result):
    if result and result.committed:
        item = result.items[0]
        memory = kb._find_memory(item.get('memory_id'))
        if memory and record_version(memory) == item.get('version'):
            return memory
        for change in item.get('changes', []):
            stored = kb.memory_v4_store.get_memory(change['memory_id']) if kb.memory_v4_store else None
            if stored and stored.revision == change['revision']:
                return kb._find_memory(change['memory_id'])
    return None


def capture_before_answer(kb, text, project_id=None, project_name=None, session=None):
    """Settle a web mutation before binding any context for its answer."""
    with kb._memory_write_lock:
        return capture(kb, text, project_id, project_name, session)


def pipeline(kb, user_input, *, operation_id=None, **options):
    context = options.get('context')
    if isinstance(context, Iterable) and not isinstance(context, (Mapping, str, bytes)):
        options['context'] = list(context)
    options = deepcopy(options)
    user_input = str(user_input or '')
    operation, scope = identity('v4', options.get('turn_id'), options.get('message_id'))
    if operation_id is not None:
        if not isinstance(operation_id, str) or not re.fullmatch(r'[a-f0-9]{32}', operation_id):
            raise ValueError('invalid_memory_operation_id')
        operation = operation_id
    plan = {'request': user_input, **options}
    fresh = []
    def write(_):
        from .memory_v4.write_pipeline import WriteIntentGate
        decision = WriteIntentGate().detect(user_input,options.get('action'))
        if decision.intent in {'explicit_correct','explicit_forget'} or options.get('intent') in {'explicit_correct','explicit_forget'}:
            kb.memory_use.advance()
            with kb.memory_use.bind():
                response = kb._process_memory_write_v4_impl(user_input, **options)
        else:
            response = kb._process_memory_write_v4_impl(user_input, **options)
        fresh.append(response)
        changes = []
        for entry in response.get('committed', []):
            memory = entry.get('memory') or {}
            changes.append({'memory_id': memory.get('id') or entry.get('memory_id'),
                            'revision': memory.get('revision'), 'operation': entry.get('operation')})
        candidates = [entry['id'] for entry in response.get('candidates', [])]
        state = ('partial' if response.get('status') in {'partial', 'cancelled'} and (changes or candidates)
                 else 'committed' if changes or candidates
                 else 'unknown' if response.get('status') in {'error', 'cancelled'} else 'not_saved')
        return {'state': state, 'status': response.get('status'), 'changes': changes,
                'candidate_ids': candidates, 'forgotten': response.get('forgotten', []),
                'target_versions': response.get('target_versions', {}),
                'projection_pending': response.get('projection_pending', []),
                'rejected': [{'reason_codes': entry.get('reason_codes') or entry.get('validation', {}).get('reason_codes', [])}
                             for entry in response.get('rejected', [])],
                'intent': {k: v for k, v in response.get('intent', {}).items() if k != 'content'}}
    result = journal(kb).run(operation, scope, options.get('turn_id') or '', 'v4', [plan],
                             verify=lambda db: True, valid=lambda plan: kb.settings.get('memory_v4_write_enabled', False),
                             write=write, retention=30 * 86400)
    if fresh:
        response = fresh[0]
    else:
        item = result.items[0]
        changes = []
        for change in item.get('changes', []):
            memory = kb.memory_v4_store.get_memory(change['memory_id']) if kb.memory_v4_store else None
            stored = (memory.to_dict() if memory and memory.revision == change['revision']
                      else {'id': change['memory_id'], 'revision': change['revision'], 'content_changed': True})
            changes.append({'operation': change['operation'], 'memory': stored})
        response = {'status': item.get('status', item['state']), 'intent': item.get('intent', {}),
                    'committed': changes, 'forgotten': item.get('forgotten', []), 'candidates': [],
                    'candidate_ids': item.get('candidate_ids', []), 'rejected': item.get('rejected', []),
                    'target_versions': item.get('target_versions', {}),
                    'projection_pending': item.get('projection_pending', [])}
    return result, {**response, 'operation_id': result.id, 'operation_state': result.items[0]['state'],
                    'replayed': not result.fresh}


def public_result(result):
    return {'operation_id': result.id, 'committed': result.committed,
            'items': result.items, 'replayed': not result.fresh}


def prompt_receipt(kb,result,enabled,*,request_text=None):
    if result is None and request_text is not None:
        from .memory_v4.write_pipeline import WriteIntentGate
        from .memory_reference import save_reference
        if WriteIntentGate().detect(request_text).intent == 'none' and not save_reference(request_text):
            return ''
    receipt=public_result(result) if result is not None else {'committed':False,'operation_id':None}
    receipt['saving_enabled']=enabled and kb.settings.get('auto_save_memories',True)
    with kb._memory_write_lock:
        memory=saved_memory(kb,result)
        if memory:
            receipt.update(saved_content=str(memory.get('content') or '')[:2000],
                           content_truncated=len(str(memory.get('content') or ''))>2000)
    return ('[Host memory write result]\n'+json.dumps(receipt,ensure_ascii=False)+
            '\nOnly this host result establishes whether this request committed a memory change. '
            'For explicit_forget, committed means removal, not a new save. '
            'Confirm only the recorded removal; do not restate the forgotten content from history. '
            'Removal from long-term memory does not establish erasure of original conversations, '
            'all copies, or all future history use. Never promise that no traces remain. '
            'For explicit_correct, use the recorded updated content. '
            'A missing, invalid, unknown or pending result does not establish a save. '
            'Use the same recorded object and content in your answer. Do not invent a save or confirmation.\n')
