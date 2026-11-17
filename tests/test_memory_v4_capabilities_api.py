from pathlib import Path
import tempfile
import unittest

from core_services import KnowledgeBase
from saya_app.services.memory_v4 import ConnectorEvent, ConnectorPolicy, ReadOnlyConnector
from saya_app.services.time_context import TimeContextService


class MemoryV4CapabilitiesAdapterTests(unittest.TestCase):
    def test_capability_manifest_is_explicit_about_unconfigured_providers(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-capabilities-"))
        kb = KnowledgeBase(str(root))
        # Production defaults now enable memory/daily services. This case
        # explicitly exercises the unconfigured/disabled rollout boundary.
        kb.settings.update(memory_v4_read_enabled=False,memory_v4_write_enabled=False,daily_state_v2_enabled=False)
        manifest = kb.memory_v4_capabilities()
        by_name = {item["name"]: item for item in manifest["capabilities"]}
        self.assertEqual(manifest["status"], "ready")
        self.assertEqual(by_name["memory.query"]["availability"], "unconfigured")
        self.assertEqual(by_name["daily.query"]["availability"], "unconfigured")
        self.assertEqual(by_name["web.search"]["availability"], "unconfigured")

    def test_enabled_memory_capability_returns_safe_summaries(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-capabilities-enabled-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"memory_v4_write_enabled": True, "memory_v4_read_enabled": True})
        result = kb.process_memory_write_v4("记住：我偏好 capability-safe summaries", turn_id="capability-turn")
        self.assertEqual(result["status"], "success")
        manifest = kb.memory_v4_capabilities()
        memory_capability = next(item for item in manifest["capabilities"] if item["name"] == "memory.query")
        self.assertEqual(memory_capability["availability"], "available")
        self.assertIn("max_items", memory_capability["input_schema"]["properties"])
        self.assertIn("token_budget", memory_capability["input_schema"]["properties"])

    def test_agent_can_use_legacy_memory_until_v4_store_is_migrated(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-capabilities-legacy-agent-"))
        kb = KnowledgeBase(str(root))
        # Simulate the pre-migration rollout instead of the current default
        # already-initialized V4 store with no corresponding imported record.
        kb.settings.update(memory_v4_read_enabled=False,memory_v4_write_enabled=False)
        kb.memories = [{"id": "legacy-1", "content": "Doctor prefers concise answers.", "category": "preference"}]
        registry = kb._memory_v4_capability_registry(allow_legacy_memory=True)
        self.assertEqual(registry.availability("memory.query"), "available")
        result = registry.execute("memory.query", {"query": "concise answers"})
        self.assertEqual(result["status"], "success")
        self.assertEqual(result["result"]["items"][0]["id"], "legacy-1")

    def test_web_capabilities_require_flag_and_explicit_provider(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-capabilities-web-"))
        kb = KnowledgeBase(str(root))

        class FakeWebTools:
            def search(self, query, **kwargs):
                return {"status": "success", "operation": "web.search", "sources": [], "query": query}

            def read(self, urls, **kwargs):
                return {"status": "success", "operation": "web.read", "documents": [], "urls": urls}

        kb.memory_v4_web_tools = FakeWebTools()
        before = {item["name"]: item for item in kb.memory_v4_capabilities()["capabilities"]}
        self.assertEqual(before["web.search"]["availability"], "unconfigured")
        request_registry = kb._memory_v4_capability_registry(web_tools=FakeWebTools())
        self.assertEqual(request_registry.availability("web.search"), "available")
        kb.update_settings({"web_tools_v2_enabled": True})
        after = {item["name"]: item for item in kb.memory_v4_capabilities()["capabilities"]}
        self.assertEqual(after["web.search"]["availability"], "available")
        self.assertEqual(after["web.read"]["availability"], "available")
        registry = kb._memory_v4_capability_registry()
        result = registry.execute("web.search", {"query": "safe query"})
        self.assertEqual(result["status"], "success")

    def test_connector_capabilities_require_matching_flag_and_use_local_inbox(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-capabilities-connector-"))
        kb = KnowledgeBase(str(root))
        event = ConnectorEvent(
            id="connector-capability-event", source="discord", source_event_id="source-event",
            author_id="author", author_display="Author", channel_id="alerts",
            content="important local connector event", trust="verified", permission_scope="L1",
        )
        connector = ReadOnlyConnector(
            "discord", lambda: [event],
            policy=ConnectorPolicy(allowed_sources={"discord"}, allowed_channels={"discord": {"alerts"}}),
        )
        kb.memory_v4_connector = connector
        before = {item["name"]: item for item in kb.memory_v4_capabilities()["capabilities"]}
        self.assertEqual(before["connector.read_feed"]["availability"], "unconfigured")
        kb.update_settings({"memory_v4_write_enabled": True, "connector_discord_enabled": True})
        after = {item["name"]: item for item in kb.memory_v4_capabilities()["capabilities"]}
        self.assertEqual(after["connector.read_feed"]["availability"], "available")
        registry = kb._memory_v4_capability_registry()
        polled = registry.execute("connector.read_feed", {"limit": 5})
        self.assertEqual(polled["status"], "success")
        self.assertEqual(polled["result"]["accepted"], 1)
        self.assertEqual(registry.execute("connector.mark_read", {"event_ids": [event.id]})["status"], "approval_required")

    def test_weather_and_time_capabilities_use_application_owned_handlers(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-context-tools-"))
        kb = KnowledgeBase(str(root / "kb"))
        clock = TimeContextService(root / "time_events.jsonl")
        clock.record_backend_started("2026-09-15T08:00:00+08:00")
        clock.ensure_session_created(
            "session_2026-09-15_08-05-00-000000-deadbeef.jsonl",
            source="test",
        )
        kb.memory_v4_time_query = clock.query
        kb.memory_v4_weather_query = lambda arguments: {
            "location": arguments.get("location"),
            "current": {"temperature": 23},
            "daily": [],
        }

        registry = kb._memory_v4_capability_registry()
        self.assertEqual(registry.availability("time.query"), "available")
        self.assertEqual(registry.availability("weather.query"), "available")
        weather = registry.execute("weather.query", {"location": "Shanghai"})
        self.assertEqual(weather["status"], "success")
        self.assertEqual(weather["result"]["current"]["temperature"], 23)
        timing = registry.execute("time.query", {"limit": 5})
        self.assertEqual(timing["status"], "success")
        self.assertEqual(timing["result"]["backend_started_at"], "2026-09-15T08:00:00+08:00")

    def test_remote_time_events_require_private_agent_consent(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-time-policy-"))
        kb = KnowledgeBase(str(root / "kb"))
        kb.memory_v4_time_query = lambda arguments: {"status": "ready", "events": []}
        kb.memory_v4_weather_query = lambda arguments: {"status": "ready"}
        policy = kb.memory_v4_agent_policy()
        self.assertNotIn("time.query", policy.allowed_names)
        self.assertIn("weather.query", policy.allowed_names)
        kb.update_settings({"memory_v4_agent_allow_private": True})
        self.assertIn("time.query", kb.memory_v4_agent_policy().allowed_names)
