"""Deterministic V4 memory write pipeline.

The pipeline is intentionally model-agnostic.  A future structured planner may
pass an ``action`` object, but every action still goes through the same intent
gate, evidence binding, validator, dedupe/conflict resolver and commit policy.
No assistant text, tool status, or hidden prompt is accepted as a user fact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import re
from dataclasses import replace
from saya_app.services.memory_rules import explicit_change, same_fact_topic
from typing import Any, Iterable, Mapping

from .models import MemoryCandidateV4, MemoryEvidence, MemoryItemV4, MemoryProposal, ValidationError
from .store import MemoryV4Store
from ..qq_tasks import checkpoint_current, TaskCancelled
from ..memory_origin import apply_origin, current_origin


INTENTS = {"explicit_save", "explicit_forget", "explicit_correct", "passive_candidate", "none"}
OPERATIONS = {"create", "update", "supersede", "forget", "correct", "candidate"}
_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|bearer\s+[a-z0-9._-]+|"
    r"secret\s*[:=]|credential|私钥|密钥|助记词|mnemonic|密码|口令|sk-[a-z0-9]{12,})"
)
_PROTOCOL_RE = re.compile(r"(?i)(?:^|\s)(?:title|summary|memory)\s*:\s*|```|\bMOTION\b|<tool[_-]?(?:call|result)>|<system>")
_DANGLING_RE = re.compile(
    r"^(?:所以|但是|然后|而且|因此|因为|并且|这个|那个|这件事|这件|它|你的人)|"
    r"^(?:so|but|then|this|that)(?:\b|$)",
    re.IGNORECASE,
)
_QUOTE_RE = re.compile(r"^\s*[「『“‘\"']|\b(?:有人说|他说|她说|用户说|someone said)\b")
_CONDITIONAL_RE = re.compile(r"^(?:如果|假设|要是|万一|若是|if\b|suppose\b|assuming\b)", re.IGNORECASE)
_QUESTION_RE = re.compile(r"[?？]\s*$|^(?:能否|可以吗|你能|是否|how much|can you|do you)\b", re.IGNORECASE)
_SAVE_RE = re.compile(
    r"^\s*(?:请|請|帮我|幫我|你要|以后|以後)?\s*(?:一定|必須|必须|务必|千萬|千万(?:要)?)?\s*"
    r"(?:记住|記住|记一下|記一下|记下来|記下來)\s*(?:这件事|這件事|这一点|這一點|[:：,，])?\s*(?P<content>.+)$",
    re.DOTALL,
)
_EN_SAVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:remember(?:\s+that|\s+this|\s*[:：,，])?|save\s+this\s+note\s*:?|keep\s+this\s+memory\s*:?)\s+(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_FORGET_RE = re.compile(
    r"^\s*(?:请|請|帮我|幫我)?\s*(?:忘记|忘掉|删除|刪除|删掉|刪掉|移除|forget|remove)\s*(?P<content>.*)$",
    re.IGNORECASE | re.DOTALL,
)
_CORRECT_RE = re.compile(
    r"^\s*(?:请|請|帮我|幫我)?\s*(?:更正|纠正|糾正|改成|更改|修正|correct|update)\s*[:：,，]?\s*(?P<content>.+)$",
    re.IGNORECASE | re.DOTALL,
)
_PIN_WORDS = ("重要", "一定", "永远", "永遠", "必须", "必須", "务必", "務必", "千万要", "pin", "important")


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _hash(value: str, length: int = 24) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _clamp(value: Any, default: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class WriteIntentDecision:
    intent: str
    confidence: float
    reason_code: str
    content: str = ""
    target_memory_id: str | None = None
    action: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.intent not in INTENTS:
            raise ValidationError(f"unsupported write intent: {self.intent}")
        object.__setattr__(self, "confidence", _clamp(self.confidence, 0.0))
        object.__setattr__(self, "content", _text(self.content))

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "confidence": self.confidence,
            "reason_code": self.reason_code,
            "content": self.content,
            "target_memory_id": self.target_memory_id,
        }


class WriteIntentGate:
    """Recognize structured actions first, then conservative sentence-initial commands."""

    def detect(self, message: Any = "", action: Mapping[str, Any] | None = None) -> WriteIntentDecision:
        from ..memory_reference import save_reference
        if save_reference(message):return WriteIntentDecision('none',1.0,'unresolved_reference')
        if isinstance(action, Mapping):
            raw_intent = str(action.get("intent") or "").strip().lower()
            operation = str(action.get("operation") or "").strip().lower()
            intent = raw_intent or {
                "create": "explicit_save", "update": "explicit_correct", "correct": "explicit_correct",
                "forget": "explicit_forget", "candidate": "passive_candidate",
            }.get(operation, "")
            if intent in INTENTS and intent != "none":
                content = action.get("content")
                proposal = action.get("proposal")
                if not content and isinstance(proposal, Mapping):
                    content = proposal.get("canonical_text")
                return WriteIntentDecision(
                    intent=intent,
                    confidence=_clamp(action.get("confidence", 1.0), 1.0),
                    reason_code="structured_action",
                    content=content or "",
                    target_memory_id=str(action.get("memory_id") or action.get("target_memory_id") or "") or None,
                    action=dict(action),
                )
            if raw_intent or operation:
                return WriteIntentDecision("none", 0.0, "invalid_structured_action")

        text = _text(message)
        if not text:
            return WriteIntentDecision("none", 1.0, "empty_message")
        for pattern in (_SAVE_RE, _EN_SAVE_RE):
            match = pattern.match(text)
            if match:
                content = _text(match.group("content"))
                reason = self._reject_save(text, content)
                if reason:
                    return WriteIntentDecision("none", 0.99, reason, content=content)
                return WriteIntentDecision(
                    "explicit_save", 0.98, "sentence_initial_save_command", content=content,
                    action={"content": content, "suggest_pin": any(word in text for word in _PIN_WORDS)},
                )
        forget = _FORGET_RE.match(text)
        if forget:
            content = _text(forget.group("content"))
            if _QUESTION_RE.search(text):
                return WriteIntentDecision("none", 0.99, "question", content=content)
            return WriteIntentDecision("explicit_forget", 0.96, "sentence_initial_forget_command", content=content)
        correct = _CORRECT_RE.match(text)
        if correct:
            content = _text(correct.group("content"))
            if not content or _QUESTION_RE.search(content):
                return WriteIntentDecision("none", 0.99, "invalid_correction", content=content)
            return WriteIntentDecision("explicit_correct", 0.94, "sentence_initial_correction_command", content=content)
        return WriteIntentDecision("none", 0.95, "no_write_intent")

    @staticmethod
    def _reject_save(text: str, content: str) -> str:
        from ..memory_reference import unresolved_reference
        if unresolved_reference(content):return 'unresolved_reference'
        if _QUESTION_RE.search(text) or _QUESTION_RE.search(content):
            return "question"
        if _QUOTE_RE.search(text[:80]):
            return "quoted_speech"
        if _CONDITIONAL_RE.search(content):
            return "conditional_hypothesis"
        if _SECRET_RE.search(content):
            return "secret_content"
        semantic = re.sub(r"[\s，。,.!！?？：:；;'\"“”‘’()（）\[\]【】、]+", "", content)
        if len(semantic) < 3:
            return "too_short"
        if re.fullmatch(r"(?:这件事|这一点|这个|那个|这些|那些|它|他|她|你|我|您的?|他的?|她的?)+", semantic):
            return "pronoun_only"
        return ""


@dataclass(frozen=True)
class CollectedEvidence:
    source_id: str
    source_type: str
    role: str
    quote: str
    quote_start: int | None = None
    quote_end: int | None = None
    observed_at: str = field(default_factory=_now)

    @property
    def source_ref(self) -> str:
        return f"{self.source_type}:{self.source_id}"

    def bind(self, memory_id: str) -> MemoryEvidence:
        evidence_id = f"evidence:{memory_id}:{_hash(self.source_ref + self.quote, 20)}"
        return MemoryEvidence(
            id=evidence_id,
            memory_id=memory_id,
            source_type=self.source_type,
            source_id=self.source_id,
            role=self.role,
            quote=self.quote,
            quote_start=self.quote_start,
            quote_end=self.quote_end,
            observed_at=self.observed_at,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id, "source_type": self.source_type, "role": self.role,
            "quote": self.quote, "quote_start": self.quote_start, "quote_end": self.quote_end,
            "observed_at": self.observed_at,
        }


class EvidenceCollector:
    """Collect only the current user message plus at most two completed Q/A pairs."""

    def collect(
        self,
        user_message: Any,
        context: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        *,
        message_id: str | None = None,
        turn_id: str | None = None,
    ) -> list[CollectedEvidence]:
        current = _text(user_message)
        if not current:
            return []
        current_id = str(message_id or f"{turn_id or 'turn'}:user:{_hash(current)}")
        result = [CollectedEvidence(current_id, "user_message", "assertion", current[:2000])]
        messages: list[Mapping[str, Any]] = []
        if isinstance(context, Mapping):
            context = context.get("messages") or context.get("turns") or []
        if isinstance(context, Iterable) and not isinstance(context, (str, bytes)):
            messages = [entry for entry in context if isinstance(entry, Mapping)]
        complete = [
            entry for entry in messages
            if str(entry.get("role") or "").lower() in {"user", "assistant"}
            and str(entry.get("status") or "completed").lower() in {"completed", "complete", "done", "success", ""}
            and _text(entry.get("content"))
        ]
        # A completed pair is two adjacent user/assistant messages.  Keep only
        # the last two pairs, and never duplicate the current message.
        pairs: list[list[Mapping[str, Any]]] = []
        index = 0
        while index < len(complete) - 1:
            left, right = complete[index], complete[index + 1]
            if str(left.get("role")).lower() == "user" and str(right.get("role")).lower() == "assistant":
                pairs.append([left, right])
                index += 2
            else:
                index += 1
        for entry in [item for pair in pairs[-2:] for item in pair]:
            quote = _text(entry.get("content"))[:2000]
            source_id = str(entry.get("id") or entry.get("message_id") or f"context:{_hash(quote)}")
            if source_id == current_id:
                continue
            role = "user_context" if str(entry.get("role")).lower() == "user" else "assistant_context"
            source_type = "conversation_context" if role == "user_context" else "assistant_context"
            result.append(CollectedEvidence(source_id, source_type, "context", quote))
        return result


@dataclass(frozen=True)
class StructuredWriteProposal:
    proposal: MemoryProposal
    operation: str = "create"
    evidence_refs: tuple[str, ...] = ()
    target_memory_id: str | None = None

    def __post_init__(self) -> None:
        if self.operation not in OPERATIONS:
            raise ValidationError(f"unsupported proposal operation: {self.operation}")

    @property
    def suggest_pin(self) -> bool:
        return bool(self.proposal.suggest_pin)

    def to_dict(self) -> dict[str, Any]:
        data = self.proposal.to_dict()
        data.update({
            "operation": self.operation,
            "evidence_refs": list(self.evidence_refs),
            "target_memory_id": self.target_memory_id,
        })
        return data


class StructuredExtractor:
    """Turn a gated message or structured action into portable proposals."""

    def extract(
        self,
        content: Any,
        decision: WriteIntentDecision,
        evidence: Iterable[CollectedEvidence],
        *,
        action: Mapping[str, Any] | None = None,
    ) -> list[StructuredWriteProposal]:
        action = {**(decision.action or {}), **(action or {})}
        raw_proposals = action.get("proposals") if isinstance(action, Mapping) else None
        if not isinstance(raw_proposals, list):
            raw_proposal = action.get("proposal") if isinstance(action, Mapping) else None
            raw_proposals = [raw_proposal] if isinstance(raw_proposal, Mapping) else []
        evidence_refs = tuple(item.source_ref for item in evidence if item.role == "assertion" or item.source_type == "conversation_context")
        defaults = {
            key: action[key]
            for key in (
                "subject", "predicate", "value", "language", "kind", "scope_type", "scope_id",
                "authority", "sensitivity", "valid_from", "valid_to", "confidence", "salience",
                "suggest_pin", "future_use", "metadata", "memory_id", "target_memory_id",
            )
            if key in action
        }
        if not raw_proposals:
            parts = self._split_facts(content)
            raw_proposals = [{**defaults, "canonical_text": part} for part in parts]
        result: list[StructuredWriteProposal] = []
        for raw in raw_proposals:
            if isinstance(raw, str):
                raw = {**defaults, "canonical_text": raw}
            if not isinstance(raw, Mapping):
                continue
            raw = {**defaults, **dict(raw)}
            text = _text(raw.get("canonical_text") or raw.get("content") or content)
            if not text:
                continue
            kind, predicate = self._classify(text, raw)
            # Text-only extraction cannot prove that two preferences/goals
            # address one property. Preserve independent facts; precise
            # replacement requires a structured predicate or a target ID.
            if not raw.get("predicate") and predicate != "has_name":
                predicate = f"{predicate}_{_hash(text.lower(), 16)}"
            intent = decision.intent
            operation = str(raw.get("operation") or action.get("operation") or ("correct" if intent == "explicit_correct" else "candidate" if intent == "passive_candidate" else "create"))
            if operation == "update" and intent == "explicit_correct":
                operation = "correct"
            proposal = MemoryProposal(
                canonical_text=text,
                subject=_text(raw.get("subject") or "user"),
                predicate=_text(raw.get("predicate") or predicate),
                value=raw.get("value", text),
                language=_text(raw.get("language") or ("zh-CN" if re.search(r"[\u3400-\u9fff]", text) else "en")),
                kind=_text(raw.get("kind") or kind),
                scope_type=_text(raw.get("scope_type") or ("project" if raw.get("scope_id") else "global")),
                scope_id=str(raw.get("scope_id") or "") or None,
                authority=_text(raw.get("authority") or ("user_confirmed" if intent in {"explicit_save", "explicit_correct"} else "inferred")),
                sensitivity=_text(raw.get("sensitivity") or "normal"),
                valid_from=raw.get("valid_from"), valid_to=raw.get("valid_to"),
                confidence=_clamp(raw.get("confidence", 0.96 if intent in {"explicit_save", "explicit_correct"} else 0.55), 0.55),
                salience=_clamp(raw.get("salience", 0.65 if intent in {"explicit_save", "explicit_correct"} else 0.35), 0.35),
                suggest_pin=bool(raw.get("suggest_pin", any(word in str(content) for word in _PIN_WORDS))) if intent != "passive_candidate" else False,
                future_use=_text(raw.get("future_use")),
                metadata={
                    **(dict(raw.get("metadata")) if isinstance(raw.get("metadata"), Mapping) else {}),
                    "write_intent": intent,
                    "reason_code": decision.reason_code,
                    "property_binding": "structured" if raw.get("predicate") else "text_fingerprint",
                },
            )
            result.append(StructuredWriteProposal(
                proposal=proposal,
                operation=operation,
                evidence_refs=tuple(str(item) for item in (raw.get("evidence_refs") or evidence_refs)),
                target_memory_id=str(raw.get("memory_id") or decision.target_memory_id or "") or None,
            ))
        return result

    @staticmethod
    def _split_facts(content: Any) -> list[str]:
        text = _text(content)
        if not text:
            return []
        parts = [part.strip() for part in re.split(r"(?:\r?\n+|[；;])", text) if part.strip()]
        return parts or [text]

    @staticmethod
    def _classify(text: str, raw: Mapping[str, Any]) -> tuple[str, str]:
        lower = text.lower()
        if raw.get("kind") or raw.get("predicate"):
            return _text(raw.get("kind") or "knowledge"), _text(raw.get("predicate") or "has_memory")
        if re.search(r"喜欢|偏好|爱吃|不喜欢|prefer|favorite|favourite|enjoy", lower):
            return "preference", "prefers"
        if re.search(r"我叫|我的名字|名字是|my name|i am called", lower):
            return "identity", "has_name"
        if re.search(r"目标|计划|打算|想要|goal|plan|intend", lower):
            return "goal", "has_goal"
        if re.search(r"步骤|流程|工作流|习惯|通常|workflow|routine|always", lower):
            return "procedure", "follows_routine"
        if re.search(r"家人|朋友|同事|伴侣|family|friend|colleague", lower):
            return "relationship", "has_relationship"
        return "knowledge", "has_memory"


@dataclass(frozen=True)
class ValidationResult:
    accepted: bool
    reason_codes: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"accepted": self.accepted, "reason_codes": list(self.reason_codes), "warnings": list(self.warnings)}


class DeterministicValidator:
    """Validate proposal text and evidence without making semantic guesses."""

    PROHIBITED_SOURCE_TYPES = {"motion", "tool", "tool_status", "system_block", "hidden_prompt", "webpage_fulltext"}

    def validate(
        self,
        proposal: StructuredWriteProposal | MemoryProposal | Mapping[str, Any],
        evidence: Iterable[CollectedEvidence | MemoryEvidence | Mapping[str, Any]],
        *,
        intent: str = "explicit_save",
    ) -> ValidationResult:
        if isinstance(proposal, StructuredWriteProposal):
            value = proposal.proposal
        elif isinstance(proposal, MemoryProposal):
            value = proposal
        else:
            try:
                value = MemoryProposal.from_dict(proposal)
            except (TypeError, ValueError) as exc:
                return ValidationResult(False, ("schema_invalid", str(exc)[:120]))
        reasons: list[str] = []
        text = _text(value.canonical_text)
        from ..memory_reference import unresolved_reference
        if unresolved_reference(text):reasons.append('unresolved_reference')
        if len(re.sub(r"[^\w\u3400-\u9fff]+", "", text, flags=re.UNICODE)) < 3:
            reasons.append("too_short")
        if _PROTOCOL_RE.search(text):
            reasons.append("protocol_residue")
        if _DANGLING_RE.search(text):
            reasons.append("dangling_start")
        if _QUESTION_RE.search(text):
            reasons.append("question")
        if _CONDITIONAL_RE.search(text):
            reasons.append("conditional_hypothesis")
        if _QUOTE_RE.search(text):
            reasons.append("quoted_or_attributed_speech")
        if _SECRET_RE.search(text) or _SECRET_RE.search(str(value.value)) or value.sensitivity == "secret":
            reasons.append("secret_content")
        if intent == "passive_candidate" and value.sensitivity in {"personal", "sensitive"}:
            reasons.append("sensitive_passive_write")
        evidence_items = list(evidence or [])
        if not evidence_items:
            reasons.append("missing_evidence")
        if isinstance(proposal, StructuredWriteProposal):
            available = {f'{item.source_type}:{item.source_id}': item for item in evidence_items
                         if isinstance(item, (CollectedEvidence, MemoryEvidence))}
            if not proposal.evidence_refs or any(ref not in available for ref in proposal.evidence_refs):
                reasons.append('unbound_evidence_reference')
            if intent in {'explicit_save', 'explicit_correct'} and not any(
                    ref in available and available[ref].source_type == 'user_message'
                    and available[ref].role == 'assertion' for ref in proposal.evidence_refs):
                reasons.append('missing_current_user_reference')
        has_user_assertion = False
        for raw in evidence_items:
            try:
                if isinstance(raw, CollectedEvidence):
                    if raw.source_type == "user_message" and raw.role == "assertion":
                        has_user_assertion = True
                    continue
                item = raw if isinstance(raw, MemoryEvidence) else MemoryEvidence.from_dict(raw)
                if item.source_type in self.PROHIBITED_SOURCE_TYPES:
                    reasons.append("prohibited_evidence_source")
                if item.source_type == "user_message" and item.role == "assertion":
                    has_user_assertion = True
                expected = hashlib.sha256(item.quote.encode("utf-8")).hexdigest()
                if item.content_hash != expected:
                    reasons.append("evidence_hash_mismatch")
            except (TypeError, ValueError):
                reasons.append("invalid_evidence")
        if intent in {"explicit_save", "explicit_correct"} and not has_user_assertion:
            reasons.append("missing_user_assertion")
        # Automatic importance is never allowed to become a pin.
        warnings = ("pin_suggestion_ignored_for_passive_candidate",) if intent == "passive_candidate" and value.suggest_pin else ()
        unique = tuple(dict.fromkeys(reasons))
        return ValidationResult(not unique, unique, warnings)


class ImportancePolicy:
    def apply(self, proposal: MemoryProposal, *, intent: str) -> MemoryProposal:
        if intent != "passive_candidate":
            return proposal
        data = proposal.to_dict()
        data["suggest_pin"] = False
        if data.get("authority") == "user_confirmed":
            data["authority"] = "inferred"
        return MemoryProposal.from_dict(data)


class WritePipeline:
    """Run intent -> evidence -> extraction -> validation -> commit."""

    def __init__(
        self,
        store: MemoryV4Store,
        *,
        intent_gate: WriteIntentGate | None = None,
        evidence_collector: EvidenceCollector | None = None,
        extractor: StructuredExtractor | None = None,
        validator: DeterministicValidator | None = None,
        importance_policy: ImportancePolicy | None = None,
        authorize=None,
    ):
        self.store = store
        self.intent_gate = intent_gate or WriteIntentGate()
        self.evidence_collector = evidence_collector or EvidenceCollector()
        self.extractor = extractor or StructuredExtractor()
        self.validator = validator or DeterministicValidator()
        self.importance_policy = importance_policy or ImportancePolicy()
        self.authorize = authorize

    def process(
        self,
        user_message: Any = "",
        *,
        context: Iterable[Mapping[str, Any]] | Mapping[str, Any] | None = None,
        action: Mapping[str, Any] | None = None,
        intent: str | None = None,
        turn_id: str | None = None,
        message_id: str | None = None,
        sequence: int = 0,
        actor: str = "user",
        expected_versions: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        checkpoint_current()
        from ..memory_versions import MemoryVersionConflict
        from ..memory_health import native_version
        versions = self.store.write_versions()
        if expected_versions is not None:
            if not isinstance(expected_versions, Mapping) or any(
                    not isinstance(key, str) or not isinstance(value, str) or not re.fullmatch(r'[a-f0-9]{64}', value)
                    for key, value in expected_versions.items()):
                raise ValueError('invalid_native_write_versions')
            versions.update(expected_versions)
        def authorize(identifier=None, operation=None):
            checkpoint_current()
            if self.authorize is not None:
                self.authorize(identifier, operation)
        missing = native_version(None, False)
        used_versions = {}
        decision = self.intent_gate.detect(user_message, action)
        if intent in INTENTS and decision.intent == "none" and decision.reason_code != "unresolved_reference":
            decision = WriteIntentDecision(intent, 1.0, "caller_override", content=str(user_message or ""), action=dict(action or {}))
        if decision.intent == "none":
            return {"status": "no_op", "intent": decision.to_dict(), "committed": [], "candidates": [], "rejected": []}
        evidence = self.evidence_collector.collect(
            user_message or decision.content,
            context,
            message_id=message_id,
            turn_id=turn_id,
        )
        if decision.intent == "explicit_forget":
            return self._forget(decision, turn_id=turn_id, sequence=sequence, actor=actor,
                                versions=versions, authorize=authorize, action=action)
        response: dict[str, Any] = {
            "status": "success", "intent": decision.to_dict(), "committed": [], "candidates": [], "rejected": [],
        }
        try:
            proposals = self.extractor.extract(decision.content or user_message, decision, evidence, action=action)
        except (TypeError, ValueError) as exc:
            response["status"] = "rejected"
            response["rejected"].append({"reason_codes": ["schema_invalid"], "detail": str(exc)[:180]})
            return response
        def cancelled_result():
            if response['committed'] or response['candidates']:
                response['status'] = 'cancelled'
                return response
            raise TaskCancelled('memory write cancelled before commit')
        for structured in proposals:
            try:
                checkpoint_current()
            except TaskCancelled:
                return cancelled_result()
            proposal = self.importance_policy.apply(structured.proposal, intent=decision.intent)
            structured = StructuredWriteProposal(proposal, structured.operation, structured.evidence_refs, structured.target_memory_id)
            validation = self.validator.validate(structured, evidence, intent=decision.intent)
            if not validation.accepted:
                response["rejected"].append({"proposal": structured.to_dict(), "validation": validation.to_dict()})
                continue
            if decision.intent == "passive_candidate":
                try:
                    with self.store.checked_write({}, lambda: authorize(None, 'candidate')):
                        candidate = self._candidate(structured, evidence)
                        self.store.upsert_candidate(candidate)
                except TaskCancelled:
                    return cancelled_result()
                except ValidationError as exc:
                    response['rejected'].append({'reason_codes': [str(exc)]})
                    continue
                response["candidates"].append(candidate.to_dict())
                continue
            try:
                current = self._target(structured, decision)
                identifier = current.id if current else structured.target_memory_id or self._memory_id(structured.proposal)
                expected = versions.get(identifier, missing)
                with self.store.checked_write({identifier: expected}, lambda: authorize(identifier, decision.intent)):
                    resolved = self._target(structured, decision)
                    if (resolved.id if resolved else structured.target_memory_id or self._memory_id(structured.proposal)) != identifier:
                        raise MemoryVersionConflict('native_write_target_changed')
                    result = self._commit(structured, evidence, decision, turn_id=turn_id, sequence=sequence, actor=actor)
                    committed_version = native_version(self.store.get_memory(identifier), self.store.is_tombstoned(identifier))
                used_versions[identifier] = expected
                # Multiple proposals in this one operation may address the same target.
                versions[identifier] = committed_version
            except TaskCancelled:
                return cancelled_result()
            except (ValidationError, MemoryVersionConflict) as exc:
                response["rejected"].append({"proposal": structured.to_dict(), "reason_codes": [str(exc)]})
                continue
            response["committed"].append(result)
        if response["rejected"] and not response["committed"] and not response["candidates"]:
            response["status"] = "rejected"
        elif response['rejected']:
            response['status'] = 'partial'
        elif not proposals:
            response["status"] = "rejected"
            response["rejected"].append({"reason_codes": ["empty_proposal"]})
        response['target_versions'] = used_versions
        return response

    def _candidate(self, structured: StructuredWriteProposal, evidence: Iterable[CollectedEvidence]) -> MemoryCandidateV4:
        proposal = structured.proposal
        candidate_id = f"candidate:{_hash('|'.join((proposal.subject, proposal.predicate, proposal.scope_type, proposal.scope_id or '')))}"
        refs = list(structured.evidence_refs) or [item.source_ref for item in evidence]
        existing = self.store.get_candidate(candidate_id)
        metadata = dict(proposal.metadata)
        apply_origin(metadata, previous=(existing.proposal.get('metadata') or {}) if existing else None,
            reason='candidate_capture')
        from ..memory_importance import update as update_importance
        update_importance(metadata,previous=existing.proposal.get('metadata') if existing else None,
            score_before=existing.proposal.get('salience',proposal.salience) if existing else proposal.salience,
            score_after=proposal.salience,reason='candidate_assessment')
        proposal = replace(proposal, metadata=metadata)
        if existing is not None:
            refs = list(dict.fromkeys(existing.evidence_ids + refs))
        current = self.store.find_by_key(proposal.predicate, proposal.scope_type, proposal.scope_id, proposal.subject)
        if current is not None and current.lifecycle == "active" and not self._same_text(current.canonical_text, proposal.canonical_text):
            proposal_data = proposal.to_dict()
            metadata = dict(proposal_data.get("metadata") or {})
            metadata["conflict_with"] = [current.id]
            proposal_data["metadata"] = metadata
            proposal = MemoryProposal.from_dict(proposal_data)
        return MemoryCandidateV4(
            id=candidate_id,
            proposal=proposal.to_dict(),
            evidence_ids=refs,
            status="pending",
            occurrence_count=(existing.occurrence_count + 1) if existing is not None else 1,
            first_seen_at=existing.first_seen_at if existing is not None else _now(),
            last_seen_at=_now(),
            review_reason="passive observation; explicit review is required",
        )

    @staticmethod
    def _memory_id(proposal: MemoryProposal) -> str:
        key = "|".join((proposal.subject, proposal.predicate, proposal.scope_type, proposal.scope_id or ""))
        return f"memory:{_hash(key)}"

    def _target(self, structured, decision):
        proposal = structured.proposal
        current = self.store.get_memory(structured.target_memory_id) if structured.target_memory_id else self.store.find_by_key(
            proposal.predicate, proposal.scope_type, proposal.scope_id, proposal.subject)
        if current is None and not structured.target_memory_id and proposal.metadata.get('property_binding') == 'text_fingerprint' and (
                explicit_change(proposal.canonical_text) or decision.intent == 'explicit_correct'):
            matches = [item for item in self.store.list_memories(lifecycle='active', limit=10000)
                if item.scope_type == proposal.scope_type and item.scope_id == proposal.scope_id
                and item.subject == proposal.subject and item.kind == proposal.kind
                and same_fact_topic(item.canonical_text, proposal.canonical_text)]
            if len(matches) > 1:
                raise ValidationError('ambiguous correction: multiple matching facts require review')
            current = matches[0] if matches else None
        if current is not None and current.lifecycle not in {'active', 'candidate'}:
            if structured.target_memory_id:
                raise ValidationError('inactive correction target requires review')
            return None
        return current

    def _commit(
        self,
        structured: StructuredWriteProposal,
        evidence: Iterable[CollectedEvidence],
        decision: WriteIntentDecision,
        *,
        turn_id: str | None,
        sequence: int,
        actor: str,
    ) -> dict[str, Any]:
        proposal = structured.proposal
        current = self.store.get_memory(structured.target_memory_id) if structured.target_memory_id else self.store.find_by_key(
            proposal.predicate, proposal.scope_type, proposal.scope_id, proposal.subject,
        )
        if current is None and not structured.target_memory_id and proposal.metadata.get("property_binding") == "text_fingerprint" and (
                explicit_change(proposal.canonical_text) or decision.intent == "explicit_correct"):
            matches = [item for item in self.store.list_memories(lifecycle="active", limit=10000)
                if item.scope_type == proposal.scope_type and item.scope_id == proposal.scope_id
                and item.subject == proposal.subject and item.kind == proposal.kind
                and same_fact_topic(item.canonical_text, proposal.canonical_text)]
            if len(matches) == 1:
                current = matches[0]
                proposal = replace(proposal, predicate=current.predicate)
            elif matches:
                raise ValidationError("ambiguous correction: multiple matching facts require review")
        if structured.target_memory_id and (current is None or current.scope_type != proposal.scope_type
                or current.scope_id != proposal.scope_id or current.subject != proposal.subject):
            raise ValidationError("correction target must exist in the same subject and scope")
        if decision.intent == "explicit_correct" and current is None:
            raise ValidationError("ambiguous correction: select the fact to correct")
        if current is not None and current.lifecycle not in {"active", "candidate"}:
            current = None
        if current is not None and self._same_text(current.canonical_text, proposal.canonical_text):
            context = current_origin()
            if context and context.get('origin', {}).get('channel') != 'unknown':
                metadata = dict(current.metadata)
                apply_origin(metadata, previous=current.metadata, reason='confirm')
                current = replace(current, metadata=metadata)
            bound = [item.bind(current.id) for item in evidence if item.source_ref in structured.evidence_refs]
            result = self.store.upsert_memory(
                current, bound, operation="confirm", reason="same property evidence", actor=actor,
            )
            if decision.intent in {"explicit_save", "explicit_correct"}:
                confirmed = self.store.confirm_memory(current.id)
                result["memory"] = confirmed.to_dict()
            self._event(result, "confirm", decision, turn_id, sequence, actor)
            return {
                "status": "updated" if decision.intent in {"explicit_save", "explicit_correct"} else "unchanged",
                "operation": "confirm", "memory": result["memory"], "revision": result.get("revision"),
            }

        if current is not None and decision.intent not in {"explicit_save", "explicit_correct"}:
            candidate = self._candidate(structured, evidence)
            data = candidate.proposal
            data.setdefault("metadata", {})["conflict_with"] = [current.id]
            candidate.proposal = data
            self.store.upsert_candidate(candidate)
            return {"status": "conflict_candidate", "candidate": candidate.to_dict(), "conflict_with": current.id}

        memory_id = current.id if current is not None else self._memory_id(proposal)
        now = _now()
        metadata = dict(proposal.metadata)
        from ..memory_temporal_history import mark
        mark(metadata, current, proposal.canonical_text)
        metadata.update({"write_pipeline": True, "intent": decision.intent, "evidence_refs": list(structured.evidence_refs)})
        apply_origin(metadata, previous=current.metadata if current else None, reason=decision.intent)
        from ..memory_importance import update as update_importance
        score=update_importance(metadata,previous=({**current.metadata,'is_locked':current.locked} if current else None),
            score_before=current.salience if current else proposal.salience,score_after=proposal.salience,reason='write_pipeline')
        item = MemoryItemV4(
            id=memory_id,
            subject=proposal.subject, predicate=proposal.predicate, value=proposal.value,
            canonical_text=proposal.canonical_text, language=proposal.language, kind=proposal.kind,
            scope_type=proposal.scope_type, scope_id=proposal.scope_id, authority=proposal.authority,
            sensitivity=proposal.sensitivity, valid_from=proposal.valid_from, valid_to=proposal.valid_to,
            last_confirmed_at=now if decision.intent in {"explicit_save", "explicit_correct"} else None,
            lifecycle="active", supersedes_id=current.id if current is not None else None,
            confidence=proposal.confidence, salience=score,
            pinned=bool((current and current.pinned) or (proposal.suggest_pin and decision.intent != 'passive_candidate')),
            pin_source=current.pin_source if current and current.pinned else "user" if proposal.suggest_pin and decision.intent != "passive_candidate" else None,
            locked=bool(current and current.locked), activation_policy="query", candidate_shown_count=0, used_count=0,
            last_used_at=None, created_at=current.created_at if current is not None else now,
            updated_at=now, revision=current.revision if current is not None else 1,
            legacy_layer="semantic", legacy_source="write_pipeline", metadata=metadata,
        )
        bound = [item.bind(memory_id) for item in evidence if item.source_ref in structured.evidence_refs]
        operation = "supersede" if current is not None else ("correct" if decision.intent == "explicit_correct" else "create")
        result = self.store.upsert_memory(item, bound, operation=operation, reason=decision.reason_code, actor=actor)
        self._event(result, operation, decision, turn_id, sequence, actor)
        return {"status": result["status"], "operation": operation, "memory": result["memory"], "revision": result.get("revision")}

    def _forget(self, decision: WriteIntentDecision, *, turn_id: str | None, sequence: int, actor: str,
                versions, authorize, action=None) -> dict[str, Any]:
        from ..memory_versions import MemoryVersionConflict
        from ..memory_health import native_version
        target = decision.target_memory_id
        action = {**(decision.action or {}), **(action or {})}
        def in_scope(item):
            return (item.scope_type == (action.get('scope_type') or ('project' if action.get('scope_id') else 'global'))
                    and item.scope_id == (str(action.get('scope_id')) if action.get('scope_id') else None)
                    and item.subject == (action.get('subject') or 'user'))
        matches = []
        if target:
            item = self.store.get_memory(target)
            matches = [target] if item is not None and in_scope(item) else []
        else:
            needle = self._normalize(decision.content)
            matches = [item.id for item in self.store.list_memories(limit=100000)
                       if in_scope(item) and needle and needle in self._normalize(item.canonical_text)]
        deleted = []
        rejected = []
        cancelled = False
        for memory_id in matches:
            try:
                with self.store.checked_write({memory_id: versions.get(memory_id, native_version(None, False))},
                        lambda: authorize(memory_id, 'explicit_forget')):
                    removed = self.store.tombstone_memory(memory_id, reason="explicit_forget", actor=actor, purge=False)
                    if removed:
                        self._event({'memory': {'id': memory_id}, 'revision': None}, 'forget', decision, turn_id, sequence, actor)
            except TaskCancelled:
                if not deleted:
                    raise
                cancelled = True
                break
            except (MemoryVersionConflict, ValidationError) as exc:
                rejected.append({'memory_id': memory_id, 'reason_codes': [str(exc)]})
                continue
            if removed:
                deleted.append(memory_id)
        return {
            "status": "cancelled" if cancelled else 'partial' if deleted and rejected else 'rejected' if rejected else "success" if deleted else "no_op", "intent": decision.to_dict(), "forgotten": deleted,
            "committed": [{"memory_id": memory_id, "operation": "forget"} for memory_id in deleted],
            "candidates": [], "rejected": rejected,
            'target_versions': {identifier: versions[identifier] for identifier in deleted},
        }

    def _event(
        self,
        result: Mapping[str, Any],
        operation: str,
        decision: WriteIntentDecision,
        turn_id: str | None,
        sequence: int,
        actor: str,
    ) -> dict[str, Any]:
        memory = result.get("memory") if isinstance(result.get("memory"), Mapping) else {}
        memory_id = str(memory.get("id") or result.get("memory_id") or "unknown")
        revision = memory.get("revision", result.get("revision"))
        event_id = f"memory.updated:{memory_id}:{revision or 'deleted'}:{_hash(operation + decision.reason_code, 12)}"
        event = {
            "id": event_id,
            "turn_id": str(turn_id or f"write:{_hash(memory_id + decision.reason_code)}"),
            "sequence": max(0, int(sequence)),
            "type": "memory.updated",
            "public_payload": {
                "memory_id": memory_id,
                "revision": revision,
                "operation": operation,
                "intent": decision.intent,
                "status": result.get("status", "success"),
            },
            "private_payload": None,
            "status": "completed",
            "created_at": _now(),
        }
        return self.store.record_event(event)

    @staticmethod
    def _normalize(value: Any) -> str:
        return re.sub(r"\s+", " ", str(value or "")).strip().lower()

    @classmethod
    def _same_text(cls, left: Any, right: Any) -> bool:
        return cls._normalize(left) == cls._normalize(right)


# Short aliases make the contracts pleasant to import from integrations.
WriteIntent = WriteIntentDecision
Validator = DeterministicValidator
