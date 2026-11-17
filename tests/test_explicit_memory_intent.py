"""Explicit memory save-intent tests (V4 plan sections 3.2 / 6.2 / 19-phase-0).

Drives ``KnowledgeBase.capture_explicit_memory`` against the corpus in
``tests/data/explicit_memory_intent_corpus.json``. Ordinary discussion of
"记住" must never write; sentence-initial explicit commands must save;
credential-like payloads are rejected even when explicitly requested.
"""

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core_services import KnowledgeBase  # noqa: E402

CORPUS_PATH = Path(__file__).resolve().parent / "data" / "explicit_memory_intent_corpus.json"


def _kb(tmp_path):
    kb = KnowledgeBase(base_dir=str(tmp_path))
    kb.sqlite_store = None
    kb.embedding_provider = None
    return kb


class ExplicitMemoryIntentCorpusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(CORPUS_PATH, "r", encoding="utf-8") as f:
            cls.cases = json.load(f)["cases"]

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = _kb(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _run_case(self, case):
        self.kb.memories = []
        result = self.kb.capture_explicit_memory(case["text"])
        if case["expect"] == "save":
            self.assertIsNotNone(result, f"must save: {case['id']}: {case['text']}")
            content = str(result.get("content") or "")
            self.assertGreaterEqual(len(content.strip()), 4)
            # No command-word residue leaks into the stored statement.
            self.assertFalse(
                content.strip().startswith(("记住", "記住", "remember", "Remember", "save", "Save", "keep", "Keep")),
                f"command residue in content: {content!r}",
            )
            self.assertFalse(content.strip().endswith(("?", "？", "吗", "吗？", "呢")),
                             f"stored a question fragment: {content!r}")
            expected_pin = bool(case.get("expect_pinned"))
            self.assertEqual(
                bool(result.get("is_important")), expected_pin,
                f"pin default mismatch for {case['id']}: got {result.get('is_important')}",
            )
        else:
            self.assertIsNone(result, f"must NOT save: {case['id']}: {case['text']}")
            self.assertEqual(len(self.kb.memories), 0,
                             f"nothing may be written for {case['id']}")

    def test_corpus_save_cases(self):
        for case in self.cases:
            if case["expect"] == "save":
                with self.subTest(case=case["id"]):
                    self._run_case(case)

    def test_corpus_reject_cases(self):
        for case in self.cases:
            if case["expect"] == "reject":
                with self.subTest(case=case["id"]):
                    self._run_case(case)

    def test_duplicate_command_returns_existing_memory(self):
        self.kb.memories = []
        first = self.kb.capture_explicit_memory("记住：我的常用字体是 JetBrains Mono")
        second = self.kb.capture_explicit_memory("记住：我的常用字体是 JetBrains Mono")
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.kb.memories), 1)


class ExplicitMemoryGuardUnitTests(unittest.TestCase):
    """The plan section 3.2 minimal repro set plus guard-specific cases."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.kb = _kb(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def _reject_reason(self, text, captured):
        return self.kb._explicit_memory_reject_reason(text, captured)

    def test_question_tail_rejected(self):
        self.assertEqual(self._reject_reason("记住密码的吗？", "密码的吗？"), "question")

    def test_fragment_tail_rejected(self):
        self.assertEqual(self._reject_reason("记住这件事了吗？", "这件事了吗？"), "question")

    def test_pronoun_only_rejected(self):
        self.assertEqual(self._reject_reason("记住这个", "这个"), "pronoun_only")

    def test_secret_hint_rejected(self):
        self.assertEqual(self._reject_reason("记住密码", "我的服务器密码在信封里"), "secret_content")

    def test_normal_statement_passes(self):
        self.assertEqual(self._reject_reason("记住：我喜欢蓝色", "我喜欢蓝色"), "")

    def test_quoted_start_never_matches(self):
        self.assertIsNone(self.kb.capture_explicit_memory("「记住要吃药」，医生这样叮嘱我"))

    def test_mid_sentence_command_never_matches(self):
        # The old unanchored regex captured "多少东西？" from this sentence.
        self.assertIsNone(self.kb.capture_explicit_memory("你能记住多少东西？"))
        self.assertIsNone(self.kb.capture_explicit_memory("我们聊到你会不会记住我随口说的话"))

    def test_settings_off_disables_capture(self):
        self.kb.settings["auto_save_memories"] = False
        self.assertIsNone(self.kb.capture_explicit_memory("记住：测试条目不该保存"))


if __name__ == "__main__":
    unittest.main()
