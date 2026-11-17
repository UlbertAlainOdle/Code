"""Host-owned provenance, separate from fact text, scores and disclosure grants.

Only ingress code binds this context. Model actions, HTTP metadata and imported
source labels cannot supply it. Persisted envelopes are copied, never inferred
from a nickname, source string, session filename or attachment contents.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import re
import uuid


FIELDS = ('origin', 'context_events', 'last_modified_channel', 'last_modified_origin')
MAX_EVENTS = 16
_current = ContextVar('memory_origin', default=None)


def _text(value, limit=160):
    return re.sub(r'\s+', ' ', str(value or '')).strip()[:limit]


def unknown_origin():
    return {'origin_version': 1, 'channel': 'unknown', 'verification': 'unknown',
        'verified_subject': None, 'bot_id': None, 'session_id': None,
        'message_id': None, 'observed_at': None, 'occurred_at': None,
        'visibility': {'kind': 'private'}}


def ingress(channel, *, subject=None, bot_id=None, session_id=None, message_id=None,
            group_id=None, quote='', observed_at=None):
    """Called with transport-verified IDs; no event occurrence time is inferred."""
    if channel not in {'web', 'qq'}:
        raise ValueError('unsupported_origin_channel')
    if channel == 'qq' and any(not re.fullmatch(r'[1-9]\d{0,19}', str(x or ''))
            for x in (subject, bot_id)):
        return {'origin': unknown_origin(), 'quote': ''}
    origin = {**unknown_origin(), 'channel': channel, 'verification': 'verified',
        'verified_subject': str(subject) if subject else None,
        'bot_id': str(bot_id) if bot_id else None, 'session_id': _text(session_id) or None,
        'message_id': _text(message_id) or 'operation:' + uuid.uuid4().hex,
        'observed_at': observed_at or datetime.now(timezone.utc).isoformat(timespec='seconds'),
        'visibility': {'kind': 'group', 'group_id': str(group_id)} if group_id else {'kind': 'private'}}
    # Provenance is bounded evidence, never a second raw conversation archive.
    evidence = _text(quote, 600)
    if re.search(r'(?i)password|passwd|api[ _-]?key|access[ _-]?token|密钥|密码|私钥|sk-[a-z0-9]{12,}', evidence):
        evidence = ''
    return {'origin': origin, 'quote': evidence}


@contextmanager
def bind_origin(context):
    token = _current.set(deepcopy(context))
    try:
        yield
    finally:
        _current.reset(token)


def current_origin():
    return deepcopy(_current.get())


def web_operation(reason):
    """Decorate synchronous web CRUD handlers, never consume request provenance."""
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            req = args[0] if args and isinstance(args[0], dict) else {}
            # Update envelopes describe the edit, not the former fact's author.
            updates = req.get('updates') if isinstance(req.get('updates'), dict) else {}
            content = req.get('content') or updates.get('content') or updates.get('canonical_text', '')
            context = ingress('web', quote=content)
            context['operation'] = reason
            with bind_origin(context):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


def projection(data):
    """Whitelist projection of already host-owned stored metadata."""
    data = data if isinstance(data, dict) else {}
    return {key: deepcopy(data[key]) for key in FIELDS if key in data}


def apply_origin(data, *, previous=None, reason='explicit_save', context=None):
    """Strip supplied provenance; preserve creation and append bounded evidence.

    `previous` must come from storage, not a request/action. Unknown old creation
    remains unknown even if the current editor is verified.
    """
    context = current_origin() if context is None else deepcopy(context)
    incoming = (context or {}).get('origin') or unknown_origin()
    stored = projection(previous)
    for key in FIELDS:
        data.pop(key, None)
    detail = data.get('source_detail')
    if isinstance(detail, dict):
        data['source_detail'] = {k: deepcopy(v) for k, v in detail.items() if k not in FIELDS}
    data.update(stored)
    data['origin'] = deepcopy(stored.get('origin') or (unknown_origin() if previous is not None else incoming))
    data['last_modified_channel'] = incoming['channel']
    data['last_modified_origin'] = deepcopy(incoming)
    events = deepcopy(stored.get('context_events') or [])
    quote = (context or {}).get('quote', '')
    if incoming['channel'] != 'unknown':
        key = json.dumps([incoming['channel'], incoming['bot_id'], incoming['verified_subject'],
            incoming['session_id'], incoming['message_id']], ensure_ascii=False)
        event_id = 'source-event:' + hashlib.sha256(key.encode()).hexdigest()[:24]
        if not any(e.get('id') == event_id for e in events):
            events.append({'id': event_id, 'title': _text(quote, 80) or '记忆操作证据',
                'summary': _text(quote, 240), 'reason': reason,
                'evidence_refs': [incoming['channel'] + ':' + incoming['message_id']],
                'participants': [incoming['verified_subject']] if incoming['verified_subject'] else [],
                'observed_at': incoming['observed_at'], 'occurred_at': None,
                'visibility': deepcopy(incoming['visibility']), 'origin': deepcopy(incoming),
                'context_coverage': 'current_message_only',
                'revises_event_id': events[-1]['id'] if events and reason in {'edit', 'explicit_correct','append','correct','restore','revision_confirm'} else None})
    # Creation evidence survives even after the bounded tail rolls over.
    data['context_events'] = events if len(events) <= MAX_EVENTS else [events[0], *events[-(MAX_EVENTS-1):]]
    if isinstance(data.get('source_detail'), dict):
        data['source_detail'].update(projection(data))
    return data


def visible_provenance(data, *, group_id=None):
    """Fact authorization never authorizes the private story behind that fact."""
    result = projection(data)
    if group_id is None:
        return result
    def visible(origin):
        scope = (origin or {}).get('visibility', {})
        return scope.get('kind') == 'group' and scope.get('group_id') == str(group_id)
    result['context_events'] = [e for e in result.get('context_events', []) if visible(e)]
    if not visible(result.get('origin')):
        result.pop('origin', None)
    if not visible(result.get('last_modified_origin')):
        result.pop('last_modified_origin', None)
        result.pop('last_modified_channel', None)
    return result
