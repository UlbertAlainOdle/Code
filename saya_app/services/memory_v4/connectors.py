"""Read-only connector event contracts and Attention Inbox."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
import hashlib
import json
import os
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from typing import Any, Callable, Iterable, Mapping

from .models import ValidationError
from .store import MemoryV4Store


READ_STATES = {"unread", "read", "ignored"}
PERMISSION_SCOPES = {"L0", "L1", "L2", "L3"}
TRUST_LEVELS = {"untrusted", "verified", "system"}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _timestamp(value: Any, fallback: str | None = None) -> str:
    text = str(value or "").strip()
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
        return text
    except ValueError:
        return fallback or _now()


@dataclass
class ConnectorEvent:
    id: str
    source: str
    source_event_id: str
    author_id: str
    author_display: str
    channel_id: str | None = None
    thread_id: str | None = None
    occurred_at: str = field(default_factory=_now)
    received_at: str = field(default_factory=_now)
    content: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    raw_url: str | None = None
    trust: str = "untrusted"
    permission_scope: str = "L0"
    read_state: str = "unread"
    dedupe_hash: str = ""
    expires_at: str = ""

    def __post_init__(self) -> None:
        self.id = str(self.id or "").strip()
        self.source = str(self.source or "").strip().lower()
        self.source_event_id = str(self.source_event_id or "").strip()
        self.author_id = str(self.author_id or "").strip()
        self.author_display = str(self.author_display or self.author_id or "unknown")[:160]
        self.channel_id = str(self.channel_id or "").strip() or None
        self.thread_id = str(self.thread_id or "").strip() or None
        self.occurred_at = _timestamp(self.occurred_at)
        self.received_at = _timestamp(self.received_at)
        self.content = str(self.content or "").strip()[:12000]
        if not self.id or not self.source or not self.source_event_id or not self.author_id or not self.content:
            raise ValidationError("connector event identity and content are required")
        if not isinstance(self.attachments, list) or not all(isinstance(item, dict) for item in self.attachments):
            raise ValidationError("attachments must be a list of objects")
        self.attachments = [dict(item) for item in self.attachments[:20]]
        json.dumps(self.attachments, ensure_ascii=False)
        self.raw_url = str(self.raw_url or "").strip()[:1000] or None
        self.trust = str(self.trust or "untrusted").lower()
        self.permission_scope = str(self.permission_scope or "L0").upper()
        self.read_state = str(self.read_state or "unread").lower()
        if self.trust not in TRUST_LEVELS or self.permission_scope not in PERMISSION_SCOPES or self.read_state not in READ_STATES:
            raise ValidationError("invalid connector event trust/permission/read state")
        self.dedupe_hash = str(self.dedupe_hash or hashlib.sha256(f"{self.source}|{self.source_event_id}|{self.content}".encode("utf-8")).hexdigest())
        self.expires_at = _timestamp(self.expires_at, (datetime.fromisoformat(self.received_at.replace("Z", "+00:00")) + timedelta(days=4)).isoformat(timespec="seconds"))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ConnectorEvent":
        names = set(cls.__dataclass_fields__)
        return cls(**{key: data[key] for key in names if key in data})

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "source": self.source, "source_event_id": self.source_event_id,
            "author_display": self.author_display, "channel_id": self.channel_id, "thread_id": self.thread_id,
            "occurred_at": self.occurred_at, "content": self.content[:1000], "attachments": self.attachments[:5],
            "raw_url": self.raw_url, "trust": self.trust, "permission_scope": self.permission_scope,
            "read_state": self.read_state, "expires_at": self.expires_at,
        }


@dataclass
class ConnectorPolicy:
    allowed_sources: set[str] = field(default_factory=set)
    allowed_channels: dict[str, set[str]] = field(default_factory=dict)
    max_scope: str = "L1"
    quiet_sources: set[str] = field(default_factory=set)

    def __post_init__(self) -> None:
        self.allowed_sources = {str(item).lower() for item in self.allowed_sources}
        self.allowed_channels = {str(source).lower(): {str(channel) for channel in channels} for source, channels in (self.allowed_channels or {}).items()}
        self.max_scope = str(self.max_scope or "L1").upper()
        self.quiet_sources = {str(item).lower() for item in self.quiet_sources}
        if self.max_scope not in PERMISSION_SCOPES:
            raise ValidationError("invalid maximum connector permission scope")

    def allows(self, event: ConnectorEvent) -> tuple[bool, str]:
        if event.source not in self.allowed_sources:
            return False, "source_not_whitelisted"
        allowed_channels = self.allowed_channels.get(event.source)
        if allowed_channels and (event.channel_id or "") not in allowed_channels:
            return False, "channel_not_whitelisted"
        if int(event.permission_scope[1]) > int(self.max_scope[1]):
            return False, "permission_scope_exceeded"
        if event.source in self.quiet_sources:
            return False, "source_quiet_hours"
        return True, ""


class ReadOnlyConnector:
    """Adapter with an ingest-only interface; deliberately has no send method."""

    def __init__(self, source: str, fetch: Callable[[], Iterable[Mapping[str, Any] | ConnectorEvent]], *, policy: ConnectorPolicy):
        self.source = str(source or "").lower()
        self.fetch = fetch
        self.policy = policy

    def poll(self) -> list[ConnectorEvent]:
        result = []
        for raw in self.fetch() or []:
            event = raw if isinstance(raw, ConnectorEvent) else ConnectorEvent.from_dict({**dict(raw), "source": self.source})
            result.append(event)
        return result


class HttpReadOnlyConnector(ReadOnlyConnector):
    """JSON HTTP adapter for an explicitly configured read endpoint.

    The transport is injectable for tests. This class intentionally exposes
    only ``poll`` and keeps credentials out of normalized events and public
    capability metadata.
    """

    def __init__(self, source: str, fetch_page: Callable[[str, Mapping[str, str], Mapping[str, str]], Any], endpoint: str, *, policy: ConnectorPolicy):
        self.endpoint = str(endpoint or "").strip()
        self._fetch_page = fetch_page
        super().__init__(source, self._fetch_events, policy=policy)

    @staticmethod
    def default_fetch_page(url: str, headers: Mapping[str, str], params: Mapping[str, str]) -> Any:
        query = urlencode({key: value for key, value in params.items() if value not in (None, "")})
        request_url = f"{url}{'&' if '?' in url else '?'}{query}" if query else url
        request = Request(request_url, headers={str(k): str(v) for k, v in headers.items()}, method="GET")
        with urlopen(request, timeout=20) as response:  # noqa: S310 - endpoint is trusted configuration
            return json.loads(response.read().decode("utf-8"))

    def _fetch_events(self) -> Iterable[Mapping[str, Any]]:
        raise NotImplementedError


class DiscordReadOnlyConnector(HttpReadOnlyConnector):
    """Discord channel-message reader using the official REST API only."""

    def __init__(self, token: str, channel_ids: Iterable[str], *, policy: ConnectorPolicy, fetch_page=None, limit: int = 50):
        self.token = str(token or "").strip()
        self.channel_ids = [str(value).strip() for value in channel_ids if str(value).strip()][:50]
        self.limit = max(1, min(100, int(limit)))
        super().__init__("discord", fetch_page or self.default_fetch_page, "https://discord.com/api/v10", policy=policy)

    @classmethod
    def from_env(cls, *, policy: ConnectorPolicy | None = None) -> "DiscordReadOnlyConnector | None":
        token = os.environ.get("SAYA_DISCORD_BOT_TOKEN", "").strip()
        channels = [item.strip() for item in os.environ.get("SAYA_DISCORD_READ_CHANNELS", "").split(",") if item.strip()]
        if not token or not channels:
            return None
        return cls(token, channels, policy=policy or ConnectorPolicy(allowed_sources={"discord"}, max_scope="L1"))

    def _fetch_events(self) -> Iterable[Mapping[str, Any]]:
        if not self.token:
            return []
        headers = {"Authorization": f"Bot {self.token}", "Accept": "application/json"}
        for channel_id in self.channel_ids:
            payload = self._fetch_page(
                f"{self.endpoint}/channels/{quote(channel_id, safe='')}/messages",
                headers, {"limit": str(self.limit)},
            )
            for raw in payload if isinstance(payload, list) else []:
                if not isinstance(raw, Mapping) or not str(raw.get("id") or "").strip():
                    continue
                author = raw.get("author") if isinstance(raw.get("author"), Mapping) else {}
                attachments = raw.get("attachments") if isinstance(raw.get("attachments"), list) else []
                yield {
                    "id": f"discord:{channel_id}:{raw.get('id')}", "source_event_id": str(raw.get("id")),
                    "author_id": str(author.get("id") or "unknown"),
                    "author_display": str(author.get("global_name") or author.get("username") or author.get("id") or "unknown"),
                    "channel_id": channel_id,
                    "thread_id": str(raw.get("thread", {}).get("id")) if isinstance(raw.get("thread"), Mapping) else None,
                    "occurred_at": raw.get("timestamp"), "content": str(raw.get("content") or ""),
                    "attachments": [{"url": item.get("url"), "filename": item.get("filename")} for item in attachments if isinstance(item, Mapping)][:10],
                    "raw_url": f"https://discord.com/channels/@me/{channel_id}/{raw.get('id')}",
                    "trust": "verified", "permission_scope": "L1",
                }


class XReadOnlyConnector(HttpReadOnlyConnector):
    """X API v2 recent-search reader; never posts, likes, follows, or edits."""

    def __init__(self, bearer_token: str, query: str, *, policy: ConnectorPolicy, fetch_page=None, limit: int = 25):
        self.bearer_token = str(bearer_token or "").strip()
        self.query = str(query or "").strip()[:500]
        self.limit = max(10, min(100, int(limit)))
        super().__init__("x", fetch_page or self.default_fetch_page, "https://api.x.com/2/tweets/search/recent", policy=policy)

    @classmethod
    def from_env(cls, *, policy: ConnectorPolicy | None = None) -> "XReadOnlyConnector | None":
        token = os.environ.get("SAYA_X_BEARER_TOKEN", "").strip()
        query = os.environ.get("SAYA_X_READ_QUERY", "").strip()
        if not token or not query:
            return None
        return cls(token, query, policy=policy or ConnectorPolicy(allowed_sources={"x"}, max_scope="L1"))

    def _fetch_events(self) -> Iterable[Mapping[str, Any]]:
        if not self.bearer_token or not self.query:
            return []
        headers = {"Authorization": f"Bearer {self.bearer_token}", "Accept": "application/json"}
        payload = self._fetch_page(
            self.endpoint, headers,
            {"query": self.query, "max_results": str(self.limit), "tweet.fields": "created_at,author_id,conversation_id"},
        )
        users = payload.get("includes", {}).get("users", []) if isinstance(payload, Mapping) else []
        names = {str(item.get("id")): str(item.get("name") or item.get("username") or item.get("id")) for item in users if isinstance(item, Mapping)}
        tweets = payload.get("data", []) if isinstance(payload, Mapping) else []
        for raw in tweets if isinstance(tweets, list) else []:
            if not isinstance(raw, Mapping) or not str(raw.get("id") or "").strip():
                continue
            tweet_id = str(raw.get("id"))
            author_id = str(raw.get("author_id") or "unknown")
            yield {
                "id": f"x:{tweet_id}", "source_event_id": tweet_id, "author_id": author_id,
                "author_display": names.get(author_id, author_id),
                "thread_id": str(raw.get("conversation_id") or "") or None,
                "occurred_at": raw.get("created_at"), "content": str(raw.get("text") or ""),
                "raw_url": f"https://x.com/i/web/status/{tweet_id}", "trust": "verified", "permission_scope": "L1",
            }


class AttentionInbox:
    """Whitelist, dedupe, cooldown, digest, and TTL for external read-only events."""

    IMPORTANT_MARKERS = ("urgent", "important", "deadline", "截止", "紧急", "重要", "提醒", "@doctor", "@saya")

    def __init__(self, store: MemoryV4Store, *, policy: ConnectorPolicy | None = None, cooldown_seconds: int = 900):
        self.store = store
        self.policy = policy or ConnectorPolicy()
        self.cooldown_seconds = max(0, int(cooldown_seconds))

    def ingest(self, event: ConnectorEvent | Mapping[str, Any]) -> dict[str, Any]:
        item = event if isinstance(event, ConnectorEvent) else ConnectorEvent.from_dict(event)
        allowed, reason = self.policy.allows(item)
        if not allowed:
            return {"status": "blocked", "reason": reason, "event_id": item.id}
        result = self.store.upsert_connector_event(item)
        return {"status": "accepted" if result["status"] == "created" else "duplicate", "event": item.to_public_dict()}

    def poll(self, connector: ReadOnlyConnector) -> dict[str, Any]:
        accepted = blocked = duplicates = 0
        for event in connector.poll():
            result = self.ingest(event)
            if result["status"] == "accepted":
                accepted += 1
            elif result["status"] == "duplicate":
                duplicates += 1
            else:
                blocked += 1
        return {"status": "success", "accepted": accepted, "duplicates": duplicates, "blocked": blocked}

    def list_attention(self, *, limit: int = 20) -> list[dict[str, Any]]:
        events = self.store.list_connector_events(unread_only=True, limit=max(1, int(limit) * 4))
        scored = [{**event, "priority": self._priority(event), "reason": self._priority_reason(event)} for event in events]
        scored.sort(key=lambda item: (item["priority"], item["occurred_at"], item["id"]), reverse=True)
        return scored[:max(1, int(limit))]

    def digest(self, *, limit: int = 5, now: str | None = None) -> dict[str, Any]:
        selected = []
        groups: dict[str, str] = {}
        current = datetime.fromisoformat((now or _now()).replace("Z", "+00:00"))
        for event in self.list_attention(limit=max(20, int(limit) * 4)):
            key = f"{event.get('source')}:{event.get('thread_id') or event.get('channel_id') or event.get('author_id')}"
            previous = groups.get(key)
            persisted_previous = self.store.get_meta(f"attention:last_digest:{key}")
            previous = previous or persisted_previous
            if previous:
                try:
                    if abs((current - datetime.fromisoformat(previous.replace("Z", "+00:00"))).total_seconds()) < self.cooldown_seconds:
                        continue
                except ValueError:
                    pass
            groups[key] = str(event.get("occurred_at"))
            self.store.set_meta(f"attention:last_digest:{key}", str(now or _now()))
            selected.append({
                "id": event["id"], "source": event["source"], "author_display": event["author_display"],
                "content": event["content"][:600], "priority": event["priority"], "reason": event["reason"],
                "occurred_at": event["occurred_at"], "raw_url": event.get("raw_url"),
            })
            if len(selected) >= max(1, int(limit)):
                break
        return {"status": "success", "items": selected, "count": len(selected), "cooldown_seconds": self.cooldown_seconds}

    def mark_read(self, event_ids: Iterable[str], *, read_state: str = "read") -> int:
        return sum(1 for event_id in event_ids or [] if self.store.mark_connector_event(str(event_id), read_state=read_state))

    def purge_expired(self, before: str | None = None) -> int:
        return self.store.purge_connector_events(before=before)

    @classmethod
    def _priority(cls, event: Mapping[str, Any]) -> float:
        content = str(event.get("content") or "").lower()
        score = 0.5
        if any(marker in content for marker in cls.IMPORTANT_MARKERS):
            score += 0.35
        if event.get("trust") == "verified":
            score += 0.1
        if event.get("permission_scope") == "L1":
            score += 0.05
        return min(1.0, score)

    @classmethod
    def _priority_reason(cls, event: Mapping[str, Any]) -> str:
        content = str(event.get("content") or "").lower()
        return "important_marker" if any(marker in content for marker in cls.IMPORTANT_MARKERS) else "new_external_event"
