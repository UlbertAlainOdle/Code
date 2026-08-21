import tempfile
import unittest

from core_services import KnowledgeBase
from saya_app.services.memory_graph import MemoryGraphStore


class MemoryGraphStoreTests(unittest.TestCase):
    def test_builds_typed_nodes_and_expands_through_shared_context(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryGraphStore(root)
            memories = [
                {
                    "id": "m1", "title": "Alpha milestone", "date": "2026-08-10",
                    "memory_layer": "semantic", "project_id": "p1", "project_name": "Alpha",
                    "tags": ["planning"],
                    "entities": [{"type": "person", "name": "Doctor", "confidence": 0.9}],
                    "source_detail": {"chat_session_id": "session_alpha.jsonl"},
                },
                {
                    "id": "m2", "title": "Alpha budget", "date": "2026-08-11",
                    "memory_layer": "semantic", "project_id": "p1", "project_name": "Alpha",
                    "tags": ["finance"],
                    "source_detail": {"chat_session_id": "session_beta.jsonl"},
                },
            ]
            result = store.ensure_synced(memories)

            self.assertTrue(result["changed"])
            status = store.status()
            self.assertEqual(status["node_types"]["memory"], 2)
            self.assertEqual(status["node_types"]["project"], 1)
            self.assertEqual(status["relation_types"]["belongs_to_project"], 2)
            expanded = store.expand_memory_ids(["m1"], mode="auto", max_results=10)
            related = next(item for item in expanded if item["memory_id"] == "m2")
            self.assertEqual(related["relations"], ["belongs_to_project", "belongs_to_project"])
            self.assertGreater(related["graph_score"], 0.8)

    def test_manual_edge_survives_deterministic_rebuild(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryGraphStore(root)
            memories = [
                {"id": "m1", "title": "One", "memory_layer": "semantic"},
                {"id": "m2", "title": "Two", "memory_layer": "semantic"},
            ]
            store.ensure_synced(memories)
            edge = store.add_manual_edge("m1", "m2", relation="supports", confidence=0.93)
            memories[0]["title"] = "One updated"
            store.ensure_synced(memories)

            stored = next(item for item in store.graph["edges"] if item["id"] == edge["id"])
            self.assertEqual(stored["source_kind"], "manual")
            self.assertEqual(stored["relation"], "supports")

    def test_incremental_sync_rebuilds_only_changed_memory_fragments(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryGraphStore(root)
            memories = [
                {"id": "m1", "title": "One", "memory_layer": "semantic", "tags": ["alpha"]},
                {"id": "m2", "title": "Two", "memory_layer": "semantic", "tags": ["beta"]},
            ]
            store.ensure_synced(memories)
            store.add_manual_edge("m1", "m2", relation="supports")
            memories[0]["title"] = "One updated"
            memories[0]["tags"] = ["gamma"]

            result = store.ensure_synced(memories)

            self.assertEqual(result["sync_mode"], "incremental")
            self.assertEqual(result["updated_memory_count"], 1)
            self.assertEqual(store.graph["nodes"]["memory:m1"]["label"], "One updated")
            self.assertTrue(any(edge["source_kind"] == "manual" for edge in store.graph["edges"]))
            self.assertFalse(any(node.get("ref_id") == "alpha" for node in store.graph["nodes"].values()))

    def test_high_degree_connector_keeps_all_memories_reachable(self):
        with tempfile.TemporaryDirectory() as root:
            store = MemoryGraphStore(root)
            memories = [
                {"id": f"m{index:03d}", "title": f"Memory {index}", "tags": ["shared-topic"]}
                for index in range(180)
            ]
            store.ensure_synced(memories)

            expanded = store.expand_memory_ids(["m000"], mode="auto", max_results=500)

            self.assertEqual(len(expanded), 179)
            self.assertIn("m179", {item["memory_id"] for item in expanded})
            self.assertGreaterEqual(store.status()["indexed_adjacency_nodes"], 181)

    def test_repeated_knowledge_base_reads_use_cached_graph_index(self):
        with tempfile.TemporaryDirectory() as root:
            kb = KnowledgeBase(root)
            kb.add_memory("Alpha detail", title="Alpha", tags=["alpha"])
            calls = []
            original = kb.memory_graph.ensure_synced

            def tracked(*args, **kwargs):
                calls.append(True)
                return original(*args, **kwargs)

            kb.memory_graph.ensure_synced = tracked
            first = kb.ensure_memory_graph()
            second = kb.ensure_memory_graph()

            self.assertTrue(first["changed"])
            self.assertEqual(second["sync_mode"], "cached")
            self.assertEqual(len(calls), 1)


class GraphRecallIntegrationTests(unittest.TestCase):
    def test_graph_mode_adds_bounded_project_context_and_audits_path(self):
        with tempfile.TemporaryDirectory() as root:
            kb = KnowledgeBase(root)
            kb.settings["memory_core_limit"] = 0
            kb.add_memory(
                "The Alpha launch milestone is next Friday.",
                title="Alpha milestone",
                category="project",
                metadata={"project_id": "alpha", "project_name": "Alpha"},
            )
            kb.add_memory(
                "The approved budget is 2400 units.",
                title="Alpha budget",
                category="finance",
                metadata={"project_id": "alpha", "project_name": "Alpha"},
            )

            off_context, off_audit = kb.build_prompt_context_with_audit(
                "launch milestone timing",
                project_id="alpha",
                graph_mode="off",
            )
            graph_context, graph_audit = kb.build_prompt_context_with_audit(
                "launch milestone timing",
                project_id="alpha",
                graph_mode="auto",
            )

            self.assertIn("Alpha milestone", off_context)
            self.assertNotIn("Alpha budget", off_context)
            self.assertIn("Alpha budget", graph_context)
            self.assertEqual(off_audit["graph"]["status"], "disabled")
            self.assertEqual(graph_audit["graph"]["status"], "expanded")
            self.assertEqual(graph_audit["graph"]["included_count"], 1)
            graph_item = next(item for item in graph_audit["selected"] if item["title"] == "Alpha budget")
            self.assertEqual(graph_item["reason"], "graph_related")
            self.assertIn("belongs_to_project", graph_item["graph"]["relations"])

    def test_graph_view_returns_bounded_enriched_neighborhood(self):
        with tempfile.TemporaryDirectory() as root:
            kb = KnowledgeBase(root)
            first = kb.add_memory(
                "Milestone details for the Alpha project.",
                title="Alpha milestone",
                metadata={
                    "project_id": "alpha", "project_name": "Alpha",
                    "source_detail": {"chat_session_id": "session_alpha.jsonl"},
                },
            )
            kb.add_memory(
                "Budget details for the Alpha project.",
                title="Alpha budget",
                metadata={"project_id": "alpha", "project_name": "Alpha"},
            )

            view = kb.get_memory_graph_view(center_memory_id=first["id"], limit=40)

            self.assertEqual(view["summary"]["memory_nodes"], 2)
            self.assertEqual(view["summary"]["connector_nodes"], 1)
            self.assertEqual(view["summary"]["edges"], 2)
            project_node = next(node for node in view["nodes"] if node["type"] == "project")
            self.assertEqual(project_node["label"], "Alpha")
            first_node = next(node for node in view["nodes"] if node.get("ref_id") == first["id"])
            self.assertEqual(first_node["session_id"], "session_alpha.jsonl")
            self.assertIn("Milestone details", first_node["preview"])


if __name__ == "__main__":
    unittest.main()
