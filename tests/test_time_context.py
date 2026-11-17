from pathlib import Path
import tempfile
import unittest

from saya_app.services.time_context import TimeContextService


class TimeContextServiceTests(unittest.TestCase):
    def test_records_bounded_content_free_backend_session_and_turn_events(self):
        with tempfile.TemporaryDirectory() as root:
            service = TimeContextService(Path(root) / "time_events.jsonl")
            service.record_backend_started("2026-09-15T08:00:00+08:00")
            session_id = "session_2026-09-15_08-05-00-123456-deadbeef.jsonl"
            created = service.ensure_session_created(session_id, source="test")
            service.ensure_session_created(session_id, source="duplicate")
            service.record(
                "turn.started",
                occurred_at="2026-09-15T08:06:00+08:00",
                session_id=session_id,
                request_id="request-1",
                details={"source": "test", "message": "must not be stored"},
            )

            result = service.query({"session_id": session_id, "limit": 10})
            self.assertEqual(created["occurred_at"], "2026-09-15T08:05:00+08:00")
            self.assertEqual(result["event_count"], 2)
            self.assertEqual(result["events"][0]["type"], "turn.started")
            self.assertNotIn("message", str(result))
            self.assertEqual(
                len([event for event in result["events"] if event["type"] == "session.created"]),
                1,
            )

    def test_filters_event_types_and_rejects_unknown_event(self):
        with tempfile.TemporaryDirectory() as root:
            service = TimeContextService(Path(root) / "time_events.jsonl")
            self.assertIsNone(service.record("message.content", details={"source": "test"}))
            service.record_backend_started("2026-09-15T08:00:00+08:00")
            service.record("session.opened", session_id="session-test", details={"source": "test"})
            result = service.query({"event_types": ["backend.started"], "limit": 10})
            self.assertEqual(result["event_count"], 1)
            self.assertEqual(result["events"][0]["type"], "backend.started")

            reloaded = TimeContextService(Path(root) / "time_events.jsonl")
            restored = reloaded.query({"limit": 1})
            self.assertEqual(restored["backend_started_at"], "2026-09-15T08:00:00+08:00")
            self.assertEqual(restored["timezone"], "Asia/Shanghai")
            self.assertIsInstance(restored["backend_uptime_seconds"], int)


if __name__ == "__main__":
    unittest.main()
