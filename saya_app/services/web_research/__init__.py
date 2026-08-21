"""Controlled web search and reading pipeline for Saya.

Submodules:
- ``models``: pure data structures.
- ``policy``: mode normalization, auto trigger, URL extraction, query cleanup.
- ``security``: URL/DNS/IP/port and redirect validation.
- ``reader``: robots, HTTP download, size limits, content extraction.
- ``search``: Brave Search API adapter.
- ``orchestrator``: search/read/dedup/numbering/fallback/context formatting.
"""

from saya_app.services.web_research.models import (
    ResearchBundle,
    PageDocument,
    SearchResult,
    WebSource,
)

__all__ = [
    "ResearchBundle",
    "PageDocument",
    "SearchResult",
    "WebSource",
]
