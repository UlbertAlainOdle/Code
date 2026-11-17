"""Phase 6 read-only connector and Attention Inbox tests."""

from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from saya_app.services.memory_v4 import (
    AttentionInbox,
    ConnectorEvent,
    ConnectorPolicy,
    MemoryV4Store,
    ReadOnlyConnector,
)


class Phase6Tests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-phase6-"))
        self.store = MemoryV4Store(root / "memory4.sqlite3")
        self.policy = ConnectorPolicy(allowed_sources={"discord"}, allowed_channels={"discord": {"saya-alerts"}}, max_scope="L1")
        self.inbox = AttentionInbox(self.store, policy=self.policy, cooldown_seconds=3600)

    def event(self, event_id, content, *, channel="saya-alerts", source_event_id=None, permission_scope="L1", expires_at=""):
        return ConnectorEvent(
            id=event_id, source="discord", source_event_id=source_event_id or event_id,
            author_id="author-1", author_display="A", channel_id=channel, thread_id="thread-1",
            occurred_at="2026-09-14T10:00:00", received_at="2026-09-14T10:01:00",
            content=content, trust="verified", permission_scope=permission_scope, expires_at=expires_at,
        )

    def test_whitelist_and_permission_policy_block_unapproved_sources(self):
        blocked_source = self.inbox.ingest({**self.event("e0", "hello").to_dict(), "source": "x"})
        self.assertEqual(blocked_source["status"], "blocked")
        blocked_channel = self.inbox.ingest(self.event("e1", "hello", channel="random"))
        self.assertEqual(blocked_channel["status"], "blocked")
        blocked_scope = self.inbox.ingest(self.event("e2", "hello", permission_scope="L2"))
        self.assertEqual(blocked_scope["status"], "blocked")

    def test_ingest_deduplicates_source_event_and_read_only_poll_has_no_send(self):
        first = self.inbox.ingest(self.event("e1", "important: review this"))
        duplicate = self.inbox.ingest(self.event("e2", "important: review this", source_event_id="e1"))
        self.assertEqual(first["status"], "accepted")
        self.assertEqual(duplicate["status"], "duplicate")
        connector = ReadOnlyConnector("discord", lambda: [self.event("e3", "new alert")], policy=self.policy)
        self.assertEqual(self.inbox.poll(connector)["accepted"], 1)
        self.assertFalse(hasattr(connector, "send"))

    def test_digest_prioritizes_important_events_and_applies_thread_cooldown(self):
        self.inbox.ingest(self.event("e1", "ordinary update"))
        self.inbox.ingest(self.event("e2", "URGENT deadline today"))
        digest = self.inbox.digest(limit=5, now="2026-09-14T10:10:00")
        self.assertEqual(digest["count"], 1)
        self.assertIn("urgent", digest["items"][0]["content"].lower())
        self.assertEqual(self.inbox.digest(limit=5, now="2026-09-14T10:20:00")["count"], 0)

    def test_mark_read_and_four_day_ttl_purge(self):
        self.inbox.ingest(self.event("old", "old", expires_at="2026-09-13T00:00:00"))
        self.inbox.ingest(self.event("new", "new", expires_at="2026-09-20T00:00:00"))
        self.assertEqual(self.inbox.mark_read(["new"]), 1)
        self.assertEqual(self.inbox.purge_expired(before="2026-09-14T00:00:00"), 1)
        self.assertEqual([event["id"] for event in self.store.list_connector_events(limit=10)], ["new"])


if __name__ == "__main__":
    unittest.main()
