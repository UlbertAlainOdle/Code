"""Explicit arrangement changes permit one bounded past-source projection.

Version storage is an audit, not general recall permission. No old records are
backfilled and no snapshot is restored or made a current memory by this reader.
"""
import hashlib
import re
from contextlib import contextmanager
from contextvars import ContextVar


_KEY = 'temporal_predecessor'
_HASH = re.compile(r'[a-f0-9]{64}')
_UNSAFE = re.compile(r'记错|说错|写错|搞错|纠错|不属实|从未|从没|并未|其实|假如|假设|如果|可能|也许|他说|她说|[？?]')
_CHANGE = re.compile(r'不再(?:使用|采用|选用|用)([^，,。；;！？?]{1,24})[，,；;\s]*(?:而是)?改(?:用|为|成)')
_REVISION = ContextVar('confirmed_temporal_revision',default=None)


def text_hash(text):
    return hashlib.sha256(str(text).encode('utf8')).hexdigest()


def explicit_arrangement_change(text):
    """The original user source, not a paraphrased proposal, grants history."""
    raw=str(text or '')
    return bool(_CHANGE.search(raw)) and not _UNSAFE.search(raw)


def predecessor(current, text, *, history_floor=None):
    """Host-only proof for an explicit old-use -> new-use statement."""
    if (current is None or current.authority != 'user_confirmed' or _UNSAFE.search(text)
            or not ((current.metadata.get('write_pipeline') is True and current.metadata.get('intent') in {'explicit_save','explicit_correct'})
                    or re.fullmatch(r'[a-f0-9]{32}',str(current.metadata.get('qq_owner_admission',''))))
            or not current.metadata.get('evidence_refs')):
        return None
    change = _CHANGE.search(text)
    if not change:
        return None
    old_object = change[1].strip()
    if not old_object or not re.search(r'(?:使用|采用|选用|用)' + re.escape(old_object), current.canonical_text):
        return None
    if _UNSAFE.search(current.canonical_text) or re.search(r'不再|未用|不用|没有用', current.canonical_text):
        return None
    return {'version':1, 'revision':current.revision, 'history_floor':history_floor,
            'previous_sha256':text_hash(current.canonical_text), 'successor_sha256':text_hash(text)}


def mark(metadata, current, text):
    # Structured proposals cannot supply or carry this proof through a factual
    # correction. Ordinary controls retaining metadata fail the successor hash.
    metadata.pop(_KEY, None)
    from .memory_use import current_history_floor
    proof = predecessor(current, text, history_floor=current_history_floor())
    if proof:
        metadata[_KEY] = proof


@contextmanager
def confirmed_revision(identifier, old, new, *, enabled):
    """Bind only the original CAS-authorized revision commit, never a restore."""
    token=_REVISION.set((identifier,text_hash(old),text_hash(new)) if enabled else None)
    try:
        yield
    finally:
        _REVISION.reset(token)


def mark_confirmed_revision(metadata, current, text, history_floor):
    metadata.pop(_KEY,None)
    if _REVISION.get() != (current.id,text_hash(current.canonical_text),text_hash(text)):
        return
    proof=predecessor(current,text,history_floor=history_floor)
    if proof:
        metadata[_KEY]=proof


def selected_history(kb, question, items):
    """Return trusted display overrides for already-authorized current items."""
    from .memory_recall_contract import single_literal_focus
    focus = single_literal_focus(question)
    if not focus or re.search(r'现在|目前|如今|比较|对比|分别', question):
        return {}
    changed_before = re.search(r'改(?:用|为|成)([^，,。？?]{1,24}?)之前', question)
    if not changed_before and not re.search(r'上一版|前一版|变更前|更改前', question):
        return {}
    store = kb._open_memory_v4_store_for_read()
    if store is None:
        return {}
    result = {}
    with kb._memory_write_lock, store._lock:
        for raw in items:
            identifier = str(raw['id'])
            current = store.get_memory(identifier)
            if (current is None or store.is_tombstoned(identifier) or current.lifecycle != 'active'
                    or not kb.memory_allowed_for_use(current) or focus not in current.canonical_text
                    or current.canonical_text != raw.get('canonical_text')):
                continue
            if changed_before and not re.search(
                    r'改(?:用|为|成)' + re.escape(changed_before[1]), current.canonical_text):
                continue
            proof = current.metadata.get(_KEY)
            if (not isinstance(proof,dict) or proof.get('version') != 1
                    or 'history_floor' not in proof or proof['history_floor'] != kb.memory_use.history_floor()
                    or type(proof.get('revision')) is not int or not 1 <= proof['revision'] < current.revision
                    or not _HASH.fullmatch(str(proof.get('previous_sha256','')))
                    or proof.get('successor_sha256') != text_hash(current.canonical_text)):
                continue
            versions = [v for v in store.list_versions(identifier,limit=100) if v.revision == proof['revision']]
            if len(versions) != 1:
                continue
            old = versions[0].snapshot
            if (old.get('lifecycle') != 'active' or old.get('authority') != 'user_confirmed'
                    or any(old.get(k) != getattr(current,k) for k in ('id','subject','scope_type','scope_id','sensitivity'))
                    or focus not in str(old.get('canonical_text',''))
                    or text_hash(old.get('canonical_text','')) != proof['previous_sha256']
                    or predecessor(type(current).from_dict(old), current.canonical_text,
                                   history_floor=proof['history_floor']) != proof):
                continue
            if store.get_memory(identifier) != current or not kb.memory_allowed_for_use(current):
                continue
            result[identifier] = {'text':old['canonical_text'], 'revision':proof['revision'],
                                  'current_revision':current.revision}
    return result


def tool_items(kb, question, items):
    """Project only a host-bound question's already-authorized tool results."""
    items=list(items)
    history=selected_history(kb,question,items)
    return [dict(item,canonical_text=history[item['id']]['text'],
                 revision=history[item['id']]['revision'],valid_from=None,valid_to=None,
                 historical_source={k:v for k,v in history[item['id']].items() if k!='text'})
            if item['id'] in history else item for item in items]
