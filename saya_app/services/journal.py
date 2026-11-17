"""Saya's diary (feature ported from airi-main's text_journal tool and the
character notebook's diary entries).

Saya can write a diary entry about the day using the managed daily-activity
snapshot plus check-in/emotion context, Doctor can add entries manually, and
entries can be fed back into chats as context.
"""

import json
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path

import requests

from ..config import (
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    KNOWLEDGE_BASE_DIR,
    PROFILE_PATH,
)

JOURNAL_FILE = Path(os.environ.get("SAYA_JOURNAL_FILE", KNOWLEDGE_BASE_DIR / "journal.json"))


def _persona_summary(limit=500):
    try:
        text = PROFILE_PATH.read_text(encoding="utf-8", errors="ignore")
        return " ".join(text.split())[:limit]
    except Exception:
        return ""


class JournalService:
    def __init__(self, file_path=None):
        self.file_path = Path(file_path or JOURNAL_FILE)
        self._lock = threading.RLock()
        self.data = {"entries": []}
        self._load()

    def _load(self):
        try:
            if self.file_path.is_file():
                self.data = json.loads(self.file_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[journal] Failed to load: {exc}")
            self.data = {"entries": []}
        self.data.setdefault("entries", [])

    def _save(self):
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.file_path.with_suffix(".tmp")
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
        os.replace(temp_path, self.file_path)

    def list(self, limit=50, date=None):
        with self._lock:
            entries = self.data["entries"]
            if date:
                entries = [item for item in entries if item.get("date") == date]
            result = list(reversed(entries))[: max(1, int(limit or 50))]
            return {"entries": result, "total": len(self.data["entries"])}

    def add(self, content, title=None, source="doctor", mood=None, date=None):
        content = str(content or "").strip()
        if not content:
            raise ValueError("Diary content is empty")
        now = datetime.now()
        entry = {
            "id": str(uuid.uuid4()),
            "date": date or now.strftime("%Y-%m-%d"),
            "created_at": now.isoformat(timespec="seconds"),
            "title": str(title or "").strip() or now.strftime("%B %d diary"),
            "content": content[:8000],
            "source": source if source in ("saya", "doctor") else "doctor",
            "mood": str(mood or "").strip()[:24] or None,
        }
        with self._lock:
            self.data["entries"].append(entry)
            self._save()
        return entry

    def delete(self, entry_id):
        with self._lock:
            before = len(self.data["entries"])
            self.data["entries"] = [item for item in self.data["entries"] if item.get("id") != entry_id]
            if len(self.data["entries"]) == before:
                raise KeyError("Diary entry not found")
            self._save()
            return {"status": "success"}

    # -- context ------------------------------------------------------------
    def _context_blocks(self, extra_context=""):
        blocks = []
        try:
            from core_services import checkin_manager, kb_system

            activity = kb_system.build_daily_activity_prompt_context()
            if activity:
                blocks.append(activity.strip())
            today = datetime.now().strftime("%Y-%m-%d")
            try:
                summary = checkin_manager.get_daily_summary(today)
                if summary:
                    blocks.append(f"Today's check-in summary: {summary}")
            except Exception:
                pass
        except Exception as exc:
            print(f"[journal] Context collection skipped: {exc}")
        if extra_context:
            blocks.append(str(extra_context).strip())
        return "\n\n".join(block for block in blocks if block)

    # -- generation ---------------------------------------------------------
    def generate(self, credentials=None, extra_context="", language="auto"):
        """Ask the model to write today's diary entry in Saya's voice."""
        credentials = credentials or {}
        api_key = str(credentials.get("api_key") or os.environ.get("API_KEY", "") or "").strip()
        api_url = str(credentials.get("api_url") or os.environ.get("API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).rstrip("/")
        model = str(credentials.get("api_model_name") or os.environ.get("API_MODEL", DEFAULT_API_MODEL) or DEFAULT_API_MODEL).strip()
        if not api_key:
            raise RuntimeError("Diary writing needs an API key. Add one in API settings.")
        context = self._context_blocks(extra_context)
        if not context:
            raise RuntimeError("Nothing to write about yet today. Chat or finish some check-ins first.")
        persona = _persona_summary()
        lang_rule = (
            "Write in the same language as the context (default English)."
            if str(language).lower() in ("", "auto")
            else ("Write in Simplified Chinese." if str(language).lower() in ("zh-cn", "zh", "chinese") else "Write in English.")
        )
        system = (
            "You are Saya, a warm virtual companion, writing your own diary entry for today. "
            "Write it in first person as Saya, about Doctor's day and your time together. "
            "Be genuine, specific to the supplied context, 120-220 words, no headings or lists."
            + (f"\nCharacter setting (excerpt):\n{persona}" if persona else "")
        )
        user = (
            f"Today's context:\n{context[:6000]}\n\n"
            f"Write today's diary entry in Saya's voice. {lang_rule}"
        )
        url = api_url + "/chat/completions" if not api_url.lower().endswith("/chat/completions") else api_url
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                "stream": False,
                "max_tokens": 700,
                "temperature": 0.85,
            },
            timeout=(10, 120),
        )
        if response.status_code != 200:
            raise RuntimeError(f"Diary model request failed: HTTP {response.status_code}")
        choices = response.json().get("choices") or []
        content = str(((choices[0] or {}).get("message") or {}).get("content") or "").strip() if choices else ""
        if not content:
            raise RuntimeError("Diary model returned no content")
        first_line = content.splitlines()[0].strip()
        title = first_line[:40] if len(first_line) <= 40 else first_line[:40] + "..."
        return self.add(content, title=title, source="saya")

    def prompt_context(self, limit=2):
        """Small block for chat prompts so Saya remembers her recent diary."""
        with self._lock:
            recent = list(reversed(self.data["entries"]))[: max(0, int(limit or 2))]
        if not recent:
            return ""
        lines = [
            f"- [{item.get('date')}] {item.get('title')}: {str(item.get('content'))[:200]}"
            for item in recent
        ]
        return (
            "[System: Saya's recent diary]\n"
            "These are diary entries Saya wrote herself. Reference them naturally if relevant.\n"
            + "\n".join(lines)
            + "\n\n"
        )


journal_service = JournalService()
