"""Listener role (memory intake): one-conversation-one-memory state machine.

Pinned contracts:
- at most ONE clarification turn, then a proposal is forced;
- confirm stores the memory and locks the conversation (derived from the
  session file - truncation auto-unlocks);
- editing the conversation then re-confirming REPLACES the same memory id
  (sidecar link), never creates a duplicate;
- direct post-confirm edits update the linked memory;
- the archive layer is never exposed; LLM garbage degrades to safe defaults.
"""

import json
import threading

import pytest

from saya_app.services.listener import ListenerError, ListenerService


class _FakeMemoryManager:
    def __init__(self, root):
        self.root = root
        self.next_id = 1

    def create_session(self, make_current=True):
        sid = f"session_listener_{self.next_id:03d}.jsonl"
        self.next_id += 1
        (self.root / sid).write_text("", encoding="utf-8")
        return sid

    def load_session_full(self, session_id, make_current=False):
        rows = []
        for line in (self.root / session_id).read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows

    def save_message(self, role, content, translation=None, metadata=None, session_id=None):
        entry = {"role": role, "content": content}
        if isinstance(metadata, dict):
            entry.update(metadata)
        with (self.root / session_id).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def truncate_session(self, index, session_id=None):
        rows = self.load_session_full(session_id)
        (self.root / session_id).write_text(
            "\n".join(json.dumps(r, ensure_ascii=False) for r in rows[:index]) + "\n",
            encoding="utf-8")
        return True


class _FakeKB:
    def __init__(self,base_dir):
        self.base_dir=str(base_dir)
        base_dir.mkdir()
        self._memory_write_lock=threading.RLock()
        from saya_app.services.memory_use import MemoryUseState
        self.memory_use=MemoryUseState(base_dir)
        self.memories = {}
        self.counter = 0

    def add_memory(self, content, **kwargs):
        self.counter += 1
        memory = {"id": f"m-{self.counter}", "content": content}
        memory.update(kwargs)
        memory.update(kwargs.get('metadata') or {})
        self.memories[memory["id"]] = memory
        return memory

    def _find_memory(self,memory_id):return self.memories.get(memory_id)

    def update_memory(self, memory_id, updates,*,expected_version=None,write_check=None):
        memory = self.memories.get(str(memory_id))
        if not memory:
            return False
        from saya_app.services.memory_versions import check_version
        check_version(memory,expected_version)
        if write_check:write_check()
        memory.update(updates)
        return True


@pytest.fixture()
def env(tmp_path):
    mm = _FakeMemoryManager(tmp_path)
    kb = _FakeKB(tmp_path/'knowledge_base')
    answers = []

    def llm(system, user_content, credentials):
        return answers.pop(0)

    service = ListenerService(
        memory_manager=mm, knowledge_base=kb, llm_call=llm,
        state_file=tmp_path / "listener_state.json",
    )
    return {"mm": mm, "kb": kb, "svc": service, "answers": answers, "root": tmp_path}


def _open(env):
    return env["svc"].open_session()


# ------------------------------------------------------------ question flow

def test_first_turn_can_ask_one_batched_question(env):
    sid = _open(env)
    env["answers"].append(json.dumps({"type": "question",
                                      "text": "大概几点开始的？\n当时和谁在一起？"}))
    result = env["svc"].handle_message(sid, "上周爬山特别累但是很开心")
    assert result["type"] == "question"
    assert "几点" in result["text"]


def test_second_turn_forces_a_proposal_even_if_model_wants_to_ask(env):
    sid = _open(env)
    env["answers"].append(json.dumps({"type": "question", "text": "什么时候的事？"}))
    env["svc"].handle_message(sid, "上次那件事我想记下来")
    # model misbehaves and asks again - the service must override
    env["answers"].append(json.dumps({"type": "question", "text": "还有呢？"}))
    result = env["svc"].handle_message(sid, "上周六下午和小明一起")
    assert result["type"] == "proposal"
    assert result["proposal"]["content"]


# ------------------------------------------------------------ proposal rules

def test_proposal_normalizes_layer_and_category(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "偏好", "content": "Doctor 喜欢爬山",
        "category": "nonsense", "layer": "archive"}))
    result = env["svc"].handle_message(sid, "我喜欢爬山")
    assert result["proposal"]["layer"] == "semantic"   # archive not exposed
    assert result["proposal"]["category"] == "general"


def test_invalid_llm_json_raises_user_facing_error(env):
    sid = _open(env)
    env["answers"].append("我觉得你应该记住这个（不是JSON）")
    with pytest.raises(ListenerError):
        env["svc"].handle_message(sid, "记点东西")


# ------------------------------------------------------------ confirm / lock

def test_confirm_locks_and_stores_then_rejects_new_messages(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "爬山", "content": "Doctor 喜欢爬山",
        "category": "preference", "layer": "semantic"}))
    env["svc"].handle_message(sid, "我喜欢爬山")
    result = env["svc"].confirm(sid, "Doctor 喜欢周末爬山放松", "semantic",
                                category="preference", title="爬山偏好")
    assert result["type"] == "confirmed" and result["locked"]
    memory_id = result["memory_id"]
    assert env["kb"].memories[memory_id]["source"] == "listener"
    locked = env["svc"].handle_message(sid, "还想再记一条")
    assert locked["type"] == "locked"
    with pytest.raises(ListenerError):
        env["svc"].confirm(sid, "再来一条", "semantic")


# --------------------------------------------------- edit-back replace flow

def test_edit_truncation_unlocks_and_next_confirm_replaces_same_memory(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "偏好", "content": "Doctor 喜欢爬山",
        "category": "preference", "layer": "semantic"}))
    env["svc"].handle_message(sid, "我喜欢爬山")
    first = env["svc"].confirm(sid, "Doctor 喜欢爬山", "semantic", "preference")
    memory_id = first["memory_id"]
    assert len(env["kb"].memories) == 1

    # User edits an earlier message -> the web UI truncates the session file.
    entries = env["mm"].load_session_full(sid)
    env["mm"].truncate_session(1, session_id=sid)      # keep only the user turn

    state = env["svc"].derive_state(sid)
    assert state["locked"] is False                     # lock was derived
    assert state["linked"] is True                      # link survived

    env["answers"].append(json.dumps({
        "type": "proposal", "title": "偏好", "content": "Doctor 非常喜欢高海拔徒步",
        "category": "preference", "layer": "core"}))
    env["svc"].handle_message(sid, "我说的是高海拔徒步，这是我的核心爱好")
    second = env["svc"].confirm(sid, "Doctor 非常喜欢高海拔徒步，是核心爱好",
                                "core", "preference")
    assert second["action"] == "updated"
    assert second["memory_id"] == memory_id             # SAME memory, replaced
    assert len(env["kb"].memories) == 1                  # no duplicate
    assert env["kb"].memories[memory_id]["content"] == "Doctor 非常喜欢高海拔徒步，是核心爱好"
    assert env["kb"].memories[memory_id]["memory_layer"] == "core"


def test_deleting_session_entries_never_deletes_the_memory(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "x", "content": "记忆内容",
        "category": "general", "layer": "semantic"}))
    env["svc"].handle_message(sid, "记一下")
    result = env["svc"].confirm(sid, "记忆内容", "semantic")
    memory_id = result["memory_id"]
    # wipe the whole conversation file (user deleted the dialog)
    (env["root"] / sid).write_text("", encoding="utf-8")
    assert memory_id in env["kb"].memories               # memory survives


def test_direct_post_confirm_edit_updates_linked_memory(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "x", "content": "初版",
        "category": "general", "layer": "semantic"}))
    env["svc"].handle_message(sid, "记一下")
    first = env["svc"].confirm(sid, "初版", "semantic")
    updated = env["svc"].update_saved(sid, "改过的内容", "core", "identity")
    assert updated["type"] == "updated"
    assert updated["memory_id"] == first["memory_id"]
    assert env["kb"].memories[first["memory_id"]]["content"] == "改过的内容"
    assert env["kb"].memories[first["memory_id"]]["memory_layer"] == "core"


def test_confirm_validates_layer_choices(env):
    sid = _open(env)
    env["answers"].append(json.dumps({
        "type": "proposal", "title": "x", "content": "内容",
        "category": "general", "layer": "semantic"}))
    env["svc"].handle_message(sid, "记一下")
    with pytest.raises(ListenerError):
        env["svc"].confirm(sid, "内容", "archive")       # never exposed
    with pytest.raises(ListenerError):
        env["svc"].confirm(sid, "  ", "semantic")        # empty content
