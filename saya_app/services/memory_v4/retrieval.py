"""Bounded V4 retrieval, reranking, and usage receipts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import re
from typing import Any, Iterable

from .models import MemoryItemV4, ValidationError
from .store import MemoryV4Store


DEPTHS = {"shallow", "normal", "deep"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _tokens(text: Any) -> set[str]:
    value = str(text or "").lower()
    words = set(re.findall(r"[a-z0-9_]+", value))
    expansions = {
        "prefers": "prefer", "preferred": "prefer", "preference": "prefer", "preferences": "prefer",
        "answers": "answer", "reply": "answer", "replies": "answer",
    }
    words.update(expansions[word] for word in list(words) if word in expansions)
    for run in re.findall(r"[\u3400-\u9fff]+", value):
        if len(run) == 1:
            words.add(run)
        else:
            words.update(run[i:i+2] for i in range(len(run)-1))
    return words - set("我你他她它的了是吗呢啊呀很在和也这那有不") - {
        "my", "i", "you", "your", "me", "the", "a", "an", "is", "are", "do", "does", "what", "which",
        "我的", "你的", "是什么", "什么", "是什", "的是", "的吗", "你还", "还记", "记得",
    }


def _time_value(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.isoformat()
    except ValueError:
        return text[:10]


def _instant(value, *, end_of_day=False):
    if not value:return None
    text=str(value).strip().replace('Z','+00:00')
    parsed=datetime.fromisoformat(text)
    if len(text)==10 and end_of_day:parsed=parsed.replace(hour=23,minute=59,second=59,microsecond=999999)
    return parsed.timestamp()


@dataclass
class MemoryQuery:
    query: str = ""
    depth: str = "normal"
    kinds: list[str] = field(default_factory=list)
    scope_type: str | None = None
    scope_id: str | None = None
    time_from: str | None = None
    time_to: str | None = None
    include_expired: bool = False
    allow_do_not_mention: bool = False
    max_candidates: int = 80
    max_items: int = 8
    token_budget: int = 1200

    def __post_init__(self) -> None:
        self.query = str(self.query or "").strip()
        self.depth = str(self.depth or "normal").lower()
        if self.depth not in DEPTHS:
            raise ValidationError("unsupported memory query depth")
        self.kinds = [str(item).strip() for item in (self.kinds or []) if str(item).strip()]
        self.scope_type = str(self.scope_type or "").strip() or None
        self.scope_id = str(self.scope_id or "").strip() or None
        self.time_from = _time_value(self.time_from)
        self.time_to = _time_value(self.time_to)
        if self.time_from and self.time_to and self.time_to < self.time_from:
            raise ValidationError("time_to must not precede time_from")
        self.include_expired = bool(self.include_expired)
        self.allow_do_not_mention = bool(self.allow_do_not_mention)
        self.max_candidates = max(1, min(1000, int(self.max_candidates)))
        self.max_items = max(1, min(self.max_candidates, int(self.max_items)))
        self.token_budget = max(64, min(100000, int(self.token_budget)))


@dataclass(frozen=True)
class MemoryCandidateSummary:
    id: str
    canonical_text: str
    kind: str
    scope_type: str
    score: float
    relevance: float
    temporal_fit: float
    reason: str
    base_score: float = 0.0
    origin_contribution: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "canonical_text": self.canonical_text,
            "kind": self.kind,
            "scope_type": self.scope_type,
            "score": self.score,
            "relevance": self.relevance,
            "temporal_fit": self.temporal_fit,
            "reason": self.reason,
            "base_score": self.base_score,
            "origin_contribution": self.origin_contribution,
        }


@dataclass
class RetrievalResult:
    query: MemoryQuery
    candidates: list[MemoryCandidateSummary]
    items: list[MemoryItemV4]
    omitted: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query.__dict__.copy(),
            "candidates": [item.to_dict() for item in self.candidates],
            "items": [item.to_dict() for item in self.items],
            "omitted": self.omitted,
        }


class MemoryNeedPlanner:
    """Conservative event/relevance gate before querying the repository."""

    _NEED_RE = re.compile(
        r"(?i)(?:我|我的|你记得|记忆|之前|上次|偏好|喜欢|项目|计划|习惯|"
        r"remember|my\b|previous|prefer|project|plan|usual|history)"
    )

    def plan(self, user_query: Any, *, depth: str = "normal", scope_type: str | None = None, scope_id: str | None = None) -> MemoryQuery:
        query = str(user_query or "").strip()
        if not self._NEED_RE.search(query):
            return MemoryQuery(query="", depth=depth, scope_type=scope_type, scope_id=scope_id, max_candidates=1, max_items=1)
        return MemoryQuery(query=query, depth=depth, scope_type=scope_type, scope_id=scope_id)


class MemoryRetriever:
    """Lexical hybrid baseline with bounded candidates and explicit receipts."""

    def __init__(self, store: MemoryV4Store, *, semantic_scores=None, candidate_ids=None, permitted=None,
                 qq_origin_preference=False, question_text=None):
        self.store = store
        self.semantic_scores = semantic_scores or {}
        self.candidate_ids = candidate_ids
        self.permitted = permitted
        self.qq_origin_preference = qq_origin_preference
        self.question_text = question_text

    def search(self, query: MemoryQuery | dict[str, Any], *, turn_id: str | None = None) -> RetrievalResult:
        query = query if isinstance(query, MemoryQuery) else MemoryQuery(**dict(query))
        if not query.query:
            return RetrievalResult(query, [], [], [])
        now = _now()
        summaries: list[MemoryCandidateSummary] = []
        omitted: list[dict[str, Any]] = []
        from ..memory_recall_contract import single_literal_focus, explicit_self_recall, named_period, matches_named_period
        focus = single_literal_focus(query.query)
        self_choice = explicit_self_recall(query.query)
        period = named_period(self.question_text or query.query)
        focused_ids = set()
        self_ids = set()
        usage_scores = self.store.memory_usage_scores()
        pool = self.store.get_memories(self.candidate_ids) if self.candidate_ids is not None else self.store.list_memories(limit=100000)
        for item in pool:
            if self.permitted is not None and not self.permitted(item):
                omitted.append({'id':item.id,'reason':'memory_use_policy'})
                continue
            reason = self._eligibility(item, query, now)
            if reason:
                omitted.append({"id": item.id, "reason": reason})
                continue
            if not matches_named_period(item.canonical_text, period):
                omitted.append({"id": item.id, "reason": "outside_named_period"})
                continue
            if focus and focus in item.canonical_text:
                focused_ids.add(item.id)
            if item.subject in {'user', 'unstructured_legacy'}:
                self_ids.add(item.id)
            relevance = self._relevance(query.query, item)
            relevance = max(relevance, self.semantic_scores.get(item.id, 0.0))
            if query.query and relevance <= 0.0:
                omitted.append({"id": item.id, "reason": "no_query_match"})
                continue
            temporal = self._temporal_fit(item, query, now)
            scope = 1.0 if not query.scope_type or item.scope_type == query.scope_type else 0.0
            score = (
                0.45 * relevance + 0.15 * temporal + 0.15 * scope +
                0.10 * item.salience + 0.10 * item.confidence +
                0.05 * usage_scores.get(item.id, 0.0)
            )
            if item.pinned:
                score += 0.05
            base_score=score
            from ..memory_importance import contribution
            origin_contribution=contribution(item.metadata.get('origin'),item.salience) if self.qq_origin_preference else 0.0
            score+=origin_contribution
            reason_text = "lexical match" if relevance else "scope/priority fallback"
            summaries.append(MemoryCandidateSummary(
                id=item.id, canonical_text=item.canonical_text[:240], kind=item.kind,
                scope_type=item.scope_type, score=round(score, 6), relevance=round(relevance, 6),
                temporal_fit=round(temporal, 6), reason=reason_text,
                base_score=round(base_score,6),origin_contribution=origin_contribution,
            ))
        if self_choice:
            omitted.extend({'id':item.id,'reason':'different_choice_subject'}
                           for item in summaries if item.id not in self_ids)
            summaries = [item for item in summaries if item.id in self_ids]
        if focused_ids:
            omitted.extend({'id':item.id,'reason':'outside_single_literal_object'}
                           for item in summaries if item.id not in focused_ids)
            summaries = [item for item in summaries if item.id in focused_ids]
        summaries.sort(key=lambda item: (item.score, item.relevance, item.id), reverse=True)
        summaries = summaries[:query.max_candidates]
        selected = summaries[:query.max_items]
        items = self.fetch_full([item.id for item in selected], query=query)
        if turn_id:
            for summary in selected:
                self.store.record_memory_usage(summary.id, turn_id=turn_id, stage="candidate_selected")
        return RetrievalResult(query, summaries, items, omitted)

    def fetch_full(self, memory_ids: Iterable[str], *, query: MemoryQuery | None = None) -> list[MemoryItemV4]:
        from ..memory_recall_contract import named_period, matches_named_period
        query = query or MemoryQuery()
        period = named_period(self.question_text or query.query)
        result = []
        for memory_id in memory_ids:
            item = self.store.get_memory(str(memory_id))
            if item is None:
                continue
            if (self.permitted is not None and not self.permitted(item)) or self._eligibility(item, query, _now()):
                continue
            if not matches_named_period(item.canonical_text, period):
                continue
            result.append(item)
        return result

    def acknowledge_used(
        self,
        memory_ids: Iterable[str],
        *,
        turn_id: str | None = None,
        prompt_included: bool = True,
        used: bool = True,
        feedback: str | None = None,
    ) -> list[dict[str, Any]]:
        return [
            self.store.record_memory_usage(
                str(memory_id), turn_id=turn_id, stage="usage_receipt",
                prompt_included=prompt_included, used=used, feedback=feedback,
            )
            for memory_id in memory_ids
            if self.store.get_memory(str(memory_id)) is not None
        ]

    def _eligibility(self, item: MemoryItemV4, query: MemoryQuery, now: str) -> str | None:
        if item.metadata.get("role_mode") == "assistant":
            return "assistant_role_scope"
        if item.lifecycle == 'candidate':
            return 'candidate_not_confirmed'
        if item.lifecycle in {"superseded", "archived", "quarantined"}:
            return f"lifecycle:{item.lifecycle}"
        if item.lifecycle == "expired" and not query.include_expired:
            return "expired"
        if item.valid_to and _instant(item.valid_to,end_of_day=True) < _instant(now) and not query.include_expired:
            return "past_valid_to"
        if item.valid_from and _instant(item.valid_from)>_instant(now) and not (
                query.time_to and _instant(query.time_to,end_of_day=True)>=_instant(item.valid_from)):
            return 'not_yet_valid'
        if query.kinds and item.kind not in set(query.kinds):
            return "kind_filter"
        # A query without a scope has global access, never project/role access.
        if not query.scope_type and item.scope_type not in {"global", "connector"}:
            return "scope_not_requested"
        if not query.scope_type and item.scope_type == "connector" and not (
                item.kind == "knowledge" and item.subject == "world" and item.sensitivity == "normal"):
            return "connector_scope_not_requested"
        if query.scope_type and item.scope_type not in {query.scope_type, "global"}:
            return "scope_type_filter"
        if query.scope_id and item.scope_type != "global" and item.scope_id != query.scope_id:
            return "scope_id_filter"
        if query.time_from and item.valid_to and _instant(item.valid_to,end_of_day=True) < _instant(query.time_from):
            return "time_range"
        if query.time_to and item.valid_from and _instant(item.valid_from) > _instant(query.time_to,end_of_day=True):
            return "time_range"
        if item.activation_policy == "brief_only":
            return "brief_only_gate"
        if item.metadata.get("do_not_mention") and not query.allow_do_not_mention:
            return "do_not_mention"
        if item.activation_policy == "event" and not re.search(r"(?i)(?:今天|昨天|明天|事件|发生|记得|上次|之前|经历|remember|last time|previous|event|today|yesterday|tomorrow)", query.query):
            return "event_gate"
        return None

    @staticmethod
    def _relevance(query: str, item: MemoryItemV4) -> float:
        if not query:
            return 0.0
        q_tokens = _tokens(query)
        text_tokens = _tokens(" ".join((item.canonical_text, item.subject, item.predicate)))
        if not q_tokens or not text_tokens:
            return 0.0
        overlap = len(q_tokens & text_tokens) / len(q_tokens)
        phrase = 0.2 if query.lower() in item.canonical_text.lower() else 0.0
        return min(1.0, overlap + phrase)

    @staticmethod
    def _temporal_fit(item: MemoryItemV4, query: MemoryQuery, now: str) -> float:
        if query.time_from or query.time_to:
            return 1.0
        if not item.valid_to:
            return 1.0
        return 0.2 if _instant(item.valid_to,end_of_day=True) >= _instant(now) else 0.0

    def _validated_usage(self, memory_id: str) -> float:
        """Compatibility helper for callers that need a single score."""
        rows = self.store.list_memory_usage(memory_id, limit=100)
        if not rows:
            return 0.0
        used = sum(1 for row in rows if row.get("used"))
        return min(1.0, used / max(1, len(rows)))


# Explicit names used by integrations and tests.
HybridMemoryRetriever = MemoryRetriever
