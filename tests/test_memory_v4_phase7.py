"""Phase 7 synthesis, user controls, temporary chat, and external-write gates."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    BackgroundSynthesisScheduler,
    BackgroundSynthesizer,
    ExternalWriteGate,
    MemoryControls,
    MemoryCandidateV4,
    MemoryEvidence,
    MemoryItemV4,
    MemoryQuery,
    MemoryRetriever,
    MemoryV4Store,
    TemporaryChat,
)


class Phase7Tests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-phase7-"))
        self.store = MemoryV4Store(root / "memory4.sqlite3")
        item = MemoryItemV4(id="m1", subject="user", predicate="prefers", value="concise", canonical_text="Doctor prefers concise replies.", kind="preference", authority="user_confirmed", sensitivity="normal")
        evidence = MemoryEvidence(id="e1", memory_id="m1", source_type="user_message", source_id="m", role="assertion", quote=item.canonical_text)
        self.store.upsert_memory(item, [evidence])

    def test_synthesis_patch_requires_evidence_and_is_not_freeform_rewrite(self):
        synthesis = BackgroundSynthesizer(self.store)
        result = synthesis.apply_patch({"operations": [{"op": "replace", "path": "/projects/saya/status", "value": "active", "evidence_refs": ["e1"]}]})
        self.assertEqual(result["summary"]["projects"]["saya"]["status"], "active")
        with self.assertRaises(ValueError):
            synthesis.apply_patch({"operations": [{"op": "replace", "path": "/secret", "value": "api_key: abc", "evidence_refs": ["e1"]}]})
        with self.assertRaises(ValueError):
            synthesis.apply_patch({"operations": [{"op": "replace", "path": "/no-evidence", "value": "x"}]})

    def test_user_controls_correct_pin_do_not_mention_and_restore(self):
        controls = MemoryControls(self.store)
        controls.pin("m1")
        corrected = controls.correct("m1", {"canonical_text": "Doctor prefers warm concise replies.", "value": "warm concise"})
        self.assertEqual(corrected["revision"], 3)
        controls.do_not_mention("m1")
        self.assertFalse(MemoryRetriever(self.store).search(MemoryQuery(query="Doctor prefers replies")).items)
        controls.unpin("m1")
        restored = controls.restore("m1", 1)
        self.assertEqual(restored["memory"]["canonical_text"], "Doctor prefers concise replies.")
        self.assertTrue(controls.summary())
        with self.assertRaises(ValueError):
            controls.correct("m1", {"revision": 99})

    def test_temporary_chat_cannot_read_or_write_memory(self):
        temporary = TemporaryChat()
        self.assertEqual(temporary.query("anything")["status"], "disabled")
        self.assertEqual(temporary.write("remember this")["status"], "blocked")
        self.assertEqual(temporary.prompt_context(), [])

    def test_external_write_requires_flag_preview_approval_and_idempotency(self):
        calls = []
        gate = ExternalWriteGate(lambda name, args: calls.append((name, args)) or "done", enabled=False)
        preview = gate.preview("connector.send", {"message": "hello"}, idempotency_key="k1")
        self.assertEqual(preview["status"], "approval_required")
        self.assertEqual(gate.execute("connector.send", {"message": "hello"}, idempotency_key="k1", approved=True)["status"], "disabled")
        gate.enabled = True
        self.assertEqual(gate.execute("connector.send", {"message": "changed"}, idempotency_key="k1", approved=True)["status"], "preview_mismatch")
        self.assertEqual(gate.execute("connector.send", {"message": "hello"}, idempotency_key="k1", approved=True)["status"], "success")
        self.assertEqual(gate.execute("connector.send", {"message": "hello"}, idempotency_key="k1", approved=True)["status"], "already_executed")
        self.assertEqual(len(calls), 1)

    def test_background_scheduler_is_disabled_without_producer_and_validates_patch(self):
        disabled = BackgroundSynthesisScheduler(self.store, enabled=False)
        self.assertEqual(disabled.run_once()["status"], "disabled")
        unconfigured = BackgroundSynthesisScheduler(self.store, enabled=True)
        self.assertEqual(unconfigured.run_once()["status"], "unconfigured")
        self.store.upsert_candidate(MemoryCandidateV4(
            id="candidate-synthesis", proposal={
                "canonical_text": "Doctor may prefer short answers.", "subject": "user",
                "predicate": "prefers", "value": "short", "kind": "preference",
                "scope_type": "global", "authority": "inferred", "sensitivity": "normal",
                "confidence": 0.5, "salience": 0.3,
            }, evidence_ids=["e1"],
        ))
        invalid = BackgroundSynthesisScheduler(self.store, lambda candidate: {"operations": [{"op": "replace", "path": "/x", "value": "no-evidence"}]}, enabled=True)
        result = invalid.run_once()
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["processed"], 1)
        self.assertEqual(result["rejected"], 1)
        self.assertEqual(self.store.get_candidate("candidate-synthesis").status, "rejected")


if __name__ == "__main__":
    unittest.main()
