"""Phase 1 contract, migration, and rollback-safety tests."""

from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    DayState,
    DualWriteCoordinator,
    FeatureFlagStore,
    MemoryEvidence,
    MemoryItemV4,
    MemoryV4Migration,
    MemoryV4Store,
    TurnEvent,
    ValidationError,
)


class MemoryV4ContractTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-tests-"))
        self.store = MemoryV4Store(self.root / "memory4.sqlite3")

    def _memory(self, memory_id="m1", **updates):
        data = {
            "id": memory_id,
            "subject": "user",
            "predicate": "prefers_response_style",
            "value": "concise",
            "canonical_text": "Doctor prefers concise replies.",
            "language": "en",
            "kind": "preference",
            "scope_type": "global",
            "authority": "user_confirmed",
            "sensitivity": "personal",
        }
        data.update(updates)
        return MemoryItemV4(**data)

    def _evidence(self, memory_id="m1"):
        return MemoryEvidence(
            id=f"e-{memory_id}", memory_id=memory_id, source_type="chat", source_id="turn-1",
            role="user", quote="Please remember that I prefer concise replies.",
        )

    def test_contracts_validate_sensitive_fields_and_public_event_boundary(self):
        with self.assertRaises(ValidationError):
            self._memory(sensitivity="secret")
        with self.assertRaises(ValidationError):
            TurnEvent(id="e", turn_id="t", sequence=0, type="debug", public_payload={"token": "redacted"})
        state = DayState(date="2026-09-14", page_load_count=2, backend_start_count=1)
        self.assertEqual(state.page_load_count, 2)
        self.assertEqual(state.backend_start_count, 1)

    def test_store_versions_restore_and_tombstone_are_explicit(self):
        created = self.store.upsert_memory(self._memory(), [self._evidence()], operation="create", actor="user")
        self.assertEqual(created["status"], "created")
        updated = self.store.update_memory("m1", {"canonical_text": "Doctor prefers warm concise replies."})
        self.assertEqual(updated["revision"], 2)
        self.assertEqual(len(self.store.list_versions("m1")), 2)
        restored = self.store.restore_version("m1", 1)
        self.assertEqual(restored["revision"], 3)
        self.assertEqual(self.store.get_memory("m1").canonical_text, "Doctor prefers concise replies.")
        self.assertTrue(self.store.tombstone_memory("m1", purge=True))
        with self.assertRaises(ValueError):
            self.store.upsert_memory(self._memory(), [self._evidence()])
        self.assertEqual(self.store.restore_version("m1", 1)["status"], "created", "restore explicitly clears the tombstone")
        self.assertFalse(self.store.is_tombstoned("m1"))
        self.assertEqual(self.store.integrity_check()["version_count"], 5)

    def test_duplicate_upsert_with_same_evidence_is_idempotent(self):
        item, evidence = self._memory(), [self._evidence()]
        self.assertEqual(self.store.upsert_memory(item, evidence)["status"], "created")
        self.assertEqual(self.store.upsert_memory(item, evidence)["status"], "unchanged")
        self.assertEqual(len(self.store.list_versions("m1")), 1)

    def test_day_state_keeps_page_load_and_backend_start_separate(self):
        self.store.upsert_day_state(DayState(date="2026-09-14", page_load_count=4, backend_start_count=1))
        current = self.store.get_day_state("2026-09-14")
        self.assertEqual(current.page_load_count, 4)
        self.assertEqual(current.backend_start_count, 1)

    def test_export_keeps_purged_memory_versions_for_explicit_recovery(self):
        self.store.upsert_memory(self._memory(), [self._evidence()], operation="create", actor="user")
        self.store.tombstone_memory("m1", purge=True)
        exported = self.store.export_state()
        self.assertNotIn("m1", {item["id"] for item in exported["memories"]})
        self.assertIn("m1", {item["memory_id"] for item in exported["versions"]})
        recovered_store = MemoryV4Store(self.root / "recovered.sqlite3")
        recovered_store.import_state(exported)
        self.assertTrue(recovered_store.is_tombstoned("m1"))
        self.assertIsNone(recovered_store.get_memory("m1"))
        recovered_store.restore_version("m1", 1)
        self.assertIsNotNone(recovered_store.get_memory("m1"))

    def test_feature_flag_updates_are_audited(self):
        settings = self.root / "memory_settings.json"
        flags = FeatureFlagStore(settings)
        self.assertFalse(flags.get("memory_v4_write_enabled"))
        flags.update({"memory_v4_write_enabled": True}, actor="test", reason="phase1")
        self.assertTrue(FeatureFlagStore(settings).get("memory_v4_write_enabled"))
        audit = self.root / "memory_v4_flag_audit.jsonl"
        lines = audit.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(json.loads(lines[0])["changes"][0]["name"], "memory_v4_write_enabled")

    def test_dual_write_can_be_disabled_and_reports_legacy_drift(self):
        item, evidence = self._memory(), [self._evidence()]
        disabled = DualWriteCoordinator(self.store)
        self.assertEqual(disabled.write(item, evidence)["status"], "disabled")
        enabled = DualWriteCoordinator(self.store, enabled=True)
        legacy = []
        enabled.write(item, evidence, legacy_sink=lambda value: legacy.append(DualWriteCoordinator._legacy_projection(value)))
        result = enabled.consistency_check(legacy)
        self.assertTrue(result["ok"])


class MemoryV4MigrationTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-migration-"))
        self.records = [
            {
                "id": "fact-1", "creation_time": "2026-01-01 10:00:00", "date": "2026-01-01",
                "title": "Tea preference", "content": "Doctor prefers jasmine tea.", "source": "manual",
                "category": "preference", "is_locked": True, "is_important": True,
            },
            {
                "id": "session-1", "creation_time": "2026-01-02 10:00:00", "date": "2026-01-02",
                "title": "Old session", "content": "A museum visit was discussed.", "source": "session_archive",
                "category": "conversation_summary", "is_locked": True, "is_important": True,
            },
            {
                "id": "system-daily-activity-current", "creation_time": "2026-09-14 08:00:00", "date": "2026-09-14",
                "title": "Today", "content": "Daily activity", "source": "system_activity",
                "memory_kind": "daily_activity", "managed_slot": "daily_activity:today",
            },
            {
                "id": "bad-explicit", "creation_time": "2026-01-03 10:00:00", "date": "2026-01-03",
                "title": "Residue", "content": "你的人，所以是有作用的。但是……", "source": "auto_explicit",
                "category": "general",
            },
        ]
        (self.root / "knowledge.json").write_text(json.dumps(self.records, ensure_ascii=False, indent=2), encoding="utf-8")
        (self.root / "memory_deletions.json").write_text(json.dumps({"schema_version": 1, "deleted": {"deleted-1": {"reason": "user_delete"}}}), encoding="utf-8")
        (self.root / "memory_candidates.json").write_text(json.dumps([{
            "id": "candidate-1", "content": "User is studying Japanese", "status": "ignored",
            "confidence": 0.8, "importance_score": 0.5, "creation_time": "2026-01-01 10:00:00",
        }], ensure_ascii=False), encoding="utf-8")

    def test_preview_is_read_only_and_exposes_high_risk_decisions(self):
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        report = MemoryV4Migration(self.root).preview()
        self.assertEqual(report["source_count"], 4)
        self.assertEqual(len(report["quarantined"]), 1)
        self.assertEqual(report["quarantined"][0]["reason"], "dangling_start")
        self.assertEqual(len(report["day_states"]), 1)
        self.assertTrue(report["suggest_unpin"])
        self.assertTrue(report["suggest_unlock"])
        self.assertTrue(report["candidates"])
        self.assertEqual({path.name: path.read_bytes() for path in self.root.iterdir()}, before)
        self.assertFalse((self.root / "memory4.sqlite3").exists())

    def test_apply_makes_backup_and_repeated_apply_does_not_duplicate_versions(self):
        migration = MemoryV4Migration(self.root)
        first = migration.apply()
        self.assertEqual(first["status"], "success")
        store = MemoryV4Store(self.root / "memory4.sqlite3")
        self.assertIsNotNone(store.get_memory("fact-1"))
        self.assertIsNotNone(store.get_memory("session-1"))
        self.assertIsNone(store.get_memory("system-daily-activity-current"))
        self.assertEqual(store.get_day_state("2026-09-14").date, "2026-09-14")
        self.assertTrue(store.is_tombstoned("deleted-1"))
        version_count = store.integrity_check()["version_count"]
        second = migration.apply()
        self.assertEqual(second["status"], "success")
        self.assertEqual(MemoryV4Store(self.root / "memory4.sqlite3").integrity_check()["version_count"], version_count)
        self.assertTrue(Path(first["backup_path"]).is_dir())

    def test_legacy_knowledge_base_double_writes_by_default(self):
        from core_services import KnowledgeBase

        root = self.root / "runtime"
        knowledge_base = KnowledgeBase(str(root))
        self.assertTrue((root / "memory4.sqlite3").exists())
        knowledge_base.update_settings({"memory_v4_write_enabled": True})
        memory = knowledge_base.add_memory("Doctor prefers a warm concise style.", source="manual")
        self.assertTrue((root / "memory4.sqlite3").exists())
        self.assertIsNotNone(knowledge_base.memory_v4_store.get_memory(memory["id"]))
        self.assertEqual(knowledge_base.get_memory_v4_status()["status"], "ready")


if __name__ == "__main__":
    unittest.main()
