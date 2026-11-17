"""Offline tests for game conversation storage separation and archiving
(2026-09 plan: per-join session files + archive into the main memory)."""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()
os.environ["SAYA_MC_BOT_CONFIG"] = tmp.name

from saya_app.services.conversation import (  # noqa: E402
    SESSION_ID,
    ConversationService,
)

# --- fake memory manager + knowledge base ------------------------------------
base_dir = tempfile.mkdtemp(prefix="saya_sessions_")


class FakeMemory:
    def __init__(self, base):
        self.base_dir = base
        self.saved = []

    def _safe_session_path(self, session_id):
        if not session_id or session_id != os.path.basename(session_id):
            return None
        if not session_id.startswith("session_") or not session_id.endswith(".jsonl"):
            return None
        return os.path.join(self.base_dir, session_id)

    def load_session(self, session_id, make_current=True):
        path = self._safe_session_path(session_id)
        if not path or not os.path.exists(path):
            return []
        with open(path, "r", encoding="utf-8") as handle:
            return [
                f"{'Doctor' if json.loads(line).get('role') == 'user' else 'Saya'}: "
                f"{json.loads(line).get('content')}"
                for line in handle if line.strip()
            ]

    def load_session_full(self, session_id, make_current=True):
        path = self._safe_session_path(session_id)
        if not path or not os.path.exists(path):
            return []
        items = []
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    items.append(json.loads(line))
        return items

    def save_message(self, role, content, translation=None, metadata=None, session_id=None):
        self.saved.append((role, content, dict(metadata or {}), session_id))
        path = self._safe_session_path(session_id or "session_default.jsonl")
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"role": role, "content": content}, ensure_ascii=False) + "\n")


class FakeKB:
    def __init__(self):
        self.memories = []

    def add_memory(self, content, title=None, source="manual", category=None, tags=None,
                   confidence=None, is_important=False, source_detail=None, **kwargs):
        memory = {
            "id": f"mem-{len(self.memories) + 1}",
            "title": title,
            "content": content,
            "source": source,
            "category": category,
            "tags": tags,
            "confidence": confidence,
            "is_important": is_important,
            "source_detail": source_detail,
        }
        self.memories.append(memory)
        return memory

    def get_relevant_memories(self, *args, **kwargs):
        return []


service = ConversationService()
fake_memory = FakeMemory(base_dir)
fake_kb = FakeKB()
service._memory = lambda: fake_memory
service._knowledge_base = lambda: fake_kb


def write_session(name, rows):
    with open(os.path.join(base_dir, name), "w", encoding="utf-8") as handle:
        for role, content in rows:
            handle.write(json.dumps({
                "role": role, "content": content, "time": "2026-09-03 05:00:00",
                "source": "minecraft",
            }, ensure_ascii=False) + "\n")


# 1. no sessions yet -> archive refuses cleanly
result = service.archive_game_session()
assert result == {"archived": False, "reason": "没有可归档的游戏会话"}, result

# 2. two game sessions -> listed newest first
write_session("session_mc_old_127_0_0_1_1111_01.jsonl", [
    ("user", "帮我做把木剑"), ("ai", "好，我来准备材料。"),
])
import time as time_mod  # noqa: E402

time_mod.sleep(0.05)
write_session("session_mc_new_127_0_0_1_1111_02.jsonl", [
    ("user", "跟着我"), ("ai", "嗯，我会一直跟着你。"),
    ("user", "做得不错"), ("ai", "谢谢主人。"),
])
os.utime(os.path.join(base_dir, "session_mc_old_127_0_0_1_1111_01.jsonl"), (1, 1))

sessions = service.list_game_sessions()
assert [s["id"] for s in sessions] == [
    "session_mc_new_127_0_0_1_1111_02.jsonl", "session_mc_old_127_0_0_1_1111_01.jsonl",
], sessions
assert sessions[0]["entries"] == 4
print("game session listing: OK")

# 3. unified session files are NOT considered game sessions
write_session(SESSION_ID, [("user", "网页聊天"), ("ai", "你好")])
assert all("unified" not in s["id"] for s in service.list_game_sessions())
print("unified session excluded from game list: OK")

# 4. archive the newest session: memory entry + file moved to archived/
result = service.archive_game_session()
assert result["archived"] is True and result["session_id"] == "session_mc_new_127_0_0_1_1111_02.jsonl"
assert result["turns"] == 2 and result["file_moved_to_archived"] is True
assert result["memory_id"] == "mem-1"
archived_mem = fake_kb.memories[0]
assert archived_mem["source"] == "session_archive"
assert archived_mem["category"] == "conversation_summary"
assert "session_archive" not in result["digest"]  # digest is human text
assert "跟着我" in result["digest"]
assert not os.path.exists(os.path.join(base_dir, "session_mc_new_127_0_0_1_1111_02.jsonl"))
assert os.path.exists(os.path.join(base_dir, "archived", "session_mc_new_127_0_0_1_1111_02.jsonl"))
print("archive to main memory + file move: OK")

# 4b. merge two sessions of the same world into the newest one
write_session("session_mc_extra_127_0_0_1_1111_03.jsonl", [
    ("user", "再来一组石头"), ("ai", "好，我去采集。"),
])
result = service.merge_game_sessions([
    "session_mc_old_127_0_0_1_1111_01.jsonl",
    "session_mc_extra_127_0_0_1_1111_03.jsonl",
])
assert result["merged"] is True and result["entries"] == 4, result
assert result["target"] == "session_mc_extra_127_0_0_1_1111_03.jsonl", result
assert os.path.exists(os.path.join(base_dir, "archived", "session_mc_old_127_0_0_1_1111_01.jsonl"))
print("session merge: OK")

# 4c. archive stores an AI-generated (or fallback) summary and raises the
# memory importance level (is_important)
result = service.archive_game_session("session_mc_extra_127_0_0_1_1111_03.jsonl")
assert result["archived"] is True, result
memory = next(m for m in fake_kb.memories if m["id"] == result["memory_id"])
assert memory["is_important"] is True, memory          # importance raised one level
assert memory["source"] == "session_archive"
assert memory["category"] == "conversation_summary"
assert memory["source_detail"]["ai_summary"] in (True, False)  # AI when API configured
print("archive importance + AI-summary path: OK")

# 5. per-session recording: game chat goes to its own file, not the unified one
import saya_app.services.minecraft_mineflayer as mm  # noqa: E402

sent = []
mm.minecraft_bot_service._send = lambda payload, process=None: sent.append(payload) or True
mm.minecraft_bot_service.state["running"] = True
game_session = mm.minecraft_bot_service.current_game_session_id()

service2 = ConversationService()
service2._memory = lambda: fake_memory
service2._knowledge_base = lambda: fake_kb
service2._record = ConversationService._record.__get__(service2)  # use the real recorder

result = service2.handle_message(
    "minecraft", "Doctor", "过来。",
    context={"session_id": game_session, "mc_status": {}},
)
assert result["commands"] == ["come"], result
saved = [row for row in fake_memory.saved if row[3] == game_session]
assert saved and saved[0][0] == "user" and saved[0][1] == "过来。", saved
assert all(row[3] != SESSION_ID for row in saved), saved
assert os.path.exists(os.path.join(base_dir, game_session))

# web/desktop chats (no session_id in context) still land in the unified file
service2._send_command = lambda command, origin="saya": True
result = service2.handle_message("desktop", "Doctor", "跳一下", context={"mc_status": {}})
assert result["commands"] == ["jump"], result
saved_unified = [row for row in fake_memory.saved if row[3] == SESSION_ID]
assert saved_unified and saved_unified[-1][1] == "好，跳！", saved_unified
print("per-session recording (game vs unified): OK")

# 6. invalid session id rejected on archive
bad = service.archive_game_session("session_../evil.jsonl")
assert bad["archived"] is False and bad["reason"] == "会话 id 不合法"
print("archive id validation: OK")

mm.minecraft_bot_service.state["running"] = False

Path(tmp.name).unlink(missing_ok=True)
print("\nGame session storage + archive smoke tests passed.")
