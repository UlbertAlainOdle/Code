"""Tests for policy + orchestrator (plan section 21, items 1-9, 13-15).

No real network: search service and reader are faked.
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from saya_app.services.web_research.models import PageDocument, ResearchBundle, SearchResult, WebSource
from saya_app.services.web_research.orchestrator import WebResearchOrchestrator, domain_of
from saya_app.services.web_research import policy


# --------------------------------------------------------------------- policy
class NormalizeModeTests(unittest.TestCase):
    def test_known_values(self):
        self.assertEqual(policy.normalize_mode("off"), "off")
        self.assertEqual(policy.normalize_mode("OFF"), "off")
        self.assertEqual(policy.normalize_mode("关闭"), "off")
        self.assertEqual(policy.normalize_mode("auto"), "auto")
        self.assertEqual(policy.normalize_mode("自动"), "auto")
        self.assertEqual(policy.normalize_mode("force"), "force")
        self.assertEqual(policy.normalize_mode("本次强制"), "force")

    def test_unknown_falls_back_to_off(self):
        self.assertEqual(policy.normalize_mode("whatever"), "off")
        self.assertEqual(policy.normalize_mode(""), "off")
        self.assertEqual(policy.normalize_mode(None), "off")

    def test_japan_study_policy_uses_official_country_plan_and_wider_window(self):
        plan = policy.build_search_plan(
            "了解可能影响一年后去日本留学的新闻或者政策",
            source_strategy="official_first",
        )
        self.assertEqual(plan.topic_type, "government")
        self.assertEqual(plan.country, "JP")
        self.assertEqual(plan.ui_lang, "ja-JP")
        self.assertEqual(plan.freshness, "py")
        self.assertTrue(plan.requires_primary)
        self.assertIn("出入国在留管理庁", plan.query)

    def test_general_travel_or_culture_topic_is_not_forced_into_research(self):
        plan = policy.build_search_plan(
            "Lisbon neighbourhood changes and cultural events official institutional or well-established source",
            source_strategy="official_first",
        )
        self.assertEqual(plan.topic_type, "general")
        self.assertFalse(plan.requires_primary)


class ExtractUrlsTests(unittest.TestCase):
    def test_extracts_http_https(self):
        msg = "see https://a.com/p and http://b.org/x?q=1"
        urls = policy.extract_direct_urls(msg)
        self.assertEqual(urls, ["https://a.com/p", "http://b.org/x?q=1"])

    def test_dedup_preserves_order(self):
        msg = "https://a.com https://a.com/ http://b.org"
        urls = policy.extract_direct_urls(msg)
        self.assertEqual(urls[0], "https://a.com/")
        self.assertEqual(len(urls), 2)

    def test_ignores_non_http(self):
        self.assertEqual(policy.extract_direct_urls("ftp://a.com/x"), [])

    def test_strips_trailing_punctuation(self):
        urls = policy.extract_direct_urls("read (https://a.com/p).")
        self.assertEqual(urls, ["https://a.com/p"])


class ShouldResearchTests(unittest.TestCase):
    def test_off_never(self):
        self.assertFalse(policy.should_research("off", "查一下最新新闻"))
        self.assertFalse(policy.should_research("off", "https://a.com"))

    def test_force_always(self):
        self.assertTrue(policy.should_research("force", "hello"))
        self.assertTrue(policy.should_research("force", ""))

    def test_auto_url_triggers(self):
        self.assertTrue(policy.should_research("auto", "总结这个 https://a.com/p"))

    def test_auto_request_word_triggers(self):
        self.assertTrue(policy.should_research("auto", "帮我搜索一下AI新闻"))

    def test_auto_time_factor_triggers(self):
        self.assertTrue(policy.should_research("auto", "Python 最新版本是多少"))
        self.assertTrue(policy.should_research("auto", "今天人民币汇率怎样"))

    def test_auto_emotional_does_not_trigger(self):
        self.assertFalse(policy.should_research("auto", "我今天有点难过"))
        self.assertFalse(policy.should_research("auto", "现在陪我聊聊"))

    def test_auto_bare_now_alone_does_not_trigger(self):
        # "现在" or "当前" alone must not trigger (plan 10.3). These messages
        # contain no time-sensitive factor and no explicit request word.
        self.assertFalse(policy.should_research("auto", "现在的颜色真好看"))
        self.assertFalse(policy.should_research("auto", "当前的气氛很安静"))

    def test_auto_persona_chat_does_not_trigger(self):
        self.assertFalse(policy.should_research("auto", "你还记得我们昨天的对话吗"))
        self.assertFalse(policy.should_research("auto", "翻译一下这段话"))


class BuildQueryTests(unittest.TestCase):
    def test_strips_prefix_and_urls(self):
        q = policy.build_query("帮我搜索一下 https://a.com/p 的内容", max_chars=400)
        self.assertNotIn("https://", q)
        self.assertNotIn("搜索", q)
        self.assertIn("内容", q)

    def test_caps_length(self):
        long = "搜索 " + ("abcdefghij" * 100)
        q = policy.build_query(long, max_chars=50)
        self.assertLessEqual(len(q), 50)

    def test_empty_message(self):
        self.assertEqual(policy.build_query(""), "")


class SearchPlanTests(unittest.TestCase):
    def test_technical_query_prefers_original_english_documentation(self):
        plan = policy.build_search_plan("DeepSeek V4 的上下文参数是多少")
        self.assertEqual(plan.topic_type, "technical")
        self.assertEqual(plan.search_lang, "en")
        self.assertIn("official documentation", plan.query)
        self.assertTrue(plan.requires_primary)

    def test_chinese_policy_query_prefers_chinese_primary_authority(self):
        plan = policy.build_search_plan("中国最新医保政策何时实施")
        self.assertEqual(plan.topic_type, "government")
        self.assertEqual(plan.search_lang, "zh-hans")
        self.assertEqual(plan.country, "CN")
        self.assertIn("官方 原文", plan.query)
        self.assertTrue(plan.requires_primary)

    def test_community_experience_query_does_not_force_official_docs(self):
        plan = policy.build_search_plan("大家使用 DeepSeek V4 的体验和评价如何")
        self.assertEqual(plan.topic_type, "community")
        self.assertEqual(plan.source_strategy, "balanced")
        self.assertNotIn("official documentation", plan.query)
        self.assertFalse(plan.requires_primary)

    def test_explicit_community_strategy_is_preserved(self):
        plan = policy.build_search_plan("Python 编辑器推荐", source_strategy="community")
        self.assertEqual(plan.source_strategy, "community")

    def test_china_public_opinion_is_not_misclassified_as_government(self):
        plan = policy.build_search_plan("高市早苗在中国的风评如何？")
        self.assertEqual(plan.topic_type, "community")
        self.assertEqual(plan.source_strategy, "balanced")
        self.assertNotIn("官方 原文", plan.query)

    def test_china_requires_a_government_topic_for_government_plan(self):
        plan = policy.build_search_plan("中国医保政策什么时候生效？")
        self.assertEqual(plan.topic_type, "government")
        self.assertIn("政府官网", plan.query)

    def test_journal_impact_factor_gets_professional_primary_plan(self):
        plan = policy.build_search_plan("WJG期刊最新的影响因子是多少？")
        self.assertEqual(plan.topic_type, "journal_metric")
        self.assertEqual(plan.search_lang, "en")
        self.assertIn("Clarivate JCR", plan.query)
        self.assertTrue(plan.requires_primary)


# --------------------------------------------------------------- fakes
class FakeSearchService:
    def __init__(self, results=None, error=""):
        self.results = results or []
        self.error = error
        self.calls = []

    def search(self, query, api_key, **kwargs):
        self.calls.append((query, api_key))
        return list(self.results), self.error


class FakeReader:
    """Returns scripted PageDocuments keyed by URL suffix.

    ``script`` maps url-substring -> PageDocument (or status string). URLs not
    in the script return a failed doc.
    """
    def __init__(self, script=None):
        self.script = script or {}
        self.calls = []

    def fetch(self, url, fallback_title="", check_robots=True):
        self.calls.append(url)
        for key, value in self.script.items():
            if key in url:
                if isinstance(value, PageDocument):
                    return value
                # value is a status string
                return PageDocument(requested_url=url, final_url=url, status=value)
        return PageDocument(requested_url=url, final_url=url, status="failed",
                            error_code="network_error")


def make_doc(url, text, title="T", status="ok"):
    return PageDocument(requested_url=url, final_url=url, title=title,
                        text=text, status=status)


# --------------------------------------------------------------- orchestrator
class ResearchDecisionTests(unittest.TestCase):
    def test_off_returns_not_performed(self):
        orch = WebResearchOrchestrator(FakeSearchService(), FakeReader())
        bundle = orch.research("off", "查一下最新新闻", "key")
        self.assertFalse(bundle.performed)
        self.assertEqual(bundle.sources, [])

    def test_auto_emotional_skips(self):
        orch = WebResearchOrchestrator(FakeSearchService(), FakeReader())
        bundle = orch.research("auto", "我今天有点难过", "key")
        self.assertFalse(bundle.performed)


class DirectUrlTests(unittest.TestCase):
    def test_direct_url_does_not_need_key(self):
        reader = FakeReader({"a.com": make_doc("https://a.com/p", "body text")})
        orch = WebResearchOrchestrator(FakeSearchService(), reader)
        bundle = orch.research("force", "读 https://a.com/p", api_key="")
        self.assertTrue(bundle.performed)
        self.assertEqual(len(bundle.sources), 1)
        self.assertEqual(bundle.sources[0].status, "read")
        self.assertEqual(bundle.sources[0].text, "body text")

    def test_direct_url_key_not_required_even_in_force(self):
        # No api_key, but direct URL present -> still works.
        reader = FakeReader({"a.com": make_doc("https://a.com/p", "x")})
        orch = WebResearchOrchestrator(FakeSearchService(), reader)
        bundle = orch.research("force", "https://a.com/p", api_key="")
        self.assertTrue(any(s.status == "read" for s in bundle.sources))


class SearchFlowTests(unittest.TestCase):
    def _results(self):
        return [
            SearchResult(title="A", url="https://a.com/1", snippet="snip a", rank=1),
            SearchResult(title="B", url="https://b.org/2", snippet="snip b", rank=2),
            SearchResult(title="C", url="https://c.net/3", snippet="snip c", rank=3),
        ]

    def test_search_then_read_three_sources(self):
        reader = FakeReader({
            "a.com": make_doc("https://a.com/1", "full a", title="A"),
            "b.org": make_doc("https://b.org/2", "full b", title="B"),
            "c.net": make_doc("https://c.net/3", "full c", title="C"),
        })
        orch = WebResearchOrchestrator(FakeSearchService(self._results()), reader)
        bundle = orch.research("force", "查一下AI新闻", api_key="KEY")
        self.assertTrue(bundle.performed)
        self.assertEqual(len(bundle.sources), 3)
        self.assertEqual([s.id for s in bundle.sources], [1, 2, 3])
        self.assertTrue(all(s.status == "read" for s in bundle.sources))

    def test_missing_key_warning(self):
        orch = WebResearchOrchestrator(FakeSearchService(self._results()), FakeReader())
        bundle = orch.research("force", "查一下新闻", api_key="")
        self.assertIn("missing_key", bundle.warnings)
        self.assertEqual(bundle.sources, [])

    def test_search_failed_warning(self):
        orch = WebResearchOrchestrator(
            FakeSearchService(error="network_error"), FakeReader()
        )
        bundle = orch.research("force", "查一下新闻", api_key="KEY")
        self.assertIn("search_failed", bundle.warnings)
        self.assertEqual(bundle.sources, [])

    def test_domain_diversification(self):
        # Two results share a domain; the third distinct domain is preferred.
        results = [
            SearchResult(title="A1", url="https://a.com/1", snippet="a1", rank=1),
            SearchResult(title="A2", url="https://a.com/2", snippet="a2", rank=2),
            SearchResult(title="B", url="https://b.org/1", snippet="b", rank=3),
        ]
        reader = FakeReader({
            "a.com/1": make_doc("https://a.com/1", "a1"),
            "a.com/2": make_doc("https://a.com/2", "a2"),
            "b.org": make_doc("https://b.org/1", "b"),
        })
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查一下", api_key="KEY")
        domains = [s.domain for s in bundle.sources]
        # Distinct domains selected first.
        self.assertIn("a.com", domains)
        self.assertIn("b.org", domains)

    def test_official_documentation_ranks_above_earlier_aggregator(self):
        results = [
            SearchResult(
                title="一文看懂 DeepSeek V4 参数", url="https://blog.csdn.net/x/post",
                snippet="个人观点和转载内容", rank=1,
            ),
            SearchResult(
                title="DeepSeek API Docs", url="https://api-docs.deepseek.com/quick_start",
                snippet="Official API documentation", rank=4,
            ),
            SearchResult(
                title="DeepSeek discussion", url="https://www.zhihu.com/question/1",
                snippet="用户讨论", rank=2,
            ),
        ]
        reader = FakeReader({
            "api-docs.deepseek.com": make_doc(
                "https://api-docs.deepseek.com/quick_start", "official body"
            ),
            "csdn.net": make_doc("https://blog.csdn.net/x/post", "blog body"),
            "zhihu.com": make_doc("https://www.zhihu.com/question/1", "community body"),
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force", "DeepSeek V4 的 API 参数", api_key="KEY", max_final_sources=2
        )
        self.assertEqual(bundle.sources[0].domain, "api-docs.deepseek.com")
        self.assertEqual(bundle.sources[0].source_type, "official")
        self.assertEqual(bundle.sources[0].authority, "primary")
        self.assertNotIn("no_primary_source", bundle.warnings)

    def test_search_failure_preserves_structured_reason(self):
        class RateLimitedSearch:
            def search(self, *args, **kwargs):
                return [], "rate_limited"

        bundle = WebResearchOrchestrator(RateLimitedSearch(), FakeReader({})).research(
            "force", "current broad topic", api_key="KEY"
        )
        self.assertIn("search_failed", bundle.warnings)
        self.assertIn("search_rate_limited", bundle.warnings)

    def test_japanese_government_and_academic_domains_are_authoritative(self):
        results = [
            SearchResult(
                title="Student status of residence",
                url="https://www.moj.go.jp/isa/applications/status/student.html",
                rank=1,
            ),
            SearchResult(
                title="Graduate admissions",
                url="https://www.example.ac.jp/admissions/international",
                rank=2,
            ),
        ]
        reader = FakeReader({
            "moj.go.jp": make_doc(
                "https://www.moj.go.jp/isa/applications/status/student.html",
                "official immigration policy",
            ),
            "example.ac.jp": make_doc(
                "https://www.example.ac.jp/admissions/international",
                "official university admissions",
            ),
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force",
            "日本留学生签证与大学院入学政策",
            api_key="KEY",
            max_final_sources=2,
        )
        by_domain = {source.domain: source for source in bundle.sources}
        self.assertEqual(by_domain["www.moj.go.jp"].source_type, "government")
        self.assertEqual(by_domain["www.moj.go.jp"].authority, "primary")
        self.assertEqual(by_domain["www.example.ac.jp"].source_type, "official")

    def test_diverse_trusted_institutions_and_company_newsroom_are_accepted(self):
        results = [
            SearchResult(title="Education outlook", url="https://www.oecd.org/education/outlook", rank=1),
            SearchResult(title="Example research", url="https://lab.example.edu/research", rank=2),
            SearchResult(title="Acme newsroom", url="https://acme.com/newsroom/update", rank=3),
            SearchResult(title="Market coverage", url="https://www.economist.com/example", rank=4),
        ]
        reader = FakeReader({
            "oecd.org": make_doc("https://www.oecd.org/education/outlook", "institutional report"),
            "example.edu": make_doc("https://lab.example.edu/research", "university research"),
            "acme.com": make_doc("https://acme.com/newsroom/update", "company announcement"),
            "economist.com": make_doc("https://www.economist.com/example", "reputable reporting"),
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force", "Acme education market update", api_key="KEY", max_final_sources=4
        )
        by_domain = {source.domain: source for source in bundle.sources}
        self.assertIn(by_domain["www.oecd.org"].source_type, {"official", "government"})
        self.assertEqual(by_domain["lab.example.edu"].source_type, "official")
        self.assertEqual(by_domain["acme.com"].source_type, "documentation")
        self.assertEqual(by_domain["www.economist.com"].source_type, "reputable_media")

    def test_two_primary_pages_may_beat_weak_domain_diversity(self):
        results = [
            SearchResult(title="Python docs A", url="https://docs.python.org/3/a", rank=2),
            SearchResult(title="Python docs B", url="https://docs.python.org/3/b", rank=3),
            SearchResult(title="Python personal post", url="https://blog.csdn.net/p", rank=1),
        ]
        reader = FakeReader({
            "/3/a": make_doc("https://docs.python.org/3/a", "a"),
            "/3/b": make_doc("https://docs.python.org/3/b", "b"),
            "csdn": make_doc("https://blog.csdn.net/p", "c"),
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force", "Python API 文档", api_key="KEY", max_final_sources=2
        )
        self.assertEqual([s.domain for s in bundle.sources], ["docs.python.org", "docs.python.org"])

    def test_primary_required_warns_when_only_community_pages_exist(self):
        results = [
            SearchResult(title="DeepSeek discussion", url="https://www.zhihu.com/question/1", rank=1),
        ]
        reader = FakeReader({
            "zhihu.com": make_doc("https://www.zhihu.com/question/1", "opinion")
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force", "DeepSeek API 参数", api_key="KEY"
        )
        self.assertIn("no_primary_source", bundle.warnings)

    def test_journal_official_sources_outrank_submission_services(self):
        results = [
            SearchResult(title="WJG影响因子", url="https://www.ifabiao.com/wjg/", rank=1),
            SearchResult(title="WJG journal metrics", url="https://www.wjgnet.com/1007-9327", rank=3),
            SearchResult(title="Journal Citation Reports", url="https://clarivate.com/jcr", rank=4),
        ]
        reader = FakeReader({
            "ifabiao": make_doc("https://www.ifabiao.com/wjg/", "service"),
            "wjgnet": make_doc("https://www.wjgnet.com/1007-9327", "publisher"),
            "clarivate": make_doc("https://clarivate.com/jcr", "jcr"),
        })
        bundle = WebResearchOrchestrator(FakeSearchService(results), reader).research(
            "force", "WJG期刊最新的影响因子是多少？", "KEY", max_final_sources=2
        )
        self.assertEqual(
            [source.domain for source in bundle.sources],
            ["clarivate.com", "www.wjgnet.com"],
        )
        self.assertTrue(all(source.authority == "primary" for source in bundle.sources))

    def test_second_page_can_fill_an_underfilled_deep_result(self):
        first = [
            SearchResult(title=f"R{i}", url=f"https://first{i}.com/p", rank=i)
            for i in range(1, 5)
        ]
        second = [
            SearchResult(title=f"R{i}", url=f"https://second{i}.com/p", rank=i)
            for i in range(1, 5)
        ]

        class PagedSearch:
            def __init__(self):
                self.calls = []

            def search(self, query, api_key, **kwargs):
                self.calls.append(kwargs)
                return (second if kwargs.get("offset") == 1 else first), ""

        reader = FakeReader({
            **{f"first{i}.com": make_doc(f"https://first{i}.com/p", "a") for i in range(1, 5)},
            **{f"second{i}.com": make_doc(f"https://second{i}.com/p", "b") for i in range(1, 5)},
        })
        search = PagedSearch()
        bundle = WebResearchOrchestrator(search, reader).research(
            "force", "general current fact", "KEY",
            search_count=16, max_final_sources=5,
        )
        self.assertEqual(len(search.calls), 2)
        self.assertEqual(search.calls[1]["offset"], 1)
        self.assertEqual(len(bundle.sources), 5)
        self.assertEqual(bundle.to_public_diagnostics()["requested_search_count"], 16)
        self.assertEqual(bundle.to_public_diagnostics()["requested_final_sources"], 5)
        self.assertEqual(bundle.to_public_diagnostics()["returned_sources"], 5)

    def test_partial_failure_continues_other_reads(self):
        # First read fails, others succeed: we still get the successful ones.
        reader = FakeReader({
            "a.com": PageDocument(requested_url="https://a.com/1", final_url="https://a.com/1",
                                  status="failed", error_code="network_error"),
            "b.org": make_doc("https://b.org/2", "full b"),
            "c.net": make_doc("https://c.net/3", "full c"),
        })
        orch = WebResearchOrchestrator(FakeSearchService(self._results()), reader)
        bundle = orch.research("force", "查一下", api_key="KEY")
        statuses = [s.status for s in bundle.sources]
        # The failed one degrades to snippet_only because snippet exists.
        self.assertIn("read", statuses)
        self.assertIn("snippet_only", statuses)

    def test_all_fail_degrades_to_snippets(self):
        reader = FakeReader()  # everything fails
        orch = WebResearchOrchestrator(FakeSearchService(self._results()), reader)
        bundle = orch.research("force", "查一下", api_key="KEY")
        self.assertTrue(bundle.sources)
        self.assertTrue(all(s.status == "snippet_only" for s in bundle.sources))
        self.assertIn("partial_read", bundle.warnings)


class NumberingStabilityTests(unittest.TestCase):
    def test_ids_start_at_one_and_are_sequential(self):
        results = [
            SearchResult(title="A", url="https://a.com/1", snippet="sa", rank=1),
            SearchResult(title="B", url="https://b.org/2", snippet="sb", rank=2),
        ]
        reader = FakeReader({
            "a.com": make_doc("https://a.com/1", "a"),
            "b.org": make_doc("https://b.org/2", "b"),
        })
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="K")
        self.assertEqual([s.id for s in bundle.sources], [1, 2])
        # Public dict ids match (no text field leaks).
        pubs = [s.to_public_dict() for s in bundle.sources]
        self.assertEqual([p["id"] for p in pubs], [1, 2])
        for p in pubs:
            self.assertNotIn("text", p)


class ContextFormattingTests(unittest.TestCase):
    def test_read_source_uses_full_page_label(self):
        results = [SearchResult(title="A", url="https://a.com/1", snippet="sa", rank=1)]
        reader = FakeReader({"a.com": make_doc("https://a.com/1", "full content here")})
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="K")
        self.assertIn("[Source 1]", bundle.context_text)
        self.assertIn("Evidence type: full page", bundle.context_text)
        self.assertIn("untrusted evidence", bundle.context_text)
        self.assertIn("Source type:", bundle.context_text)
        self.assertIn("Authority:", bundle.context_text)
        # Snippet-only sources are clearly labeled as snippets.
        self.assertNotIn("search snippet", bundle.context_text.split("[Source 1]")[1])

    def test_snippet_only_source_labeled_as_snippet(self):
        results = [SearchResult(title="A", url="https://a.com/1", snippet="only a snippet", rank=1)]
        reader = FakeReader({"a.com": make_doc("https://a.com/1", "", status="failed")})
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="K")
        self.assertIn("Evidence type: search snippet", bundle.context_text)
        self.assertIn("only a snippet", bundle.context_text)
        self.assertIn("Search snippet, not full page content", bundle.context_text)

    def test_no_sources_force_yields_status_block(self):
        orch = WebResearchOrchestrator(
            FakeSearchService(error="network_error"), FakeReader()
        )
        bundle = orch.research("force", "查", api_key="K")
        self.assertIn("Web Research Status", bundle.context_text)

    def test_local_hard_limit_applied(self):
        from saya_app.config import WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS
        big = "Z" * (WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS * 3)
        results = [SearchResult(title="A", url="https://a.com/1", snippet="sa", rank=1)]
        reader = FakeReader({"a.com": make_doc("https://a.com/1", big)})
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="K", local_model=True)
        self.assertLessEqual(len(bundle.context_text), WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS + 200)

    def test_multi_source_fair_trim(self):
        from saya_app.config import WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS
        big = "Y" * (WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS)
        results = [
            SearchResult(title="A", url="https://a.com/1", snippet="sa", rank=1),
            SearchResult(title="B", url="https://b.org/2", snippet="sb", rank=2),
        ]
        reader = FakeReader({
            "a.com": make_doc("https://a.com/1", big),
            "b.org": make_doc("https://b.org/2", big),
        })
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="K", local_model=True)
        # Neither source monopolizes the whole budget.
        self.assertLessEqual(len(bundle.context_text), WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS + 200)
        self.assertIn("[Source 1]", bundle.context_text)
        self.assertIn("[Source 2]", bundle.context_text)


class PrivacyTests(unittest.TestCase):
    def test_public_dict_excludes_text(self):
        s = WebSource(id=1, title="t", url="https://a.com", domain="a.com",
                      snippet="s", status="read", text="SECRET BODY")
        d = s.to_public_dict()
        self.assertNotIn("text", d)
        self.assertNotIn("SECRET BODY", str(d))

    def test_public_dict_exposes_classification_but_not_internal_score(self):
        s = WebSource(
            id=1, title="t", url="https://example.gov/p", domain="example.gov",
            status="read", text="body", source_type="government",
            authority="primary", quality_score=123.0,
        )
        d = s.to_public_dict()
        self.assertEqual(d["source_type"], "government")
        self.assertEqual(d["authority"], "primary")
        self.assertNotIn("quality_score", d)

    def test_public_diagnostics_contain_counts_only(self):
        bundle = ResearchBundle(
            query="private query",
            requested_search_count=16,
            requested_final_sources=5,
            candidate_count=9,
            selected_count=8,
            read_count=4,
            snippet_count=1,
        )
        diagnostics = bundle.to_public_diagnostics()
        self.assertEqual(diagnostics["requested_search_count"], 16)
        self.assertEqual(diagnostics["requested_final_sources"], 5)
        self.assertNotIn("query", diagnostics)

    def test_key_not_in_bundle(self):
        results = [SearchResult(title="A", url="https://a.com/1", snippet="sa", rank=1)]
        reader = FakeReader({"a.com": make_doc("https://a.com/1", "body")})
        orch = WebResearchOrchestrator(FakeSearchService(results), reader)
        bundle = orch.research("force", "查", api_key="SUPER-SECRET-KEY")
        self.assertNotIn("SUPER-SECRET-KEY", bundle.context_text)
        for s in bundle.sources:
            self.assertNotIn("SUPER-SECRET-KEY", s.to_public_dict().__str__())


if __name__ == "__main__":
    unittest.main()
