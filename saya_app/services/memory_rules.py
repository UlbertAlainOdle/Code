"""Shared memory policy: conservative updates and evidence-based promotion."""
import re

POLICY_VERSION = 2026101201
DEFAULT_FEATURES = {
    "auto_save_memories": True,
    "memory_2_enabled": True, "layered_memory_enabled": True,
    "sqlite_memory_enabled": True, "sqlite_fts_enabled": True,
    "memory_v4_write_enabled": True, "memory_v4_read_enabled": True,
    "prompt_compiler_v2_enabled": True, "daily_state_v2_enabled": True,
    "embedding_enabled": True, "embedding_auto_index_new": True,
    "autonomous_memory_enabled": True,
}

_CORRECTION = re.compile(r"不再|改成|改为|更正|纠正|修正|不喜欢|no longer|not anymore|instead|changed to|correction", re.I)
_TRANSIENT = re.compile(r"今天|此刻|这会儿|暂时|临时|这几天|today|right now|temporarily|for now", re.I)
_UNSUPPORTED = re.compile(r"可能|也许|大概|猜测|如果|假设|开玩笑|他说|她说|有人说|maybe|perhaps|suppose|if\b|joking|someone said", re.I)
_GENERIC = re.compile(r"(?:我|你|他|她|的|是|了|不再|不喜欢|喜欢|偏好|以后|现在|之前|已经|改成|改为|更正|纠正|记住|请|不要|讨厌|最|非常|很|爱吃|目标|计划)")
_EN_STOP = {"i", "you", "he", "she", "the", "a", "an", "is", "am", "are", "my", "your", "like", "likes", "prefer", "prefers", "now", "not", "no", "longer", "instead", "to", "of", "and", "remember", "please"}


def explicit_change(text):
    return bool(_CORRECTION.search(str(text or ""))) and not _UNSUPPORTED.search(str(text or ""))


def fact_terms(text):
    text = str(text or "").lower()
    words = set(re.findall(r"[a-z][a-z0-9_-]{1,}", text)) - _EN_STOP
    for run in re.findall(r"[\u3400-\u9fff]+", _GENERIC.sub(" ", text)):
        if len(run) >= 2:
            words.update(run[i:i+2] for i in range(len(run)-1))
    return words


def same_fact_topic(left, right):
    # A text-only matcher cannot resolve other people's identity safely.
    # Their updates require an explicit target, rather than sharing "coffee".
    third_party = r"他|她|我的(?:朋友|同事|家人|妈妈|爸爸|父亲|母亲|弟弟|姐姐)|\b(?:he|she|they|my friend|my mother|my father)\b"
    if re.search(third_party, str(left), re.I) or re.search(third_party, str(right), re.I):
        return False
    a, b = fact_terms(left), fact_terms(right)
    return bool(a and b and len(a & b) / min(len(a), len(b)) >= .5)


def recurring_candidate_block_reason(candidate):
    """A model score and repeated summaries do not constitute user evidence."""
    text = str(candidate.get("content") or "")
    if _UNSUPPORTED.search(text):
        return "uncertain_or_attributed_statement"
    if _TRANSIENT.search(text):
        return "temporary_state_requires_review"
    if candidate.get("conflicts_with") or candidate.get("supersedes"):
        return "conflicting_candidate_requires_review"
    detail = candidate.get("source_detail") if isinstance(candidate.get("source_detail"), dict) else {}
    if detail.get("evidence_type") != "user_assertion":
        return "user_assertion_evidence_required"
    evidence = detail.get("user_evidence", [])
    # Evidence must be verbatim statements, bound to distinct source sessions.
    sessions = {str(e.get("session_id")) for e in evidence if isinstance(e, dict)
                and e.get("session_id") and str(e.get("quote") or "").strip()
                and not re.search(r"[?？]", str(e.get("quote")))
                and not _UNSUPPORTED.search(str(e.get("quote")))}
    if len(sessions) < 3:
        return "independent_user_evidence_required"
    return ""
