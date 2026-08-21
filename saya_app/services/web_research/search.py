"""Brave Search API adapter (plan section 11).

A thin, injectable adapter around the Brave Web Search endpoint. It:
- requests one search page with bounded locale/source parameters;
- normalizes results into :class:`SearchResult`, preserving rank order;
- normalizes and de-duplicates result URLs;
- maps HTTP / JSON / field errors into a structured failure result, never
  raising to the caller;
- never logs the ``X-Subscription-Token`` header.
"""

from urllib.parse import urlsplit, urlunsplit

import requests

from saya_app.config import (
    BRAVE_SEARCH_ENDPOINT,
    WEB_SEARCH_CONNECT_TIMEOUT,
    WEB_SEARCH_COUNT,
    WEB_SEARCH_MAX_RESULTS,
    WEB_SEARCH_READ_TIMEOUT,
)
from saya_app.services.web_research.models import SearchResult


def canonicalize_url(url: str) -> str:
    """Normalize a URL for de-duplication.

    Lowercases scheme/host, drops fragment, and strips a trailing slash on the
    path root so ``https://a.com`` and ``https://a.com/`` collide.
    """
    if not url:
        return ""
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not (
        (scheme == "http" and port == 80) or (scheme == "https" and port == 443)
    ):
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, parts.query, ""))


class BraveSearchService:
    """Adapter for the Brave Search API."""

    def __init__(self, session=None, timeout=None, endpoint=None):
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout or (
            WEB_SEARCH_CONNECT_TIMEOUT,
            WEB_SEARCH_READ_TIMEOUT,
        )
        self.endpoint = endpoint or BRAVE_SEARCH_ENDPOINT

    def search(
        self, query: str, api_key: str, count=None, max_results=None,
        search_lang="", country="", ui_lang="", freshness="",
        safesearch="moderate", goggles=None, offset=0,
    ):
        """Run one Brave search.

        Returns ``(results, error_code)``. ``results`` is a list of
        :class:`SearchResult` (ranked, de-duplicated, capped at
        ``max_results`` or ``WEB_SEARCH_MAX_RESULTS``). ``error_code`` is
        ``""`` on success. Never raises network/HTTP errors to the caller.
        """
        if not query or not query.strip():
            return [], "empty_query"
        if not api_key:
            return [], "missing_key"

        requested_count = max(1, min(int(count or WEB_SEARCH_COUNT), 20))
        safe_level = safesearch if safesearch in ("off", "moderate", "strict") else "moderate"
        params = {
            "q": query,
            "count": requested_count,
            "extra_snippets": True,
            "safesearch": safe_level,
            "result_filter": "web",
            "offset": max(0, min(int(offset or 0), 9)),
        }
        if search_lang:
            params["search_lang"] = search_lang
        if country:
            params["country"] = country
        if ui_lang:
            params["ui_lang"] = ui_lang
        if freshness:
            params["freshness"] = freshness
        if goggles:
            params["goggles"] = goggles
        # Header value is held only in the request; never put it in logs/errors.
        headers = {
            "Accept": "application/json",
            "X-Subscription-Token": api_key,
        }

        try:
            resp = self.session.get(
                self.endpoint,
                params=params,
                headers=headers,
                timeout=self.timeout,
            )
        except requests.RequestException:
            return [], "network_error"

        if resp.status_code == 401 or resp.status_code == 403:
            return [], "auth_error"
        if resp.status_code == 429:
            return [], "rate_limited"
        if resp.status_code >= 500:
            return [], "server_error"
        if resp.status_code != 200:
            return [], f"http_{resp.status_code}"

        try:
            data = resp.json()
        except ValueError:
            return [], "invalid_json"

        raw_results = self._extract_results(data)
        result_cap = max_results if max_results is not None else requested_count
        return self._normalize(raw_results, max_results=result_cap), ""

    # ----------------------------------------------------------- helpers
    def _extract_results(self, data: dict):
        """Pull the list of generic web results from the Brave payload.

        Brave places web results under ``web.results``. We are defensive: any
        structural surprise returns an empty list rather than raising.
        """
        if not isinstance(data, dict):
            return []
        web = data.get("web")
        if not isinstance(web, dict):
            return []
        results = web.get("results")
        if not isinstance(results, list):
            return []
        return results

    def _normalize(self, raw_results, max_results=None):
        """Convert raw Brave items to ``SearchResult`` with rank + de-dup."""
        cap = max(1, min(int(max_results or WEB_SEARCH_MAX_RESULTS), WEB_SEARCH_MAX_RESULTS))
        normalized = []
        seen = set()
        rank = 0
        kept = 0
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            url = item.get("url") or ""
            if not url:
                continue
            canon = canonicalize_url(url)
            if not canon or canon in seen:
                continue
            seen.add(canon)
            rank += 1
            snippet = item.get("description") or item.get("text") or ""
            extra = item.get("extra_snippets") or []
            if not isinstance(extra, list):
                extra = []
            extra = [str(s) for s in extra if s]
            normalized.append(
                SearchResult(
                    title=item.get("title") or "",
                    url=canon,
                    snippet=str(snippet),
                    extra_snippets=extra,
                    published_at=item.get("page_age")
                    or item.get("published")
                    or "",
                    rank=rank,
                )
            )
            kept += 1
            if kept >= cap:
                break
        return normalized
