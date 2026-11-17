"""Prompt personas, persisted locally and independent of Saya's tool agent."""

import copy
import json
import re
import threading
from uuid import uuid4

from saya_app.config import KNOWLEDGE_BASE_DIR
from saya_app.services.state_storage import atomic_save_json


SAYA = {"id": "saya", "name": "Saya", "prompt": "", "builtin": True}
MAX_AGENTS = 12


class AgentStore:
    def __init__(self, path=None):
        self.path = path or KNOWLEDGE_BASE_DIR / "chat_agents.json"
        self.lock = threading.RLock()

    def list(self):
        with self.lock:
            if not self.path.exists():
                return [dict(SAYA)]
            with self.path.open(encoding="utf-8") as handle:
                agents = json.load(handle)
            if not isinstance(agents, list):
                raise ValueError("智能体设置文件格式错误")
            return [dict(SAYA)] + copy.deepcopy(agents)

    def save(self, name, prompt, agent_id=None):
        name, prompt = name.strip(), prompt.strip()
        if not re.fullmatch(r"[\w\-\u4e00-\u9fff]{1,32}", name):
            raise ValueError("名称须为 1–32 个中文、字母、数字、下划线或连字符")
        if not prompt or len(prompt) > 20000:
            raise ValueError("提示词须为 1–20000 个字符")
        if agent_id == "saya":
            raise ValueError("请在 System Prompt / Saya Style 中修改 Saya")
        with self.lock:
            agents = self.list()[1:]
            if agent_id and not any(a["id"] == agent_id for a in agents):
                raise KeyError(agent_id)
            if any(a["name"].casefold() == name.casefold() and a["id"] != agent_id
                   for a in [SAYA] + agents):
                raise ValueError("智能体名称不能重复")
            if not agent_id and len(agents) >= MAX_AGENTS:
                raise ValueError(f"最多创建 {MAX_AGENTS} 个自定义智能体")
            value = {"id": agent_id or uuid4().hex, "name": name, "prompt": prompt, "builtin": False}
            agents = [value if a["id"] == agent_id else a for a in agents] if agent_id else agents + [value]
            self.path.parent.mkdir(parents=True, exist_ok=True)
            atomic_save_json(self.path, agents)
            return value

    def delete(self, agent_id):
        if agent_id == "saya":
            raise ValueError("Saya 是默认智能体，不能删除")
        with self.lock:
            agents = self.list()[1:]
            remaining = [a for a in agents if a["id"] != agent_id]
            if len(remaining) == len(agents):
                raise KeyError(agent_id)
            atomic_save_json(self.path, remaining)

    def resolve(self, ids):
        catalog = {a["id"]: a for a in self.list()}
        unique = list(dict.fromkeys(ids))
        if not unique or len(unique) > MAX_AGENTS + 1:
            raise ValueError("请选择 1–13 个智能体")
        if any(agent_id not in catalog for agent_id in unique):
            raise ValueError("所选智能体已被删除，请重新选择")
        return [catalog[agent_id] for agent_id in unique]


def entries_for_agent(items, agent_id):
    """The same persona boundary applies to text, timestamps and summaries."""
    return [item for item in items if isinstance(item, dict) and (
        item.get('role') == 'user' or
        item.get('role') in {'ai', 'assistant'} and item.get('agent_id', 'saya') == agent_id)]


def history_for_agent(items, agent_id):
    """Each persona sees users and its own replies, never another persona as itself."""
    history = []
    for item in entries_for_agent(items, agent_id):
        if item.get("role") == "user":
            history.append("Doctor: " + str(item.get("content") or ""))
        elif item.get("role") in {"ai", "assistant"} and item.get("agent_id", "saya") == agent_id:
            history.append("Saya: " + str(item.get("content") or ""))
    return history


def persona_prompt(agent, user, history, web_context="", local=False):
    prompt = f"你当前的身份是 {agent['name']}。仅以该身份回答当前用户，不代替其他智能体发言。\n{agent['prompt']}"
    if web_context:
        prompt += "\n\n" + web_context
    if local:
        local_history = [agent['name'] + line[4:] if line.startswith('Saya:') else line for line in history]
        prompt += "\n\n" + "\n".join(local_history) + f"\nDoctor: {user}\n{agent['name']}:"
    return prompt


agent_store = AgentStore()
