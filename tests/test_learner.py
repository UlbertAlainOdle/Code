import json
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from saya_app.services.learner import LearnerService, _completion_content
from saya_app.services.video_recommendations import VideoRecommendationService
from saya_app.services.web_research.models import ResearchBundle, WebSource


class InlineExecutor:
    class Done:
        def done(self):
            return True

    def submit(self, func, *args, **kwargs):
        func(*args, **kwargs)
        return self.Done()


class FakeResearch:
    def __init__(self, sources):
        self.sources = sources

    def research(self, *args, **kwargs):
        return ResearchBundle(performed=True, sources=self.sources)


class SequencedResearch:
    def __init__(self, source_batches):
        self.source_batches = list(source_batches)
        self.queries = []

    def research(self, mode, query, *args, **kwargs):
        self.queries.append(query)
        self.last_kwargs = kwargs
        batch = self.source_batches[min(len(self.queries) - 1, len(self.source_batches) - 1)]
        return ResearchBundle(performed=True, sources=batch)


class CountingFailureResearch:
    def __init__(self, warnings=None):
        self.calls = 0
        self.warnings = list(warnings or [])

    def research(self, *args, **kwargs):
        self.calls += 1
        return ResearchBundle(performed=True, sources=[], warnings=self.warnings)


class FakeVideoService:
    def __init__(self):
        self.refresh_calls = 0

    def get_state(self):
        return {"creators": [{"id": "creator-1", "platform": "youtube", "display_name": "Official Lab", "enabled": True}]}

    def get_recommendations(self, refresh=False):
        self.refresh_calls += 1
        return {"management_videos": []}


class SelectionVideoService:
    def __init__(self, videos):
        self.videos = videos
        self.contexts = {
            item["id"]: {
                "description": "",
                "transcript": f"Transcript for {item['id']}",
                "transcript_status": "automatic",
                "transcript_language": "en",
            }
            for item in videos
        }

    def get_state(self):
        creators = []
        for creator_id in {item["creator_id"] for item in self.videos}:
            creators.append({"id": creator_id, "platform": "youtube", "display_name": creator_id, "enabled": True})
        return {"creators": creators, "management_videos": self.videos}

    def get_recommendations(self, refresh=False):
        return {"management_videos": self.videos}

    def get_video_learning_context(self, video_id):
        return self.contexts[video_id]

    def get_video_view_count(self, video_id):
        return next(item.get("view_count") for item in self.videos if item["id"] == video_id)


class FakeKnowledgeBase:
    def __init__(self, memories):
        self.memories = memories

    def search_memories(self, **kwargs):
        return list(self.memories)

    def update_memory(self, memory_id, updates):
        memory = next(item for item in self.memories if item["id"] == memory_id)
        memory.update(updates)
        return memory

    def add_memory(self, content, **kwargs):
        memory = {
            "id": f"created-{len(self.memories) + 1}",
            "content": content,
            "source": kwargs.get("source", "manual"),
            "source_detail": kwargs.get("source_detail") or {},
            "is_important": bool(kwargs.get("is_important", False)),
            "is_archived": False,
            **(kwargs.get("metadata") or {}),
        }
        self.memories.append(memory)
        return memory

    def ensure_memory_graph(self, force=False):
        return {"changed": bool(force)}


class LifecycleKnowledgeBase(FakeKnowledgeBase):
    def __init__(self, memories):
        super().__init__(memories)
        self.plans = {}
        self.fail_delete = False

    def preview_memory_deletion(self, memory_ids, reason="user_delete"):
        token = str(len(self.plans) + 1)
        blocked = [
            {"id": memory_id, "reason": "Unlock this memory before permanently deleting it"}
            for memory_id in memory_ids
            if next((item for item in self.memories if item.get("id") == memory_id), {}).get("is_locked")
        ]
        deletable = [memory_id for memory_id in memory_ids if memory_id not in {item["id"] for item in blocked}]
        self.plans[token] = {"ids": deletable, "reason": reason}
        return {
            "status": "confirmation_required", "confirmation_token": token,
            "delete_count": len(deletable), "memory_ids": deletable,
            "blocked": blocked, "rewired_links": [], "warnings": [],
            "cleared_reference_count": 0, "removed_manual_edge_count": 0,
        }

    def delete_memories(
        self, memory_ids, reason="user_delete", confirmation_token=None,
        require_all=False,
    ):
        if self.fail_delete:
            return {"status": "error", "message": "simulated persistence failure"}
        plan = self.plans.pop(str(confirmation_token or ""), None)
        deletable = [
            memory_id for memory_id in memory_ids
            if next((item for item in self.memories if item.get("id") == memory_id), {}).get("is_locked") is not True
        ]
        if not plan or plan != {"ids": deletable, "reason": reason}:
            return {"status": "confirmation_required", "deleted_ids": []}
        self.memories[:] = [item for item in self.memories if item.get("id") not in set(deletable)]
        return {"status": "success", "deleted_count": len(deletable), "deleted_ids": deletable}


class LearnerServiceTests(unittest.TestCase):
    def make_service(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return LearnerService(Path(temp.name) / "learner.json", executor=InlineExecutor())

    def test_completion_parser_supports_text_parts_and_reports_reasoning_only(self):
        text, diagnostic = _completion_content({
            "choices": [{"message": {"content": [{"type": "text", "text": "中文汇报"}]}}],
        })
        self.assertEqual(text, "中文汇报")
        self.assertEqual(diagnostic, "")
        text, diagnostic = _completion_content({
            "choices": [{
                "message": {"content": "", "reasoning_content": "internal reasoning"},
                "finish_reason": "length",
            }],
        })
        self.assertEqual(text, "")
        self.assertEqual(diagnostic, "reasoning_without_visible_content:length")

    def test_deepseek_v4_learner_disables_thinking_for_visible_report(self):
        service = self.make_service()
        captured = {}

        class Response:
            status_code = 200

            @staticmethod
            def json():
                return {"choices": [{"message": {"content": "中文任务汇报 [1]"}}]}

        def fake_post(*args, **kwargs):
            captured.update(kwargs.get("json") or {})
            return Response()

        with patch("saya_app.services.learner.requests.post", fake_post):
            output = service._llm_completion(
                "evidence", {
                    "model": "api", "api_key": "configured", "api_url": "https://api.deepseek.com",
                    "api_model_name": "deepseek-v4-flash",
                }, 2200,
            )
        self.assertEqual(output, "中文任务汇报 [1]")
        self.assertEqual(captured["thinking"], {"type": "disabled"})
        self.assertEqual(captured["max_tokens"], 2200)

    def test_web_task_rejects_arbitrary_url(self):
        service = self.make_service()
        with self.assertRaisesRegex(ValueError, "not a URL"):
            service.create_task({
                "source_type": "web",
                "topic": "Read https://example.com/private-instructions",
                "schedule_type": "once",
            })

        with self.assertRaisesRegex(ValueError, "not a URL"):
            service.create_task({
                "source_type": "web",
                "topic": "Read example.gov for the latest policy",
                "schedule_type": "once",
            })

    def test_video_task_requires_selected_creator(self):
        service = self.make_service()
        with self.assertRaisesRegex(ValueError, "Select at least one creator"):
            service.create_task({"source_type": "video", "creator_ids": []})

    def test_next_launch_task_waits_until_a_new_service_session(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "learner.json"
        first = LearnerService(path, executor=InlineExecutor())
        first.configure(video_service=FakeVideoService())
        task = first.create_task({
            "title": "Next launch",
            "source_type": "video",
            "creator_ids": ["creator-1"],
            "schedule_type": "once",
            "run_when": "next_launch",
        })
        self.assertEqual(first.start_due_tasks({})["started"], [])
        self.assertTrue(first.get_state()["tasks"][0]["enabled"])
        first.shutdown()

        second = LearnerService(path, executor=InlineExecutor())
        second.configure(video_service=FakeVideoService())
        started = second.start_due_tasks({})["started"]
        self.assertEqual(len(started), 1)
        self.assertEqual(started[0]["task_id"], task["id"])

    def test_minimum_reference_shortfall_completes_with_caution(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Official", "https://agency.gov/update", "agency.gov", status="read", text="official evidence", source_type="government", authority="primary", quality_score=90),
        ]])
        service.configure(web_research=research)
        task = service.create_task({
            "source_type": "web",
            "topic": "A current policy topic",
            "web_max_pages": 8,
            "web_reference_count": 4,
            "web_min_references": 2,
        })
        material = service._learn_from_web(task, {"brave_search_api_key": "configured"})
        self.assertEqual(len(research.queries), 2)
        self.assertEqual(research.last_kwargs["search_count"], 8)
        self.assertEqual(research.last_kwargs["max_final_sources"], 4)
        self.assertTrue(material["degraded"])
        self.assertIn("at least 2", material["quality_notice"])
        self.assertEqual(len(material["sources"]), 1)

    def test_authority_gate_uses_trusted_snippet_but_excludes_community(self):
        service = self.make_service()
        service.configure(web_research=FakeResearch([
            WebSource(1, "Forum", "https://reddit.com/a", "reddit.com", status="read", text="opinion", source_type="community", authority="community"),
            WebSource(2, "Official snippet", "https://docs.example.com/a", "docs.example.com", status="snippet_only", snippet="In August 2026 the agency published updated guidance after reviewing 220 applications, reporting an 18% increase.", source_type="documentation", authority="primary"),
        ]))
        material = service._learn_from_web({"topic": "Test topic"}, {"brave_search_api_key": "configured"})
        self.assertTrue(material["degraded"])
        self.assertEqual(material["quality_tier"], "trusted_snippet")
        self.assertEqual([item["domain"] for item in material["sources"]], ["docs.example.com"])
        self.assertEqual(material["sources"][0]["evidence_type"], "search_snippet")

    def test_community_only_still_fails_every_quality_tier(self):
        service = self.make_service()
        service.configure(web_research=FakeResearch([
            WebSource(1, "Forum", "https://reddit.com/a", "reddit.com", status="read", text="opinion", source_type="community", authority="community"),
        ]))
        with self.assertRaisesRegex(RuntimeError, "No sufficiently reliable readable source"):
            service._learn_from_web({"topic": "Test topic"}, {"brave_search_api_key": "configured"})

    def test_japan_policy_task_retries_with_authority_focused_query(self):
        service = self.make_service()
        research = SequencedResearch([
            [WebSource(1, "News", "https://example.com/news", "example.com", status="read", text="secondary", source_type="web", authority="unknown")],
            [WebSource(1, "MEXT", "https://www.mext.go.jp/policy", "www.mext.go.jp", status="read", text="official policy", source_type="government", authority="primary")],
        ])
        service.configure(web_research=research)
        material = service._learn_from_web(
            {"topic": "了解可能影响一年后去日本留学的新闻或者政策"},
            {"brave_search_api_key": "configured"},
        )
        self.assertEqual(len(research.queries), 2)
        self.assertNotIn("新闻", research.queries[1])
        self.assertIn("文部科学省", research.queries[1])
        self.assertEqual(material["sources"][0]["domain"], "www.mext.go.jp")

    def test_failed_once_task_stops_without_automatic_api_retry(self):
        service = self.make_service()
        research = CountingFailureResearch()
        service.configure(web_research=research)
        task = service.create_task({
            "source_type": "web",
            "topic": "A broad current topic",
            "schedule_type": "once",
        })
        first = service.start_due_tasks({"brave_search_api_key": "configured"})
        self.assertEqual(len(first["started"]), 1)
        calls_after_failure = research.calls
        state = service.get_state()
        updated = next(item for item in state["tasks"] if item["id"] == task["id"])
        self.assertEqual(updated["last_status"], "failed")
        self.assertFalse(updated["enabled"])
        self.assertIsNone(updated["next_run_at"])

        second = service.start_due_tasks({"brave_search_api_key": "configured"})
        self.assertEqual(second["started"], [])
        self.assertEqual(research.calls, calls_after_failure)

    def test_terminal_search_error_does_not_make_fallback_api_call(self):
        service = self.make_service()
        research = CountingFailureResearch(["search_failed", "search_rate_limited"])
        service.configure(web_research=research)
        with self.assertRaisesRegex(RuntimeError, "rate limited.*No fallback API request"):
            service._learn_from_web(
                {"topic": "Current developments in a broad field"},
                {"brave_search_api_key": "configured"},
            )
        self.assertEqual(research.calls, 1)

    def test_two_strong_independent_sources_pass_without_second_search(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Specialist report", "https://alpha.example/report", "alpha.example", status="read", text="independent evidence one", source_type="web", authority="unknown", quality_score=62),
            WebSource(2, "Industry analysis", "https://beta.example/analysis", "beta.example", status="read", text="independent evidence two", source_type="web", authority="unknown", quality_score=58),
        ]])
        service.configure(web_research=research)
        material = service._learn_from_web(
            {"topic": "A niche topic with no central authority"},
            {"brave_search_api_key": "configured"},
        )
        self.assertEqual(len(research.queries), 1)
        self.assertEqual(len(material["sources"]), 2)
        self.assertTrue(all(source["acceptance"] == "cross_checked" for source in material["sources"]))

    def test_one_unknown_source_still_fails_quality_gate(self):
        service = self.make_service()
        research = SequencedResearch([
            [WebSource(1, "Only page", "https://alpha.example/report", "alpha.example", status="read", text="one claim", source_type="web", authority="unknown", quality_score=70)],
            [],
        ])
        service.configure(web_research=research)
        with self.assertRaisesRegex(RuntimeError, "No sufficiently reliable readable source"):
            service._learn_from_web(
                {"topic": "A niche topic"},
                {"brave_search_api_key": "configured"},
            )

    def test_degraded_web_run_requires_a_real_synthesized_report(self):
        service = self.make_service()
        research = FakeResearch([
            WebSource(
                1, "Official snippet", "https://agency.gov/update", "agency.gov",
                status="snippet_only", snippet="In August 2026 the agency announced a current policy update after reviewing 220 applications and reported an 18% increase.",
                source_type="government", authority="primary", quality_score=85,
            ),
        ])
        service.configure(web_research=research, local_completion=lambda *args: "")
        task = service.create_task({
            "title": "Policy watch",
            "source_type": "web",
            "topic": "A current policy update",
            "schedule_type": "once",
        })
        service.start_task(task["id"], {"brave_search_api_key": "configured"})
        state = service.get_state()
        self.assertEqual(state["runs"][0]["status"], "failed")
        self.assertEqual(state["tasks"][0]["last_status"], "failed")
        self.assertEqual(state["counts"]["completed"], 0)
        self.assertEqual(state["counts"]["caution"], 0)
        self.assertIn("No task report was generated", state["runs"][0]["summary"])
        self.assertIn("stricter tiers", state["runs"][0]["quality_notice"])

    def test_completed_inline_work_does_not_leave_future_reference(self):
        service = self.make_service()
        service.configure(video_service=FakeVideoService())
        task = service.create_task({
            "source_type": "video",
            "creator_ids": ["creator-1"],
            "schedule_type": "once",
        })
        service.start_due_tasks({})
        self.assertEqual(service._futures, {})

    def test_no_new_video_uses_no_model_api_and_does_not_restart(self):
        service = self.make_service()
        videos = FakeVideoService()
        model_calls = []
        service.configure(
            video_service=videos,
            local_completion=lambda *args, **kwargs: model_calls.append(args) or "unexpected",
        )
        task = service.create_task({
            "source_type": "video",
            "creator_ids": ["creator-1"],
            "schedule_type": "once",
            "manage_memory": True,
        })
        service.start_due_tasks({})
        self.assertEqual(model_calls, [])
        self.assertEqual(videos.refresh_calls, 1)
        self.assertFalse(service.get_state()["tasks"][0]["enabled"])

        service.start_due_tasks({})
        self.assertEqual(model_calls, [])
        self.assertEqual(videos.refresh_calls, 1)

    def test_missing_knowledge_base_skips_memory_model_call(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Official", "https://agency.gov/update", "agency.gov", status="read", text="official evidence", source_type="government", authority="primary", quality_score=90),
        ]])
        model_calls = []
        service.configure(
            web_research=research,
            local_completion=lambda *args, **kwargs: model_calls.append(args) or "Model summary [1]",
        )
        service.create_task({
            "source_type": "web",
            "topic": "A policy topic",
            "schedule_type": "once",
            "manage_memory": True,
        })
        service.start_due_tasks({"brave_search_api_key": "configured"})
        self.assertEqual(len(model_calls), 1)

    def test_failed_summary_model_is_not_retried_for_memory_extraction(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Official", "https://agency.gov/update", "agency.gov", status="read", text="official evidence", source_type="government", authority="primary", quality_score=90),
        ]])
        model_calls = []
        service.configure(
            web_research=research,
            knowledge_base=FakeKnowledgeBase([]),
            local_completion=lambda *args, **kwargs: model_calls.append(args) or "",
        )
        service.create_task({
            "source_type": "web",
            "topic": "A policy topic",
            "schedule_type": "once",
            "manage_memory": True,
        })
        service.start_due_tasks({"brave_search_api_key": "configured"})
        self.assertEqual(len(model_calls), 1)
        self.assertFalse(service.get_state()["tasks"][0]["enabled"])

        service.start_due_tasks({})
        self.assertEqual(len(model_calls), 1)

    def test_summary_obeys_persisted_chinese_language_and_rejects_copied_source(self):
        service = self.make_service()
        japanese = "令和八年の留学生政策について公表します。" * 30
        captured = []
        service.configure(
            local_completion=lambda prompt, *_args: captured.append(prompt) or japanese,
            response_language_provider=lambda: "zh-cn",
        )
        summary, used_model = service._summarize(
            {"title": "Japan‘s News", "topic": "一年后去日本留学"},
            {
                "evidence": japanese,
                "sources": [{"id": 1, "kind": "web", "title": "官方资料", "url": "https://agency.go.jp/policy"}],
                "raw_sources": [WebSource(1, "官方资料", "https://agency.go.jp/policy", "agency.go.jp", status="read", text=japanese)],
            },
            "",
            {"model": "local"},
        )
        self.assertIn("简体中文", captured[0])
        self.assertIn("最新动态索引", captured[0])
        self.assertIn("正文至少 70%", captured[0])
        self.assertIn("目标长度 800–1800 字", captured[0])
        self.assertFalse(used_model)
        self.assertIn("没有生成任务汇报", summary)
        self.assertNotIn(japanese[:100], summary)

    def test_low_information_navigation_snippet_is_excluded(self):
        service = self.make_service()
        service.configure(web_research=FakeResearch([
            WebSource(
                1, "Newsletter", "https://institute.edu/news", "institute.edu",
                status="snippet_only",
                snippet="Get the latest news and education program updates in your inbox weekly. Sign up for latest news.",
                source_type="official", authority="high", quality_score=90,
            ),
            WebSource(
                2, "Study result", "https://agency.gov/study", "agency.gov",
                status="snippet_only",
                snippet="The agency published a 2026 study of 220 hospitals and found adoption increased by 18%.",
                published_at="2026-08-10T10:00:00",
                source_type="government", authority="primary", quality_score=88,
            ),
        ]))
        material = service._learn_from_web(
            {"topic": "Latest medical AI research"},
            {"brave_search_api_key": "configured"},
        )
        self.assertEqual([item["domain"] for item in material["sources"]], ["agency.gov"])
        self.assertNotIn("Sign up", material["evidence"])

    def test_web_evidence_is_recent_first_and_preserves_each_source(self):
        service = self.make_service()
        old_text = "In 2026 the institute published an older policy analysis. " * 220
        new_text = "On 2026-08-12 the ministry announced a new rule affecting student applications by 15%. " * 220
        service.configure(web_research=FakeResearch([
            WebSource(
                1, "Older analysis", "https://institute.edu/old", "institute.edu",
                status="read", text=old_text, published_at="2026-05-01T00:00:00",
                source_type="official", authority="high", quality_score=95,
            ),
            WebSource(
                2, "New rule", "https://ministry.gov/new", "ministry.gov",
                status="read", text=new_text, published_at="2026-08-12T00:00:00",
                source_type="government", authority="primary", quality_score=90,
            ),
        ]))
        material = service._learn_from_web(
            {"topic": "student application policy", "web_reference_count": 2},
            {"brave_search_api_key": "configured"},
        )
        self.assertEqual([item["title"] for item in material["sources"]], ["New rule", "Older analysis"])
        self.assertIn("new rule affecting student applications", material["evidence"])
        self.assertIn("older policy analysis", material["evidence"])
        self.assertLessEqual(len(material["evidence"]), 24000)

    def test_failed_model_does_not_present_raw_news_details_as_a_report(self):
        service = self.make_service()
        material = {
            "degraded": True,
            "quality_notice": "Strict tier lacked readable pages.",
            "sources": [
                {
                    "id": 1, "kind": "web", "title": "Hospital AI study",
                    "url": "https://agency.gov/study", "domain": "agency.gov",
                    "published_at": "2026-08-10T13:00:00", "status": "snippet_only",
                    "evidence_type": "search_snippet",
                    "excerpt": "The agency studied 220 hospitals and found adoption increased by 18% in 2026.",
                },
                {
                    "id": 2, "kind": "web", "title": "Funding agreement",
                    "url": "https://example.org/funding", "domain": "example.org",
                    "published_at": "2026-06-03T00:00:00", "status": "snippet_only",
                    "evidence_type": "search_snippet",
                    "excerpt": "A laboratory announced a USD 200 million funding round in June 2026.",
                },
            ],
        }
        summary = service._failed_report(
            {"title": "Medical AI", "topic": "latest developments"},
            material,
            {"response_language": "zh-cn"},
        )
        self.assertIn("没有生成任务汇报", summary)
        self.assertNotIn("220 hospitals", summary)
        self.assertNotIn("USD 200 million", summary)
        self.assertNotIn("摘要线索", summary)

    def test_unrelated_unknown_web_result_is_removed_before_reporting(self):
        service = self.make_service()
        medical = WebSource(
            1, "Medical AI regulation study", "https://agency.gov/medical-ai", "agency.gov",
            status="snippet_only",
            snippet="A 2026 medical AI study of 122 developers found 67% had not adopted the regulatory framework.",
            source_type="government", authority="primary", quality_score=90,
        )
        unrelated = WebSource(
            2, "Best colleges and B.Tech programmes", "https://college.example/courses", "college.example",
            status="snippet_only",
            snippet="B.Tech programmes are approved by AICTE and affiliated with a university for the 2026 session.",
            source_type="web", authority="unknown", quality_score=70,
        )
        service.configure(web_research=FakeResearch([unrelated, medical]))
        material = service._learn_from_web(
            {"topic": "最新全球医疗AI发展", "web_reference_count": 6},
            {"brave_search_api_key": "configured"},
        )
        self.assertEqual([item["title"] for item in material["sources"]], ["Medical AI regulation study"])

    def test_local_model_evidence_is_bounded_for_eight_k_context(self):
        service = self.make_service()
        long_text = "In 2026 the medical AI regulator announced a quantified policy update affecting 220 hospitals. " * 200
        service.configure(web_research=FakeResearch([
            WebSource(
                index, f"Medical AI source {index}", f"https://agency{index}.gov/update", f"agency{index}.gov",
                status="read", text=long_text, source_type="government", authority="primary", quality_score=90,
            )
            for index in range(1, 7)
        ]))
        material = service._learn_from_web(
            {"topic": "latest medical AI development", "web_reference_count": 6},
            {"brave_search_api_key": "configured", "model": "mistral_22b_q6"},
        )
        self.assertLessEqual(len(material["evidence"]), 4000)

    def test_fallback_summary_is_not_saved_as_long_term_memory(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Official", "https://agency.gov/update", "agency.gov", status="read", text="official evidence", source_type="government", authority="primary", quality_score=90),
        ]])
        kb = FakeKnowledgeBase([])
        memory_calls = []
        kb.add_memory = lambda *args, **kwargs: memory_calls.append((args, kwargs)) or {"id": "unexpected"}
        kb.ensure_memory_graph = lambda force=False: None
        service.configure(
            web_research=research,
            knowledge_base=kb,
            local_completion=lambda *args, **kwargs: "",
            response_language_provider=lambda: "zh-cn",
        )
        service.create_task({
            "source_type": "web",
            "topic": "一年后去日本留学的政策变化",
            "schedule_type": "once",
            "manage_memory": True,
        })
        service.start_due_tasks({"brave_search_api_key": "configured", "model": "local"})
        run = service.get_state()["runs"][0]
        self.assertTrue(run["memory_result"]["skipped"])
        self.assertEqual(memory_calls, [])

    def test_activity_can_be_read_deleted_and_bulk_cleared(self):
        service = self.make_service()
        service.configure(video_service=FakeVideoService())
        for title in ("First", "Second"):
            task = service.create_task({
                "title": title,
                "source_type": "video",
                "creator_ids": ["creator-1"],
                "schedule_type": "once",
            })
            service.start_task(task["id"], {}, force=True)
        state = service.get_state()
        self.assertEqual(state["counts"]["unread"], 2)
        first_id = state["runs"][0]["id"]
        service.set_run_read(first_id, True)
        self.assertEqual(service.get_state()["counts"]["unread"], 1)
        self.assertEqual(service.clear_runs("read"), 1)
        remaining_id = service.get_state()["runs"][0]["id"]
        self.assertTrue(service.delete_run(remaining_id))
        self.assertEqual(service.get_state()["runs"], [])

    def test_memory_extraction_api_failure_falls_back_without_failing_task(self):
        service = self.make_service()
        research = SequencedResearch([[
            WebSource(1, "Official", "https://agency.gov/update", "agency.gov", status="read", text="official evidence", source_type="government", authority="primary", quality_score=90),
        ]])
        calls = []

        def completion(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                return (
                    "# Latest conclusion\n\nThe official update establishes a documented current policy change and "
                    "provides a clear baseline for comparison. The responsible agency published a concrete update "
                    "that should be treated as the newest confirmed development in this task. [1]\n\n"
                    "## Latest development index\n\nThe evidence identifies the responsible institution, the newly announced "
                    "policy direction, and its present applicability. Readers should distinguish the confirmed "
                    "announcement from future implementation details that are not yet available. [1]\n\n"
                    "## Impact and next step\n\nThe practical response is to monitor the agency for implementation guidance, "
                    "compare later notices against this baseline, and avoid assuming that an announced direction "
                    "already applies to every institution or individual case. This preserves the confirmed fact, "
                    "the current time boundary, and the remaining uncertainty without substituting generic claims "
                    "for evidence. The limited source set means broader effects remain uncertain, but the indexed "
                    "official update is still actionable as a monitoring baseline. [1]"
                )
            raise RuntimeError("temporary model failure")

        kb = FakeKnowledgeBase([])
        kb.add_memory = lambda content, **kwargs: {"id": "new-memory"}
        kb.ensure_memory_graph = lambda force=False: None
        service.configure(web_research=research, knowledge_base=kb, local_completion=completion)
        service.create_task({
            "source_type": "web",
            "topic": "A policy topic",
            "schedule_type": "once",
            "manage_memory": True,
        })
        service.start_due_tasks({"brave_search_api_key": "configured"})
        state = service.get_state()
        self.assertEqual(len(calls), 2)
        self.assertEqual(state["runs"][0]["status"], "completed")
        self.assertEqual(state["runs"][0]["memory_result"]["created_ids"], ["new-memory"])

    def test_real_executor_future_reference_is_removed_after_completion(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = LearnerService(
            Path(temp.name) / "learner.json",
            executor=ThreadPoolExecutor(max_workers=1),
        )
        self.addCleanup(service.shutdown)
        service.configure(video_service=FakeVideoService())
        service.create_task({
            "source_type": "video",
            "creator_ids": ["creator-1"],
            "schedule_type": "once",
        })
        service.start_due_tasks({})
        deadline = time.time() + 2
        while service.get_state()["counts"]["running"] and time.time() < deadline:
            time.sleep(0.01)
        self.assertEqual(service.get_state()["counts"]["running"], 0)
        self.assertEqual(service._futures, {})

    def test_once_task_runs_on_launch_and_disables_after_completion(self):
        service = self.make_service()
        service.configure(video_service=FakeVideoService())
        task = service.create_task({
            "title": "Latest creator update",
            "source_type": "video",
            "creator_ids": ["creator-1"],
            "schedule_type": "once",
            "read_memory": False,
            "manage_memory": True,
        })
        state = service.start_due_tasks({})
        self.assertEqual(len(state["started"]), 1)
        final = service.get_state()
        updated = next(item for item in final["tasks"] if item["id"] == task["id"])
        self.assertFalse(updated["enabled"])
        self.assertEqual(updated["last_status"], "completed")
        self.assertEqual(final["runs"][0]["status"], "completed")
        self.assertIsNone(final["runs"][0]["memory_result"])

    def test_interrupted_run_is_recoverable_without_credentials(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "learner.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "tasks": [],
            "runs": [{"id": "run-1", "status": "running"}],
        }), encoding="utf-8")
        service = LearnerService(path, executor=InlineExecutor())
        run = service.get_run("run-1")
        self.assertEqual(run["status"], "interrupted")
        self.assertIn("run again", run["error"])

    def test_legacy_failed_once_retry_is_disabled_on_load(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / "learner.json"
        path.write_text(json.dumps({
            "schema_version": 1,
            "tasks": [{
                "id": "old-task",
                "schedule_type": "once",
                "last_status": "failed",
                "last_run_at": "2026-08-13T05:29:24",
                "enabled": True,
                "next_run_at": "2026-08-13T05:59:24",
            }],
            "runs": [],
        }), encoding="utf-8")
        service = LearnerService(path, executor=InlineExecutor())
        task = service.get_state()["tasks"][0]
        self.assertFalse(task["enabled"])
        self.assertIsNone(task["next_run_at"])
        self.assertEqual(service.start_due_tasks({})["started"], [])

    def test_memory_merge_archives_only_unprotected_duplicates(self):
        service = self.make_service()
        source_detail = {"learner_task_id": "task-1", "learner_run_id": "run-1"}
        memories = [
            {"id": "important-1", "source": "learner", "source_detail": source_detail, "content": "Alpha model release adds tool calling.", "is_important": True, "importance_score": 0.9},
            {"id": "important-2", "source": "learner", "source_detail": source_detail, "content": "Alpha model release adds tool calling.", "is_locked": True, "importance_score": 0.8},
            {"id": "ordinary", "source": "learner", "source_detail": source_detail, "content": "Alpha model release adds tool calling.", "importance_score": 0.4},
        ]
        service.configure(knowledge_base=FakeKnowledgeBase(memories))
        result = service._upsert_learning_memory(
            {"id": "task-1", "source_type": "web"},
            "run-1",
            {"title": "Alpha", "content": "Alpha model release adds tool calling.", "tags": [], "importance": 0.7, "important": False},
            {"sources": []},
        )
        self.assertIn("ordinary", result["archived"])
        self.assertIn("important-2", result["preserved"])
        self.assertFalse(next(item for item in memories if item["id"] == "important-2").get("is_archived", False))
        self.assertTrue(next(item for item in memories if item["id"] == "ordinary").get("is_archived"))

    def test_repeated_task_runs_create_distinct_memories(self):
        service = self.make_service()
        kb = FakeKnowledgeBase([])
        service.configure(knowledge_base=kb)
        candidate = {
            "title": "Alpha", "content": "Alpha model release adds tool calling.",
            "tags": [], "importance": 0.7, "important": False,
        }
        task = {"id": "task-1", "source_type": "web"}
        first = service._upsert_learning_memory(task, "run-1", candidate, {"sources": []})
        second = service._upsert_learning_memory(task, "run-2", candidate, {"sources": []})
        self.assertEqual(len(first["created"]), 1)
        self.assertEqual(len(second["created"]), 1)
        self.assertNotEqual(first["created"][0], second["created"][0])
        self.assertEqual(
            {item["source_detail"]["learner_run_id"] for item in kb.memories},
            {"run-1", "run-2"},
        )

    def test_deleting_one_run_cascades_only_that_runs_memories(self):
        service = self.make_service()
        task = service.create_task({
            "title": "Watch", "source_type": "video", "creator_ids": ["creator-1"],
            "schedule_type": "once",
        })
        service.data["runs"] = [
            {"id": "run-1", "task_id": task["id"], "status": "completed", "is_read": True},
            {"id": "run-2", "task_id": task["id"], "status": "completed", "is_read": True},
        ]
        service._save()
        kb = LifecycleKnowledgeBase([
            {"id": "memory-1", "source": "learner", "source_detail": {"learner_task_id": task["id"], "learner_run_id": "run-1"}},
            {"id": "memory-2", "source": "learner", "source_detail": {"learner_task_id": task["id"], "learner_run_id": "run-2"}},
        ])
        service.configure(knowledge_base=kb)
        preview = service.preview_run_deletion(["run-1"])
        result = service.delete_runs(["run-1"], preview["confirmation_token"])
        self.assertEqual(result["status"], "success")
        self.assertEqual([item["id"] for item in service.get_state()["runs"]], ["run-2"])
        self.assertEqual([item["id"] for item in kb.memories], ["memory-2"])

    def test_task_delete_cascades_all_history_and_memories_after_confirmation(self):
        service = self.make_service()
        task = service.create_task({
            "title": "Watch", "source_type": "video", "creator_ids": ["creator-1"],
            "schedule_type": "once",
        })
        service.data["runs"] = [
            {"id": "run-1", "task_id": task["id"], "status": "completed", "is_read": True},
            {"id": "run-2", "task_id": task["id"], "status": "failed", "is_read": False},
        ]
        service._save()
        kb = LifecycleKnowledgeBase([
            {"id": "memory-1", "source": "learner", "source_detail": {"learner_task_id": task["id"], "learner_run_id": "run-1"}},
            {"id": "memory-2", "source": "learner", "source_detail": {"learner_task_id": task["id"], "learner_run_id": "run-2"}},
        ])
        service.configure(knowledge_base=kb)
        preview = service.preview_task_deletion(task["id"])
        result = service.delete_task_with_history(task["id"], preview["confirmation_token"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(service.get_state()["tasks"], [])
        self.assertEqual(service.get_state()["runs"], [])
        self.assertEqual(kb.memories, [])

    def test_failed_memory_cascade_restores_run_history(self):
        service = self.make_service()
        service.data["runs"] = [
            {"id": "run-1", "task_id": "task-1", "status": "completed", "is_read": True},
        ]
        service._save()
        kb = LifecycleKnowledgeBase([
            {"id": "memory-1", "source": "learner", "source_detail": {"learner_task_id": "task-1", "learner_run_id": "run-1"}},
        ])
        kb.fail_delete = True
        service.configure(knowledge_base=kb)
        preview = service.preview_run_deletion(["run-1"])
        result = service.delete_runs(["run-1"], preview["confirmation_token"])
        self.assertEqual(result["status"], "error")
        self.assertEqual([item["id"] for item in service.get_state()["runs"]], ["run-1"])

    def test_protected_linked_memory_blocks_history_deletion_before_mutation(self):
        service = self.make_service()
        service.data["runs"] = [
            {"id": "run-1", "task_id": "task-1", "status": "completed", "is_read": True},
        ]
        service._save()
        kb = LifecycleKnowledgeBase([
            {"id": "memory-1", "source": "learner", "is_locked": True, "source_detail": {"learner_task_id": "task-1", "learner_run_id": "run-1"}},
        ])
        service.configure(knowledge_base=kb)
        preview = service.preview_run_deletion(["run-1"])
        result = service.delete_runs(["run-1"], preview["confirmation_token"])
        self.assertEqual(result["status"], "blocked")
        self.assertEqual([item["id"] for item in service.get_state()["runs"]], ["run-1"])
        self.assertEqual([item["id"] for item in kb.memories], ["memory-1"])

    def test_orphaned_learner_memory_requires_preview_before_cleanup(self):
        service = self.make_service()
        kb = LifecycleKnowledgeBase([
            {
                "id": "orphan-memory", "title": "Old report memory", "source": "learner",
                "source_detail": {"learner_task_id": "deleted-task", "learner_run_id": "deleted-run"},
            },
        ])
        service.configure(knowledge_base=kb)
        state = service.get_state()
        self.assertEqual(state["counts"]["orphaned_memories"], 1)
        with self.assertRaisesRegex(ValueError, "scope changed or expired"):
            service.delete_orphaned_memories("")
        preview = service.preview_orphaned_memory_deletion()
        result = service.delete_orphaned_memories(preview["confirmation_token"])
        self.assertEqual(result["status"], "success")
        self.assertEqual(kb.memories, [])

    def test_youtube_caption_track_is_parsed_for_learning(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = VideoRecommendationService(Path(temp.name) / "videos.json")
        service.data["cache"]["videos"] = [{
            "id": "video-1",
            "source_id": "youtube-id",
            "platform": "youtube",
            "url": "https://www.youtube.com/watch?v=youtube-id",
            "description": "Fallback description",
        }]
        service._get_text = lambda *args, **kwargs: (
            '"captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?id=1",'
            '"languageCode":"en","kind":"asr"}]'
        )
        service._get_json = lambda *args, **kwargs: {
            "events": [{"segs": [{"utf8": "First line"}]}, {"segs": [{"utf8": "Second line"}]}]
        }
        service._fetch_video_discussion_context = lambda video: {"description": video["description"], "notes": []}
        context = service.get_video_learning_context("video-1")
        self.assertEqual(context["transcript_status"], "automatic")
        self.assertEqual(context["transcript_language"], "en")
        self.assertEqual(context["transcript"], "First line\nSecond line")

    def test_youtube_caption_track_can_fall_back_to_player_endpoint(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = VideoRecommendationService(Path(temp.name) / "videos.json")
        video = {
            "id": "video-1", "source_id": "youtube-id", "platform": "youtube",
            "url": "https://www.youtube.com/watch?v=youtube-id",
        }
        service._get_text = lambda *args, **kwargs: "<html without caption tracks>"
        service._post_json = lambda *args, **kwargs: {
            "captions": {"playerCaptionsTracklistRenderer": {"captionTracks": [{
                "baseUrl": "https://www.youtube.com/api/timedtext?id=1",
                "languageCode": "en", "kind": "asr",
            }]}}
        }
        service._get_json = lambda *args, **kwargs: {
            "events": [{"segs": [{"utf8": "Player endpoint line"}]}]
        }
        result = service._fetch_youtube_transcript(video)
        self.assertEqual(result["status"], "automatic")
        self.assertEqual(result["text"], "Player endpoint line")

    def test_youtube_caption_vtt_response_is_parsed(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = VideoRecommendationService(Path(temp.name) / "videos.json")
        video_url = "https://www.youtube.com/watch?v=youtube-id"
        service._get_text = lambda url, **kwargs: (
            '"captionTracks":[{"baseUrl":"https://www.youtube.com/api/timedtext?id=1",'
            '"languageCode":"en","kind":"asr"}]'
            if "watch?v=" in url else "WEBVTT\n\n00:00.000 --> 00:01.000\nVTT line"
        )
        service._get_json = lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("not json"))
        result = service._fetch_youtube_transcript({
            "source_id": "youtube-id", "url": video_url,
        })
        self.assertEqual(result["text"], "VTT line")

    def test_caption_discussion_keeps_display_text_short_but_prompt_full(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = VideoRecommendationService(Path(temp.name) / "videos.json")
        service.data["cache"]["videos"] = [{
            "id": "video-1", "source_id": "youtube-id", "platform": "youtube",
            "creator_name": "Creator", "title": "A captioned video",
            "url": "https://www.youtube.com/watch?v=youtube-id",
        }]
        full_transcript = ("First complete line.\n" + ("A detailed caption sentence. " * 80)).strip()
        service._fetch_youtube_transcript = lambda video: {
            "status": "manual", "language": "en", "text": full_transcript,
        }
        result = service.prepare_caption_discussion("video-1")
        self.assertEqual(result["display_text"], "Doctor给你分享了一个视频：A captioned video")
        self.assertIn(full_transcript, result["prompt"])
        self.assertEqual(result["transcript_status"], "manual")
        self.assertEqual(result["transcript_chars"], len(full_transcript))

    def test_bilibili_creator_import_is_rejected(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        service = VideoRecommendationService(Path(temp.name) / "videos.json")
        with self.assertRaisesRegex(ValueError, "Only YouTube"):
            service.add_creator("bilibili", "some creator")

    def test_one_time_video_task_requires_and_reads_selected_videos(self):
        service = self.make_service()
        videos = [
            {"id": "video-1", "creator_id": "creator-1", "creator_name": "Creator", "title": "One", "platform": "youtube", "published_at": "2026-08-20T10:00:00+00:00"},
            {"id": "video-2", "creator_id": "creator-1", "creator_name": "Creator", "title": "Two", "platform": "youtube", "published_at": "2026-08-20T09:00:00+00:00"},
        ]
        video_service = SelectionVideoService(videos)
        service.configure(video_service=video_service)
        with self.assertRaisesRegex(ValueError, "one or more recommended videos"):
            service.create_task({"source_type": "video", "creator_ids": ["creator-1"], "video_ids": [], "schedule_type": "once"})
        task = service.create_task({
            "source_type": "video", "creator_ids": ["creator-1"],
            "video_ids": ["video-2", "video-1"], "schedule_type": "once",
        })
        material = service._learn_from_videos(task, {})
        self.assertEqual(material["source_ids"], ["video-2", "video-1"])
        self.assertTrue(all(item["transcript_status"] == "automatic" for item in material["sources"]))

    def test_recurring_new_video_strategy_uses_only_videos_after_task_start(self):
        service = self.make_service()
        videos = [
            {"id": "old", "creator_id": "creator-1", "creator_name": "Creator", "title": "Old", "platform": "youtube", "published_at": "2026-08-19T10:00:00+00:00"},
            {"id": "new-1", "creator_id": "creator-1", "creator_name": "Creator", "title": "New 1", "platform": "youtube", "published_at": "2026-08-21T10:00:00+00:00"},
            {"id": "new-2", "creator_id": "creator-1", "creator_name": "Creator", "title": "New 2", "platform": "youtube", "published_at": "2026-08-21T09:00:00+00:00"},
        ]
        service.configure(video_service=SelectionVideoService(videos))
        task = service.create_task({
            "source_type": "video", "creator_ids": ["creator-1"],
            "schedule_type": "daily", "video_selection_mode": "new",
            "created_at": "2026-08-20T12:00:00",
        })
        material = service._learn_from_videos(task, {})
        self.assertEqual(material["source_ids"], ["new-1", "new-2"])

    def test_recurring_most_viewed_strategy_selects_one_per_creator(self):
        service = self.make_service()
        videos = [
            {"id": "creator-1-old", "creator_id": "creator-1", "creator_name": "One", "title": "Old", "platform": "youtube", "published_at": "2026-08-21T08:00:00+00:00", "view_count": 10},
            {"id": "creator-1-top", "creator_id": "creator-1", "creator_name": "One", "title": "Top", "platform": "youtube", "published_at": "2026-08-21T07:00:00+00:00", "view_count": 1000},
            {"id": "creator-2-top", "creator_id": "creator-2", "creator_name": "Two", "title": "Top 2", "platform": "youtube", "published_at": "2026-08-21T06:00:00+00:00", "view_count": 500},
        ]
        service.configure(video_service=SelectionVideoService(videos))
        task = service.create_task({
            "source_type": "video", "creator_ids": ["creator-1", "creator-2"],
            "schedule_type": "weekly", "video_selection_mode": "most_viewed",
            "created_at": "2026-08-20T12:00:00",
        })
        material = service._learn_from_videos(task, {})
        self.assertEqual(set(material["source_ids"]), {"creator-1-top", "creator-2-top"})


if __name__ == "__main__":
    unittest.main()
