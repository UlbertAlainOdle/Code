from pathlib import Path
import tempfile
import unittest

from core_services import KnowledgeBase
from saya_app.services.memory_v4 import AgentModelSettings, LocalLlamaAgentStep


class MemoryV4AgentAdapterTests(unittest.TestCase):
    def test_agent_loop_is_disabled_by_default(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-disabled-"))
        kb = KnowledgeBase(str(root))
        calls = []
        result = kb.run_memory_v4_agent_loop("disabled-turn", lambda context: calls.append(context))
        self.assertEqual(result["status"], "disabled")
        self.assertEqual(calls, [])

    def test_agent_loop_uses_safe_memory_capability_and_returns_public_events(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-enabled-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"memory_v4_write_enabled": True, "memory_v4_read_enabled": True, "agent_loop_enabled": True})
        written = kb.process_memory_write_v4("记住：我偏好 agent loop 的简洁回答", turn_id="agent-write")
        self.assertEqual(written["status"], "success")

        steps = []

        def model_step(context, capabilities):
            steps.append((context, capabilities))
            if len(steps) == 1:
                return {"tool_calls": [{"id": "memory-call", "name": "memory.query", "arguments": {"query": "简洁回答"}}]}
            return {"final_answer": "已读取相关记忆。"}

        result = kb.run_memory_v4_agent_loop(
            "agent-turn", model_step,
            initial_context=[{"role": "user", "content": "请按我的偏好回答"}],
        )
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "已读取相关记忆。")
        self.assertEqual(len(result["tool_results"]), 1)
        self.assertEqual(result["tool_results"][0]["status"], "success")
        self.assertTrue(any(event["type"] == "tool.call.completed" for event in result["events"]))
        self.assertTrue(all(event.get("private_payload") is None for event in result["events"]))
        self.assertTrue(all("system_prompt" not in str(event) for event in result["events"]))

    def test_http_safe_adapter_requires_server_registered_model_step(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-http-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"agent_loop_enabled": True})
        self.assertEqual(kb.run_memory_v4_agent_once("http-unconfigured")["status"], "unconfigured")
        kb.memory_v4_model_step = lambda context: {"final_answer": "server adapter answer"}
        result = kb.run_memory_v4_agent_once("http-configured", policy_options={"max_steps": 1})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "server adapter answer")

    def test_explicit_chat_opt_in_can_run_without_global_rollout_flag(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-opt-in-"))
        kb = KnowledgeBase(str(root))
        kb.memory_v4_model_step = lambda context: {"final_answer": "opted in"}
        result = kb.run_memory_v4_agent_once("opt-in", request_opt_in=True)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["answer"], "opted in")

    def test_http_policy_does_not_widen_private_tools_without_server_opt_in(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-private-policy-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"agent_loop_enabled": True, "memory_v4_write_enabled": True, "memory_v4_read_enabled": True})
        kb.process_memory_write_v4("记住：私有策略测试", turn_id="private-policy-write")
        seen = []

        def model_step(context, capabilities):
            seen.append({item["name"]: item["availability"] for item in capabilities})
            return {"tool_calls": [{"name": "memory.query", "arguments": {"query": "私有策略"}}]}

        kb.memory_v4_model_step = model_step
        result = kb.run_memory_v4_agent_once(
            "private-policy-turn", policy_options={"allowed_names": ["memory.query"], "max_steps": 1},
        )
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["tool_results"][0]["status"], "disabled")
        self.assertNotIn("memory.query", seen[0])

    def test_persisted_private_agent_consent_allows_remote_memory_tools(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-private-consent-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"memory_v4_agent_allow_private": "false"})
        self.assertFalse(kb.settings["memory_v4_agent_allow_private"])
        kb.update_settings({
            "memory_v4_agent_allow_private": True,
            "memory_v4_read_enabled": True,
            "daily_state_v2_enabled": True,
        })
        policy = kb.memory_v4_agent_policy()
        self.assertIn("memory.query", policy.allowed_names)
        self.assertIn("memory.read", policy.allowed_names)
        self.assertIn("daily.query", policy.allowed_names)

    def test_server_owned_local_agent_can_choose_private_memory_tools(self):
        root = Path(tempfile.mkdtemp(prefix="saya-memory-v4-agent-local-policy-"))
        kb = KnowledgeBase(str(root))
        kb.update_settings({"agent_loop_enabled": True, "memory_v4_write_enabled": True, "memory_v4_read_enabled": True})
        kb.memory_v4_model_step = LocalLlamaAgentStep(lambda *args, **kwargs: {"choices": [{"text": "{}"}]}, AgentModelSettings("local"))
        policy = kb.memory_v4_agent_policy({"allowed_names": ["memory.query"]})
        self.assertIn("memory.query", policy.allowed_names)


if __name__ == "__main__":
    unittest.main()
