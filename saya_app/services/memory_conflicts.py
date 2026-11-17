"""Memory conflict negotiation: ask before silently overwriting a memory.

Most memory systems supersede contradicting facts silently. Here, when a new
memory (explicit save or confirmed candidate) lands in the same category and
topic band as an already-confirmed memory, a single bounded model call checks
whether they truly contradict. If (and only if) it says yes, a negotiation
card is attached to the reply's confirmation box letting Doctor choose:
adopt the new fact and retire the old one (graph supersede edges), keep
both, or discard the new candidate.

Fail-open everywhere: no invoke, unparseable answers or the daily cap being
reached all mean "no card", never a blocked save.
"""

import json
import copy
from .memory_versions import record_version, check_version, MemoryVersionConflict
from .qq_tasks import guarded_memory_write
import os
import re
import threading
from datetime import datetime, timedelta

_LOCK = threading.Lock()

# Categories the store classifies facts into (the set its own correction
# heuristic considers); a vague "general" save is compared against all of them.
TYPED_CATEGORIES = ("identity", "preference", "relationship", "project", "goal", "schedule")

# Topic-band similarity: below this the memories are unrelated, above it the
# existing near-duplicate merge already handled them.
CONFLICT_SIMILARITY_MIN = 0.05
CONFLICT_SIMILARITY_MAX = 0.60


def _state_path(base_dir):
    return os.path.join(str(base_dir), "memory_conflicts.json")


def _load(base_dir):
    path = _state_path(base_dir)
    if not os.path.exists(path):
        return {"conflicts": []}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("conflicts"), list):
            return data
    except (OSError, ValueError):
        pass
    return {"conflicts": []}


def _save(base_dir, state):
    path = _state_path(base_dir)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)
    os.replace(tmp, path)


def _today():
    return datetime.now().strftime("%Y-%m-%d")


def _tokenize(text):
    text = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]{2,}", text))
    for segment in re.findall(r"[\u3400-\u9fff]+", text):
        words.update(segment[i:i + 2] for i in range(max(0, len(segment) - 1)))
    return words


def _jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _similarity(a, b):
    """Best of Jaccard and containment.

    Containment (|A n B| / min(|A|,|B|)) survives heavy rewording: "喜欢喝拿铁"
    vs "改喝美式了，拿铁太甜" share only one token, but that token carries the
    topic. The model verification and the daily caps are what keep precision,
    so recall is the right bias here.
    """
    if not a or not b:
        return 0.0
    shared = len(a & b)
    if shared == 0:
        return 0.0
    return max(_jaccard(a, b), shared / max(1, min(len(a), len(b))))


def find_shortlist(kb, new_memory, limit=3):
    """Confirmed memories the new one may contradict.

    Primary source: the store's own correction heuristic already retired some
    memories when this one was saved - those supersedes edges ARE the silent
    overwrite this feature exists to surface, so they rank first and are never
    filtered out for carrying superseded_by. Secondary: same-category memories
    inside the contradiction similarity band that the heuristic missed (their
    overlap was under its threshold, yet a model may still call them
    contradictory).
    """
    new_memory = new_memory if isinstance(new_memory, dict) else {}
    new_id = str(new_memory.get("id") or "")
    category = str(new_memory.get("category") or "").strip().lower()
    # An explicit "记住..." save often lands in the low-information "general"
    # bucket, while the fact it contradicts was classified (preference etc.).
    # Compare vague saves against every typed category instead of missing the
    # conflict entirely; typed saves stay category-scoped for precision.
    if category in TYPED_CATEGORIES:
        allowed_categories = {category}
    elif category in {"", "general"}:
        allowed_categories = set(TYPED_CATEGORIES)
    else:
        allowed_categories = {category}
    retired_ids = {str(i) for i in (new_memory.get("supersedes") or [])}
    retired_ids |= {str(i) for i in (new_memory.get("conflicts_with") or [])}
    new_terms = _tokenize(new_memory.get("content"))
    hits = []
    for memory in kb.memories:
        memory_id = str(memory.get("id") or "")
        if not memory_id or memory_id == new_id:
            continue
        if str(memory.get("category") or "").strip().lower() not in allowed_categories:
            continue
        if memory.get('managed_slot') or not kb.memory_allowed_for_use(memory):
            continue
        if memory.get("is_archived") or memory.get("status") == "archived":
            continue
        superseded_by = str(memory.get("superseded_by") or "")
        if memory_id in retired_ids or superseded_by == new_id:
            hits.append((1.0, memory))   # silently retired by this very save
            continue
        if superseded_by:
            continue                     # settled in an earlier round
        score = _similarity(new_terms, _tokenize(memory.get("content")))
        if CONFLICT_SIMILARITY_MIN <= score <= CONFLICT_SIMILARITY_MAX:
            hits.append((score, memory))
    hits.sort(key=lambda pair: -pair[0])
    return [memory for _, memory in hits[:limit]]


def build_verification_prompt(old_memory, new_content, new_title=""):
    old = str(old_memory.get("content") or "")[:400]
    new = str(new_content or "")[:400]
    return (
        "You are Saya's memory conflict checker.\n"
        "Decide whether the NEW fact directly contradicts the OLD fact (same topic, "
        "mutually exclusive statements). A mere related fact, an update with more "
        "detail, or a different topic is NOT a contradiction.\n"
        'Return JSON only: {"is_contradiction": true/false, "reason": "one short sentence"}\n\n'
        f"OLD fact: {old}\n"
        f"NEW fact: {new_title} - {new}\n"
    )


def parse_verification(text):
    """True/False from the model answer, or None when unparseable (fail-open)."""
    raw = str(text or "").strip()
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except ValueError:
        return None
    value = data.get("is_contradiction")
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    return None


def pending(base_dir):
    return [c for c in _load(base_dir)["conflicts"] if c.get("status") in {"pending", "applying"}]


def get(base_dir, conflict_id):
    for conflict in _load(base_dir)["conflicts"]:
        if str(conflict.get("id")) == str(conflict_id):
            return conflict
    return None


def maybe_create(base_dir, kb, invoke, new_memory, origin, daily_limit=2):
    """Kept for callers that only want the shortlist without verification."""
    if not new_memory or not str(new_memory.get("id") or ""):
        return None
    shortlist = find_shortlist(kb, new_memory)
    if not shortlist or not callable(invoke):
        return None
    return {"old": shortlist[0]}


def verified_create(base_dir, kb, invoke, new_memory, origin, daily_limit=2):
    """Full pipeline: shortlist -> model verification -> pending conflict.

    ``invoke`` is a blocking callable(prompt) -> str; run it from a worker
    thread. Returns the conflict card payload dict, or None (fail-open).
    """
    with kb._memory_write_lock:
        if not isinstance(new_memory, dict): return None
        new_memory = new_memory.get('memory', new_memory)
        if not isinstance(new_memory, dict): return None
        new_id = str(new_memory.get('id') or '')
        current_new = kb._find_memory(new_id) or kb._find_memory_candidate(new_id)
        if current_new is None or record_version(current_new) != record_version(new_memory): return None
        if current_new.get('managed_slot') or (kb._find_memory(new_id) and not kb.memory_allowed_for_use(current_new)): return None
        provisional = maybe_create(base_dir, kb, invoke, current_new, origin, daily_limit)
        if not provisional: return None
        old = copy.deepcopy(provisional['old'])
        new_memory = copy.deepcopy(current_new)
        targets = {old['id']: {'kind': 'memory', 'version': record_version(old)},
            new_id: {'kind': 'memory' if kb._find_memory(new_id) else 'candidate', 'version': record_version(new_memory)}}
        use_epoch = kb.memory_use.snapshot()
    try:
        answer = invoke(build_verification_prompt(old, new_memory.get("content"), new_memory.get("title")))
    except Exception:
        return None
    if parse_verification(answer) is not True:
        return None
    with kb._memory_write_lock, _LOCK:
        if kb.memory_use.snapshot() != use_epoch: return None
        for target_id, target in targets.items():
            current = kb._find_memory(target_id) if target['kind'] == 'memory' else kb._find_memory_candidate(target_id)
            if current is None or record_version(current) != target['version']: return None
        state = _load(base_dir)
        # Re-check cap and avoid duplicating an identical pending conflict.
        today = _today()
        for conflict in state["conflicts"]:
            if (conflict.get("status") == "pending"
                    and conflict.get("old_memory", {}).get("id") == old.get("id")
                    and conflict.get("new_memory", {}).get("content") == new_memory.get("content")):
                return None
        if sum(1 for c in state["conflicts"] if str(c.get("created_at", ""))[:10] == today) >= max(1, int(daily_limit)):
            return None
        conflict_id = f"cf-{datetime.now().strftime('%Y%m%d%H%M%S')}-{len(state['conflicts'])}"
        conflict = {
            "id": conflict_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "origin": str(origin or "explicit"),
            "status": "pending",
            "resolution": None,
            "target_versions": targets,
            "new_memory": {
                "id": str(new_memory.get("id")),
                "title": str(new_memory.get("title") or "")[:120],
                "content": str(new_memory.get("content") or "")[:600],
                "category": str(new_memory.get("category") or "general"),
            },
            "old_memory": {
                "id": str(old.get("id")),
                "title": str(old.get("title") or "")[:120],
                "content": str(old.get("content") or "")[:600],
                "category": str(old.get("category") or "general"),
            },
        }
        state["conflicts"].append(conflict)
        if len(state["conflicts"]) > 200:
            state["conflicts"] = [c for c in state["conflicts"] if c.get("status") == "pending"][-50:]
        _save(base_dir, state)
        return card_payload(conflict)


def card_payload(conflict):
    """The payload rendered inside the reply's confirmation box."""
    return {
        "id": conflict["id"],
        "content_version": record_version(conflict),
        "origin": conflict.get("origin", "explicit"),
        "new": conflict.get("new_memory", {}),
        "old": conflict.get("old_memory", {}),
    }


@guarded_memory_write
def resolve(base_dir, kb, conflict_id, action, *, expected_version=None):
    """Bind a verdict to the verified targets. Unknown partial writes never replay."""
    action = str(action or '').strip().lower()
    if action not in {'supersede_old', 'keep_both', 'discard_new'}:
        return {'status': 'error', 'message': 'unknown action'}
    with kb._memory_write_lock, _LOCK:
        state = _load(base_dir)
        conflict = next((c for c in state['conflicts'] if str(c.get('id')) == str(conflict_id)), None)
        if conflict is None or conflict.get('status') == 'resolved':
            return {'status': 'error', 'message': 'conflict not found or already resolved'}
        if conflict.get('status') != 'pending':
            return {'status': 'unknown', 'message': '上次裁决结果待核对，未再次执行；请重新读取记忆。'}
        check_version(conflict, expected_version)
        targets = conflict.get('target_versions')
        if not isinstance(targets, dict) or len(targets) != 2:
            raise MemoryVersionConflict('旧冲突缺少已核实的对象版本，请重新核对后生成裁决。')
        old_id, new_id = conflict['old_memory']['id'], conflict['new_memory']['id']
        for target_id in [old_id, new_id]:
            target = targets.get(target_id, {})
            current = kb._find_memory(target_id) if target.get('kind') == 'memory' else kb._find_memory_candidate(target_id)
            check_version(current, target.get('version') or '')
            if current.get('managed_slot') or (target.get('kind') == 'memory' and not kb.memory_allowed_for_use(current)):
                raise MemoryVersionConflict('对象已停用或不可修改，请重新核对。')
        if action == 'discard_new' and conflict.get('origin') != 'candidate':
            return {'status': 'error', 'message': '显式保存的记忆不能由候选丢弃操作撤销。'}
        pending_candidate = targets[new_id]['kind'] == 'candidate'
        if pending_candidate and action != 'discard_new':
            return {'status': 'error', 'message': '请先在候选管理中确认保存该条，再裁决关系。'}
        # Persist intent first; a crash or unknown commit must not replay the action.
        conflict.update(status='applying', resolution=action)
        try: _save(base_dir, state)
        except OSError:
            return {'status': 'error', 'message': '裁决意图未保存，未修改记忆。'}
        if pending_candidate:
            applied = kb.ignore_memory_candidate(new_id, expected_version=targets[new_id]['version'])
        else:
            current = kb._find_memory(new_id)
            supersedes = [str(i) for i in current.get('supersedes', []) if str(i) != old_id]
            if action == 'supersede_old': supersedes.append(old_id)
            updates = {'supersedes': supersedes}
            if action == 'discard_new': updates['is_archived'] = True
            # The existing relationship synchronizer updates both ends in one save.
            applied = kb.update_memory(new_id, updates, expected_version=targets[new_id]['version'])
        if not applied:
            return {'status': 'unknown', 'message': '记忆提交未确认，裁决保留待核对；未标记处理成功。'}
        conflict.update(status='resolved', resolved_at=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
        try: _save(base_dir, state)
        except OSError:
            return {'status': 'partial', 'committed': True, 'message': '记忆已提交，裁决状态未保存；请重新读取，勿重复执行。'}
        return {'status': 'success', 'committed': True, 'action': action, 'conflict_id': conflict_id}


def prune_expired(base_dir, max_age=timedelta(days=7)):
    with _LOCK:
        state = _load(base_dir)
        kept = []
        for conflict in state["conflicts"]:
            try:
                created = datetime.strptime(str(conflict.get("created_at")), "%Y-%m-%d %H:%M:%S")
                if conflict.get("status") == "pending" and datetime.now() - created > max_age:
                    continue
            except ValueError:
                pass
            kept.append(conflict)
        state["conflicts"] = kept
        _save(base_dir, state)
