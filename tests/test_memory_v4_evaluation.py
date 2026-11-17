import unittest

from scripts.eval_memory_v4 import build_write_corpus, run_evaluation


class MemoryV4EvaluationTests(unittest.TestCase):
    def test_corpus_has_required_size_and_category_balance(self):
        corpus = build_write_corpus()
        self.assertEqual(len(corpus), 200)
        categories = {case.category for case in corpus}
        self.assertTrue({"save", "forget", "correct", "question", "secret"}.issubset(categories))

    def test_clean_process_contract_suite_goes(self):
        result = run_evaluation()
        self.assertTrue(result["clean_process"])
        self.assertTrue(result["go"], result)
        self.assertGreaterEqual(result["write"]["intent_macro_f1"], 0.90)
        self.assertEqual(result["write"]["secret_permanent_write_count"], 0)
        baseline = result["write"]["legacy_baseline"]
        self.assertEqual(baseline["comparator"], "naive_legacy_keyword_baseline")
        self.assertGreater(baseline["unwanted_permanent_write_rate"], result["write"]["unwanted_write_rate"])
        self.assertGreater(baseline["secret_permanent_write_count"], result["write"]["secret_permanent_write_count"])

    def test_retrieval_and_prompt_metrics_are_reported(self):
        result = run_evaluation()
        self.assertGreaterEqual(result["retrieval"]["recall_at_5"], 0.90)
        self.assertGreaterEqual(result["retrieval"]["mrr"], 0.90)
        self.assertTrue(result["prompt"]["static_contracts"])
        self.assertFalse(result["prompt"]["audit_has_full_prompt"])
        self.assertTrue(result["tool_selection"]["go"])
        self.assertEqual(result["tool_selection"]["forbidden_call_count"], 0)
        self.assertTrue(result["daily_connector"]["go"])


if __name__ == "__main__":
    unittest.main()
