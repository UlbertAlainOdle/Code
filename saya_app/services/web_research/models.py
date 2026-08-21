"""Pure data structures for the web research pipeline.

These dataclasses follow the fixed contracts in
``docs/saya-web-research-implementation-plan.md`` (section 7). They are used
inside the service layer and intentionally do not depend on FastAPI/Pydantic
request models.
"""

from dataclasses import dataclass, field
from typing import List


# Allowed values for ``WebSource.status``.
SOURCE_STATUSES = ("read", "snippet_only", "blocked", "failed")


@dataclass
class SearchResult:
    """A single result returned by the Brave Search API."""

    title: str
    url: str
    snippet: str = ""
    extra_snippets: List[str] = field(default_factory=list)
    published_at: str = ""
    rank: int = 0


@dataclass
class SearchPlan:
    """A deterministic plan describing how a web query should be sourced."""

    query: str
    topic_type: str = "general"
    source_strategy: str = "official_first"
    search_lang: str = ""
    country: str = ""
    ui_lang: str = ""
    freshness: str = ""
    requires_primary: bool = False


@dataclass
class PageDocument:
    """The outcome of reading a single webpage.

    ``text`` holds the extracted main text only. ``status`` is ``ok`` on a
    successful read, otherwise an internal error code is stored in
    ``error_code``.
    """

    requested_url: str
    final_url: str = ""
    title: str = ""
    text: str = ""
    content_type: str = ""
    status: str = ""
    error_code: str = ""


@dataclass
class WebSource:
    """A numbered source shared across SSE, prompt evidence and memory.

    ``text`` is internal-only and MUST be excluded when serializing to the
    frontend or persistent memory. Use :meth:`to_public_dict` for that.
    """

    id: int
    title: str
    url: str
    domain: str
    snippet: str = ""
    published_at: str = ""
    status: str = "failed"
    source_type: str = "web"
    authority: str = "unknown"
    quality_score: float = 0.0
    # Internal only: never serialize this field to SSE or memory.
    text: str = ""

    def to_public_dict(self) -> dict:
        """Return a safe mapping for SSE and memory.

        This deliberately omits ``text`` (full page content) per the privacy
        contract: page text must not be persisted or sent to the frontend.
        """
        return {
            "id": self.id,
            "title": self.title,
            "url": self.url,
            "domain": self.domain,
            "snippet": self.snippet,
            "published_at": self.published_at,
            "status": self.status,
            "source_type": self.source_type,
            "authority": self.authority,
        }


@dataclass
class ResearchBundle:
    """The final result of one web research run."""

    query: str = ""
    sources: List[WebSource] = field(default_factory=list)
    context_text: str = ""
    warnings: List[str] = field(default_factory=list)
    performed: bool = False
    requested_search_count: int = 0
    requested_final_sources: int = 0
    candidate_count: int = 0
    selected_count: int = 0
    read_count: int = 0
    snippet_count: int = 0

    def to_public_diagnostics(self) -> dict:
        """Return non-sensitive counters for debugging preset propagation."""
        return {
            "requested_search_count": self.requested_search_count,
            "requested_final_sources": self.requested_final_sources,
            "candidate_count": self.candidate_count,
            "selected_count": self.selected_count,
            "read_count": self.read_count,
            "snippet_count": self.snippet_count,
            "returned_sources": len(self.sources),
        }
