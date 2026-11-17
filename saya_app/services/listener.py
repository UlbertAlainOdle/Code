"""Listener role: one-conversation-one-memory intake interviews (2026-09-07).

Design contracts (agreed with Doctor):

- A Listener conversation records EXACTLY ONE memory.  After the confirm
  entry lands in the session file the conversation is LOCKED for further
  input; a new conversation is required for the next memory.
- Saya gets at most ONE clarification turn: the prompt forces every needed
  (simplified, possibly multiple short) questions into that single turn;
  afterwards the model MUST produce a proposal.
- The proposal is editable (content + memory layer core/semantic/episodic;
  the least-important archive layer is never exposed) with Saya's layer
  pre-selected.
- Editing the conversation (the web edit/regenerate path truncates the
  session file) automatically UNLOCKS it - lock state is DERIVED from the
  file, never stored separately.  The produced memory is NOT deleted; the
  session -> memory link (sidecar listener_state.json) makes the NEXT
  confirm REPLACE the same memory id in place.
- Direct edits of the saved memory re-call confirm, which updates the same
  linked memory.

All storage flows through the existing MemoryManager session files and the
KnowledgeBase - no new persistence format beyond the small sidecar map.
"""

from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path

from ..config import KNOWLEDGE_BASE_DIR

STATE_FILE = KNOWLEDGE_BASE_DIR / "listener_state.json"

EXPOSED_LAYERS = ("core", "semantic", "episodic")

LISTENER_SYSTEM_PROMPT = """你是 Saya 的记忆录入助手（Listener 模式）。Doctor 正在专门录入一条长期记忆。

你的输出必须是严格的单个 JSON 对象，两种形态之一：

1）需要补充细节（整个对话只有这一次提问机会）：
{"type": "question", "text": "你的提问"}
规则：把所有需要的细节一次性问清；每个问题要简短直白；需要多个信息就在 text 里并列几个简短问题（用换行分隔）；不要问复杂到需要反复追问的问题——把复杂问题拆成几个简单问题一次问完。禁止寒暄，禁止解释规则。

2）信息已足够，给出记忆提案：
{"type": "proposal", "title": "简短标题(<=20字)", "content": "用第三人称整理好的记忆内容（这条会被存入 Saya 的长期记忆，写成独立可检索的知识条目，包含关键细节：谁/什么/何时/偏好程度等）", "category": "identity|preference|goal|project|health|relationship|schedule|emotion|general 之一", "layer": "core|semantic|episodic"}
layer 判断：core=关于 Doctor 的根本身份/长期不变的核实事；semantic=稳定的知识、偏好、项目事实；episodic=某次具体发生的事件/经历。
判断依据：内容具体完整就直接提案；缺关键细节才提问。不要输出 JSON 以外的任何文字。"""


class ListenerError(Exception):
    """User-facing listener flow error."""


def _extract_json(text):
    matches = re.findall(r"\{.*\}", str(text or ""), re.DOTALL)
    for candidate in reversed(matches):
        try:
            return json.loads(candidate)
        except json.JSONDecodeError:
            continue
    return None


class ListenerService:
    def __init__(self, memory_manager=None, knowledge_base=None, llm_call=None,
                 state_file=None):
        self._memory_manager = memory_manager
        self._knowledge_base = knowledge_base
        self._llm_call = llm_call
        self._state_file = Path(state_file) if state_file else STATE_FILE
        self._lock = threading.RLock()

    # ------------------------------------------------------------ deps
    def memory_manager(self):
        if self._memory_manager is None:
            from core_services import memory_manager  # noqa: WPS433 (lazy)

            self._memory_manager = memory_manager
        return self._memory_manager

    def knowledge_base(self):
        if self._knowledge_base is None:
            from core_services import kb_system  # noqa: WPS433 (lazy)

            self._knowledge_base = kb_system
        return self._knowledge_base

    def _default_llm_call(self, system_prompt, user_content, credentials):
        import requests

        url = str(credentials.get("api_url") or "").rstrip("/")
        if not url.casefold().endswith("/chat/completions"):
            url += "/chat/completions"
        response = requests.post(
            url,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {credentials.get('api_key') or ''}",
            },
            json={
                "model": credentials.get("model"),
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_content},
                ],
                "stream": False,
                "temperature": 0.3,
                "max_tokens": 900,
            },
            timeout=90,
        )
        if response.status_code != 200:
            raise ListenerError(f"模型接口返回 {response.status_code}")
        content = str(
            ((response.json().get("choices") or [{}])[0].get("message") or {}).get("content")
            or ""
        ).strip()
        if not content:
            raise ListenerError("模型返回了空内容")
        return content

    def llm_call(self, system_prompt, user_content, credentials):
        if self._llm_call is not None:
            return self._llm_call(system_prompt, user_content, credentials)
        return self._default_llm_call(system_prompt, user_content, credentials)

    def credentials(self):
        try:
            from .conversation import conversation_service

            return conversation_service.credentials()
        except Exception:
            return {"api_key": "", "api_url": "", "model": ""}

    # ------------------------------------------------------ sidecar state
    def _load_state(self):
        try:
            data = json.loads(self._state_file.read_text(encoding="utf-8"))
            if not isinstance(data,dict):raise ValueError('invalid_listener_state')
            return data
        except FileNotFoundError:
            return {}
        except (OSError,ValueError):
            raise ListenerError('Listener关联文件不可读，未覆盖原文件；请先核对本地文件。') from None

    def _save_state(self, state):
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        from .state_storage import atomic_save_json
        atomic_save_json(self._state_file,state)

    def linked_memory_id(self, session_id):
        from .listener_writes import linked_id
        return linked_id(self,str(session_id))

    def _linked_memory_id(self, session_id):
        entry = self._load_state().get(str(session_id)) or {}
        return entry.get("memory_id") or None

    def register_session(self, session_id):
        with self._lock:
            state = self._load_state()
            state.setdefault(str(session_id), {"created_at": time.time()})
            self._save_state(state)

    def listener_sessions(self):
        return sorted(
            (sid for sid, entry in self._load_state().items() if entry),
        )

    # ---------------------------------------------------- state derivation
    def _entries(self, session_id):
        try:
            items = self.memory_manager().load_session_full(
                str(session_id), make_current=False)
        except Exception:
            items = []
        return [item for item in (items or []) if isinstance(item, dict)]

    def derive_state(self, session_id):
        from .listener_writes import state
        return state(self,str(session_id))

    def _derive_state(self, session_id):
        """(locked, memory_id, question_used, last_proposal).

        Lock is derived purely from the session file: a confirm entry makes
        the conversation locked; editing truncates the file and thereby
        unlocks it again.
        """
        entries = self._entries(session_id)
        question_used = False
        last_proposal = None
        memory_id = None
        locked = False
        for entry in entries:
            marker = str(entry.get("listener") or "")
            if marker == "question":
                question_used = True
            elif marker == "proposal":
                proposal = entry.get("listener_proposal")
                last_proposal = proposal if isinstance(proposal, dict) else None
            elif marker == "confirm":
                locked = True
                memory_id = str(entry.get("listener_memory_id") or "") or None
                proposal = entry.get("listener_proposal")
                if isinstance(proposal, dict):
                    last_proposal = proposal
        if memory_id is None:
            memory_id = self.linked_memory_id(session_id)
        return {
            "locked": locked,
            "memory_id": memory_id,
            "question_used": question_used,
            "proposal": last_proposal,
            "linked": bool(self.linked_memory_id(session_id)),
        }

    # ------------------------------------------------------------- flows
    def open_session(self):
        session_id = self.memory_manager().create_session(make_current=True)
        self.register_session(session_id)
        return session_id

    def _record(self, session_id, role, content, metadata):
        self.memory_manager().save_message(
            role, str(content)[:4000], metadata=metadata, session_id=str(session_id))

    def handle_message(self, session_id, text):
        """One Listener turn: question OR proposal; rejects locked sessions."""
        session_id = str(session_id)
        value = str(text or "").strip()
        if not value:
            raise ListenerError("请输入要记录的内容")
        from .listener_writes import version
        from .memory_versions import MemoryVersionConflict
        kb=self.knowledge_base()
        with kb._memory_write_lock,self._lock:
            state = self.derive_state(session_id)
            if state["locked"]:
                return {"type":"locked","memory_id":state['memory_id'],
                    'pending_operation':state.get('pending_operation'),
                    "message":"此会话已保存或有待核对操作，请核对原结果；记录下一条请开启新的Listener对话。"}
            self._record(session_id,"user",value,{'listener':'input'})
            source_version=version(self,session_id)
        def record_reply(content,metadata):
            with kb._memory_write_lock,self._lock:
                if version(self,session_id)!=source_version:
                    raise MemoryVersionConflict('Listener会话、关联记忆或使用权限已变化，未落盘旧模型结果')
                self._record(session_id,'ai',content,metadata)
                return version(self,session_id)

        question_used = state["question_used"]
        phase = (
            "你已经用掉了唯一的提问机会，现在必须直接给出记忆提案（type=proposal），"
            "基于已有信息把内容整理到最好。"
            if question_used else
            "这是第一轮：判断信息是否足够。够就直接提案；不够就用你唯一的一次提问机会，"
            "把所有缺的细节拆成几个简短问题一次问完。"
        )
        transcript = self._transcript(session_id)
        user_content = (
            f"{phase}\n\n[对话记录]\n{transcript}\n\n[Doctor 刚说的话]\n{value}\n\n请输出 JSON。"
        )
        try:
            answer = self.llm_call(LISTENER_SYSTEM_PROMPT, user_content, self.credentials())
        except ListenerError:
            raise
        except Exception as exc:
            raise ListenerError(f"模型调用失败：{exc}") from exc
        payload = _extract_json(answer)
        if not isinstance(payload, dict):
            raise ListenerError("模型没有返回有效的 JSON 提案")

        kind = str(payload.get("type") or "").lower()
        if kind == "question" and not question_used:
            question_text = str(payload.get("text") or "").strip()
            if not question_text:
                raise ListenerError("模型的问题为空")
            record_reply(question_text, {"listener": "question"})
            return {"type": "question", "text": question_text}

        # Anything else (including a question when the chance is spent) is a
        # proposal - normalize it.  A misbehaving question payload carries no
        # content, so fall back to the user's own words rather than erroring.
        if kind != "proposal" and not str(payload.get("content") or "").strip():
            payload = dict(payload)
            payload["type"] = "proposal"
            payload["content"] = value if not question_used else (
                f"{self._transcript(session_id)[-600:]}\n（Doctor 最新补充：{value}）")
            payload.setdefault("title", value[:20])
            payload.setdefault("layer", "semantic")
            payload.setdefault("category", "general")
        proposal = self._normalize_proposal(payload)
        content_text = f"记忆提案：{proposal['content']}"
        proposal_version=record_reply(content_text, {
            "listener": "proposal",
            "listener_proposal": proposal,
        })
        return {"type":"proposal","proposal":proposal,
                'confirmation_version':proposal_version}

    def _normalize_proposal(self, payload):
        content = str(payload.get("content") or "").strip()
        if not content:
            raise ListenerError("提案缺少记忆内容")
        title = str(payload.get("title") or "").strip()[:40] or content[:20]
        category = str(payload.get("category") or "").strip().lower()
        allowed_categories = {
            "identity", "preference", "goal", "project", "health",
            "relationship", "schedule", "emotion", "general",
        }
        if category not in allowed_categories:
            category = "general"
        layer = str(payload.get("layer") or "").strip().lower()
        if layer not in EXPOSED_LAYERS:
            layer = "semantic"
        return {
            "title": title,
            "content": content[:2000],
            "category": category,
            "layer": layer,
        }

    def _transcript(self, session_id):
        lines = []
        for entry in self._entries(session_id):
            role = str(entry.get("role") or "")
            content = str(entry.get("content") or "").strip()
            if not content or str(entry.get("listener") or "") == "confirm":
                continue
            who = "Doctor" if role == "user" else "Saya"
            lines.append(f"{who}：{content[:300]}")
        return "\n".join(lines[-12:])

    def confirm(self,session_id,content,layer,category=None,title=None,*,expected_version=None,operation_id=None):
        from .listener_writes import commit
        from .memory_versions import MemoryVersionConflict
        try:
            return commit(self,str(session_id),content,layer,category,title,
                          expected_version=expected_version,operation_id=operation_id)
        except MemoryVersionConflict:raise
        except ValueError as error:raise ListenerError(str(error)) from None

    def update_saved(self,session_id,content,layer,category=None,title=None,*,expected_version=None,operation_id=None):
        from .listener_writes import commit
        from .memory_versions import MemoryVersionConflict
        try:
            return commit(self,str(session_id),content,layer,category,title,update=True,
                          expected_version=expected_version,operation_id=operation_id)
        except MemoryVersionConflict:raise
        except ValueError as error:raise ListenerError(str(error)) from None


listener_service = ListenerService()
