"""External game bridge (pattern ported from airi-main's minecraft-bot
integration: the external bot pushes context updates, the character can push
high-level commands back).

An external game bot (a Minecraft bot, a Factorio client, or any script) POSTs
status text here, the latest status per source joins the chat prompt (airi's
ReplaceSelf context strategy), and commands queue for the bot to poll - the
HTTP equivalent of airi's ``context:update`` / ``spark:command`` WebSocket
events. Online detection uses a heartbeat window instead of airi's module
registry health events.
"""

import re
import threading
import time
import uuid

ONLINE_WINDOW_S = 300        # heartbeat seen within 5 minutes -> online
CONTEXT_FRESH_S = 1800       # sources silent for 30+ minutes leave the prompt
MAX_EVENTS_PER_SOURCE = 50
MAX_PENDING_COMMANDS = 20
COMMAND_DEDUPE_S = 60        # identical command text within a minute is merged

KNOWN_SOURCES = {
    "minecraft": "Minecraft",
    "factorio": "Factorio",
}

SOURCE_ID_RE = re.compile(r"^[a-z0-9_-]{1,24}$")


def validate_source(source):
    source = str(source or "").strip().lower()
    if not SOURCE_ID_RE.match(source):
        raise ValueError("source id must be 1-24 chars of a-z 0-9 _ -")
    return source


class GameBridge:
    def __init__(self):
        self._lock = threading.RLock()
        self.sources = {}

    # -- internals -----------------------------------------------------------
    def _entry(self, source):
        source = validate_source(source)
        if source not in self.sources:
            self.sources[source] = {
                "label": KNOWN_SOURCES.get(source, source),
                "latest_text": "",
                "latest_at": 0.0,
                "events": [],      # [(ts, lane, text)]
                "commands": [],    # [{id, ts, text, origin, status}]
                "_seen_texts": {},  # text -> ts for queue dedupe
            }
        return self.sources[source]

    # -- external bot side ----------------------------------------------------
    def push_status(self, source, text, lane="general"):
        """Bot-side context:update. ``lane='status'`` mirrors airi's
        'minecraft:status' lane: recorded as history but never used as the
        live context line."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("status text is empty")
        entry = self._entry(source)
        now = time.time()
        with self._lock:
            entry["events"].append((now, str(lane or "general"), text[:400]))
            del entry["events"][:-MAX_EVENTS_PER_SOURCE]
            if lane != "status":
                entry["latest_text"] = text[:1200]
                entry["latest_at"] = now
        return self._view(source, entry)

    def pop_commands(self, source, take=5):
        """Bot-side command pull: hand over pending spark commands."""
        entry = self._entry(source)
        take = max(1, min(int(take or 5), 10))
        with self._lock:
            pending = [c for c in entry["commands"] if c["status"] == "pending"][:take]
            for command in pending:
                command["status"] = "delivered"
            return pending

    # -- saya / doctor side ---------------------------------------------------
    def queue_command(self, source, text, origin="saya"):
        """Queue a high-level command for the external bot (spark:command)."""
        text = str(text or "").strip()
        if not text:
            raise ValueError("command text is empty")
        entry = self._entry(source)
        now = time.time()
        with self._lock:
            seen = entry["_seen_texts"].get(text)
            if seen and now - seen < COMMAND_DEDUPE_S:
                # Streaming chunks can rediscover a command while it is still
                # pending. Once delivered, the same instruction must be
                # allowed again (e.g. repeatedly saying "come").
                still_pending = any(
                    item["text"] == text and item["status"] == "pending"
                    for item in entry["commands"]
                )
                if still_pending:
                    return None
            entry["_seen_texts"][text] = now
            command = {
                "id": str(uuid.uuid4())[:8],
                "ts": now,
                "text": text[:400],
                "origin": origin if origin in ("saya", "doctor") else "saya",
                "status": "pending",
            }
            entry["commands"].append(command)
            del entry["commands"][:-MAX_PENDING_COMMANDS]
            return command

    # -- views ----------------------------------------------------------------
    def _view(self, source, entry):
        now = time.time()
        return {
            "source": source,
            "label": entry["label"],
            "online": (now - entry["latest_at"]) < ONLINE_WINDOW_S if entry["latest_at"] else False,
            "latest_text": entry["latest_text"],
            "latest_at": entry["latest_at"],
            "events": [{"ts": ts, "lane": lane, "text": text} for ts, lane, text in entry["events"][-20:]],
            "pending": sum(1 for c in entry["commands"] if c["status"] == "pending"),
            "commands": list(entry["commands"][-10:]),
        }

    def state(self):
        with self._lock:
            items = [self._view(s, e) for s, e in self.sources.items()]
        items.sort(key=lambda item: (not item["online"], item["source"]))
        return {"sources": items, "known_sources": KNOWN_SOURCES}

    def active_source_ids(self):
        """Sources that pushed status recently enough to receive commands."""
        with self._lock:
            now = time.time()
            return [
                source
                for source, entry in self.sources.items()
                if entry["latest_at"] and (now - entry["latest_at"]) < CONTEXT_FRESH_S
            ]

    def prompt_context(self):
        """Prompt block with only live sources (airi injects nothing until a
        minecraft service has actually been observed)."""
        with self._lock:
            live = [
                (source, entry)
                for source, entry in self.sources.items()
                if entry["latest_at"] and (time.time() - entry["latest_at"]) < CONTEXT_FRESH_S
            ]
        if not live:
            return ""
        lines = []
        for source, entry in live:
            status = "online" if (time.time() - entry["latest_at"]) < ONLINE_WINDOW_S else "offline (stale)"
            text = entry["latest_text"].strip().replace("\n", " ")
            lines.append(
                f"- {entry['label']} bridge is {status}. Latest bot context: {text or 'none yet'}"
            )
        return (
            "[System: Game bridge]\n"
            "Saya can oversee external game bots through these live bridges.\n"
            "Bot status is side context; do not assume a bot can act unless its status is online.\n"
            + "\n".join(lines) + "\n"
            "To give a bot a high-level instruction, end the reply with one short tag like "
            "[MC: follow Doctor] (MC = Minecraft, up to 12 words, imperative). The tag is stripped "
            "from the visible reply and queued for the bot to poll.\n\n"
        )


game_bridge = GameBridge()
