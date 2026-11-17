"""Read-only R5 ranking and source-bound relationship proposals."""
from dataclasses import dataclass
import json
import math
import re
import time

from .memory_v4.retrieval import _tokens
from .memory_versions import record_version


MAX_RECORDS = 128
MAX_SOURCES = 32
_ASSERTIVE = {'assertion', 'experience', 'opinion', 'commitment', 'progress', 'decision', 'objection'}


@dataclass(frozen=True)
class Source:
    id: str
    root: str
    author: str
    text: str
    mode: str
    observed_at: float
    expires: float | str | None = None
    subject: str = ''
    span: tuple[int | None, int | None] = (None, None)


@dataclass(frozen=True)
class Record:
    id: str
    revision: str
    scope: tuple[str, ...]
    sources: tuple[Source, ...]
    tags: tuple[str, ...] = ()
    label: str = ''
    occurrence: tuple = ()
    split_from: str = ''

    @property
    def version(self):
        return record_version({'record': self.id, 'revision': self.revision, 'scope': self.scope,
                               'sources': [vars(source) for source in self.sources], 'tags': self.tags,
                               'label': self.label, 'occurrence': self.occurrence, 'split_from': self.split_from})


@dataclass(frozen=True)
class Reading:
    """A semantic proposal, bound to an exact span of one currently readable source.

    Keys are advisory normalized meanings, not facts or write authorizations.
    An empty condition means explicitly unconditioned, None means unknown.
    """
    source: str
    start: int
    quote: str
    object_key: str
    attribute: str
    condition: str | None
    value: str
    negated: bool
    period: str | None


@dataclass(frozen=True)
class PairReading:
    anchor_version: str
    candidate_version: str
    anchor: Reading
    candidate: Reading


def _bound_source(record, reading):
    if (type(reading.start) is not int or reading.start < 0 or
            not isinstance(reading.quote, str) or not reading.quote.strip() or len(reading.quote) > 2000 or
            type(reading.negated) is not bool or
            any(not isinstance(key, str) or not key.strip() or len(key) > 160
                for key in (reading.object_key, reading.attribute, reading.value)) or
            any(key is not None and (not isinstance(key, str) or len(key) > 160)
                for key in (reading.condition, reading.period))):
        return None
    matches = [source for source in record.sources if source.id == reading.source]
    if len(matches) != 1:
        return None
    source = matches[0]
    if source.text[reading.start:reading.start + len(reading.quote)] == reading.quote:
        return source
    return None


def _relation(anchor, candidate, proposal, common_sources):
    if anchor.split_from == candidate.id or candidate.split_from == anchor.id:
        return 'related_separate', 'explicit_split'
    if anchor.occurrence and candidate.occurrence and anchor.occurrence != candidate.occurrence:
        return 'related_separate', 'different_recorded_occurrence_or_period'
    if common_sources and common_sources == {_root_key(s) for s in anchor.sources} == {
        _root_key(s) for s in candidate.sources
    }:
        return 'duplicate_evidence', 'same_source_set'
    if proposal is None:
        return 'needs_review', 'no_source_bound_semantic_proposal'
    if (proposal.anchor_version, proposal.candidate_version) != (anchor.version, candidate.version):
        return 'needs_review', 'snapshot_changed'
    left, right = proposal.anchor, proposal.candidate
    a, b = _bound_source(anchor, left), _bound_source(candidate, right)
    if not a or not b:
        return 'needs_review', 'invalid_source_span'
    if a.mode not in _ASSERTIVE or b.mode not in _ASSERTIVE:
        return 'needs_review', 'non_assertive_or_unknown_source'
    if not a.author or not b.author or not a.subject or a.subject != b.subject:
        return 'needs_review', 'subject_or_author_unresolved'
    if a.mode != b.mode:
        return 'related_separate', 'different_modality'
    if anchor.scope[0] != 'memory' and not (common_sources and anchor.occurrence and candidate.occurrence):
        # Equal titles, dates, and proposed meanings do not identify an activity round.
        return 'needs_review', 'same_occurrence_unproven'
    if left.object_key != right.object_key:
        return 'related_separate', 'different_object'
    if left.condition is None or right.condition is None:
        return 'needs_review', 'condition_unresolved'
    if left.period is None or right.period is None:
        return 'needs_review', 'period_unresolved'
    if left.period != right.period:
        return 'related_separate', 'different_proposed_period'
    if left.attribute != right.attribute or left.condition != right.condition:
        return 'complementary', 'retain_both_attributes_or_conditions'
    if (left.value, left.negated) != (right.value, right.negated):
        return 'conflict_or_version', 'retain_both_claims'
    if a.author != b.author:
        return 'related_separate', 'independent_attribution'
    return 'duplicate_claim', 'retain_independent_source_ids'


def _root_key(source):
    # Two stored projections count as the same evidence only when the stable
    # source, original wording, author, and semantics all agree.
    return (source.root, source.author, source.text, source.mode, source.subject, source.span)


def review_candidates(anchor_id, candidate_ids, load, *, limit=12, proposals=None):
    """Re-read a bounded inventory through its existing authorization reader."""
    if (not isinstance(candidate_ids, (list, tuple)) or len(candidate_ids) > MAX_RECORDS or
            type(limit) is not int or not 1 <= limit <= MAX_RECORDS):
        raise ValueError('invalid_maintenance_candidate_budget')
    if proposals is not None and (not isinstance(proposals, dict) or len(proposals) > MAX_RECORDS or
            any(plan is not None and (not isinstance(plan, PairReading) or
                not isinstance(plan.anchor, Reading) or not isinstance(plan.candidate, Reading))
                for plan in proposals.values())):
        raise ValueError('invalid_maintenance_proposals')
    ids = (anchor_id, *candidate_ids)
    if any(not isinstance(key, str) or not key or len(key) > 128 for key in ids):
        raise ValueError('invalid_maintenance_record_id')
    anchor = load(anchor_id)
    if anchor is None:
        return {'status': 'anchor_unavailable', 'anchor': None, 'candidates': []}
    proposals = proposals or {}
    anchor_tags = set(anchor.tags)
    anchor_words = _words(anchor)
    anchor_at = max(source.observed_at for source in anchor.sources)
    anchor_roots = {_root_key(source) for source in anchor.sources if source.root and source.author}
    ranked = []
    for key in dict.fromkeys(candidate_ids):
        if key == anchor_id:
            continue
        candidate = load(key)
        if candidate is None or candidate.scope != anchor.scope:
            continue
        common = anchor_roots & {_root_key(source) for source in candidate.sources}
        tags = len(anchor_tags.intersection(candidate.tags))
        words = _words(candidate)
        lexical = len(anchor_words & words) / max(1, len(anchor_words | words))
        proposal = proposals.get(key)
        # A valid proposal can recall a paraphrase with no tag or word overlap.
        # Stale or fabricated proposals alone must not surface unrelated rows.
        semantic_match = (proposal is not None and
                          (proposal.anchor_version, proposal.candidate_version) == (anchor.version, candidate.version) and
                          _bound_source(anchor, proposal.anchor) and _bound_source(candidate, proposal.candidate))
        if not (tags or lexical or common or semantic_match):
            continue
        age = abs(anchor_at - max(source.observed_at for source in candidate.sources))
        relation, reason = _relation(anchor, candidate, proposal, common)
        ranked.append((bool(common), bool(semantic_match), tags, lexical, -age, key, {
            'record': candidate, 'relation': relation, 'reason': reason,
            'shared_source_count': len(common), 'shared_tag_count': tags, 'lexical_overlap': lexical,
        }))
    ranked.sort(key=lambda item: item[:6], reverse=True)
    return {'status': 'ready', 'anchor': anchor, 'candidates': [item[-1] for item in ranked[:limit]]}


def source_preview(review, anchor_version, candidate_version, *, source_limit=MAX_SOURCES, comparison=False):
    """Project one freshly re-read pair, without producing a merge operation."""
    anchor = review['anchor']
    if anchor is None or len(review['candidates']) != 1:
        return {'status': 'snapshot_changed'}
    selected = review['candidates'][0]
    candidate = selected['record']
    if (anchor_version, candidate_version) != (anchor.version, candidate.version):
        return {'status': 'snapshot_changed'}
    relation = selected['relation']
    if comparison and relation == 'needs_review' and selected['reason'] == 'no_source_bound_semantic_proposal':
        relation = 'parallel_review'
    if relation not in {'duplicate_evidence', 'duplicate_claim', 'complementary', 'parallel_review'}:
        return {'status': 'separate_review_required', 'relation': relation, 'reason': selected['reason']}
    return combine_sources(anchor, candidate, relation, source_limit=source_limit)


def combine_sources(anchor, candidate, relation, *, source_limit):
    """Build the projection from live, already authorized member snapshots."""
    groups = {}
    for member in (anchor, candidate):
        for source in member.sources:
            key = _root_key(source) if source.root and source.author else (member.id, source.id)
            groups.setdefault(key, []).append({'record_id': member.id, 'source': source})
    if len(groups) > min(source_limit, MAX_SOURCES):
        return {'status': 'source_capacity_exceeded', 'source_count': len(groups),
                'source_limit': min(source_limit, MAX_SOURCES)}
    return {'status': 'ready', 'relation': relation,
            'members': {anchor.id: anchor.version, candidate.id: candidate.version},
            'source_count': len(groups), 'sources': list(groups.values())}


def _words(record):
    return _tokens(' '.join([record.label, *(source.text for source in record.sources)]))


def _matches_current_user_assertion(canonical, quote):
    from .memory_reference import explicit_save_body

    original = _selected_user_text(canonical)
    if original is not None:
        return original == quote
    if canonical.startswith(('用户选择保存本次', '用户明确选择保存的陈述：')):
        return False  # A user's consent to save does not establish original authorship.
    return canonical == quote or canonical == explicit_save_body(quote)


def _selected_user_text(canonical):
    for prefix in ('用户选择保存本次用户的表述：「', '用户选择保存本次用户的原文片段：「'):
        if canonical.startswith(prefix) and canonical.endswith('」'):
            return canonical[len(prefix):-1]
    return None


def _compatible_reference_lineage(store, item):
    """Accept metadata-only native projections, never a content revision or restore."""
    if item.revision > 32:
        return False
    versions = sorted(store.list_versions(item.id, limit=item.revision + 1), key=lambda v: v.revision)
    if [version.revision for version in versions] != list(range(1, item.revision + 1)):
        return False
    current = item.to_dict()
    fields = ('canonical_text', 'authority', 'subject', 'scope_type', 'scope_id',
              'lifecycle', 'valid_from', 'valid_to', 'pinned', 'locked')
    return all(
        version.operation == ('create' if index == 0 else 'dual_write')
        and all(version.snapshot.get(field) == current[field] for field in fields)
        and version.snapshot.get('metadata', {}).get('origin') == item.metadata.get('origin')
        and version.snapshot.get('metadata', {}).get('evidence_refs') == item.metadata.get('evidence_refs')
        for index, version in enumerate(versions)
    )


def _committed_reference_source(journal, actor, session, item, legacy, evidence, store):
    """Resolve synthetic save commands through the exact committed R3 plan."""
    from .memory_reference import explicit_save_body
    from .memory_write_operations import fingerprint

    original = _selected_user_text(item.canonical_text)
    if (journal is None or not original or not legacy or not any(
            explicit_save_body(row.quote) == item.canonical_text for row in evidence)):
        return ()
    scope = f'{actor.bot_id}:private:{actor.user_id}:shared'
    with journal.connect() as db:
        if db.execute("select count(*) from sqlite_master where type='table' and "
                      "name in ('qq_chat_save_operations','qq_action_proposals')").fetchone()[0] != 2:
            return ()
        proofs = db.execute('''select o.fingerprint,o.kind,p.payload,r.key,r.value
            from qq_chat_save_operations o join qq_action_proposals p
              on p.id=o.id and p.scope=o.scope and p.session_id=o.session and p.kind=o.kind
            join json_each(o.result) r
            where o.scope=? and o.session=? and o.expires>? and p.state='done'
              and o.kind in ('save_reference','save_reference_set')
              and json_extract(r.value,'$.state')='committed' and json_extract(r.value,'$.memory_id')=?
            order by o.rowid desc limit 8''', (scope, session, time.time(), item.id)).fetchall()
    if not proofs or not _compatible_reference_lineage(store, item):
        return ()
    version = record_version(legacy)
    for proof in proofs:
        payload = json.loads(proof['payload'])
        plans = payload['plans'] if proof['kind'] == 'save_reference_set' else [payload['plan']]
        if (fingerprint(plans) != proof['fingerprint'] or not 0 <= proof['key'] < len(plans)
                or (item.revision == 1 and json.loads(proof['value']).get('version') != version)):
            continue
        plan = plans[proof['key']]
        if (plan.get('scope'), plan.get('session'), plan.get('author'), plan.get('content')) != (
                scope, session, '用户', item.canonical_text):
            continue
        sources = plan.get('sources') or []
        if len(sources) != 1:
            continue
        source = sources[0]
        root = source.get('hash')
        observed = source.get('sent_at')
        if (isinstance(root, str) and re.fullmatch('[a-f0-9]{64}', root)
                and type(observed) in (int, float) and math.isfinite(observed)):
            return (Source('saved-user:' + root, 'saved-user:' + root, actor.user_id,
                           original, 'assertion', observed, item.valid_to, actor.user_id),)
    return ()


def memory_record(kb, reader, actor, identifier, *, protect=False, journal=None):
    """Use the current native body, never archived or superseded quotes."""
    from .memory_health import native_version
    from .memory_v4.retrieval import _instant

    items = reader.fetch_full([identifier])
    if not items:
        return None
    item = items[0]
    legacy = kb._find_memory(item.id) or {}
    if protect and (item.locked or item.pinned or legacy.get('is_locked') or
                    legacy.get('is_important') or legacy.get('managed_slot')):
        return None
    origin = item.metadata.get('origin')
    if not isinstance(origin, dict):
        return None
    visibility = origin.get('visibility')
    session = origin.get('session_id')
    if (item.scope_type != 'global' or item.subject != 'user' or item.authority != 'user_confirmed' or
            origin.get('channel') != 'qq' or origin.get('verification') != 'verified' or
            origin.get('verified_subject') != actor.user_id or origin.get('bot_id') != actor.bot_id or
            not isinstance(visibility, dict) or visibility.get('kind') != 'private' or
            not isinstance(session, str) or not session):
        return None
    prefix = session + ':user:'
    evidence_rows = [evidence for evidence in reader.store.list_evidence(item.id)
                     if evidence.source_type == 'user_message' and evidence.role == 'assertion'
                     and evidence.source_id.startswith(prefix)]
    sources = tuple(
        Source(evidence.id, evidence.source_id, actor.user_id, evidence.quote, evidence.role,
               _instant(evidence.observed_at), item.valid_to, actor.user_id,
               (evidence.quote_start, evidence.quote_end))
        for evidence in evidence_rows if _matches_current_user_assertion(item.canonical_text, evidence.quote)
    )
    if not sources:
        sources = _committed_reference_source(journal, actor, session, item, legacy, evidence_rows, reader.store)
    if not 1 <= len(sources) <= MAX_SOURCES:
        return None
    tags = tuple(tag for tag in legacy.get('tags', ()) if isinstance(tag, str))
    period = (item.valid_from, item.valid_to) if item.valid_from or item.valid_to else ()
    return Record(item.id, native_version(item, False), ('memory', actor.bot_id, actor.user_id),
                  sources, tags, item.canonical_text, period)


def event_record(unified, message, kind, group, identifier):
    """Only requester-visible provenance can contribute to a review snapshot."""
    service = unified.service
    provenance = service.provenance
    card = provenance.load(message, kind, identifier)
    if not card or (kind == 'group' and card['group'] != group):
        return None
    now = service.clock()
    retention = service.retention(message)
    facts = [fact for fact in card['_facts'] if fact['at'] + retention > now]
    if not 1 <= len(facts) <= MAX_SOURCES:
        return None
    sources = []
    for fact in facts:
        author = fact.get('actor') or fact.get('speaker') or ''
        subject = fact.get('subject') or ''
        span = fact.get('source_span') or (None, None)
        sources.append(Source(
            str(provenance.source_id(kind, fact)), fact.get('evidence_id') or fact['source'],
            author, fact.get('text', fact.get('quote', '')), fact.get('mode') or '',
            fact['at'], fact['expires'], author if subject == 'self' else subject,
            tuple(span),
        ))
    if kind == 'group':
        decision = next((fact for fact in facts if fact['id'] == card.get('current_decision')), None)
        appointment = ((decision or {}).get('proposed_timing') or {}).get('appointment') or {}
    else:
        appointment = (card.get('timing') or {}).get('appointment') or {}
        if not any(fact['source'] == appointment.get('source') for fact in facts) or appointment.get('source_expires', 0) <= now:
            appointment = {}
    occurrence = (appointment['at'], appointment['precision']) if appointment.get('status') == 'known' else ()
    label = card.get('topic_anchor') or card.get('title') or ''
    if not label or not any(label in source.text for source in sources):
        label = ''
    tags = (label,) if label else ()
    scope = (kind, unified.bot, group if kind == 'group' else message.user_id)
    revision = provenance.view_hash(kind, {**card, '_facts': facts})
    return Record(identifier, revision, scope, tuple(sources), tags, label, occurrence,
                  (card.get('split_from') or {}).get('id', ''))
