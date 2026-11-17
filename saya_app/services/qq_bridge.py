"""Opt-in OneBot 11 HTTP ingress/outbox. No QQ credentials or SDK are required here.

Run one application worker: SQLite owns dedupe/recovery, one async consumer owns
conversation order. A send with an uncertain outcome is never retried blindly.
"""

import asyncio
from contextlib import contextmanager
from dataclasses import asdict, dataclass
import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Literal
import re
import sqlite3
import time
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from saya_app.config import KNOWLEDGE_BASE_DIR
from saya_app.services.qq_controls import QQControls, QQControlsBusy, QQControlsConflict, QQControlsUnsupported, QQControlsStore, QQSearchCredentialsStore, SHARED_FIELDS, parse_command_setting
from saya_app.services.shared_chat_config import SharedChatConfigStore, revision_of
from saya_app.services.qq_lifecycle import InstanceLease
from saya_app.services import qq_delivery
from saya_app.services import qq_quotes
from saya_app.services.qq_tasks import QQTaskRegistry, QQTaskGuard, TaskCancelled, checkpoint_current, effect_current, current_guard, bound_guard


class QQSettings(BaseModel):
    model_config = ConfigDict(frozen=True)
    enabled: bool = False
    bot_id: str = ""
    api_url: str = "http://127.0.0.1:3000"
    access_token: str = ""
    webhook_token: str = ""
    webhook_secret: str = ""
    admin_token: str = ""
    allowed_users: frozenset[str] = frozenset()
    allowed_groups: frozenset[str] = frozenset()
    owner_ids: frozenset[str] = frozenset()
    shared_memory: bool = False
    group_mention_only: bool = True
    auto_accept_owner_invites: bool = False
    owner_group_auto_enable: bool = False
    model: str = "auto"
    cooldown_seconds: float = Field(3, ge=0, le=3600)
    queue_size: int = Field(32, ge=1, le=256)
    max_event_age: int = Field(300, ge=10, le=3600)
    reply_timeout: float = Field(180, ge=1, le=900)
    history_turns: int = Field(12, ge=1, le=50)
    retention_days: int = Field(7, ge=1, le=90)
    chunk_chars: int = Field(1500, ge=100, le=3000)
    max_reply_chars: int = Field(6000, ge=100, le=12000)
    thinking_effort: str = QQControls.model_fields["thinking_effort"].default
    agent_mode: bool = False
    recall_mode: str = "auto"
    recall_memory_count: int = Field(30, ge=1, le=200)
    memory_token_budget: int = Field(12000, ge=512, le=32000)
    graph_recall_mode: str = "auto"
    memory_write_enabled: bool = True
    web_search_mode: str = "off"
    max_output_tokens: int = Field(8192, ge=512, le=32768)
    delivery_mode: str = 'chat'
    bubble_strategy: Literal['length', 'natural'] = 'length'
    reply_reference: bool = True
    bubble_chars: int = Field(default=240, ge=100, le=1500)
    reply_max_messages: int = Field(default=3, ge=1, le=6)
    task_control_enabled: bool = False
    memory_management_enabled: bool = False

    @field_validator("bot_id")
    @classmethod
    def valid_bot(cls, value):
        if value and not re.fullmatch(r"[1-9]\d{0,19}", value):
            raise ValueError("bot_id must be a positive QQ number")
        return value

    @field_validator("allowed_users", "allowed_groups", "owner_ids")
    @classmethod
    def valid_ids(cls, values):
        if any(not re.fullmatch(r"[1-9]\d{0,19}", value) for value in values):
            raise ValueError("QQ allowlists must contain positive numeric IDs")
        return values

    @field_validator("api_url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("api_url must be an HTTP(S) base URL without credentials or query")
        return value.rstrip("/")

    @model_validator(mode="after")
    def ready(self):
        QQControls.model_validate({key: getattr(self, key) for key in QQControls.model_fields})
        if self.enabled:
            if not self.bot_id or not (self.webhook_secret or self.webhook_token):
                raise ValueError("enabled QQ requires bot_id and authenticated webhook")
            if not self.access_token:
                raise ValueError("enabled QQ requires a OneBot access token")
            if not (self.allowed_users or self.allowed_groups or self.owner_ids):
                raise ValueError("enabled QQ requires an allowlist")
        if self.shared_memory and not self.owner_ids:
            raise ValueError("shared memory requires explicit owner_ids")
        return self

    @classmethod
    def from_env(cls):
        values = {}
        for name in cls.model_fields:
            key = "SAYA_QQ_" + name.upper()
            if key in os.environ:
                value = os.environ[key].strip()
                if name in {"allowed_users", "allowed_groups", "owner_ids"}:
                    value = frozenset(re.split(r"[,，\s]+", value)) - {""}
                values[name] = value
        return cls(**values)


@dataclass
class QQMessage:
    key: str
    scope: str
    bot_id: str
    user_id: str
    group_id: str
    message_id: str
    text: str
    shared: bool
    kind: str = "message"
    request_id: str = ""
    reply_to: str = ""
    sender_name: str = ""
    sender_nickname: str = ""
    sent_at: float = 0
    previous_conversation_at: float = 0
    reply_wake_revision: int = 0
    reply_wake_expires: float = 0
    media_types: tuple = ()
    media_only: bool = False
    forward_present: bool = False
    forward_only: bool = False


def qq_id(value):
    value = str(value or "")
    return value if re.fullmatch(r"[1-9]\d{0,19}", value) else ""


def cq_unescape(text):
    # Decode ampersand last, preventing double-unescaping literal CQ codes.
    return text.replace("&#91;", "[").replace("&#93;", "]").replace("&#44;", ",").replace("&amp;", "&")


def normalize_event(event, settings, now=None):
    """Return (accepted message, reason); ignore non-message events without side effects."""
    now = time.time() if now is None else now
    if not isinstance(event, dict) or event.get("post_type") != "message":
        return None, "not_message"
    bot, user = qq_id(event.get("self_id")), qq_id(event.get("user_id"))
    if bot != settings.bot_id or not user or user == bot:
        return None, "wrong_account_or_self"
    try:
        timestamp = float(event["time"])
        if not now - settings.max_event_age <= timestamp <= now + 60:
            return None, "stale_event"
    except (ValueError, TypeError, KeyError):
        return None, "invalid_time"
    message_id = str(event.get("message_id", ""))
    if not re.fullmatch(r"-?\d{1,20}", message_id):
        return None, "invalid_message_id"
    kind = event.get("message_type")
    group = qq_id(event.get("group_id")) if kind == "group" else ""
    if kind == "private":
        # Temporary group-private sessions are not authenticated owner DMs.
        if event.get("sub_type") != "friend":
            return None, "not_friend_private"
        if user not in settings.allowed_users | settings.owner_ids:
            return None, "user_not_allowed"
    elif kind == "group":
        if not group or group not in settings.allowed_groups:
            return None, "group_not_allowed"
        if event.get("anonymous"):
            return None, "anonymous"
    else:
        return None, "unsupported_message_type"
    raw = event.get("message")
    mentioned = False
    reply_to = ''
    parts = []
    if isinstance(raw, str):
        # Strip protocol segments BEFORE entity decoding; never interpret text as CQ.
        def strip_segment(match):
            nonlocal mentioned, reply_to
            if match.group(0) == f"[CQ:at,qq={bot}]":
                mentioned = True
            quoted = re.fullmatch(r'\[CQ:reply,id=(-?[0-9]{1,20})(?:,[^\]]*)?\]',match.group(0))
            if quoted and not reply_to:
                reply_to = quoted.group(1)
            return " "
        parts.append(cq_unescape(re.sub(r"\[CQ:[^\]]*\]", strip_segment, raw)))
    elif isinstance(raw, list) and len(raw) <= 256:
        for segment in raw:
            if not isinstance(segment, dict) or not isinstance(segment.get("data"), dict):
                continue
            if segment.get("type") == "text":
                parts.append(str(segment["data"].get("text", "")))
            elif segment.get("type") == "at" and str(segment["data"].get("qq")) == bot:
                mentioned = True
            elif segment.get('type') == 'reply' and not reply_to:
                reply_to = qq_quotes.message_id(segment['data'].get('id'))
    else:
        return None, "invalid_message"
    if group and settings.group_mention_only and not mentioned:
        return None, "not_mentioned"
    text = "".join(parts).strip()
    from .qq_media import media_types
    media = media_types(raw)
    from .qq_forward_input import forward_present
    forward_hint=forward_present(raw)
    forward_only=bool(forward_hint and not text and not media)
    if forward_only:text='[QQ分享聊天记录]'
    media_only = bool(media and not text)
    if media_only:
        text = '[QQ附件]'
    if not text:
        return None, "no_text"
    if len(text) > 4000:
        return None, "text_too_long"
    scope = f"{bot}:group:{group}:user:{user}" if group else f"{bot}:private:{user}"
    shared = not group and user in settings.owner_ids and settings.shared_memory
    key = f"{scope}:message:{message_id}"
    sender = event.get('sender') if isinstance(event.get('sender'), dict) else {}
    if sender.get('user_id') is not None and qq_id(sender['user_id']) != user:
        sender = {}
    def label(value):
        return re.sub(r'[\x00-\x1f\x7f\u202a-\u202e\u2066-\u2069]', '', value).strip()[:80] if isinstance(value,str) else ''
    nickname = label(sender.get('nickname'))
    name = (label(sender.get('card')) if group else '') or nickname
    return QQMessage(key, scope, bot, user, group, message_id, text, shared, reply_to=reply_to,
                     sender_name=name, sender_nickname=nickname, sent_at=timestamp,
                     media_types=media, media_only=media_only,forward_present=forward_hint,forward_only=forward_only), "accepted"


class QQStore:
    """Content lives in a separate database, never in the common memory store."""

    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def initialize(self, retention_days):
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS inbox (
                    id INTEGER PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    scope TEXT NOT NULL, payload TEXT NOT NULL, created REAL NOT NULL,
                    state TEXT NOT NULL, error TEXT NOT NULL DEFAULT '');
                CREATE INDEX IF NOT EXISTS inbox_state ON inbox(state, id);
                CREATE TABLE IF NOT EXISTS conversations (
                    scope TEXT PRIMARY KEY, history TEXT NOT NULL,
                    session_id TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS group_access (
                    bot_id TEXT NOT NULL, group_id TEXT NOT NULL, enabled INTEGER NOT NULL,
                    updated REAL NOT NULL, PRIMARY KEY(bot_id, group_id));
                CREATE TABLE IF NOT EXISTS group_invites (
                    id TEXT PRIMARY KEY, event_key TEXT UNIQUE NOT NULL,
                    bot_id TEXT NOT NULL, group_id TEXT NOT NULL, user_id TEXT NOT NULL,
                    flag TEXT NOT NULL, created REAL NOT NULL, state TEXT NOT NULL);
            """)
            db.execute("UPDATE inbox SET state='interrupted', error='restart_during_generation', payload='{}' WHERE state='generating'")
            db.execute("UPDATE inbox SET state='delivery_unknown', error='restart_during_send', payload='{}' WHERE state='sending'")
            db.execute("UPDATE group_invites SET state='result_unknown',flag='' WHERE state IN ('approving','rejecting')")
            if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_control_events'").fetchone():
                for row in db.execute('SELECT event_key,scope,created FROM qq_control_events').fetchall():
                    scope=row['scope']
                    if scope.endswith((':shared',':isolated')):scope=scope.rsplit(':',1)[0]
                    db.execute("INSERT OR IGNORE INTO inbox(event_key,scope,payload,created,state,error) VALUES(?,?,'{}',?,'control','')",
                        (row['event_key'],scope,row['created']))
        qq_delivery.initialize(self)
        self.prune(retention_days)

    def prune(self, retention_days):
        cutoff = time.time() - retention_days * 86400
        with self.connect() as db:
            db.execute("DELETE FROM inbox WHERE created < ? AND state != 'queued'", (cutoff,))
            db.execute("DELETE FROM conversations WHERE updated < ?", (cutoff,))
            db.execute("DELETE FROM group_invites WHERE created < ?", (cutoff,))
            db.execute("UPDATE group_invites SET state='expired',flag='' WHERE state='pending' AND created<?", (time.time() - 86400,))
        qq_delivery.prune(self)

    def queued(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute("SELECT * FROM inbox WHERE state='queued' ORDER BY id")]

    def admit(self, message, settings):
        with self.connect() as db:
            # Serialize check+insert even if an ASGI transport uses multiple threads.
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM inbox WHERE event_key=?", (message.key,)).fetchone():
                return "duplicate", None
            pending = db.execute("SELECT count(*) FROM inbox WHERE state IN ('queued','generating','sending')").fetchone()[0]
            if pending >= settings.queue_size:
                return "busy", None
            recent = db.execute("SELECT 1 FROM inbox WHERE scope=? AND created>? AND state!='control' LIMIT 1",
                                (message.scope, time.time() - settings.cooldown_seconds)).fetchone()
            if recent:
                return "rate_limited", None
            payload = asdict(message)
            if not payload.get('reply_to'):
                payload.pop('reply_to',None)
            cursor = db.execute("INSERT INTO inbox(event_key,scope,payload,created,state) VALUES(?,?,?,?,'queued')",
                                (message.key, message.scope, json.dumps(payload, ensure_ascii=False), time.time()))
            return "queued", cursor.lastrowid

    def set_state(self, row_id, state, error=""):
        with self.connect() as db:
            # Terminal records keep only a dedupe key and content-free diagnostics.
            db.execute("UPDATE inbox SET state=?,error=?,payload=CASE WHEN ? IN ('queued','generating','sending') THEN payload ELSE '{}' END WHERE id=?",
                       (state, error, state, row_id))

    def conversation(self, scope):
        with self.connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE scope=?", (scope,)).fetchone()
            if row:
                return json.loads(row["history"]), row["session_id"]
        return [], "session_qq_" + uuid4().hex + ".jsonl"

    def save_conversation(self, scope, history, session_id):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO conversations VALUES(?,?,?,?)",
                       (scope, json.dumps(history, ensure_ascii=False), session_id, time.time()))

    def counts(self):
        with self.connect() as db:
            return dict(db.execute("SELECT state,count(*) FROM inbox GROUP BY state").fetchall())

    def group_overrides(self, bot_id):
        with self.connect() as db:
            return {row[0]: bool(row[1]) for row in db.execute(
                "SELECT group_id,enabled FROM group_access WHERE bot_id=?", (bot_id,))}

    def set_group(self, bot_id, group_id, enabled):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO group_access VALUES(?,?,?,?)",
                       (bot_id, group_id, int(enabled), time.time()))

    def record_invite(self, event, bot_id):
        event_key = hashlib.sha256((bot_id + ":" + event["flag"]).encode()).hexdigest()
        with self.connect() as db:
            existing = db.execute("SELECT id,state FROM group_invites WHERE event_key=?", (event_key,)).fetchone()
            if existing:
                return existing["id"], False
            if db.execute("SELECT count(*) FROM group_invites WHERE bot_id=? AND state='pending'", (bot_id,)).fetchone()[0] >= 128:
                return None, False
            request_id = uuid4().hex[:12]
            db.execute("INSERT INTO group_invites VALUES(?,?,?,?,?,?,?, 'pending')",
                       (request_id, event_key, bot_id, str(event["group_id"]), str(event["user_id"]), event["flag"], time.time()))
            return request_id, True

    def invites(self, bot_id):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT id,group_id,user_id,created,state FROM group_invites WHERE bot_id=? ORDER BY CASE state WHEN 'pending' THEN 0 ELSE 1 END, created DESC LIMIT 128", (bot_id,))]

    def invite(self, bot_id, request_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM group_invites WHERE id=? AND bot_id=?", (request_id, bot_id)).fetchone()
            return dict(row) if row else None

    def claim_invite(self, bot_id, request_id, state):
        with self.connect() as db:
            return db.execute("UPDATE group_invites SET state=? WHERE bot_id=? AND id=? AND state='pending'",
                              (state, bot_id, request_id)).rowcount == 1

    def finish_invite(self, bot_id, request_id, state, enable_group=False):
        with self.connect() as db:
            # Approval state and reply authorization commit together.
            row = db.execute("SELECT group_id FROM group_invites WHERE bot_id=? AND id=?", (bot_id, request_id)).fetchone()
            if enable_group and row:
                db.execute("INSERT OR REPLACE INTO group_access VALUES(?,?,1,?)", (bot_id, row[0], time.time()))
            db.execute("UPDATE group_invites SET state=?,flag='' WHERE bot_id=? AND id=?", (state, bot_id, request_id))


class OneBotActionRejected(RuntimeError):
    def __init__(self, reply_unsupported=False):
        super().__init__('onebot_action_failed')
        self.reply_unsupported = reply_unsupported


class OneBotClient:
    def __init__(self, settings):
        self.settings = settings
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(15, connect=5), trust_env=False,
                                     follow_redirects=False, headers={"Authorization": "Bearer " + settings.access_token})

    async def call(self, action, payload):
        response = await self.http.post(self.settings.api_url + "/" + action, json=payload)
        response.raise_for_status()
        data = response.json()
        if isinstance(data, dict) and data.get('status') == 'failed':
            detail = str(data.get('message') or data.get('wording') or '').lower()
            unsupported = data.get('retcode') == 100 and any(marker in detail for marker in
                ('unsupported reply', 'reply not supported', '不支持引用', '不支持回复'))
            raise OneBotActionRejected(unsupported)
        if not isinstance(data, dict) or data.get("status") != "ok" or data.get("retcode") != 0:
            raise RuntimeError("onebot_action_failed")
        return data.get("data") if data.get("data") is not None else {}

    async def call_bounded(self,action,payload):
        """Read-only media metadata calls, with a decompressed response cap."""
        if action not in {'get_msg','get_image','get_file','fetch_custom_face_detail','get_forward_msg'}:raise ValueError('unsupported_bounded_action')
        raw=bytearray()
        async with self.http.stream('POST',self.settings.api_url+'/'+action,json=payload) as response:
            response.raise_for_status()
            async for part in response.aiter_bytes(65536):
                checkpoint_current()
                if len(raw)+len(part)>(2*1024*1024 if action=='get_forward_msg' else 512*1024):raise RuntimeError('onebot_metadata_too_large')
                raw.extend(part)
        data=json.loads(raw)
        if not isinstance(data,dict) or data.get('status')!='ok' or data.get('retcode')!=0:
            raise RuntimeError('onebot_metadata_failed')
        return data.get('data') if data.get('data') is not None else {}

    async def send(self, message, text):
        return await self.send_part(message, text)

    async def send_part(self, message, text, *, reference=False, mention=True):
        from .qq_presentation import clean_control_output, project_operation_hints
        text = project_operation_hints(clean_control_output(text), message.text) or '（没有可显示的正文。）'
        segments = []
        if reference:
            segments.append({'type':'reply', 'data':{'id':message.message_id}})
        if message.group_id and mention:
            segments.append({"type": "at", "data": {"qq": message.user_id}})
            segments.append({"type": "text", "data": {"text": " "}})
        # An array text segment prevents model-produced CQ strings executing actions.
        segments.append({"type": "text", "data": {"text": text}})
        action = "send_group_msg" if message.group_id else "send_private_msg"
        target = {"group_id": int(message.group_id)} if message.group_id else {"user_id": int(message.user_id)}
        try:
            result = await self.call(action, {**target, "message": segments})
        except OneBotActionRejected as error:
            if not reference or not error.reply_unsupported:
                raise
            # Only a definite validation rejection permits a fallback send.
            # Timeouts and ambiguous transport failures never come here.
            segments = [segment for segment in segments if segment['type'] != 'reply']
            segments.insert(0, {'type':'text','data':{'text':f'回复消息 #{message.message_id}\n'}})
            result = await self.call(action, {**target, 'message':segments})
        receipt = str(result.get('message_id', '')) if isinstance(result, dict) else ''
        if not re.fullmatch(r'-?\d{1,20}', receipt):
            raise RuntimeError("missing_delivery_receipt")
        return receipt

    async def send_inline(self,message,text,*,kind,key,offset,reference=False,mention=True):
        from .qq_expressions import KAOMOJI
        from .qq_presentation import clean_control_output
        if clean_control_output(text)!=text:raise ValueError('unclean_inline_text')
        if not isinstance(offset,int) or not 0<=offset<=len(text):raise ValueError('invalid_inline_offset')
        if kind=='kaomoji' and key in KAOMOJI:middle={'type':'text','data':{'text':KAOMOJI[key]}}
        else:raise ValueError('invalid_inline_expression')
        segments=[]
        if reference:segments.append({'type':'reply','data':{'id':message.message_id}})
        if message.group_id and mention:segments.extend([{'type':'at','data':{'qq':message.user_id}},{'type':'text','data':{'text':' '}}])
        if text[:offset]:segments.append({'type':'text','data':{'text':text[:offset]}})
        segments.append(middle)
        if text[offset:]:segments.append({'type':'text','data':{'text':text[offset:]}})
        target={'group_id':int(message.group_id)} if message.group_id else {'user_id':int(message.user_id)}
        action='send_group_msg' if message.group_id else 'send_private_msg'
        try:data=await self.call(action,{**target,'message':segments})
        except OneBotActionRejected as error:
            if not reference or not error.reply_unsupported:raise
            segments=[x for x in segments if x['type']!='reply']
            segments.insert(0,{'type':'text','data':{'text':f'回复消息 #{message.message_id}\n'}})
            data=await self.call(action,{**target,'message':segments})
        receipt=str(data.get('message_id','')) if isinstance(data,dict) else ''
        if not re.fullmatch(r'-?[0-9]{1,20}',receipt):raise RuntimeError('missing_inline_receipt')
        return receipt

    async def send_poke(self,message):
        payload={'user_id':int(message.user_id)}
        if message.group_id:payload['group_id']=int(message.group_id)
        await self.call('send_poke',payload)

    async def send_voice(self,message,path):
        # Only the private voice service creates this path; callers cannot
        # supply model-produced protocol segments or arbitrary media URLs.
        action='send_group_msg' if message.group_id else 'send_private_msg'
        target={'group_id':int(message.group_id)} if message.group_id else {'user_id':int(message.user_id)}
        result=await self.call(action,{**target,'message':[{'type':'record','data':{'file':Path(path).resolve().as_uri(),'cache':'0'}}]})
        receipt=str(result.get('message_id','')) if isinstance(result,dict) else ''
        if not re.fullmatch(r'-?\d{1,20}',receipt):raise RuntimeError('missing_voice_receipt')
        return receipt

    async def send_expression(self,message,kind,key=None,path=None):
        from .qq_expressions import KAOMOJI
        if kind=='sticker' and isinstance(path,Path) and path.is_absolute():segment={'type':'image','data':{'file':path.resolve().as_uri(),'sub_type':1}}
        elif kind=='kaomoji' and key in KAOMOJI:segment={'type':'text','data':{'text':KAOMOJI[key]}}
        else:raise ValueError('invalid_expression_segment')
        action='send_group_msg' if message.group_id else 'send_private_msg'
        target={'group_id':int(message.group_id)} if message.group_id else {'user_id':int(message.user_id)}
        data=await self.call(action,{**target,'message':[segment]})
        receipt=str(data.get('message_id','')) if isinstance(data,dict) else ''
        if not re.fullmatch(r'-?[0-9]{1,20}',receipt):raise RuntimeError('missing_expression_receipt')
        return receipt

    async def send_self_part(self,bot_id,text):
        result=await self.call('send_private_msg',{'user_id':int(bot_id),
            'message':[{'type':'text','data':{'text':text}}]})
        receipt=str(result.get('message_id','')) if isinstance(result,dict) else ''
        if not re.fullmatch(r'-?\d{1,20}',receipt):raise RuntimeError('missing_self_receipt')
        return receipt

    async def send_merged(self,message,receipts):
        if not receipts or len(receipts)>100 or any(not re.fullmatch(r'-?\d{1,20}',str(x)) for x in receipts):
            raise ValueError('invalid_forward_sources')
        action='send_group_forward_msg' if message.group_id else 'send_private_forward_msg'
        target={'group_id':int(message.group_id)} if message.group_id else {'user_id':int(message.user_id)}
        result=await self.call(action,{**target,'messages':[{'type':'node','data':{'id':str(x)}} for x in receipts]})
        receipt=str(result.get('message_id','')) if isinstance(result,dict) else ''
        if not re.fullmatch(r'-?\d{1,20}',receipt):raise RuntimeError('missing_forward_receipt')
        return receipt

    async def close(self):
        await self.http.aclose()


def split_reply(text, settings):
    from .qq_presentation import clean_control_output
    text = clean_control_output(text)
    if len(text) > settings.max_reply_chars:
        text = text[:settings.max_reply_chars - 20] + "\n（内容较长，已截断，请继续提问。）"
    chunks = []
    while text:
        end = min(len(text), settings.chunk_chars)
        if end < len(text):
            newline = text.rfind("\n", 0, end)
            if newline > end // 2:
                end = newline + 1
        chunks.append(text[:end])
        text = text[end:]
    return chunks


class QQBridge:
    def __init__(self, settings=None, store=None, responder=None, client=None):
        self.settings = settings
        self.store = store or QQStore(KNOWLEDGE_BASE_DIR / "qq" / "bridge.sqlite3")
        self._expression_root=Path(KNOWLEDGE_BASE_DIR).parent if store is None else self.store.path.parent
        self.controls_store = QQControlsStore(self.store.path.parent / "settings.json")
        self.search_credentials = QQSearchCredentialsStore(self.store.path.parent / "search_credentials.json",
            (os.environ.get("SAYA_API_CREDENTIALS_FILE") or KNOWLEDGE_BASE_DIR / "api_credentials.json") if store is None else None)
        self.shared_config = SharedChatConfigStore(self.store.path.parent / "shared_settings.json")
        self._qq_values = None
        self._inherit_fields = []
        self._instance_lease = InstanceLease(self.store.path.parent / "consumer.lock")
        self.responder = responder
        self.client = client
        self.task = None
        self.queue = None
        self.config_error = False
        self.last_event_at = None
        self.last_sent_at = None
        self.last_error = ""
        self._last_prune = 0
        self._management_lock = asyncio.Lock()
        self._settings_lock = asyncio.Lock()
        self.last_ignored_reason = ""
        self.ignored_counts = {}
        self.draining = False
        self.task_registry = None
        self.task_controls = None
        self.task_progress = None
        self.context_links = None
        self.context_sessions = None
        self.action_proposals = None
        self.context_salience = None
        self.forward_input = None
        self.dialogue_experience = None
        self.episodes = None
        self.turn_times = None
        self.memory_management = None
        self.group_context = None
        self.event_memory_use = None
        self.contact_review = None
        self.media = None
        self.voice = None
        self.expressions = None
        self.proactive = None
        self.daily_memory = None
        self._task_workers = set()

    def bind_responder(self,responder):
        """Also support the server's replacement after bridge startup."""
        self.responder=responder
        if responder is not None and hasattr(responder,'__dict__'):
            for name in ('dialogue_experience','context_salience','forward_input','relationships','episodes','daily_memory'):
                setattr(responder,name,getattr(self,name,None))
            if hasattr(responder,'settings'):responder.settings=self.settings
        return responder

    async def start(self):
        if self.task and not self.task.done():
            return
        try:
            self.settings = self.settings or QQSettings.from_env()
            document = self.controls_store.load_document()
            self._qq_values = document["settings"] or {key: getattr(self.settings, key) for key in QQControls.model_fields}
            self._inherit_fields = document["inherit_fields"]
            self._refresh_configuration()
        except ValueError:
            # Never expose ValidationError: Pydantic includes raw secret inputs.
            self.config_error = True
            return
        if self.responder is not None and hasattr(self.responder, "settings"):
            self.responder.settings = self.settings
        if not self.settings.enabled:
            return
        self._instance_lease.acquire()
        try:
            self.store.initialize(self.settings.retention_days)
            from .qq_dialogue_experience import QQDialogueExperience
            self.dialogue_experience=QQDialogueExperience(self)
            if self.responder is not None and hasattr(self.responder,'__dict__'):
                self.responder.dialogue_experience=self.dialogue_experience
            from .qq_context_salience import QQContextSalience
            self.context_salience=QQContextSalience(self)
            from .qq_natural_actions import QQNaturalActions
            self.natural_actions=QQNaturalActions(self)
            from .qq_forward_input import QQForwardInput
            self.forward_input=QQForwardInput(self)
            if self.responder is not None and hasattr(self.responder,'__dict__'):
                self.responder.context_salience=self.context_salience
                self.responder.forward_input=self.forward_input
            from .qq_forward import QQForward
            self.forward=QQForward(self)
            from .qq_favorites import QQFavorites
            self.favorites=QQFavorites(self)
            from .qq_official_packs import QQOfficialPacks
            self.official_packs=QQOfficialPacks(self)
            from .qq_expressions import QQExpressions
            self.expressions=QQExpressions(self,self._expression_root)
            from .qq_social import QQSocial
            self.social=QQSocial(self)
            from .qq_relationships import QQRelationships
            self.relationships=QQRelationships(self)
            from .qq_episode_context import QQEpisodeContext
            self.episodes=QQEpisodeContext(self)
            if self.responder is not None and hasattr(self.responder,'__dict__'):self.responder.relationships=self.relationships
            if self.responder is not None and hasattr(self.responder,'__dict__'):self.responder.episodes=self.episodes
            self.bind_responder(self.responder)
            with self.store.connect() as db:
                existing_tasks = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='qq_tasks'").fetchone()
            if self.settings.task_control_enabled or existing_tasks:
                self.task_registry = QQTaskRegistry(self.store)
                self.task_registry.recover()
                self.task_registry.prune(self.settings.retention_days)
            self.queue = asyncio.Queue(maxsize=self.settings.queue_size)
            for row in self.store.queued():
                if time.time() - row["created"] > self.settings.max_event_age:
                    self.store.set_state(row["id"], "expired")
                elif self.queue.full():
                    self.store.set_state(row["id"], "expired", "queue_capacity_changed")
                else:
                    self.queue.put_nowait((row["id"], QQMessage(**json.loads(row["payload"])), row["created"]))
            self.client = self.client or OneBotClient(self.settings)
            if self.voice:self.voice.start()
            if self.settings.task_control_enabled:
                from .qq_task_controls import QQTaskControls
                self.task_controls = QQTaskControls(self, self.task_registry, self.client.send)
                self.task_controls.start()
                from .qq_task_progress import QQTaskProgress
                self.task_progress=QQTaskProgress(self)
                self.task_progress.start()
            self.task = asyncio.create_task(self._schedule() if self.settings.task_control_enabled else self._consume(), name="qq-message-consumer")
        except BaseException:
            self._instance_lease.release()
            raise

    async def stop(self):
        if self.daily_memory:await self.daily_memory.stop()
        if self.proactive:await self.proactive.stop()
        try:
            try:
                if self.task_controls:
                    await self.task_controls.stop()
                    self.task_controls = None
                if self.task:
                    self.task.cancel()
                    try:
                        await self.task
                    except asyncio.CancelledError:
                        pass
            finally:
                self.task = None
                if self.voice:await self.voice.stop()
                if self.task_progress:
                    await self.task_progress.stop()
                    self.task_progress=None
                if self.client:
                    await self.client.close()
        finally:
            self.client = None
            self._instance_lease.release()

    def authenticate(self, body, authorization, signature):
        settings = self.settings
        if not settings or self.config_error:
            return False
        # When configured, HMAC is mandatory; bearer alone cannot downgrade it.
        if settings.webhook_secret:
            digest = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha1).hexdigest()
            return hmac.compare_digest(str(signature or ""), "sha1=" + digest)
        return bool(settings.webhook_token) and hmac.compare_digest(
            str(authorization or ""), "Bearer " + settings.webhook_token)

    def status(self):
        ready = bool(self.task and not self.task.done())
        settings = self.settings
        return {
            "enabled": bool(settings and settings.enabled), "running": ready, "draining": self.draining,
            "configuration_error": self.config_error, "transport": "onebot11_http",
            "counts": self._work_counts() if ready else {},
            "last_event_at": self.last_event_at, "last_sent_at": self.last_sent_at,
            "last_error": self.last_error,
            "shared_memory": bool(settings and settings.shared_memory),
            "memory_policy": "owner_friend_private_only; groups_and_other_users_isolated",
            "group_mention_only": settings.group_mention_only if settings else True,
            "owner_identity_configured": bool(settings and settings.owner_ids),
            "auto_accept_owner_invites": bool(settings and settings.auto_accept_owner_invites),
            "allowed_group_count": len(self.effective_groups()) if ready else 0,
            "pending_invite_count": sum(x["state"] == "pending" for x in self.store.invites(settings.bot_id)) if ready else 0,
            "owner_group_auto_enable": bool(settings and settings.owner_group_auto_enable),
            "last_ignored_reason": self.last_ignored_reason,
            "ignored_counts": dict(self.ignored_counts),
        }

    def controls(self):
        if not self.settings or self.config_error:
            raise ValueError("QQ 连接配置有误，请先检查本地账号和连接配置")
        shared = self.shared_config.load()
        if self._qq_values is not None and not self._configuration_busy():
            self._refresh_configuration(shared)
        settings = QQControls.model_validate({key: getattr(self.settings, key) for key in QQControls.model_fields}).model_dump()
        revision = revision_of({"settings": settings, "inherit_fields": self._inherit_fields, "shared_revision": shared["revision"]})
        return {
            "settings": settings, "revision": revision,
            "inherit_fields": list(self._inherit_fields), "shared": shared,
            "value_sources": {key: ("global" if key in self._inherit_fields and key in shared["values"] else
                "qq_fallback" if key in self._inherit_fields else "qq_override") for key in settings},
            "identity": {"bot_id": self.settings.bot_id, "owner_ids": sorted(self.settings.owner_ids),
                         "allowed_users": sorted(self.settings.allowed_users), "model": self.settings.model},
            "status": self.status(),
            "quote_diagnostics": qq_quotes.diagnostics(self.store),
            "capabilities": self.responder.capabilities() if callable(getattr(self.responder, "capabilities", None)) else {},
        }

    def _work_counts(self):
        counts = self.store.counts()
        if self.task_controls:
            controls = self.task_controls.counts()
            for key in ('queued','sending'):
                counts[key] = counts.get(key,0) + controls.get(key,0)
        if self.task_progress and self.task_progress.busy():
            counts['sending']=counts.get('sending',0)+1
        if self.voice:counts['voice_rendering']=int(self.voice.busy)
        if self.task_registry:counts['active_tasks']=len(self.task_registry.list_active())
        return counts

    def cancel_pending(self):
        if not self.draining:raise ValueError('drain_required')
        cancelled=0
        if self.task_registry:
            for task in self.task_registry.list_live():
                if task['state']=='queued':
                    self.task_registry.cancel(task['id'],task['scope']);cancelled+=1
        # Items may have reached the durable inbox but not the scheduler yet.
        with self.store.connect() as db:
            cancelled+=db.execute("update inbox set state='cancelled',payload='{}',error='cancel_requested' where state='queued'").rowcount
        return cancelled

    def _configuration_busy(self, command=False):
        if not self.task or self.task.done():
            return False
        try:current = asyncio.current_task()
        except RuntimeError:
            # Native console workers may read the current revision, but must
            # not refresh live settings from outside the bridge event loop.
            return True
        counts = self._work_counts()
        if any(not worker.done() and (not command or worker is not current) for worker in self._task_workers):
            return True
        if command and (current is self.task or current in self._task_workers):
            counts["generating"] = max(0, counts.get("generating", 0) - 1)
        return any(counts.get(state, 0) for state in ("queued", "generating", "sending"))

    def _effective_values(self, values, inherited, shared):
        if any(key not in SHARED_FIELDS for key in inherited):
            raise ValueError("setting cannot inherit")
        return QQControls.model_validate(values | {key: shared["values"][key] for key in inherited if key in shared["values"]}).model_dump()

    def _refresh_configuration(self, shared=None):
        values = self._effective_values(self._qq_values, self._inherit_fields, shared or self.shared_config.load())
        if any(getattr(self.settings, key) != value for key, value in values.items()):
            self.settings = QQSettings.model_validate(self.settings.model_dump() | values)
        if self.context_links and (not self.settings.shared_memory or not self.settings.task_control_enabled):
            self.context_links.revoke_bot(self.settings.bot_id)
        if self.responder is not None and hasattr(self.responder, "settings"):
            self.responder.settings = self.settings

    def _validate_capabilities(self, controls):
        if controls["thinking_effort"] not in {"auto", "none"} and callable(getattr(self.responder, "capabilities", None)):
            if not self.responder.capabilities().get("thinking_supported", False):
                raise QQControlsUnsupported("当前接口或模型不支持该思考档位，请选择自动或关闭。设置未改变。")

    async def update_shared_controls(self, values, web_follow_global, expected_revision):
        async with self._settings_lock:
            if self._inherit_fields and self._configuration_busy():
                raise QQControlsBusy("QQ 正在使用共享设置，请等待回复完成后再保存。")
            from saya_app.services.shared_chat_config import validate_shared
            validated = validate_shared(values)
            if web_follow_global and validated.get("thinking_effort") not in {None, "auto", "none"}:
                self._validate_capabilities(self.controls()["settings"] | validated)
            candidate = self._effective_values(self._qq_values or self.controls()["settings"], self._inherit_fields, {"values": validated})
            self._validate_capabilities(candidate)
            self.shared_config.save(validated, web_follow_global, expected_revision)
            self._refresh_configuration()
            return self.shared_config.load()

    async def update_controls(self, values, expected_revision=None, command=False, inherit_fields=None):
        async with self._settings_lock:
            checkpoint_current()
            current = self.controls()
            if expected_revision is not None and expected_revision != current["revision"]:
                raise QQControlsConflict("设置已被 QQ 或其他网页修改，请重新读取后再保存，避免覆盖较新的设置。")
            # Updating memory permissions midway through a reply would give
            # confusing guarantees; require the current queue to finish first.
            if self._configuration_busy(command):
                raise QQControlsBusy("QQ 正在处理消息，请等待当前回复完成后再保存。")
            controls = QQControls.model_validate(values).model_dump()
            inherited = list(self._inherit_fields if inherit_fields is None else sorted(set(inherit_fields)))
            effective = self._effective_values(controls, inherited, self.shared_config.load())
            self._validate_capabilities(effective)
            updated = QQSettings.model_validate(self.settings.model_dump() | effective)
            # Persist before touching the running state. Disk failures leave the
            # previous settings and consumer intact.
            previous = dict(self._qq_values or current["settings"])
            old_inherited = list(self._inherit_fields)
            self.controls_store.save(controls, inherited)
            old = self.settings
            try:
                if self.context_links and (not updated.shared_memory or not updated.enabled or not updated.task_control_enabled):
                    self.context_links.revoke_bot(old.bot_id)
                if old.enabled and (not updated.enabled or old.task_control_enabled != updated.task_control_enabled):
                    await self.stop()
                self.settings = updated
                self._qq_values, self._inherit_fields = controls, inherited
                if self.responder is not None and hasattr(self.responder, "settings"):
                    self.responder.settings = updated
                if updated.enabled and (not self.task or self.task.done()):
                    await self.start()
                    if self.config_error or not self.task:
                        raise RuntimeError("QQ bridge unavailable")
            except Exception:
                self.controls_store.save(previous, old_inherited)
                self._qq_values, self._inherit_fields = previous, old_inherited
                self.settings = old
                self.config_error = False
                if self.responder is not None and hasattr(self.responder, "settings"):
                    self.responder.settings = old
                if old.enabled and (not self.task or self.task.done()):
                    await self.start()
                raise
            return self.controls()

    def submit_signed(self, body, authorization='', signature=''):
        if not self.authenticate(body, authorization, signature):
            return 'unauthorized', None
        try:
            event = json.loads(body)
        except (ValueError, UnicodeError):
            return 'ignored', 'invalid_json'
        return self.submit(event, _control_auth=(body, authorization, signature))

    def submit(self, event, *, _control_auth=None):
        if self.draining:
            return "unavailable", "service_draining"
        if not self.task or self.task.done():
            return "unavailable", None
        self.last_event_at = time.time()
        if isinstance(event, dict) and event.get("post_type") == "request":
            if event.get('request_type')=='friend' and self.contact_review:return self.contact_review.submit(event)
            return self.submit_invite(event)
        if isinstance(event, dict) and event.get("post_type") == "notice":
            if self.episodes:self.episodes.unified.notice(event)
            if self.forward_input and self.forward_input.revoke_notice(event):return 'processed',None
            if event.get('notice_type')=='notify' and event.get('sub_type')=='poke' and getattr(self,'social',None):
                message,reason=self.social.admit(event)
                if message is None:return 'ignored',reason
            else:return self.submit_notice(event)
        else:message=None
        self.enable_owner_mentioned_group(event)
        active_settings = self.settings.model_copy(update={"allowed_groups": self.effective_groups()})
        unified=getattr(getattr(self,'episodes',None),'unified',None)
        if unified and unified.event_admitted(event):
            active_settings=active_settings.model_copy(update={'allowed_users':active_settings.allowed_users|{str(event['user_id'])}})
        if message is None:message, reason = normalize_event(event, active_settings)
        if not message and reason=='not_mentioned' and self.group_context:
            proof=self.group_context.wake_proof(event)
            if proof:
                message,reason=normalize_event(event,active_settings.model_copy(update={'group_mention_only':False}))
                if message:
                    message.reply_wake_revision=proof['revision']
                    message.reply_wake_expires=proof['expires']
        if not message:
            if isinstance(event, dict) and event.get("post_type") == "message":
                self.last_ignored_reason = reason
                self.ignored_counts[reason] = self.ignored_counts.get(reason, 0) + 1
            return "ignored", reason
        from .qq_natural_actions import control_alias
        alias=control_alias(message.text)
        if alias:message.text=alias
        if self.proactive:self.proactive.note_qq(message)
        if self.task_controls and _control_auth and message.text.split()[0].lower() in {'/status','/help','/tasks','/cancel','/expression','/表情模式','/proactive','/主动'}:
            body, authorization, signature = _control_auth
            return self.task_controls.submit(body, authorization=authorization, signature=signature)
        if time.monotonic() - self._last_prune > 3600:
            self.store.prune(self.settings.retention_days)
            if self.media:self.media.prune()
            if self.voice:self.voice.prune()
            if self.expressions:self.expressions.prune()
            if getattr(self,'social',None):self.social.prune()
            self._last_prune = time.monotonic()
        admission_settings = self.settings
        if (not message.group_id and message.user_id in self.settings.owner_ids
                and message.text.split()[0].lower() in {"/whoami", "/owner", "/身份", "/help", "/status", "/memory", "/reset", "/groups", "/requests", "/group", "/approve", "/reject", "/settings", "/set", "/设置","/expression","/表情模式"}):
            admission_settings = self.settings.model_copy(update={"cooldown_seconds": 0})
        elif (not message.group_id and message.user_id in self.settings.owner_ids and message.shared
                and self.settings.shared_memory and self.settings.memory_write_enabled and self.action_proposals):
            history_scope=message.scope+':shared'
            _,session_id=self.store.conversation(history_scope)
            if self.action_proposals.confirmation_admissible(history_scope,session_id,message.text,self.controls()['revision']):
                admission_settings=self.settings.model_copy(update={'cooldown_seconds':0})
        result, row_id = self.store.admit(message, admission_settings)
        if row_id:
            if self.settings.task_control_enabled:
                try:
                    self.task_registry.register(message.key, message.scope + (':shared' if message.shared else ':isolated'),
                        self.controls()['revision'], is_group=bool(message.group_id), max_pending=self.settings.queue_size)
                except Exception:
                    self.store.set_state(row_id,'failed','task_registration_failed')
                    return 'unavailable', None
            self.queue.put_nowait((row_id, message, time.time()))
        return result, None

    def enqueue_proactive(self,identifier,owner):
        if (not self.proactive or self.draining or not self.task or self.task.done() or not self.settings.task_control_enabled
            or owner not in self.settings.owner_ids or not re.fullmatch('[a-f0-9]{32}',identifier)):return False
        job=self.proactive.store.get(identifier)
        if not job or job['channel']!='qq' or job['principal']!='owner:'+owner or not self.proactive.valid(job):return False
        message=QQMessage(f'{self.settings.bot_id}:proactive:{identifier}',f'{self.settings.bot_id}:private:{owner}',
            self.settings.bot_id,owner,'','0','主动互动机会（不是主人新输入）',bool(self.settings.shared_memory),kind='proactive',sent_at=time.time())
        result,row_id=self.store.admit(message,self.settings)
        if result!='queued':return False
        try:self.queue.put_nowait((row_id,message,time.time()));return True
        except asyncio.QueueFull:self.store.set_state(row_id,'failed','queue_capacity_changed');return False

    def enable_owner_mentioned_group(self, event):
        # Some QQ invitations join immediately without a request event or a
        # usable operator ID. An authenticated owner explicitly addressing the
        # bot provides the missing group grant; a prior manual block wins.
        if (not self.settings.owner_group_auto_enable or not isinstance(event, dict)
                or event.get("message_type") != "group"
                or qq_id(event.get("user_id")) not in self.settings.owner_ids):
            return
        group_id = qq_id(event.get("group_id"))
        if not group_id or group_id in self.effective_groups():
            return
        if self.store.group_overrides(self.settings.bot_id).get(group_id) is False:
            return
        probe = self.settings.model_copy(update={"allowed_groups": frozenset({group_id}), "group_mention_only": True})
        parsed, _ = normalize_event(event, probe)
        if parsed:
            self.set_group_access(group_id, True)

    def submit_notice(self, event):
        if (not self.settings.auto_accept_owner_invites or event.get("notice_type") != "group_increase"
                or event.get("sub_type") not in {"invite", "approve"}
                or qq_id(event.get("self_id")) != self.settings.bot_id
                or qq_id(event.get("user_id")) != self.settings.bot_id
                or qq_id(event.get("operator_id")) not in self.settings.owner_ids
                or not qq_id(event.get("group_id"))):
            return "ignored", "unsupported_notice"
        try:
            if not time.time() - self.settings.max_event_age <= float(event["time"]) <= time.time() + 60:
                return "ignored", "stale_event"
        except (ValueError, TypeError, KeyError):
            return "ignored", "invalid_time"
        group_id = qq_id(event["group_id"])
        if self.store.group_overrides(self.settings.bot_id).get(group_id) is False:
            return "ignored", "group_manually_blocked"
        self.set_group_access(group_id, True)
        return "group_enabled", None

    def effective_groups(self):
        allowed = set(self.settings.allowed_groups)
        for group_id, enabled in self.store.group_overrides(self.settings.bot_id).items():
            if enabled:
                allowed.add(group_id)
            else:
                allowed.discard(group_id)
        return frozenset(allowed)

    def set_group_access(self, group_id, enabled):
        group_id = qq_id(group_id)
        if not group_id:
            raise ValueError("群号必须为正整数")
        if not enabled and self.settings.task_control_enabled and self.task_registry:
            prefix=f'{self.settings.bot_id}:group:{group_id}:user:'
            tasks=[t for t in self.task_registry.list_live() if t['scope'].startswith(prefix)]
            for task in tasks:
                self.task_registry.cancel(task['id'],task['scope'])
            if any(e['outcome']=='in_flight' for task in tasks
                   for e in self.task_registry.get(task['id'],task['scope'])['effects'].values()):
                raise QQControlsBusy('已请求取消该群任务；有操作正在提交，请等待结束后再关闭群回复。')
        self.store.set_group(self.settings.bot_id, group_id, enabled)
        return {"group_id": group_id, "enabled": enabled}

    def submit_invite(self, event):
        if (event.get("request_type") != "group" or event.get("sub_type") != "invite"
                or qq_id(event.get("self_id")) != self.settings.bot_id
                or not qq_id(event.get("user_id")) or not qq_id(event.get("group_id"))):
            return "ignored", "unsupported_request"
        try:
            if not time.time() - self.settings.max_event_age <= float(event["time"]) <= time.time() + 60:
                return "ignored", "stale_event"
        except (ValueError, TypeError, KeyError):
            return "ignored", "invalid_time"
        if not isinstance(event.get("flag"), str) or not 1 <= len(event["flag"]) <= 2048:
            return "ignored", "invalid_request_flag"
        self.store.prune(self.settings.retention_days)
        request_id, created = self.store.record_invite(event, self.settings.bot_id)
        if not request_id:
            return "busy", "too_many_pending_invites"
        row = self.store.invite(self.settings.bot_id, request_id)
        if row["state"] != "pending":
            return "duplicate", None
        blocked = self.store.group_overrides(self.settings.bot_id).get(str(event["group_id"])) is False
        if not (self.settings.auto_accept_owner_invites and str(event["user_id"]) in self.settings.owner_ids and not blocked):
            return ("pending" if created else "duplicate"), None
        job = QQMessage("invite:" + request_id, "invite:" + request_id, self.settings.bot_id,
                        str(event["user_id"]), str(event["group_id"]), "0", "", False,
                        kind="group_invite", request_id=request_id)
        result, row_id = self.store.admit(job, self.settings)
        if row_id:
            self.queue.put_nowait((row_id, job, time.time()))
        return result, None

    async def decide_invite(self, request_id, approve):
        async with self._management_lock:
            row = self.store.invite(self.settings.bot_id, request_id)
            if not row:
                raise ValueError("邀请记录不存在")
            if row["state"] != "pending":
                raise ValueError("邀请已处理或结果不明确，请在 QQ 核对")
            if time.time() - row["created"] > 86400:
                self.store.finish_invite(self.settings.bot_id, request_id, "expired")
                raise ValueError("邀请已过期，请重新邀请")
            if not self.store.claim_invite(self.settings.bot_id, request_id, "approving" if approve else "rejecting"):
                raise ValueError("邀请已被处理")
            try:
                await self.client.call("set_group_add_request", {"flag": row["flag"], "sub_type": "invite", "approve": approve})
                self.store.finish_invite(self.settings.bot_id, request_id, "approved" if approve else "rejected", enable_group=approve)
            except BaseException:
                # A timeout/cancellation can occur AFTER QQ applied the action.
                self.store.finish_invite(self.settings.bot_id, request_id, "result_unknown")
                raise
            return {"id": request_id, "group_id": row["group_id"], "state": "approved" if approve else "rejected"}

    async def group_management(self):
        raw = await self.client.call("get_group_list", {})
        if not isinstance(raw, list):
            raise RuntimeError("invalid_group_list")
        allowed = self.effective_groups()
        joined = {qq_id(row.get("group_id")): row for row in raw if isinstance(row, dict) and qq_id(row.get("group_id"))}
        groups = [{"group_id": gid, "name": str(row.get("group_name") or "")[:100], "joined": True, "enabled": gid in allowed}
                  for gid, row in joined.items()]
        groups += [{"group_id": gid, "name": "", "joined": False, "enabled": True} for gid in allowed if gid not in joined]
        return {"owner_ids": sorted(self.settings.owner_ids), "auto_accept_owner_invites": self.settings.auto_accept_owner_invites,
                "groups": groups, "invites": self.store.invites(self.settings.bot_id)}

    async def owner_command(self, message):
        text = message.text.strip()
        command = text.split()[0].lower() if text else ""
        if command not in {"/groups", "/requests", "/group", "/approve", "/reject", "/settings", "/set", "/设置"}:
            return None
        if message.user_id not in self.settings.owner_ids or message.group_id:
            return "QQ 设置与群管理命令仅供已配置的主人在好友私聊中使用。"
        args = text.split()
        try:
            if command in {"/settings", "/set", "/设置"}:
                help_text = ("QQ 设置（与网页一致）：\n"
                    "/set memory on/off · 主人私聊共用长期记忆\n"
                    "/set mention on/off · 群内仅 @回复\n"
                    "/set invites on/off · 自动接受主人邀请\n"
                    "/set owner_group on/off · 主人首次 @授权\n"
                    "/set cooldown 3 · 冷却秒数（0–3600）\n"
                    "/set history 12 · 上下文轮数（1–50）\n"
                    "/set timeout 180 · 生成超时（1–900 秒）\n"
                    "也可用 /设置 记忆 开启 等中文命令。\n"
                    "/set thinking auto/none/low/high/max · 思考档位\n"
                    "/set thinking inherit · 跟随共享默认；其他共享项也支持 inherit\n"
                    "/set tools on/off · 记忆与搜索工具\n"
                    "/set recall off/auto/on · 召回模式\n"
                    "/set memories 50 · 记忆数量（1–200）\n"
                    "/set budget 12000 · 记忆 token 预算（512–32000）\n"
                    "/set graph off/auto/deep · 关联记忆\n"
                    "/set save on/off · 保存记忆\n"
                    "/set search off/auto/force · 网络搜索\n"
                    "/set output 8192 · 模型输出预算\n"
                    "/set delivery chat/legacy · 多消息或旧格式\n"
                    "/set split natural/length · 自然分消息或按长度\n"
                    "/set quote on/off · 引用原消息\n"
                    "/set bubble 240 · 单条目标（100–1500字）\n"
                    "/set bubbles 3 · 每次最多条数（1–6）\n"
                    "/continue [结果编号] · 续读已生成内容（24小时内）\n"
                    "/topics · 查询本会话没说完的话题；也可直接说“继续说”\n"
                    "/recall 关键词 · 本次深度召回；/search 内容 · 本次搜索\n"
                    "/remember 内容 · 明确保存；/memories · 候选记忆；/memory-save 编号 · 确认；/memory-ignore 编号 · 忽略\n"
                    "总开关请在网页修改；群聊按群权限和逐条私人记忆授权使用，不读取整份私人历史。")
                snapshot = self.controls()
                if len(args) == 1:
                    values = snapshot["settings"]
                    switch = lambda key: "开启" if values[key] else "关闭"
                    return (f"记忆共享：{switch('shared_memory')}；仅 @回复：{switch('group_mention_only')}；"
                            f"自动入群：{switch('auto_accept_owner_invites')}；自动授权：{switch('owner_group_auto_enable')}。\n"
                            f"冷却 {values['cooldown_seconds']:g} 秒；上下文 {values['history_turns']} 轮；超时 {values['reply_timeout']:g} 秒。\n"
                            f"思考 {values['thinking_effort']}；工具 {switch('agent_mode')}；召回 {values['recall_mode']}；"
                            f"记忆 {values['recall_memory_count']} 条/{values['memory_token_budget']} token；关联 {values['graph_recall_mode']}；"
                            f"保存 {switch('memory_write_enabled')}；搜索 {values['web_search_mode']}；输出 {values['max_output_tokens']} token。\n"
                            f"消息 {values['delivery_mode']}；分消息 {values['bubble_strategy']}；引用 {switch('reply_reference')}；单条目标 {values['bubble_chars']} 字；每次最多 {values['reply_max_messages']} 条。\n"
                            + ("跟随共享默认：" + "、".join(snapshot['inherit_fields']) + "。\n" if snapshot['inherit_fields'] else "当前各项使用 QQ 独立覆写。\n") + help_text)
                if len(args) != 3:
                    return help_text
                key, value = parse_command_setting(args[1], args[2])
                inherited = set(snapshot["inherit_fields"])
                if value is None:
                    inherited.add(key)
                else:
                    inherited.discard(key)
                try:
                    result = await self.update_controls(snapshot["settings"] | ({key: value} if value is not None else {}),
                        snapshot["revision"], command=True, inherit_fields=sorted(inherited))
                except (QQControlsBusy, QQControlsConflict, QQControlsUnsupported):
                    raise
                except ValueError:
                    return "设置未修改：请检查开关值和参数范围。发送 /settings 查看用法。"
                display = "跟随共享默认：" + str(result['settings'][key]) if value is None else ("开启" if value else "关闭") if isinstance(value, bool) else str(result['settings'][key])
                return f"QQ 设置已保存并生效：{key} = {display}。网页使用同一份设置。"
            if command == "/groups":
                data = await self.group_management()
                lines = [f"{x['group_id']} · {x['name']} · {'已加入' if x['joined'] else '尚未加入'} · {'回复开启' if x['enabled'] else '回复关闭'}" for x in data["groups"][:30]]
                return "QQ 群列表：\n" + ("\n".join(lines) or "尚未加入群。你可以邀请我入群。")
            if command == "/requests":
                pending = [x for x in self.store.invites(self.settings.bot_id) if x["state"] == "pending"]
                return "待审核邀请：\n" + ("\n".join(f"{x['id']} · 群{x['group_id']} · 邀请人{x['user_id']}" for x in pending[:30]) or "暂无待审核邀请。")
            if command == "/group" and len(args) == 3 and args[1].lower() in {"allow", "block"}:
                data = self.set_group_access(args[2], args[1].lower() == "allow")
                return f"群 {data['group_id']} 的回复已{'开启（群内请 @我）' if data['enabled'] else '关闭（不会退群）'}。"
            if command in {"/approve", "/reject"} and len(args) == 2:
                data = await self.decide_invite(args[1], command == "/approve")
                return f"群 {data['group_id']} 邀请已{'同意，群内 @我即可对话' if command == '/approve' else '拒绝'}。"
            return "用法：/groups、/requests、/group allow 群号、/group block 群号、/approve 邀请编号、/reject 邀请编号。"
        except ValueError as exc:
            return str(exc)
        except Exception:
            return "QQ 管理操作未能确认，请在 QQ 或网页设置核对；不会自动重复操作。"

    async def diagnostics(self):
        try:
            data = await self.client.call("get_login_info", {})
            account_matches = str(data.get("user_id")) == self.settings.bot_id
            return {"reachable": True, "account_matches": account_matches,
                    "message": "QQ 账号匹配" if account_matches else "登录账号与 SAYA_QQ_BOT_ID 不符"}
        except Exception:
            return {"reachable": False, "account_matches": False,
                    "message": "OneBot 连接或鉴权失败，请检查地址、Token 和 QQ 登录状态"}

    def _task_permission(self, message):
        if not self.settings.enabled or message.bot_id != self.settings.bot_id:
            return False
        if message.kind=='proactive':
            if not self.proactive or message.group_id or message.user_id not in self.settings.owner_ids:return False
            if not self.proactive.valid(self.proactive.store.get(message.key.rsplit(':',1)[-1])):return False
        if self.group_context and message.group_id and not self.group_context.wake_allowed(message):return False
        if message.kind == 'group_invite':
            return message.user_id in self.settings.owner_ids and self.settings.auto_accept_owner_invites
        allowed = message.group_id in self.effective_groups() if message.group_id else message.user_id in self.settings.allowed_users | self.settings.owner_ids
        shared = bool(not message.group_id and self.settings.shared_memory and message.user_id in self.settings.owner_ids)
        configuration_command = message.text.split()[0].lower() in {'/set','/settings','/设置','/groups','/requests','/group','/approve','/reject'}
        if not allowed and not message.group_id and self.episodes:allowed=self.episodes.unified.admitted(message.user_id,message.text)
        return allowed and (configuration_command or bool(message.shared) == shared)

    async def _schedule(self):
        pending = []
        busy_scopes = set()
        jobs={}
        pulse=0.0
        try:
            while True:
                while not self.queue.empty():
                    item = self.queue.get_nowait()
                    row_id, message, created = item
                    with self.store.connect() as db:row=db.execute('select state from inbox where id=?',(row_id,)).fetchone()
                    if not row or row[0]!='queued':
                        self.queue.task_done();continue
                    scope = message.scope + (':shared' if message.shared else ':isolated')
                    task = self.task_registry.register(message.key,scope,self.controls()['revision'],
                        is_group=bool(message.group_id),max_pending=self.settings.queue_size)
                    pending.append((item,task))
                    jobs[task['id']]=(item,task)
                for item, task in list(pending):
                    state = self.task_registry.get(task['id'],task['scope'])['state']
                    if state != 'queued':
                        self.store.set_state(item[0], 'cancelled' if state=='cancelled' else 'interrupted')
                        self.queue.task_done()
                        pending.remove((item,task))
                        jobs.pop(task['id'],None)
                        continue
                    lane=self.group_context.lane(item[1]) if self.group_context else task['scope']
                    if len(self._task_workers)>=2 or lane in busy_scopes:
                        continue
                    claimed = self.task_registry.claim(task['id'],task['scope'])
                    if claimed is None:
                        continue
                    pending.remove((item,task))
                    busy_scopes.add(lane)
                    worker = asyncio.create_task(self._run_registered(item,task,busy_scopes,jobs,lane),name='qq-scoped-task')
                    self._task_workers.add(worker)
                    worker.add_done_callback(self._task_workers.discard)
                if self.task_progress and time.monotonic()-pulse>.1:
                    for task in self.task_registry.list_active():
                        if task['id'] not in jobs:self.task_registry.reconcile_terminal_inbox(task['id'],task['scope'])
                    self.task_progress.update(list(jobs.values()))
                    pulse=time.monotonic()
                await asyncio.sleep(.02)
        finally:
            # A deliberate stop never closes a shared client under live work.
            for task in self.task_registry.list_active():
                self.task_registry.cancel(task['id'],task['scope'])
            await asyncio.gather(*self._task_workers,return_exceptions=True)
            for item,task in pending:
                self.task_registry.finish(task['id'],task['scope'],'interrupted',error='restart_interrupted')
                self.store.set_state(item[0],'interrupted','shutdown')
                self.queue.task_done()

    async def _run_registered(self, item, task, busy_scopes, jobs, lane=None):
        guard = QQTaskGuard(self.task_registry,task['id'],task['scope'])
        guard.permission_check = lambda: self._task_permission(item[1])
        progress_stopped=False
        async def stop_progress():
            nonlocal progress_stopped
            if self.task_progress and not progress_stopped:
                progress_stopped=True
                await self.task_progress.pause_and_flush(item[1])
        guard.stop_progress=stop_progress
        try:
            with guard.bind():
                await self._process(*item)
            with self.store.connect() as db:
                row = db.execute('SELECT state,error FROM inbox WHERE id=?',(item[0],)).fetchone()
            terminal = 'suppressed' if row and row['state']=='suppressed' else 'completed' if row and row['state'] in {'sent','processed'} else 'failed'
            await stop_progress()
            if self.task_registry.get(task['id'],task['scope'])['state']=='cancel_requested':
                for result_id in guard.result_ids:
                    qq_delivery.freeze(self.store,task['scope'],result_id)
            while any(e['outcome']=='in_flight' for e in self.task_registry.get(task['id'],task['scope'])['effects'].values()):
                await asyncio.sleep(.02)
            known_errors={'generation_timeout':'model_timeout','delivery_uncertain':'delivery_unknown',
                'generation_failed':'generation_failed','model_unconfigured':'model_unconfigured',
                'authorization_changed':'authorization_changed'}
            error='model_timeout' if guard.timed_out else known_errors.get(row['error'] if row else '', '')
            if row and row['state']=='expired':error='task_expired'
            self.task_registry.finish(task['id'],task['scope'],terminal,error=error)
            self.task_registry.prune(self.settings.retention_days)
        except asyncio.CancelledError:
            self.task_registry.cancel(task['id'],task['scope'])
            while any(e['outcome']=='in_flight' for e in self.task_registry.get(task['id'],task['scope'])['effects'].values()):
                await asyncio.sleep(.02)
            self.task_registry.finish(task['id'],task['scope'],'interrupted',error='restart_interrupted')
            raise
        except Exception:
            self.last_error = 'task_state_failed'
            current=self.task_registry.get(task['id'],task['scope'])
            if not any(e['outcome']=='in_flight' for e in current['effects'].values()):
                self.task_registry.finish(task['id'],task['scope'],'failed',error='generation_failed')
        finally:
            await stop_progress()
            jobs.pop(task['id'],None)
            if self.task_progress and progress_stopped:
                self.task_progress.release(item[1])
            busy_scopes.discard(lane or task['scope'])

    async def _consume(self):
        while True:
            row_id, message, created = await self.queue.get()
            await self._process(row_id, message, created)

    async def _process(self, row_id, message, created):
        sending = False
        generated = False
        experience_observation=None
        response_task=None
        try:
            checkpoint_current()
            if time.time() - created > self.settings.max_event_age:
                self.store.set_state(row_id, "expired")
                return
            if message.kind == "group_invite":
                row = self.store.invite(self.settings.bot_id, message.request_id)
                blocked = self.store.group_overrides(self.settings.bot_id).get(message.group_id) is False
                if (not row or row["state"] != "pending" or row["user_id"] not in self.settings.owner_ids
                        or not self.settings.auto_accept_owner_invites or blocked
                        or row["group_id"] != message.group_id):
                    self.store.set_state(row_id, "ignored", "invite_policy_changed")
                    return
                await self.decide_invite(message.request_id, True)
                self.store.set_state(row_id, "processed")
                return
            # Re-evaluate after recovery; never inherit a previously enabled grant.
            message.shared = bool(not message.group_id and self.settings.shared_memory
                                  and message.user_id in self.settings.owner_ids)
            allowed = (message.group_id in self.effective_groups() if message.group_id
                       else message.user_id in self.settings.allowed_users | self.settings.owner_ids)
            if not allowed and not message.group_id and self.episodes:allowed=self.episodes.unified.admitted(message.user_id,message.text)
            if not allowed or message.bot_id != self.settings.bot_id:
                self.store.set_state(row_id, "ignored", "authorization_changed")
                return
            self.store.set_state(row_id, "generating")
            # A later privacy-setting change must not replay shared-memory
            # answers into an isolated conversation (or the reverse).
            history_scope = message.scope + (":shared" if message.shared else ":isolated")
            await self.episodes.unified.prepare(message)
            event_control=await self.episodes.operations.handle(message)
            if event_control is None and not getattr(message,'event_operation_checked',False):
                event_control=self.episodes.unified.handle(message)
            if event_control is None and not getattr(message,'event_operation_checked',False):
                event_control=(self.episodes.people.controls.handle(message)
                    if self.episodes and self.episodes.people.active() else None)
            if event_control is None and not self.episodes.topics.active():event_control=await self.episodes.unified.capture(message)
            from .qq_context_salience import topic_request
            topic_only=False
            topic_change=topic_request(message.text)
            if self.context_salience.state()['values']['enabled'] and topic_change is not None:
                if self.episodes and not self.episodes.topics.active():self.episodes.pause(message)
                self.context_salience.switch(history_scope,message.key)
                topic_only=not bool(topic_change)
                if topic_change:
                    message.text=topic_change
                    message.context_text=topic_change
            message.context_snapshot=self.context_salience.snapshot(history_scope)
            if event_control is None and self.episodes.topics.active() and not topic_only:
                event_control=await self.episodes.unified.capture(message)
            if event_control is not None and getattr(message,'event_control_dialogue',False):
                message.event_control_receipt=getattr(event_control,'payload',str(event_control))
            if message.context_snapshot['enabled']:
                guard=current_guard()
                if guard:
                    previous_check=guard.permission_check
                    guard.permission_check=lambda:(not previous_check or previous_check()) and self.context_salience.valid(message.context_snapshot)
            if getattr(message,'event_operation_proposal',None):
                guard=current_guard()
                if guard:
                    operation_previous=guard.permission_check
                    guard.permission_check=lambda:(not operation_previous or operation_previous()) and self.episodes.operations.can_deliver(message)
            if self.context_links and not message.group_id and message.user_id in self.settings.owner_ids:
                scope=self.context_links.scope(message.bot_id,message.user_id)
                linked=self.context_links.state(scope)['enabled']
                snapshot=await asyncio.to_thread(self.context_links.snapshot,message.bot_id,message.user_id,
                    is_owner=True,shared=message.shared,group_id='')
                checkpoint_current()
                if linked and not snapshot:
                    self.store.save_conversation(scope,[],'session_qq_'+uuid4().hex+'.jsonl')
                    qq_delivery.clear_scope(self.store,scope)
            history, session_id = self.store.conversation(history_scope)
            if message.kind=='proactive':
                await self.proactive.deliver_qq(message)
                state=self.proactive.store.get(message.key.rsplit(':',1)[-1])['state']
                self.store.set_state(row_id,'sent' if state=='sent' else 'delivery_unknown' if state=='unknown' else 'processed')
                return
            if message.kind=='poke':
                if self.memory_management and (message.shared or (self.daily_memory and self.daily_memory.fences_scope(message,history_scope))):
                    history,poke_epoch=self.memory_management.prepare_scope(history_scope,history)
                    guard=current_guard()
                    if guard:
                        previous_permission=guard.permission_check
                        guard.permission_check=lambda:(not previous_permission or previous_permission()) and self.memory_management.kb.memory_use.snapshot()==poke_epoch
                await self.social.respond(message,history,session_id)
                with self.store.connect() as db:poke_state=db.execute('select state from qq_poke_events where id=?',(message.key,)).fetchone()
                uncertain=bool(poke_state and poke_state[0]=='unknown')
                self.store.set_state(row_id,'delivery_unknown' if uncertain else 'processed','delivery_uncertain' if uncertain else '')
                return
            if (message.shared and self.memory_management) or (self.action_proposals and not message.group_id):
                # Bind a new private topic before issuing a version/identity-
                # scoped management confirmation. conversation() alone returns
                # a fresh, unpersisted ID when the scope has no prior reply.
                with self.store.connect() as db:
                    db.execute("insert or ignore into conversations values(?,'[]',?,?)",(history_scope,session_id,time.time()))
                history,session_id=self.store.conversation(history_scope)
            memory_epoch = None
            if self.memory_management and (message.shared or (self.daily_memory and self.daily_memory.fences_scope(message,history_scope))):
                unprojected_history=list(history)
                history, memory_epoch = self.memory_management.prepare_scope(history_scope, history)
                if history!=unprojected_history:
                    # Invalidate derived context independently of adding a new
                    # dialogue turn. A control view must not resurrect it or
                    # pretend the original conversation happened just now.
                    with self.store.connect() as db:db.execute('update conversations set history=? where scope=? and session_id=?',
                        (json.dumps(history,ensure_ascii=False),history_scope,session_id))
                # Control-only turns can issue a new preview after the history
                # was filtered, so bind its epoch before processing consent.
                self.memory_management.record_scope(history_scope,memory_epoch)
                message.memory_use_epoch = memory_epoch
            if history:
                with self.store.connect() as db:
                    timing=db.execute('select updated from conversations where scope=?',(history_scope,)).fetchone()
                message.previous_conversation_at=timing[0] if timing else 0
            message.history_times=self.turn_times.history(history_scope,session_id,history) if self.turn_times else []
            from .api_failures import project_legacy_errors
            history_for_model=project_legacy_errors(self.store,history_scope,history)
            retained_times=[];position=0
            for line in history_for_model:
                while position<len(history) and history[position]!=line:position+=1
                if position<len(message.history_times):retained_times.append(message.history_times[position])
                position+=1
            model_history,model_times=self.context_salience.project(history_scope,session_id,history_for_model,
                retained_times,query=message.text,snapshot=message.context_snapshot)
            if message.context_snapshot['enabled']:message.history_times=model_times
            if self.group_context and message.group_id:
                self.group_context.prepare_delivery_scope(message,history_scope)
            if self.media:
                self.media.prepare_delivery_scope(message,history_scope)
            command = message.text.strip().lower()
            continuation = None
            is_owner = message.user_id in self.settings.owner_ids
            natural_receipt=event_control
            natural_tail=message.text if event_control is not None and getattr(message,'event_control_dialogue',False) else ''
            if natural_tail:message.event_control_receipt=getattr(event_control,'payload',str(event_control))
            from .qq_natural_actions import parse_action
            pieces=re.split(r'[，,；;]\s*顺便',message.text,maxsplit=1)
            if event_control is None and parse_action(pieces[0]):
                natural_receipt=await self.natural_actions.execute(message,pieces[0])
                message.context_snapshot=self.context_salience.snapshot(history_scope)
                if len(pieces)>1 and pieces[1].strip() and not message.group_id and is_owner:
                    natural_tail=pieces[1].strip();message.text=natural_tail;message.context_text=natural_tail
                    command=message.text.lower()
                else:natural_tail=''
            managed = natural_receipt if natural_receipt and not natural_tail else await self.owner_command(message)
            consent_answer=None
            if event_control is not None and self.action_proposals and not message.group_id:
                self.action_proposals.supersede(history_scope,session_id)
            if event_control is None and self.action_proposals and not message.group_id:
                decision=self.action_proposals.reply(history_scope,session_id,message.text,self.controls()['revision'],is_owner,reply_to=message.reply_to)
                if decision:
                    consent_answer='这项建议已处理或结果待核对，我不会重复执行。'
                    if decision['status']=='already' and decision.get('kind') in {'save_reference','save_reference_set'} and message.shared and self.memory_management:
                        consent_answer=self.memory_management.references.result(
                            self.memory_management.references.actor(message),session_id,decision['id'],missing=consent_answer)
                    if decision['status']=='unrelated_reply':consent_answer='这条回复没有对应刚才的操作建议，这次没有执行。'
                    if decision['status'] in {'selection_required','invalid_selection'}:consent_answer='请先选择预览中列出的一个编号，例如“选1”；本次尚未保存。'
                    if decision['status']=='rejected':consent_answer='好，这次不执行。'
                    elif decision['status'] in {'expired','stale'}:consent_answer='刚才的建议已过期或设置已变化，这次先不执行。'
                    elif decision['status']=='approved':
                        try:
                            checkpoint_current()
                            if decision['kind']=='search_on':
                                current=self.controls()
                                await self.update_controls(current['settings']|{'web_search_mode':'auto'},current['revision'],command=True,
                                    inherit_fields=[x for x in current['inherit_fields'] if x!='web_search_mode'])
                                consent_answer='已开启自动搜索，遇到需要实时信息的问题会查询。'
                                success=self.settings.web_search_mode=='auto'
                            elif decision['kind'] in {'select_reference','save_reference','save_reference_set'} and message.shared and self.memory_management:
                                service=self.memory_management.references
                                if decision['kind']=='select_reference':
                                    consent_answer=service.select(message,decision['payload']);success=True
                                elif decision['kind']=='save_reference_set':
                                    consent_answer=service.sets.commit(message,decision['id'],decision['payload'])
                                    success=getattr(consent_answer,'committed',False)
                                else:
                                    consent_answer=service.commit(message,decision['payload']['plan'],decision['id'])
                                    success=consent_answer.committed
                            elif decision['kind']=='revise_candidate' and message.shared and self.memory_management:
                                from .qq_memory_access import MemoryActor
                                plan=decision['payload'].get('reference_plan')
                                current=(await asyncio.to_thread(self.memory_management.revise_candidate,
                                    MemoryActor(message.bot_id,message.user_id,True,is_owner),decision['payload']['candidate_id'],
                                    decision['payload'].get('candidate_version'),decision['payload']['content'])) if not plan or self.memory_management.references.valid(message,plan) else None
                                success=bool(current)
                                if current:
                                    payload,_=current
                                    consent_answer=self.memory_management.references.offer_candidate(message,session_id,
                                        payload['candidate_id'],payload['candidate_version'],expires=decision['expires'])
                                else:consent_answer='候选已变化或不可用，请重新核对；本次没有保存。'
                            elif decision['kind']=='save_candidate' and message.shared and self.settings.memory_write_enabled and self.memory_management:
                                from .qq_memory_access import MemoryAccessDenied
                                from .memory_versions import MemoryVersionConflict
                                version=decision['payload'].get('candidate_version')
                                if not version:
                                    consent_answer='候选确认方式已更新，请重新读取候选后确认。'
                                else:
                                    try:
                                        consent_answer=self.memory_management.references.offer_candidate(message,session_id,
                                            decision['payload']['candidate_id'],version,expires=decision['expires'])
                                    except MemoryVersionConflict:
                                        consent_answer='候选内容已变化，请重新读取后确认；本次没有保存。'
                                    except MemoryAccessDenied:
                                        consent_answer='候选来源或保存权限已变化，请重新读取后确认；本次没有保存。'
                                from .qq_responses import ActionProposal
                                success=isinstance(consent_answer,ActionProposal)
                            elif decision['kind']=='save_candidate' and message.shared and not self.memory_management:
                                success=False;consent_answer='完整候选预览服务暂不可用，本次没有保存。'
                            else:
                                success=False;consent_answer='当前权限不允许执行这项建议，这次没有执行。'
                            self.action_proposals.finish(decision['id'],success)
                        except BaseException:
                            self.action_proposals.finish(decision['id'],False)
                            raise
            conversational = command.rstrip('?？!！。').strip()
            natural_continue = conversational in {'继续', '继续说', '接着说', '往下说', '说下去', '把剩下的说完'}
            cached = qq_delivery.lookup(self.store, history_scope)[0] if natural_continue else None
            relationship_decision=None
            if (managed is None and consent_answer is None and not topic_only and not command.startswith('/')
                and not message.media_types and not message.forward_present):
                relationship_decision=await self.relationships.consider(message,model_history,session_id)
                message.relationship_view=relationship_decision['view']
                message.relationship_action=relationship_decision['action']
                if relationship_decision['view']['enabled']:
                    guard=current_guard()
                    if guard:
                        old_relation_check=guard.permission_check
                        guard.permission_check=lambda:(not old_relation_check or old_relation_check()) and self.relationships.valid(message,relationship_decision['view'])
                if relationship_decision['action']=='silent':
                    try:self.relationships.commit(message,relationship_decision,'suppressed')
                    except Exception:self.last_relationship_error='relationship_update_unavailable'
                    if natural_receipt:managed=natural_receipt;natural_tail=''
                    else:
                        self.store.set_state(row_id,'suppressed','')
                        return
            if topic_only:
                answer='已换到新话题；前面的聊天降低优先级，记录和长期记忆保留。'
            elif command.split()[0] in {'/topic','/换话题'}:
                answer=self.context_salience.command(message)
                message.context_snapshot=self.context_salience.snapshot(history_scope)
            elif command.split()[0]=='/experience':
                args=message.text.strip().split();state=self.dialogue_experience.state()
                if len(args)==1 or args[1:] in [['status'],['状态']]:
                    answer='对话体验总开关：'+('开启' if state['values']['enabled'] else '关闭')+'；篇幅引导：'+('开启' if state['values']['concise_replies'] else '关闭')+'；短句语音：'+('开启' if state['values']['short_voice'] else '关闭')+'。/experience on|off；/experience concise on|off；/experience short-voice on|off。此设置不改变人物或原语音/表情模式。'
                elif message.group_id or message.user_id not in self.settings.owner_ids:
                    answer='体验策略修改仅供主人好友私聊。'
                elif len(args)==2 and args[1] in {'on','off'}:
                    self.dialogue_experience.configure(message.user_id,state['revision'],{'enabled':args[1]=='on'})
                    answer='已'+('开启' if args[1]=='on' else '关闭')+'对话体验优化；人物及原有设置保留。'
                elif len(args)==3 and args[1] in {'concise','short-voice'} and args[2] in {'on','off'}:
                    key='concise_replies' if args[1]=='concise' else 'short_voice'
                    self.dialogue_experience.configure(message.user_id,state['revision'],{key:args[2]=='on'})
                    answer='已更新'+('篇幅引导' if key=='concise_replies' else '短句语音')+'；体验总开关'+('开启。' if state['values']['enabled'] else '仍关闭。')
                else:answer='用法：/experience status|on|off；/experience concise on|off；/experience short-voice on|off'
            elif command.split()[0]=='/forward-input':
                answer=self.forward_input.command(message)
            elif command.split()[0]=='/relation':
                answer=self.relationships.command(message)
            elif command.split()[0]=='/events':
                reads=getattr(getattr(self,'memory_management',None),'reads',None)
                try:answer=reads.offer(message,session_id) if reads else None
                except ValueError:
                    from .qq_responses import ConsoleNotice
                    answer=ConsoleNotice('这次查询未能放入后台操作台，请在操作台直接读取；没有执行保存或修改。')
                console=getattr(self.episodes.operations,'console',None)
                if answer is None and console:answer=console.command(message)
                if answer is None:answer=self.episodes.command(message)
            elif consent_answer is not None:
                answer=consent_answer
                generated=False
            elif command in {'/topics','/未完话题'} or conversational in {'未完话题','查看未完话题','还有哪些没说完的话题','还有什么没说完的话题','有哪些没说完的话题'}:
                answer = qq_delivery.topics(self.store, history_scope)
            elif command.split()[0] in {'/continue','/more','/续读'} or cached:
                args = message.text.split()
                if self.settings.delivery_mode == 'legacy':
                    answer = '当前使用旧消息格式；请先开启多消息模式，再续读未过期的结果。'
                elif len(args)>2:
                    answer = '用法：/continue 或 /continue 结果编号。'
                else:
                    record, _ = qq_delivery.lookup(self.store, history_scope, args[1] if len(args)==2 and not cached else None)
                    if record:
                        continuation = record['id']
                        # A new topic pauses automatic old content; an explicit
                        # continuation may resume only that soft pause. Privacy
                        # stops/cancellation and unknown receipts remain intact.
                        with self.store.connect() as db:
                            db.execute("delete from qq_delivery_stops where result_id=? and reason='topic_changed'",(continuation,))
                        answer = ''
                    else:
                        answer = '结果不存在、已过期，或不属于当前会话。'
            elif managed is not None:
                answer = managed
            elif command.split()[0] in {'/proactive','/主动'}:
                answer=self.proactive.command(message) if self.proactive else '主动互动服务暂不可用。'
            elif command.split()[0] in {'/expression','/表情模式'}:
                answer=self.expressions.command(message)
            elif command in {"/whoami", "/owner", "/身份"} or re.fullmatch(r"(?:我是谁|你知道我是谁吗|我是不是你的主人|你认得我吗|我的身份是什么)[?？!！。]*", command):
                answer = f"当前 QQ：{message.user_id}。" + ("你是我已配置并验证的主人（博士）。" if is_owner else "你是当前对话用户，未被配置为主人。")
                answer += "主人身份与长期记忆权限独立；当前" + ("可共用个人长期记忆。" if message.shared else "长期记忆隔离。")
            elif command in {"/help", "/帮助"}:
                from .qq_help import command_help
                answer=command_help(self,message)
            elif command in {"/reset", "/重置"}:
                if self.context_links:self.context_links.revoke(history_scope)
                history, session_id = [], "session_qq_" + uuid4().hex + ".jsonl"
                self.store.save_conversation(history_scope, history, session_id)
                qq_delivery.clear_scope(self.store, history_scope)
                answer = "已重置当前 QQ 对话上下文。已保存的长期记忆与网页历史不会删除。"
            elif command.split()[0] == '/context':
                if not is_owner or message.group_id:
                    answer = '网页话题接续仅供已验证主人好友私聊使用。'
                elif not self.context_links:
                    answer = '网页话题接续尚未准备好。'
                elif command in {'/context off','/context new'}:
                    self.context_links.revoke(history_scope)
                    history,session_id=[], 'session_qq_'+uuid4().hex+'.jsonl'
                    self.store.save_conversation(history_scope,history,session_id)
                    qq_delivery.clear_scope(self.store,history_scope)
                    answer = '已停止网页话题接续，开始独立QQ话题；原网页历史和长期记忆保留。'
                elif command in {'/context','/context status'}:
                    state=self.context_links.state(history_scope)
                    answer=('正在接续网页会话：'+state['session_id']+'。' if state['enabled'] and state['expires']>time.time() and message.shared
                            else '当前QQ话题独立。')+'可在网页QQ接入中选择会话并确认；/context off 停止，/context new 新话题。'
                else:
                    answer='请在网页QQ接入中选择并确认来源会话；QQ文字不能直接建立绑定。'
            elif command in {"/memory", "/记忆"}:
                answer = ("当前为主人好友私聊：共用 Saya 个人长期记忆，可按网页端规则明确要求记住。QQ 和网页对话历史仍独立。"
                          if message.shared else "当前对话隔离：只使用本会话近期上下文，不读取或写入网页个人记忆、日记和项目资料。群内按发言人分别隔离。")
            elif command in {"/status", "/状态"}:
                answer = "Saya QQ 接入运行中。身份：" + ("主人" if is_owner else "普通用户") + "。记忆：" + ("主人私聊共享" if message.shared else "会话隔离")
            elif command.split()[0] in {'/cancel','/tasks'} and not self.settings.task_control_enabled:
                answer = '任务控制尚未开启，可在网页 QQ 接入中开启“任务状态与取消”。'
            else:
                if not self.responder:
                    raise RuntimeError("responder_unconfigured")
                async def respond_with_quote():
                    checkpoint_current()
                    if hasattr(self.responder,'expression_managed'):
                        self.responder.expression_managed=bool(self.expressions and self.expressions.active())
                    await qq_quotes.resolve(self.client,message,self.store)
                    if self.group_context and message.group_id:self.group_context.protect_quote(message)
                    checkpoint_current()
                    return await self.responder(message,model_history,session_id)
                guard = current_guard()
                try:
                    if guard is None:
                        response_task = asyncio.create_task(respond_with_quote())
                        answer = await asyncio.wait_for(response_task,timeout=self.settings.reply_timeout)
                    else:
                        response_task = asyncio.create_task(respond_with_quote())
                        done, _ = await asyncio.wait({response_task},timeout=self.settings.reply_timeout)
                        if not done:
                            guard.timed_out = True
                            guard.registry.cancel(guard.task_id,guard.scope)
                        # Do not cancel a coroutine while its synchronous commit
                        # may still be settling in a worker thread.
                        answer = await response_task
                        checkpoint_current()
                except asyncio.CancelledError:
                    # A cancelled child is not a service shutdown. Only a
                    # cancellation of this worker may take the shutdown path;
                    # otherwise use the normal failure receipt and terminal state.
                    if asyncio.current_task().cancelling():
                        raise
                    if guard is not None:
                        current = guard.registry.get(guard.task_id,guard.scope)
                        if any(e['outcome']=='in_flight' for e in current['effects'].values()):
                            # Stop new effects while an already reserved write
                            # settles; do not send a failure notice ahead of it.
                            guard.registry.cancel(guard.task_id,guard.scope)
                    checkpoint_current()
                    raise RuntimeError('qq_response_cancelled') from None
                generated = True
                if natural_receipt and natural_tail:
                    from .qq_responses import dialogue_body,ControlReceipt
                    if isinstance(answer,ControlReceipt):answer=ControlReceipt(natural_receipt.payload+'\n'+answer.payload)
                    else:answer=qq_delivery.QQAnswer(dialogue_body(answer),[natural_receipt,*getattr(answer,'notes',())],mood_state=getattr(answer,'mood_state',None),context_eligible=getattr(answer,'context_eligible',True))
            from .qq_memory_management import QQMemoryReceipt
            from .qq_group_context import GroupReceipt
            from .qq_media import MediaReceipt
            if isinstance(answer,(GroupReceipt,MediaReceipt)):generated=False
            if isinstance(answer,QQMemoryReceipt) and message.shared:
                # A typed storage receipt is independent of old model content.
                # Do not relabel the former history as current after a mutation.
                generated=False
                memory_epoch=self.memory_management.kb.memory_use.snapshot()
            from .qq_responses import ControlReceipt,dialogue_body
            if isinstance(answer,ControlReceipt):generated=False
            if not generated and not continuation:answer=ControlReceipt(answer)
            expression_plan=None
            if self.expressions and self.expressions.active() and generated and not continuation and consent_answer is None and self.settings.delivery_mode=='chat':
                expression_task = asyncio.create_task(self.expressions.plan(message,answer))
                try:
                    answer,expression_plan = await expression_task
                except asyncio.CancelledError:
                    if asyncio.current_task().cancelling():
                        raise
                    # A failed optional planner must not discard the text.
                    logging.getLogger(__name__).warning('qq_expression_plan_cancelled')
            checkpoint_current()
            guard=current_guard()
            if guard is not None and hasattr(guard,'stop_progress'):
                await guard.stop_progress()
                checkpoint_current()
                guard.registry.set_phase(guard.task_id,guard.scope,'sending')
            if message.group_id and message.group_id not in self.effective_groups():
                self.store.set_state(row_id, "ignored", "group_access_revoked")
                return
            from .qq_maintenance_commands import MaintenanceSourceReceipt
            if isinstance(answer, MaintenanceSourceReceipt):
                self.store.set_state(row_id, 'sending')
                sending = True
                await qq_delivery.send_live_control(self.store, self.client, message, history_scope,
                                                    answer, self.settings.reply_reference)
                self.store.set_state(row_id, 'sent')
                self.last_sent_at = time.time()
                self.last_error = ''
                return
            chunks = split_reply(str(answer or ""), self.settings) if self.settings.delivery_mode == 'legacy' else None
            if chunks == []:
                raise RuntimeError("empty_reply")
            result_id = None if chunks is not None else continuation or qq_delivery.create(
                self.store, message, history_scope, answer, self.settings.bubble_chars if generated else 1500,
                kind='final' if generated else 'control',
                strategy=self.settings.bubble_strategy if generated else 'length', limit=self.settings.reply_max_messages,
                memory_epoch=memory_epoch if generated else None)
            if expression_plan is not None and result_id:self.expressions.bind(result_id,history_scope,expression_plan)
            if generated and not continuation and result_id:experience_observation=(result_id,message,answer,expression_plan or {})
            if result_id and self.forward_input and not continuation:self.forward_input.bind_result(message,result_id)
            if result_id and self.episodes and not continuation:self.episodes.bind_result(message,result_id)
            group_snapshot=getattr(message,'group_snapshot',None)
            if self.group_context and message.group_id and result_id and generated:
                self.group_context.bind_result(message,result_id,group_snapshot)
            def delivery_check():
                return ((not message.context_snapshot['enabled'] or self.context_salience.valid(message.context_snapshot))
                    and (memory_epoch is None or self.memory_management.valid_delivery(history_scope,result_id,memory_epoch))
                    and (not self.media or self.media.valid_delivery(message,result_id))
                    and (not getattr(message,'forward_snapshot',None) or self.forward_input.valid(message,message.forward_snapshot))
                    and (not self.forward_input or self.forward_input.valid_delivery(message,result_id))
                    and self.episodes.operations.can_deliver(message,result_id)
                    and (not self.episodes or self.episodes.valid_delivery(message,result_id))
                    and (not self.daily_memory or self.daily_memory.valid_personal_context(message,getattr(message,'personal_memory_snapshot',None)))
                    and (not self.group_context or not message.group_id or self.group_context.valid_delivery(message,result_id)))
            self.store.set_state(row_id, "sending")
            sending = True
            forwarded=False
            if chunks is not None:
                for chunk in chunks:
                    if not self.episodes.operations.can_deliver(message):
                        raise TaskCancelled('event_operation_source_changed')
                    if getattr(message,'forward_snapshot',None) and not self.forward_input.valid(message,message.forward_snapshot):
                        raise TaskCancelled('forward_source_changed')
                    if self.media and getattr(message,'media_snapshot',None):
                        self.media.check(message,message.media_snapshot)
                    if self.group_context and group_snapshot and not self.group_context.valid(message,group_snapshot):
                        raise TaskCancelled('group_context_changed')
                    if memory_epoch is not None and self.memory_management.kb.memory_use.snapshot() != memory_epoch:
                        from .memory_use import MemoryUseChanged
                        raise MemoryUseChanged('memory_context_changed')
                    with effect_current('send',scope=history_scope) as effect:
                        await self.client.send(message, chunk)
                        effect.committed()
            else:
                if generated and not continuation:
                    forwarded=await self.forward.try_send(message,history_scope,result_id,permission_check=delivery_check)
                notice = None if forwarded else await qq_delivery.send_batch(self.store, self.client, message, history_scope,
                    result_id, self.settings.reply_max_messages, self.settings.reply_reference, expressions=self.expressions if expression_plan is not None else None,
                    memory_check=delivery_check if memory_epoch is not None or (self.group_context and message.group_id) or self.media or self.forward_input
                    or message.context_snapshot['enabled'] or continuation or getattr(message,'event_operation_proposal',None) else None)
                if notice:
                    # This event already owns its final-result identity. Do
                    # not create a second result, replay it, or persist the
                    # rejected model reply as successfully delivered history.
                    invalid_source=not delivery_check()
                    if invalid_source:
                        qq_delivery.freeze(self.store,history_scope,result_id)
                    from .qq_responses import ControlReceipt
                    outcome=getattr(message,'event_control_receipt',None)
                    text=('事件操作结果：'+outcome+'；' if outcome else '')+notice
                    await qq_delivery.send_live_control(self.store,self.client,message,history_scope,
                        ControlReceipt(text),self.settings.reply_reference)
                    self.store.set_state(row_id,'cancelled' if invalid_source else 'processed',
                        'context_changed' if invalid_source else '')
                    self.last_sent_at=time.time()
                    self.last_error='context_changed' if invalid_source else ''
                    return
            checkpoint_current()
            self.episodes.operations.delivered(message,result_id)
            if self.action_proposals and not message.group_id:
                if chunks is not None:confirmed=''.join(chunks)
                else:
                    with self.store.connect() as db:
                        confirmed=''.join(r[0] for r in db.execute("select text from qq_delivery_parts where result_id=? and state='sent' order by seq",(result_id,)))
                self.action_proposals.delivered(history_scope,session_id,confirmed)
            if generated and getattr(answer,'context_eligible',True):
                if getattr(self,'social',None):self.social.commit(message,getattr(answer,'mood_state',None))
                if self.group_context and group_snapshot:
                    if chunks is not None:public_answer=qq_delivery.clean(dialogue_body(answer))
                    else:
                        public_answer=qq_delivery.sent_dialogue(self.store,history_scope,result_id)
                    self.group_context.record_turn(message,group_snapshot,public_answer)
                else:
                    if memory_epoch is not None:
                        self.memory_management.record_scope(history_scope, memory_epoch)
                    canonical = qq_delivery.clean(dialogue_body(answer))[:qq_delivery.MAX_CHARS]
                    history = (history + [f"Doctor: {getattr(message,'context_text',message.text)}", "Saya: " + canonical])[-self.settings.history_turns * 2:]
                    self.store.save_conversation(history_scope, history, session_id)
                    episode_source=self.episodes.source_snapshot(message.episode_snapshot) if getattr(message,'episode_snapshot',None) else None
                    self.context_salience.record(history_scope,session_id,history[-2:],[message.sent_at,time.time()],message.context_snapshot,episode_source=episode_source,
                        event_dependencies=getattr(message,'event_provenance_response',None),user_dependencies=getattr(message,'event_provenance_user',None))
                    if self.turn_times:
                        self.turn_times.record(history_scope,session_id,history[-2:],[message.sent_at,time.time()])
            if generated and not continuation and result_id:
                try:self.dialogue_experience.record(result_id,message,answer,expression_plan or {})
                except Exception:self.last_experience_error='diagnostics_unavailable'
            if self.voice and generated and not continuation and consent_answer is None and result_id and not forwarded:
                try:
                    if self.expressions and self.expressions.active():
                        await self.expressions.after_text(message,history_scope,result_id,session_id=session_id,memory_check=delivery_check)
                    else:await self.voice.after_text(message,history_scope,result_id,session_id=session_id,memory_check=delivery_check)
                except (TaskCancelled,asyncio.CancelledError):raise
                except Exception:pass # Optional voice cannot relabel confirmed text as unknown.
            elif self.expressions and self.expressions.active() and generated and not continuation and consent_answer is None and result_id and not forwarded:
                await self.expressions.after_text(message,history_scope,result_id,session_id=session_id,memory_check=delivery_check)
            if generated and not continuation and result_id and hasattr(message,'experience_started'):
                message.experience_timing['reply_complete_ms']=(time.monotonic()-message.experience_started)*1000
            if relationship_decision:
                try:self.relationships.commit(message,relationship_decision,'sent')
                except Exception:self.last_relationship_error='relationship_update_unavailable'
            if generated and getattr(answer,'context_eligible',True) and not continuation:
                if self.daily_memory and not forwarded and delivery_check():
                    try:await self.daily_memory.observe(message,session_id)
                    except (TaskCancelled,asyncio.CancelledError):pass
                    except Exception:self.daily_memory.last_error='daily_observation_unavailable'
                try:await self.episodes.observe(message,model_history)
                except (TaskCancelled,asyncio.CancelledError):pass
                except Exception:self.last_episode_error='event_update_unavailable'
            self.store.set_state(row_id, "sent")
            self.last_sent_at = time.time()
            self.last_error = ""
        except TaskCancelled as exc:
            guard = current_guard()
            context_notice = bool(guard and str(exc)=='task permission changed' and not sending and
                not guard.timed_out and message.kind=='message' and not message.group_id and
                message.user_id in self.settings.owner_ids and not message.text.lstrip().startswith('/') and
                guard.registry.get(guard.task_id,guard.scope)['state']=='running' and self._task_permission(message))
            if guard is not None:
                guard.registry.cancel(guard.task_id,guard.scope)
                for result_id in guard.result_ids:
                    qq_delivery.freeze(self.store,guard.scope,result_id)
            self.store.set_state(row_id,'cancelled','context_changed' if context_notice else 'cancel_requested')
            if context_notice:
                # This contains no model/source data. Recheck present ingress
                # permission and do not reuse the revoked generation guard.
                # Explicit user cancellation, revoked access and uncertain
                # sends remain silent here; no answer or write is retried.
                from .qq_responses import ControlReceipt
                try:
                    if self._task_permission(message):
                        with bound_guard(None):
                            await qq_delivery.send_live_control(self.store,self.client,message,history_scope,
                                ControlReceipt('生成过程中可用上下文已变化，本次旧回答已停止。请发送新问题，我会按当前记录重新核对。'),
                                self.settings.reply_reference)
                except Exception:
                    self.last_error='context_notice_delivery_uncertain'
        except asyncio.CancelledError:
            guard=current_guard()
            logging.getLogger(__name__).warning('qq_reply_worker_cancelled phase=%s',
                'sending' if sending else 'post_generation' if generated else 'generation')
            if guard:guard.registry.cancel(guard.task_id,guard.scope)
            if response_task is not None and not response_task.done():
                response_task.cancel()
                await asyncio.gather(response_task,return_exceptions=True)
            self.store.set_state(row_id, "delivery_unknown" if sending else "interrupted", "shutdown")
            raise
        except Exception as exc:
            # Content-free code only: HTTP exceptions can contain tokens/URLs.
            code = ("group_request_failed" if message.kind != "message" else
                    "delivery_uncertain" if sending else ("generation_timeout" if isinstance(exc, TimeoutError) else "generation_failed"))
            if not sending and str(exc)=='qq_model_unconfigured':
                code='model_unconfigured'
            self.last_error = code
            trace=exc.__traceback__
            while trace and trace.tb_next:trace=trace.tb_next
            # Log a local site and exception type, never provider exception
            # text, prompts, transport identity, headers or credentials.
            site=Path(trace.tb_frame.f_code.co_filename).name if trace else 'unknown'
            logging.getLogger(__name__).warning('qq_response_failed type=%s site=%s:%s code=%s',
                type(exc).__name__,site,trace.tb_lineno if trace else 0,code)
            self.store.set_state(row_id, "delivery_unknown" if sending else "failed", code)
            if not sending and message.kind == "message":
                # A bounded, static failure notice is useful in QQ, and
                # never forwards provider exceptions, keys, or stack traces.
                try:
                    self.store.set_state(row_id, "sending", code)
                    notice = "Saya 暂时无法生成回答，请稍后发送新消息重试。可查看 QQ 接入页排查模型配置。"
                    if getattr(message,'event_control_receipt',None):
                        from .qq_responses import ControlReceipt
                        notice=ControlReceipt('事件操作结果：'+message.event_control_receipt+'；本次正常答复生成失败，请稍后继续聊天，无需重复操作。')
                    elif any(x.get('status')=='committed' for x in getattr(message,'topic_results',[])):
                        notice='临时话题更新已部分或全部提交，但本次回答生成失败。请先查看事件列表核对，无需重复提交。'
                    elif (getattr(message,'shared_event_receipt',None) or {}).get('status')=='committed':
                        notice='群事件补充已提交，但本次回答生成失败。请先查看事件列表核对，无需重复提交补充。'
                    if self.settings.delivery_mode == 'legacy':
                        await self.client.send(message, notice)
                    else:
                        notice_id = qq_delivery.create(self.store,message,history_scope,notice,
                            self.settings.bubble_chars,kind='system')
                        await qq_delivery.send_batch(self.store,self.client,message,history_scope,
                            notice_id,1,self.settings.reply_reference)
                    self.store.set_state(row_id, "failed", code)
                except TaskCancelled:
                    guard = current_guard()
                    if guard:
                        guard.registry.cancel(guard.task_id,guard.scope)
                    self.store.set_state(row_id,'cancelled','cancel_requested')
                except asyncio.CancelledError:
                    self.store.set_state(row_id, "delivery_unknown", "shutdown_during_failure_notice")
                    raise
                except Exception:
                    self.store.set_state(row_id, "delivery_unknown", "failure_notice_delivery_uncertain")
        finally:
            if experience_observation is not None:
                try:self.dialogue_experience.record(*experience_observation)
                except Exception:self.last_experience_error='diagnostics_unavailable'
            self.queue.task_done()


qq_bridge = QQBridge()
