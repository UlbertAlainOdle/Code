"""Non-secret QQ controls persisted independently of connection credentials."""

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from saya_app.services.state_storage import atomic_save_json


class QQControls(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    shared_memory: bool
    group_mention_only: bool
    auto_accept_owner_invites: bool
    owner_group_auto_enable: bool
    cooldown_seconds: float = Field(ge=0, le=3600)
    history_turns: int = Field(ge=1, le=50)
    reply_timeout: float = Field(ge=1, le=900)
    thinking_effort: Literal["auto", "none", "low", "high", "max"] = "auto"
    agent_mode: bool = False
    recall_mode: Literal["off", "auto", "on"] = "auto"
    recall_memory_count: int = Field(default=30, ge=1, le=200)
    memory_token_budget: int = Field(default=12000, ge=512, le=32000)
    graph_recall_mode: Literal["off", "auto", "deep"] = "auto"
    memory_write_enabled: bool = True
    web_search_mode: Literal["off", "auto", "force"] = "off"
    max_output_tokens: int = Field(default=8192, ge=512, le=32768)
    delivery_mode: Literal['chat', 'legacy'] = 'chat'
    bubble_strategy: Literal['length', 'natural'] = 'length'
    reply_reference: bool = True
    bubble_chars: int = Field(default=240, ge=100, le=1500)
    reply_max_messages: int = Field(default=3, ge=1, le=6)
    task_control_enabled: bool = False
    memory_management_enabled: bool = False


CONTROL_FIELDS = frozenset(QQControls.model_fields)
DELIVERY_FIELDS = frozenset({'delivery_mode','bubble_strategy','reply_reference','bubble_chars','reply_max_messages'})
SHARED_FIELDS = frozenset({"thinking_effort", "agent_mode", "recall_mode", "recall_memory_count",
    "memory_token_budget", "graph_recall_mode", "memory_write_enabled", "web_search_mode", "max_output_tokens", "history_turns"})


class QQControlsStore:
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        return self.load_document()["settings"]

    def load_document(self):
        if not self.path.exists():
            return {"settings": {}, "inherit_fields": []}
        # Fail closed for invalid overrides rather than silently enabling an old
        # environment value after the user disabled the bridge in the UI.
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid QQ controls")
        raw = dict(raw)
        version = raw.pop("schema_version", 1)
        inherited = raw.pop("inherit_fields", [])
        if version not in {1, 2} or not isinstance(inherited, list) or any(key not in SHARED_FIELDS for key in inherited):
            raise ValueError("invalid QQ controls schema")
        return {"settings": QQControls.model_validate(raw).model_dump(), "inherit_fields": sorted(set(inherited))}

    def save(self, values, inherit_fields=None):
        controls = QQControls.model_validate(values).model_dump()
        if inherit_fields is None:
            inherit_fields = self.load_document()["inherit_fields"] if self.path.exists() else []
        if any(key not in SHARED_FIELDS for key in inherit_fields):
            raise ValueError("setting cannot inherit")
        document = dict(controls)
        if inherit_fields:
            document.update(schema_version=2, inherit_fields=sorted(set(inherit_fields)))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_json(self.path, document)
        return controls


class QQSearchCredentialsStore:
    def __init__(self, path, canonical_path=None):
        self.path = Path(path)
        self.canonical_path = Path(canonical_path) if canonical_path else None

    def key(self):
        try:
            if self.canonical_path:
                from .credentials_storage import read_credentials
                canonical = read_credentials(self.canonical_path)
                if "brave_search_api_key" in canonical:
                    return str(canonical["brave_search_api_key"] or "")
            return str(json.loads(self.path.read_text(encoding="utf-8")).get("api_key") or "")
        except (OSError, ValueError):
            return ""

    def save(self, key):
        if not isinstance(key, str) or not 1 <= len(key.strip()) <= 512 or any(c in key for c in "\r\n\x00"):
            raise ValueError("请填写有效的网页搜索密钥。")
        if self.canonical_path:
            from .credentials_storage import update_credentials
            update_credentials(self.canonical_path, {"brave_search_api_key": key.strip()})
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_json(self.path, {"api_key": key.strip()})


class QQControlsBusy(ValueError):
    pass


class QQControlsConflict(ValueError):
    pass


class QQControlsUnsupported(ValueError):
    pass


class QQControlsUpdate(QQControls):
    revision: str | None = Field(default=None, min_length=64, max_length=64, pattern=r"^[0-9a-f]+$")
    inherit_fields: list[str] | None = Field(default=None, max_length=10)


COMMAND_SETTINGS = {
    "memory": "shared_memory", "记忆": "shared_memory",
    "mention": "group_mention_only", "群艾特": "group_mention_only",
    "invites": "auto_accept_owner_invites", "自动入群": "auto_accept_owner_invites",
    "owner_group": "owner_group_auto_enable", "自动授权": "owner_group_auto_enable",
    "cooldown": "cooldown_seconds", "冷却": "cooldown_seconds",
    "history": "history_turns", "上下文": "history_turns",
    "timeout": "reply_timeout", "超时": "reply_timeout",
    "thinking": "thinking_effort", "思考": "thinking_effort",
    "tools": "agent_mode", "工具": "agent_mode",
    "recall": "recall_mode", "召回": "recall_mode",
    "memories": "recall_memory_count", "记忆数量": "recall_memory_count",
    "budget": "memory_token_budget", "记忆预算": "memory_token_budget",
    "graph": "graph_recall_mode", "关联记忆": "graph_recall_mode",
    "save": "memory_write_enabled", "保存记忆": "memory_write_enabled",
    "search": "web_search_mode", "搜索": "web_search_mode",
    "output": "max_output_tokens", "输出预算": "max_output_tokens",
    "delivery": "delivery_mode", "消息模式": "delivery_mode",
    "split": "bubble_strategy", "分消息": "bubble_strategy",
    "quote": "reply_reference", "引用": "reply_reference",
    "bubble": "bubble_chars", "单条长度": "bubble_chars",
    "bubbles": "reply_max_messages", "每轮条数": "reply_max_messages",
}
BOOL_CONTROLS = frozenset({"shared_memory", "group_mention_only", "auto_accept_owner_invites", "owner_group_auto_enable", "agent_mode", "memory_write_enabled", "reply_reference"})
ENUM_CONTROLS = {
    "thinking_effort": {"auto", "none", "low", "high", "max"},
    "recall_mode": {"off", "auto", "on"},
    "graph_recall_mode": {"off", "auto", "deep"},
    "web_search_mode": {"off", "auto", "force"},
    "delivery_mode": {"chat", "legacy"},
    "bubble_strategy": {"length", "natural"},
}


def parse_command_setting(name, value):
    key = COMMAND_SETTINGS.get(name.lower(), name.lower())
    if key == "enabled":
        raise ValueError("QQ 总开关请到网页 Settings → QQ 接入修改；暂停后 QQ 无法接收恢复命令。")
    if key in {'task_control_enabled','memory_management_enabled'}:
        raise ValueError('该管理开关请在网页 QQ 接入设置中修改。')
    if key not in CONTROL_FIELDS:
        raise ValueError("未知设置，请发送 /settings 查看可修改项；账号、密钥和模型不能通过聊天修改。")
    if value.lower() in {"inherit", "跟随", "继承"}:
        if key not in SHARED_FIELDS:
            raise ValueError("此 QQ 专属设置不能继承全局。")
        return key, None
    if key in ENUM_CONTROLS:
        value = {"自动": "auto", "关闭": "off", "开启": "on", "深度": "deep", "强制": "force"}.get(value, value.lower())
        if key == "thinking_effort":
            value = {"off": "none", "on": "high", "deep": "high", "轻度": "low", "最高": "max"}.get(value, value)
        if key == "web_search_mode" and value == "on": value = "auto"
        if value not in ENUM_CONTROLS[key]:
            raise ValueError("可用值：" + "/".join(sorted(ENUM_CONTROLS[key])))
        return key, value
    if key in BOOL_CONTROLS:
        choices = {"on": True, "off": False, "true": True, "false": False, "1": True, "0": False,
                   "开": True, "关": False, "开启": True, "关闭": False}
        if value.lower() not in choices:
            raise ValueError("开关值请使用 on/off 或 开启/关闭。")
        return key, choices[value.lower()]
    try:
        return key, float(value) if key in {"cooldown_seconds", "reply_timeout"} else int(value)
    except ValueError:
        raise ValueError("参数必须为数字，上下文轮数必须为整数。") from None
