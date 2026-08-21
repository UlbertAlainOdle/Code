import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from saya_app.services.web_research.citation_validator import validate_response_citations
from saya_app.services.web_research.models import WebSource


class WebCitationValidationTests(unittest.TestCase):
    def test_maps_claim_to_supporting_passage(self):
        source = WebSource(
            id=1,
            title="Official release notes",
            url="https://example.com/releases",
            domain="example.com",
            status="read",
            source_type="official",
            authority="primary",
            text=(
                "The Example API version 3.2 was released on 10 August 2026. "
                "It adds batch processing and a new audit endpoint."
            ),
        )
        report = validate_response_citations(
            "Example API 3.2 was released on 10 August 2026. [1]",
            [source],
        )
        self.assertEqual(report["status"], "pass")
        self.assertIn("API 3.2", report["claims"][0]["claim"])
        citation = report["claims"][0]["citations"][0]
        self.assertEqual(citation["support"], "supported")
        self.assertIn("10 August 2026", citation["passage"])
        self.assertLessEqual(len(citation["passage"]), 320)

    def test_invalid_reference_is_reported(self):
        source = WebSource(
            id=1, title="A", url="https://a.example", domain="a.example",
            status="read", text="A verified statement.",
        )
        report = validate_response_citations("A verified statement. [4]", [source])
        self.assertEqual(report["status"], "needs_review")
        self.assertEqual(report["summary"]["invalid_references"], 1)

    def test_number_mismatch_cannot_pass(self):
        source = WebSource(
            id=1, title="Metric", url="https://a.example", domain="a.example",
            status="read", text="The official metric was 4.2 in 2025.",
        )
        report = validate_response_citations("The official metric was 9.8 in 2026. [1]", [source])
        self.assertNotEqual(report["status"], "pass")
        self.assertIn(
            report["claims"][0]["citations"][0]["support"],
            {"partial", "unsupported"},
        )

    def test_snippet_is_never_marked_fully_supported(self):
        source = WebSource(
            id=1, title="Search result", url="https://a.example", domain="a.example",
            status="snippet_only", snippet="The release date is 10 August 2026.",
        )
        report = validate_response_citations("The release date is 10 August 2026. [1]", [source])
        self.assertEqual(report["status"], "partial")
        self.assertEqual(report["claims"][0]["citations"][0]["support"], "snippet_only")

    def test_no_citations_is_explicit(self):
        source = WebSource(
            id=1, title="A", url="https://a.example", domain="a.example",
            status="read", text="Version 4 was released in 2026.",
        )
        report = validate_response_citations("Version 4 was released in 2026.", [source])
        self.assertEqual(report["status"], "no_citations")
        self.assertEqual(report["summary"]["cited_claims"], 0)


if __name__ == "__main__":
    unittest.main()
