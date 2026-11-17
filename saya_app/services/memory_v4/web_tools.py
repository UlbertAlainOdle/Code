"""Atomic Web Tools V2 facade over the existing safe search/reader services."""

from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Any, Iterable, Mapping

from .capabilities import _safe_public


_SECRET_RE = re.compile(
    r"(?i)(?:password|passwd|api[ _-]?key|access[ _-]?token|bearer\s+[a-z0-9._-]+|"
    r"secret\s*[:=]|credential|private[ _-]?key|sk-[a-z0-9]{12,})"
)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"(?<!\w)(?:\+?\d[\d ()-]{7,}\d)(?!\w)")
_INJECTION_RE = re.compile(
    r"(?i)(?:ignore\s+(?:all\s+)?previous instructions|system prompt|developer message|"
    r"disregard the rules|忽略(?:之前|以上)指令|系统提示|开发者消息)"
)


@dataclass
class WebToolBudget:
    max_search_calls: int = 2
    max_read_urls: int = 5
    max_chars_each: int = 12000
    max_total_chars: int = 50000
    search_calls: int = 0
    read_urls: int = 0
    total_chars: int = 0

    def search_available(self) -> bool:
        return self.search_calls < max(0, int(self.max_search_calls))

    def read_available(self) -> bool:
        return self.read_urls < max(0, int(self.max_read_urls))


class SourceRegistry:
    """Stable numeric source ids for one thread/session without persisting page text."""

    def __init__(self):
        self._by_url: dict[str, int] = {}
        self._sources: dict[int, dict[str, Any]] = {}

    def register(self, url: str, *, title: str = "", snippet: str = "", published_at: str = "") -> int:
        key = str(url or "").strip()
        try:
            from saya_app.services.web_research.search import canonicalize_url
            key = canonicalize_url(key) or key
        except Exception:
            pass
        if key not in self._by_url:
            source_id = len(self._by_url) + 1
            self._by_url[key] = source_id
        source_id = self._by_url[key]
        current = self._sources.setdefault(source_id, {"source_id": source_id, "url": key})
        for name, value in (("title", title), ("snippet", snippet), ("published_at", published_at)):
            if value:
                current[name] = str(value)[:2000]
        return source_id

    def resolve(self, value: Any) -> str | None:
        try:
            source_id = int(value)
        except (TypeError, ValueError):
            return str(value).strip() if str(value or "").startswith(("http://", "https://")) else None
        source = self._sources.get(source_id)
        return str(source["url"]) if source else None

    def public_sources(self) -> list[dict[str, Any]]:
        return [dict(self._sources[key]) for key in sorted(self._sources)]


class WebToolsV2:
    """Expose search/read as separate bounded operations with DLP and fail-open."""

    def __init__(self, *, search_service=None, reader=None, api_key: str = "", budget: WebToolBudget | None = None, private_terms: Iterable[str] | None = None, resolver=None):
        self.search_service = search_service
        self.reader = reader
        self.api_key = str(api_key or "")
        self.budget = budget or WebToolBudget()
        self.sources = SourceRegistry()
        self.private_terms = [str(term) for term in (private_terms or []) if str(term).strip()]
        self.resolver = resolver

    def search(self, query: Any, *, freshness: str = "", domains: Iterable[str] | None = None, max_results: int = 5, private_context: Any = "") -> dict[str, Any]:
        if not self.budget.search_available():
            return {"status": "budget_exhausted", "operation": "web.search", "sources": [], "redaction": {"applied": False, "fields": []}}
        cleaned, fields = self.redact_query(query, private_context=private_context)
        if not cleaned:
            return {"status": "blocked", "operation": "web.search", "error": "redacted_empty_query", "sources": [], "redaction": {"applied": bool(fields), "fields": fields}}
        if self.search_service is None:
            try:
                from saya_app.services.web_research.search import BraveSearchService
                self.search_service = BraveSearchService()
            except Exception:
                return {"status": "unconfigured", "operation": "web.search", "sources": [], "redaction": {"applied": bool(fields), "fields": fields}}
        self.budget.search_calls += 1
        count = max(1, min(20, int(max_results or 5)))
        if domains:
            domain_text = " OR ".join(str(domain).strip() for domain in domains if str(domain).strip())
            if domain_text:
                cleaned = f"{cleaned} ({domain_text})"
        try:
            results, error = self.search_service.search(cleaned, self.api_key, count=count, max_results=count, freshness=freshness)
        except Exception as exc:
            return {"status": "failed", "operation": "web.search", "error": "provider_error", "detail": str(exc)[:160], "sources": [], "redaction": {"applied": bool(fields), "fields": fields}}
        if error:
            return {"status": "failed", "operation": "web.search", "error": str(error)[:80], "sources": [], "redaction": {"applied": bool(fields), "fields": fields}}
        output = []
        emitted_ids: set[int] = set()
        for result in results or []:
            url = str(getattr(result, "url", "") or (result.get("url") if isinstance(result, Mapping) else ""))
            if not url:
                continue
            title = getattr(result, "title", "") if not isinstance(result, Mapping) else result.get("title", "")
            snippet = getattr(result, "snippet", "") if not isinstance(result, Mapping) else result.get("snippet", "")
            published = getattr(result, "published_at", "") if not isinstance(result, Mapping) else result.get("published_at", "")
            source_id = self.sources.register(url, title=title, snippet=snippet, published_at=published)
            if source_id in emitted_ids:
                continue
            emitted_ids.add(source_id)
            output.append({
                "source_id": source_id, "title": str(title or "")[:300], "url": url,
                "snippet": str(snippet or "")[:2000], "published_at": str(published or "")[:80],
                "status": "snippet_only",
            })
        return {"status": "success", "operation": "web.search", "query": cleaned, "sources": output,
                "warnings":['safe_search_unavailable'] if getattr(self.search_service,'provider','brave')=='deepseek' else [],
                "redaction": {"applied": bool(fields), "fields": fields}}

    def read(self, urls: Iterable[Any] | Any, *, max_chars_each: int | None = None) -> dict[str, Any]:
        if isinstance(urls, (str, bytes)):
            urls = [urls]
        requested = []
        for value in urls or []:
            url = self.sources.resolve(value) or str(value or "").strip()
            if url and url not in requested:
                requested.append(url)
        allowed = max(0, int(self.budget.max_read_urls)) - self.budget.read_urls
        requested = requested[:allowed]
        if not requested:
            return {"status": "budget_exhausted", "operation": "web.read", "documents": [], "sources": self.sources.public_sources()}
        if self.reader is None:
            try:
                from saya_app.services.web_research.reader import WebReader
                self.reader = WebReader()
            except Exception:
                return {"status": "unconfigured", "operation": "web.read", "documents": [], "sources": self.sources.public_sources()}
        cap = max(500, min(int(max_chars_each or self.budget.max_chars_each), self.budget.max_chars_each))
        documents = []
        for url in requested:
            self.budget.read_urls += 1
            source_id = self.sources.register(url)
            try:
                from saya_app.services.web_research.security import is_url_safe
                safe, code, _info = is_url_safe(url, resolver=self.resolver)
            except Exception:
                safe, code = False, "url_validation_error"
            if not safe:
                documents.append({
                    "source_id": source_id, "url": url, "status": "blocked", "error_code": code or "unsafe_url",
                    "text": "", "untrusted_content": True,
                })
                continue
            try:
                page = self.reader.fetch(url)
                text = str(getattr(page, "text", "") or "")[:cap]
                remaining = max(0, self.budget.max_total_chars - self.budget.total_chars)
                text = text[:remaining]
                self.budget.total_chars += len(text)
                status = "read" if str(getattr(page, "status", "")) == "ok" and text else str(getattr(page, "status", "failed") or "failed")
                injection = bool(_INJECTION_RE.search(text))
                documents.append({
                    "source_id": source_id, "url": str(getattr(page, "final_url", "") or url),
                    "title": str(getattr(page, "title", "") or "")[:300], "status": status,
                    "error_code": str(getattr(page, "error_code", "") or "")[:80],
                    "text": text, "untrusted_content": True, "prompt_injection_detected": injection,
                })
            except Exception as exc:
                documents.append({"source_id": source_id, "url": url, "status": "failed", "error_code": "reader_error", "detail": str(exc)[:160], "text": "", "untrusted_content": True})
        overall = "success" if any(document["status"] == "read" for document in documents) else "partial"
        return {"status": overall, "operation": "web.read", "documents": documents, "sources": self.sources.public_sources()}

    def redact_query(self, query: Any, *, private_context: Any = "") -> tuple[str, list[str]]:
        text = str(query or "").strip()
        fields: list[str] = []
        for term in self.private_terms + ([str(private_context)] if private_context else []):
            term = term.strip()
            if term and term.lower() in text.lower():
                text = re.sub(re.escape(term), "[PRIVATE]", text, flags=re.IGNORECASE)
                fields.append("private_term")
        if _SECRET_RE.search(text):
            text = _SECRET_RE.sub("[REDACTED]", text)
            fields.append("secret")
        if _EMAIL_RE.search(text):
            text = _EMAIL_RE.sub("[PRIVATE_EMAIL]", text)
            fields.append("email")
        if _PHONE_RE.search(text):
            text = _PHONE_RE.sub("[PRIVATE_PHONE]", text)
            fields.append("phone")
        return re.sub(r"\s+", " ", text).strip(), list(dict.fromkeys(fields))

    def source_citations(self) -> list[dict[str, Any]]:
        return self.sources.public_sources()

    def validate_answer(self, answer: str) -> dict[str, Any]:
        """Validate [n] citations against only this session's sources."""
        try:
            from saya_app.services.web_research.citation_validator import validate_response_citations
            from saya_app.services.web_research.models import WebSource
            sources = []
            for raw in self.sources.public_sources():
                sources.append(WebSource(
                    id=int(raw["source_id"]), title=raw.get("title", ""), url=raw.get("url", ""),
                    domain="", snippet=raw.get("snippet", ""), published_at=raw.get("published_at", ""),
                    status=raw.get("status", "snippet_only"), text="",
                ))
            return validate_response_citations(str(answer or ""), sources)
        except Exception:
            return {"status": "validator_unavailable", "cited_claims": 0, "invalid_references": 0}
