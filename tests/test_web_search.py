"""Tests for the Brave Search adapter (plan section 21, items 6, 9).

No real network: ``requests.Session`` is faked.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saya_app.services.web_research import search as search_mod
from saya_app.services.web_research.search import (
    BraveSearchService,
    canonicalize_url,
)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json = json_data
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class FakeSession:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.last_call = None

    def get(self, url, params=None, headers=None, timeout=None):
        self.last_call = {"url": url, "params": params, "headers": headers}
        if self.error is not None:
            raise self.error
        return self.response


def brave_payload(results):
    return {"web": {"results": results}}


class CanonicalizeTests(unittest.TestCase):
    def test_lowercases_scheme_and_host(self):
        self.assertEqual(canonicalize_url("HTTP://Example.COM/Path"), "http://example.com/Path")

    def test_drops_fragment(self):
        self.assertEqual(canonicalize_url("https://a.com/p#frag"), "https://a.com/p")

    def test_trailing_slash_collides_with_root(self):
        self.assertEqual(canonicalize_url("https://a.com"), "https://a.com/")
        self.assertEqual(canonicalize_url("https://a.com/"), "https://a.com/")

    def test_default_port_normalized(self):
        self.assertEqual(canonicalize_url("https://a.com:443/p"), "https://a.com/p")
        self.assertEqual(canonicalize_url("http://a.com:80/p"), "http://a.com/p")


class SearchSuccessTests(unittest.TestCase):
    def test_returns_ranked_deduped_results(self):
        payload = brave_payload([
            {"title": "A", "url": "https://a.com/1", "description": "snip a"},
            {"title": "B", "url": "https://b.org/x", "description": "snip b",
             "extra_snippets": ["extra1", "extra2"], "page_age": "2024-01-01"},
            # Duplicate of A after canonicalization -> dropped.
            {"title": "A2", "url": "https://a.com/1/", "description": "dup"},
            {"title": "C", "url": "https://c.net/", "description": "snip c"},
        ])
        svc = BraveSearchService(session=FakeSession(FakeResponse(json_data=payload)))
        results, err = svc.search("query", "key")
        self.assertEqual(err, "")
        self.assertEqual(len(results), 3)
        self.assertEqual([r.rank for r in results], [1, 2, 3])
        self.assertEqual(results[0].url, "https://a.com/1")
        self.assertEqual(results[1].snippet, "snip b")
        self.assertEqual(results[1].extra_snippets, ["extra1", "extra2"])
        self.assertEqual(results[1].published_at, "2024-01-01")

    def test_capped_at_max_results(self):
        payload = brave_payload([
            {"title": f"R{i}", "url": f"https://d{i}.com/p", "description": "s"}
            for i in range(10)
        ])
        svc = BraveSearchService(session=FakeSession(FakeResponse(json_data=payload)))
        results, _ = svc.search("q", "key")
        from saya_app.config import WEB_SEARCH_COUNT
        self.assertEqual(len(results), WEB_SEARCH_COUNT)

    def test_locale_quality_and_safe_search_params_are_forwarded(self):
        session = FakeSession(FakeResponse(json_data=brave_payload([])))
        svc = BraveSearchService(session=session)
        svc.search(
            "DeepSeek context", "key", count=99, search_lang="en",
            country="US", ui_lang="en-US", freshness="pw", safesearch="strict",
        )
        params = session.last_call["params"]
        self.assertEqual(params["count"], 20)
        self.assertEqual(params["search_lang"], "en")
        self.assertEqual(params["country"], "US")
        self.assertEqual(params["ui_lang"], "en-US")
        self.assertEqual(params["freshness"], "pw")
        self.assertEqual(params["safesearch"], "strict")
        self.assertEqual(params["result_filter"], "web")
        self.assertEqual(params["offset"], 0)

    def test_offset_is_bounded(self):
        session = FakeSession(FakeResponse(json_data=brave_payload([])))
        svc = BraveSearchService(session=session)
        svc.search("query", "key", offset=99)
        self.assertEqual(session.last_call["params"]["offset"], 9)

    def test_invalid_safe_search_falls_back_to_moderate(self):
        session = FakeSession(FakeResponse(json_data=brave_payload([])))
        BraveSearchService(session=session).search("q", "key", safesearch="invalid")
        self.assertEqual(session.last_call["params"]["safesearch"], "moderate")

    def test_missing_results_key_yields_empty_not_error(self):
        # Brave payload without the expected shape.
        svc = BraveSearchService(session=FakeSession(FakeResponse(json_data={})))
        results, err = svc.search("q", "key")
        self.assertEqual(results, [])
        self.assertEqual(err, "")


class SearchErrorTests(unittest.TestCase):
    def test_empty_query(self):
        svc = BraveSearchService(session=FakeSession())
        self.assertEqual(svc.search("", "key"), ([], "empty_query"))

    def test_missing_key(self):
        svc = BraveSearchService(session=FakeSession())
        self.assertEqual(svc.search("q", ""), ([], "missing_key"))
        self.assertEqual(svc.search("q", None), ([], "missing_key"))

    def test_auth_error(self):
        svc = BraveSearchService(session=FakeSession(FakeResponse(status_code=401)))
        self.assertEqual(svc.search("q", "bad"), ([], "auth_error"))

    def test_rate_limited(self):
        svc = BraveSearchService(session=FakeSession(FakeResponse(status_code=429)))
        self.assertEqual(svc.search("q", "k"), ([], "rate_limited"))

    def test_server_error(self):
        svc = BraveSearchService(session=FakeSession(FakeResponse(status_code=503)))
        self.assertEqual(svc.search("q", "k"), ([], "server_error"))

    def test_invalid_json(self):
        svc = BraveSearchService(session=FakeSession(FakeResponse(json_data=None)))
        self.assertEqual(svc.search("q", "k"), ([], "invalid_json"))

    def test_network_error_not_raised(self):
        import requests
        svc = BraveSearchService(
            session=FakeSession(error=requests.RequestException("boom"))
        )
        results, err = svc.search("q", "k")
        self.assertEqual(results, [])
        self.assertEqual(err, "network_error")


class KeySafetyTests(unittest.TestCase):
    def test_key_only_in_header_never_in_params(self):
        payload = brave_payload([])
        session = FakeSession(FakeResponse(json_data=payload))
        svc = BraveSearchService(session=session)
        svc.search("query", "SECRET-KEY-123")
        self.assertEqual(session.last_call["headers"]["X-Subscription-Token"], "SECRET-KEY-123")
        # Key must not leak into URL query params.
        self.assertNotIn("SECRET-KEY-123", str(session.last_call["params"]))
        self.assertNotIn("SECRET-KEY-123", str(session.last_call["url"]))


if __name__ == "__main__":
    unittest.main()
