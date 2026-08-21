import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class MemoryGraphUiContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.javascript = (ROOT / "static" / "js" / "app.js").read_text(encoding="utf-8")
        cls.css = (ROOT / "static" / "css" / "style.css").read_text(encoding="utf-8")
        cls.html = (ROOT / "static" / "Chat4o5-Modern.html").read_text(encoding="utf-8")
        cls.server = (ROOT / "server5-22B----.py").read_text(encoding="utf-8")

    def test_network_is_an_alternate_view_inside_memory_workspace(self):
        self.assertIn('onclick="openMemoryGraphViewer()"', self.javascript)
        self.assertIn('setMemoryModalHeader(\'Memory network\', \'openMemoryViewer()\')', self.javascript)
        self.assertIn("/api/kb/graph/view?", self.javascript)
        self.assertIn("Focus neighborhood", self.javascript)
        self.assertIn("Open source conversation", self.javascript)

    def test_network_supports_interactive_2d_and_3d_views(self):
        self.assertIn("id=\"memory-graph-view-3d\"", self.javascript)
        self.assertIn("function setMemoryGraphViewMode", self.javascript)
        self.assertIn("function renderMemoryGraph3D", self.javascript)
        self.assertIn("function setMemoryGraphSurfaceHidden", self.javascript)
        self.assertIn("element.setAttribute('hidden', '')", self.javascript)
        self.assertIn('id="memory-graph-line-scale"', self.javascript)
        self.assertIn('id="memory-graph-node-scale"', self.javascript)
        self.assertIn('id="memory-graph-text-scale"', self.javascript)
        self.assertLess(self.javascript.index('id="memory-graph-text-scale"'), self.javascript.index('id="memory-graph-line-scale"'))
        self.assertIn("settings2d.text === 0 ? 'none' : ''", self.javascript)
        self.assertIn("label.visible = settings3d.text > 0", self.javascript)
        self.assertIn("saya_memory_graph_display_scales_v1", self.javascript)
        self.assertIn("function loadMemoryGraphDisplayScales", self.javascript)
        self.assertIn("function saveMemoryGraphDisplayScales", self.javascript)
        self.assertIn("saveMemoryGraphDisplayScales();", self.javascript)
        self.assertIn("function setMemoryGraphDisplayScale", self.javascript)
        self.assertIn("new THREE.CylinderGeometry", self.javascript)
        self.assertIn("displayScales", self.javascript)
        self.assertIn("getMemoryGraphDisplaySettings('3d').node * interactionScale", self.javascript)
        self.assertIn("`${memoryGraphState.viewMode.toUpperCase()} settings`", self.javascript)
        self.assertIn("new THREE.WebGLRenderer", self.javascript)
        self.assertIn("new THREE.Raycaster", self.javascript)
        self.assertIn("function disposeMemoryGraph3D", self.javascript)
        self.assertIn(".memory-graph-3d", self.css)
        self.assertIn("g42-memory-network-3d-scale", self.html)
        self.assertIn("g43-three-r106-fix", self.html)
        self.assertIn("g44-memory-graph-surface-fix", self.html)
        self.assertIn("g45-memory-graph-display-controls", self.html)
        self.assertIn("g46-independent-display-settings", self.html)
        self.assertIn("g47-memory-graph-text-scale", self.html)
        self.assertIn("g48-persist-display-settings", self.html)

    def test_mode_settings_are_grouped_and_ordered(self):
        start = self.javascript.index("function organizeModeSettingSections()")
        end = self.javascript.index("function openModeSettingModal()", start)
        organizer = self.javascript[start:end]
        search_ids = [
            "default-search-mode-select", "web-search-preset-select",
            "web-source-strategy-select", "safe-search-select",
        ]
        recall_ids = [
            "default-recall-mode-select", "recall-preset-select", "graph-recall-mode-select",
        ]
        self.assertIn("WEB SEARCH", organizer)
        self.assertIn("MEMORY RECALL", organizer)
        self.assertEqual([organizer.index(value) for value in search_ids], sorted(organizer.index(value) for value in search_ids))
        self.assertEqual([organizer.index(value) for value in recall_ids], sorted(organizer.index(value) for value in recall_ids))
        self.assertLess(organizer.index(search_ids[-1]), organizer.index(recall_ids[0]))

    def test_cache_version_includes_network_ui_release(self):
        self.assertEqual(self.html.count("g22-20260811-ai-memory-compression"), 2)

    def test_anki_daily_preset_is_opt_in_and_read_only(self):
        self.assertIn('id="new-checkin-task-preset"', self.html)
        self.assertIn('<option value="">Choose a preset task...</option>', self.html)
        self.assertIn('option value="anki"', self.html)
        self.assertIn("task.completion_mode === 'automatic'", self.javascript)
        self.assertIn('disabled aria-label="Automatically verified by Anki"', self.javascript)
        self.assertIn("async function addCheckinPresetTask()", self.javascript)

    def test_capacity_governance_is_visible_and_recoverable(self):
        self.assertIn("function showMemoryMaintenanceNotice", self.javascript)
        self.assertIn("/api/kb/maintenance/status", self.javascript)
        self.assertIn("function openColdArchiveViewer", self.javascript)
        self.assertIn("Restore to Archive", self.javascript)
        self.assertIn("memory-maintenance-toast", self.css)
        self.assertIn("This engine uses no LLM tokens", self.javascript)

    def test_ai_compression_requires_scope_estimate_and_consent(self):
        self.assertIn('id="ai-memory-compression-toggle"', self.html)
        self.assertIn('id="ai-memory-compression-modal"', self.html)
        self.assertIn("MEMORY CAPACITY", self.javascript)
        self.assertIn("function openAiMemoryCompressionPlanner", self.javascript)
        self.assertIn("/api/kb/ai-compression/preview", self.javascript)
        self.assertIn("/api/kb/ai-compression/execute", self.javascript)
        self.assertIn("Estimated total", self.javascript)
        self.assertIn("ai-compression-consent", self.javascript)
        self.assertIn('@app.post("/api/kb/ai-compression/execute")', self.server)
        self.assertIn('"response_format": {"type": "json_object"}', self.server)
        self.assertIn("apply_ai_compression_plan", self.server)


if __name__ == "__main__":
    unittest.main()
