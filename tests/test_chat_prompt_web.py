"""Phase 4 regression tests: build_prompt off-mode equivalence + web injection.

These verify that adding the ``web_context`` parameter did not change the prompt
when it is empty (plan section 17: "off 模式下最终提示词应与改造前保持语义等价"),
and that web evidence lands in the contractually-correct position.

The server module has an interactive model-selection prompt at import time, so
we feed a model choice on stdin and capture the module via importlib.
"""

import io
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_server_module():
    """Import server5-22B----.py, bypassing its interactive model prompt."""
    import importlib.util

    # The module calls input() at top level for model selection. Feed "5"
    # (API mode) so no local GGUF model is loaded.
    old_stdin = sys.stdin
    sys.stdin = io.StringIO("5\n")
    try:
        spec = importlib.util.spec_from_file_location(
            "saya_server_under_test", str(ROOT / "server5-22B----.py")
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.stdin = old_stdin


SERVER = load_server_module()


class BuildPromptOffModeTests(unittest.TestCase):
    """Empty web_context must produce a byte-identical prompt to before."""

    def _common_args(self):
        return dict(
            user="hello",
            word_limit_msg="",
            mode="normal",
            checkin_context="",
            project_context="",
            project_id=None,
            style_settings={},
            role_mode="",
            context_window_tokens=8192,
        )

    def test_explicit_empty_web_context_equals_omitted(self):
        # Calling with web_context="" must equal calling without the kwarg.
        args = self._common_args()
        prompt_default = SERVER.build_prompt(**args)
        prompt_empty = SERVER.build_prompt(**args, web_context="")
        self.assertEqual(prompt_default, prompt_empty)

    def test_web_context_present_inserts_evidence(self):
        args = self._common_args()
        evidence = "[System: Web Research Evidence]\nuntrusted evidence here"
        prompt_without = SERVER.build_prompt(**args)
        prompt_with = SERVER.build_prompt(**args, web_context=evidence)
        # The evidence text must appear, and the prompt must be longer.
        self.assertIn("[System: Web Research Evidence]", prompt_with)
        self.assertNotIn("[System: Web Research Evidence]", prompt_without)
        self.assertGreater(len(prompt_with), len(prompt_without))

    def test_off_mode_has_no_web_marker(self):
        args = self._common_args()
        prompt = SERVER.build_prompt(**args)
        self.assertNotIn("Web Research Evidence", prompt)
        self.assertNotIn("Web Research Status", prompt)


class BudgetTests(unittest.TestCase):
    def test_zero_window_returns_zero(self):
        self.assertEqual(SERVER.compute_web_context_budget("x" * 100, 0, 100, True), 0)

    def test_local_hard_cap_applied(self):
        # Tiny base prompt + huge window -> capped at the local 7000-char limit.
        from saya_app.config import WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS
        budget = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, True)
        self.assertEqual(budget, WEB_CONTEXT_LOCAL_HARD_LIMIT_CHARS)

    def test_api_hard_cap_applied(self):
        from saya_app.config import WEB_CONTEXT_API_HARD_LIMIT_CHARS
        budget = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, False)
        self.assertEqual(budget, WEB_CONTEXT_API_HARD_LIMIT_CHARS)

    def test_output_reserve_reduces_budget(self):
        small_out = SERVER.compute_web_context_budget("x" * 30, 1000000, 100, True)
        large_out = SERVER.compute_web_context_budget("x" * 30, 1000000, 5000, True)
        # Larger output reserve must not increase the budget.
        self.assertLessEqual(large_out, small_out)


class ChatRequestModelTests(unittest.TestCase):
    def test_default_web_fields_are_off_and_empty(self):
        req = SERVER.ChatWithMotion(user_input="hi")
        self.assertEqual(req.web_search_mode, "off")
        self.assertEqual(req.web_search_api_key, "")
        self.assertEqual(req.web_source_strategy, "official_first")
        self.assertEqual(req.web_safe_search, "moderate")

    def test_web_fields_accepted(self):
        req = SERVER.ChatWithMotion(
            user_input="hi", web_search_mode="force", web_search_api_key="KEY",
            web_search_count=16, web_final_sources=5,
            web_source_strategy="balanced", web_safe_search="strict",
        )
        self.assertEqual(req.web_search_mode, "force")
        self.assertEqual(req.web_search_api_key, "KEY")
        self.assertEqual(req.web_search_count, 16)
        self.assertEqual(req.web_final_sources, 5)
        self.assertEqual(req.web_source_strategy, "balanced")
        self.assertEqual(req.web_safe_search, "strict")


class WebResearchMetadataTests(unittest.TestCase):
    """Public source metadata contract (plan section 18)."""

    def test_public_source_dict_excludes_text_and_key(self):
        from saya_app.services.web_research.models import WebSource
        src = WebSource(
            id=1, title="t", url="https://a.com/p", domain="a.com",
            snippet="s", status="read", text="SECRET PAGE BODY",
        )
        public = src.to_public_dict()
        # Public schema fields only.
        self.assertEqual(
            set(public.keys()),
            {
                "id", "title", "url", "domain", "snippet", "published_at",
                "status", "source_type", "authority",
            },
        )
        self.assertNotIn("text", public)
        self.assertNotIn("SECRET PAGE BODY", json.dumps(public))

    def test_metadata_shape_matches_contract(self):
        # The exact dict shape written to ai_metadata["web_research"].
        from saya_app.services.web_research.models import ResearchBundle, WebSource
        bundle = ResearchBundle(
            query="q", performed=True,
            sources=[WebSource(id=1, title="t", url="https://a.com", domain="a.com",
                               snippet="s", status="read")],
        )
        meta = {
            "performed": bundle.performed,
            "mode": "auto",
            "query": bundle.query,
            "sources": [s.to_public_dict() for s in bundle.sources],
            "diagnostics": bundle.to_public_diagnostics(),
        }
        self.assertTrue(meta["performed"])
        self.assertEqual(meta["sources"][0]["id"], 1)
        self.assertNotIn("text", meta["sources"][0])
        self.assertIn("returned_sources", meta["diagnostics"])


if __name__ == "__main__":
    unittest.main()
