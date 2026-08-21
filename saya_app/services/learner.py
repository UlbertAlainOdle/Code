"""Persistent scheduled learning tasks for the Learner role.

The service deliberately accepts topics and selected creator ids, never user
supplied URLs.  Web tasks reuse Saya's guarded reader and source-quality
classification.  Credentials are supplied for one launch/run and are never
written to the state file.
"""

from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import html
import json
import os
from pathlib import Path
import re
import threading
import uuid

import requests

from saya_app.config import (
    BRAVE_SEARCH_API_KEY,
    DEFAULT_API_MODEL,
    DEFAULT_API_URL,
    KNOWLEDGE_BASE_DIR,
)
from saya_app.services.web_research.citation_validator import validate_response_citations


_PRIMARY_SOURCE_TYPES = {
    "government", "standards", "official", "documentation", "research",
}
_SECONDARY_AUTHORITY_TYPES = {"reputable_media"}
_CROSS_CHECKABLE_TYPES = {"web", "repository"}
_URL_RE = re.compile(
    r"(?:https?://|www\.|\b(?:[a-z0-9-]+\.)+[a-z]{2,63}(?:[/:?#]|\b)|\b(?:\d{1,3}\.){3}\d{1,3}(?:[/:?#]|\b))",
    re.I,
)
_WORD_RE = re.compile(r"[A-Za-z0-9_+-]{2,}|[\u4e00-\u9fff]")
_NEWS_WORD_RE = re.compile(
    r"新闻|最新消息|刚刚|今天|昨日|本周|news|breaking|latest\s+news",
    re.I,
)
_JAPAN_STUDY_RE = re.compile(
    r"日本|赴日|Japan|Japanese|留学|留学生|学生签证|在留资格|大学院|博士",
    re.I,
)
_TERMINAL_SEARCH_WARNINGS = {
    "search_missing_key", "search_auth_error", "search_rate_limited",
    "search_network_error", "search_server_error", "search_invalid_json",
}
_GENERIC_SNIPPET_RE = re.compile(
    r"(?:sign\s*up|subscribe|newsletter|in\s+your\s+inbox|accept\s+cookies?|cookie\s+policy|"
    r"privacy\s+policy|enable\s+javascript|click\s+here|log\s*in|register|"
    r"订阅|注册|登录|隐私政策|Cookie|启用\s*JavaScript|点击这里)",
    re.I,
)
_FACT_SIGNAL_RE = re.compile(
    r"(?:announc|launch|release|publish|approve|adopt|introduc|implement|report|study|survey|"
    r"research|find|found|increase|decrease|funding|rais|partner|regulat|policy|guidance|"
    r"发布|宣布|推出|实施|更新|批准|采用|研究|调查|报告|显示|发现|增长|下降|融资|合作|"
    r"政策|法规|指南|発表|導入|調査|研究|規制|認可|公表)",
    re.I,
)
_TOPIC_STOPWORDS = {
    "about", "after", "and", "before", "best", "current", "development", "evidence",
    "find", "for", "from", "information", "latest", "news", "official", "policy",
    "reliable", "report", "source", "study", "task", "the", "this", "update", "with",
    "了解", "一年", "可能", "影响", "出国", "查找", "告诉", "目前", "发展", "最新",
    "相关", "信息", "新闻", "政策", "报告", "情况", "哪个", "国家",
}
_LANGUAGE_LABELS = {
    "zh-cn": "简体中文", "zh-tw": "繁體中文", "en": "English", "ja": "日本語",
    "ko": "한국어", "es": "español", "fr": "français", "de": "Deutsch",
    "ru": "русский", "pt": "português", "ar": "العربية", "hi": "हिन्दी", "it": "italiano",
}


def _now():
    return datetime.now()


def _iso(value=None):
    return (value or _now()).isoformat(timespec="seconds")


def _clean(value, limit=4000):
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _clean_evidence(value, limit=6000):
    text = html.unescape(str(value or ""))
    text = re.sub(r"<[^>]{1,200}>", " ", text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def _parse_datetime(value):
    try:
        return datetime.fromisoformat(str(value or "").replace("Z", "+00:00")).replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def _completion_content(payload):
    """Extract visible text from common OpenAI-compatible response shapes."""
    if not isinstance(payload, dict):
        return "", "invalid_payload"
    choices = payload.get("choices") or []
    if choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content.strip(), ""
        if isinstance(content, list):
            parts = []
            for item in content:
                if not isinstance(item, dict):
                    continue
                value = item.get("text") or item.get("content")
                if isinstance(value, dict):
                    value = value.get("value") or value.get("text")
                if isinstance(value, str) and value.strip():
                    parts.append(value.strip())
            if parts:
                return "\n".join(parts), ""
        direct = choice.get("text")
        if isinstance(direct, str) and direct.strip():
            return direct.strip(), ""
        reasoning = message.get("reasoning_content") or choice.get("reasoning_content")
        finish_reason = str(choice.get("finish_reason") or "").strip()
        if reasoning and not content:
            return "", f"reasoning_without_visible_content:{finish_reason or 'unknown_finish'}"
        return "", f"empty_choice:{finish_reason or 'unknown_finish'}"

    output_text = payload.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text.strip(), ""
    output = payload.get("output") or []
    parts = []
    for item in output if isinstance(output, list) else []:
        if not isinstance(item, dict):
            continue
        for content_item in item.get("content") or []:
            if not isinstance(content_item, dict):
                continue
            value = content_item.get("text")
            if isinstance(value, dict):
                value = value.get("value") or value.get("text")
            if isinstance(value, str) and value.strip():
                parts.append(value.strip())
    if parts:
        return "\n".join(parts), ""
    return "", "response_has_no_supported_text_field"


class LearnerService:
    """Owns Learner task scheduling, execution history, and memory curation."""

    SCHEMA_VERSION = 3
    MAX_RUNS = 120

    def __init__(self, file_path=None, executor=None):
        self.path = Path(file_path or (KNOWLEDGE_BASE_DIR / "learner_tasks.json"))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._executor = executor or ThreadPoolExecutor(max_workers=2, thread_name_prefix="saya-learner")
        self.session_id = str(uuid.uuid4())
        self._futures = {}
        self._delete_plans = {}
        self._shutdown_event = threading.Event()
        self.web_research = None
        self.video_service = None
        self.knowledge_base = None
        self.local_completion = None
        self.response_language_provider = None
        self._last_completion_error = ""
        self.data = self._load()
        self._migrate_legacy_retry_state()
        self._recover_interrupted_runs()
        self._migrate_run_read_state()

    def configure(
        self, web_research=None, video_service=None, knowledge_base=None,
        local_completion=None, response_language_provider=None,
    ):
        self.web_research = web_research or self.web_research
        self.video_service = video_service or self.video_service
        self.knowledge_base = knowledge_base or self.knowledge_base
        self.local_completion = local_completion or self.local_completion
        self.response_language_provider = response_language_provider or self.response_language_provider
        return self

    def _default_data(self):
        return {"schema_version": self.SCHEMA_VERSION, "tasks": [], "runs": []}

    def _load(self):
        if not self.path.exists():
            return self._default_data()
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                return self._default_data()
            loaded.setdefault("tasks", [])
            loaded.setdefault("runs", [])
            return loaded
        except Exception:
            return self._default_data()

    def _save(self):
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(self.data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temp, self.path)

    def _recover_interrupted_runs(self):
        changed = False
        for run in self.data.get("runs", []):
            if run.get("status") in {"queued", "running"}:
                run.update({
                    "status": "interrupted",
                    "stage": "waiting_to_retry",
                    "finished_at": _iso(),
                    "error": "The application stopped during this run. It can be run again.",
                    "is_read": False,
                    "read_at": None,
                })
                changed = True
        if changed:
            self._save()

    def _migrate_run_read_state(self):
        """Make old terminal activity visible once, then persist its read state."""
        changed = self.data.get("schema_version") != self.SCHEMA_VERSION
        self.data["schema_version"] = self.SCHEMA_VERSION
        for run in self.data.get("runs", []):
            if "is_read" not in run:
                run["is_read"] = False
                changed = True
            if "read_at" not in run:
                run["read_at"] = None
                changed = True
        if changed:
            self._save()

    def _migrate_legacy_retry_state(self):
        """Remove pre-audit automatic retries from failed one-time tasks."""
        changed = False
        for task in self.data.get("tasks", []):
            if (
                task.get("schedule_type") == "once"
                and task.get("last_status") == "failed"
                and task.get("last_run_at")
                and (task.get("enabled") or task.get("next_run_at"))
            ):
                task["enabled"] = False
                task["next_run_at"] = None
                task["updated_at"] = _iso()
                changed = True
        if changed:
            self._save()

    # ---------------------------------------------------------------- state
    def get_state(self):
        with self._lock:
            tasks = [self._public_task(item) for item in self.data.get("tasks", [])]
            runs = [self._public_run(item) for item in self.data.get("runs", [])]
            orphaned_memories = self._orphaned_learner_memories()
        runs.sort(key=lambda item: item.get("created_at", ""), reverse=True)
        counts = {
            "running": sum(item.get("status") in {"queued", "running"} for item in runs),
            "completed": sum(item.get("status") in {"completed", "completed_caution"} for item in runs),
            "caution": sum(item.get("status") == "completed_caution" for item in runs),
            "failed": sum(item.get("status") in {"failed", "interrupted"} for item in runs),
            "unread": sum(
                item.get("status") not in {"queued", "running"} and not item.get("is_read", False)
                for item in runs
            ),
            "due": sum(self._task_due(item) for item in tasks if item.get("enabled")),
            "orphaned_memories": len(orphaned_memories),
        }
        creators = []
        video_options = []
        if self.video_service:
            video_state = self.video_service.get_state()
            creators = [
                {
                    "id": item.get("id"),
                    "platform": item.get("platform"),
                    "name": item.get("display_name") or item.get("name"),
                    "enabled": item.get("enabled", True),
                }
                for item in video_state.get("creators", [])
            ]
            video_options = [
                self._public_video_option(item)
                for item in (video_state.get("management_videos") or [])[:100]
            ]
        return {
            "tasks": tasks, "runs": runs[:self.MAX_RUNS], "counts": counts,
            "creators": creators, "video_options": video_options,
            "orphaned_memories": [
                {"id": item.get("id"), "title": item.get("title") or "Untitled Learner memory"}
                for item in orphaned_memories[:50]
            ],
        }

    def _public_task(self, task):
        return {key: value for key, value in task.items() if key != "processed_source_ids"}

    @staticmethod
    def _public_video_option(video):
        return {
            "id": video.get("id"),
            "platform": video.get("platform"),
            "creator_id": video.get("creator_id"),
            "creator_name": video.get("creator_name"),
            "title": video.get("title"),
            "url": video.get("url"),
            "published_at": video.get("published_at"),
            "thumbnail": video.get("thumbnail"),
            "view_count": video.get("view_count"),
            "watched": bool(video.get("watched")),
        }

    def _public_run(self, run):
        return dict(run)

    def get_run(self, run_id):
        with self._lock:
            run = next((item for item in self.data.get("runs", []) if item.get("id") == run_id), None)
            if not run:
                raise ValueError("Learning run not found")
            return self._public_run(run)

    def set_run_read(self, run_id, is_read=True):
        with self._lock:
            run = next((item for item in self.data.get("runs", []) if item.get("id") == run_id), None)
            if not run:
                raise ValueError("Learning run not found")
            run["is_read"] = bool(is_read)
            run["read_at"] = _iso() if is_read else None
            self._save()
            return self._public_run(run)

    def mark_all_runs_read(self):
        changed = 0
        with self._lock:
            for run in self.data.get("runs", []):
                if run.get("status") in {"queued", "running"} or run.get("is_read", False):
                    continue
                run["is_read"] = True
                run["read_at"] = _iso()
                changed += 1
            if changed:
                self._save()
        return changed

    def delete_run(self, run_id):
        preview = self.preview_run_deletion([run_id])
        return self.delete_runs(
            [run_id], confirmation_token=preview.get("confirmation_token"),
        ).get("removed", 0) == 1

    def _learner_memory_ids(self, run_ids=None, task_ids=None):
        if not self.knowledge_base:
            return []
        run_ids = {str(value) for value in (run_ids or []) if value}
        task_ids = {str(value) for value in (task_ids or []) if value}
        memory_ids = []
        for memory in list(self.knowledge_base.memories):
            detail = memory.get("source_detail") if isinstance(memory.get("source_detail"), dict) else {}
            run_match = run_ids and str(detail.get("learner_run_id") or "") in run_ids
            task_match = task_ids and str(detail.get("learner_task_id") or "") in task_ids
            if str(memory.get("source") or "") == "learner" and (run_match or task_match):
                memory_ids.append(str(memory.get("id")))
        return list(dict.fromkeys(memory_ids))

    def _orphaned_learner_memories(self):
        if not self.knowledge_base:
            return []
        task_ids = {
            str(item.get("id")) for item in self.data.get("tasks", [])
            if isinstance(item, dict) and item.get("id")
        }
        run_ids = {
            str(item.get("id")) for item in self.data.get("runs", [])
            if isinstance(item, dict) and item.get("id")
        }
        orphaned = []
        for memory in list(self.knowledge_base.memories):
            if str(memory.get("source") or "") != "learner":
                continue
            detail = memory.get("source_detail") if isinstance(memory.get("source_detail"), dict) else {}
            task_id = str(detail.get("learner_task_id") or "")
            run_id = str(detail.get("learner_run_id") or "")
            if (
                not task_id or not run_id
                or run_id not in run_ids or task_id not in task_ids
            ):
                orphaned.append(memory)
        return orphaned

    def preview_orphaned_memory_deletion(self):
        with self._lock:
            memory_ids = [str(item.get("id")) for item in self._orphaned_learner_memories()]
            memory_preview = self.knowledge_base.preview_memory_deletion(
                memory_ids, reason="learner_orphan_cleanup",
            ) if self.knowledge_base else {
                "confirmation_token": str(uuid.uuid4()), "delete_count": 0,
                "blocked": [], "rewired_links": [], "warnings": [],
                "cleared_reference_count": 0, "removed_manual_edge_count": 0,
            }
            token = memory_preview.get("confirmation_token")
            self._remember_delete_plan(
                token, "orphan_memories", memory_ids, memory_ids, memory_preview,
            )
            return {
                "status": "confirmation_required", "memory_count": len(memory_ids),
                "memory_ids": memory_ids, "memory_preview": memory_preview,
                "confirmation_token": token,
            }

    def delete_orphaned_memories(self, confirmation_token):
        with self._lock:
            token = str(confirmation_token or "")
            plan = self._delete_plans.get(token)
            if not plan or plan.get("kind") != "orphan_memories":
                raise ValueError("Orphan cleanup scope changed or expired. Review it again.")
            memory_ids = [str(item.get("id")) for item in self._orphaned_learner_memories()]
            self._consume_delete_plan(token, "orphan_memories", memory_ids)
            if memory_ids != plan.get("memory_ids"):
                raise ValueError("Orphaned Learner memories changed. Review cleanup again.")
            if plan.get("blocked_memory_ids"):
                return {
                    "status": "blocked",
                    "message": "One or more orphaned memories are protected. Unlock them and review cleanup again.",
                    "blocked_memory_ids": plan["blocked_memory_ids"],
                }
            if not memory_ids or not self.knowledge_base:
                return {"status": "success", "deleted_count": 0, "deleted_ids": []}
            return self.knowledge_base.delete_memories(
                memory_ids, reason="learner_orphan_cleanup",
                confirmation_token=confirmation_token,
                require_all=True,
            )

    def _remember_delete_plan(self, token, kind, ids, memory_ids, memory_preview):
        now = _now()
        self._delete_plans = {
            key: value for key, value in self._delete_plans.items()
            if value.get("expires_at") and value["expires_at"] >= now
        }
        self._delete_plans[str(token or "")] = {
            "kind": str(kind),
            "ids": list(ids),
            "memory_ids": list(memory_ids),
            "blocked_memory_ids": [
                str(item.get("id")) for item in (memory_preview.get("blocked") or [])
                if isinstance(item, dict) and item.get("id")
            ],
            "expires_at": now + timedelta(minutes=10),
        }

    def _consume_delete_plan(self, confirmation_token, kind, ids):
        token = str(confirmation_token or "")
        plan = self._delete_plans.pop(token, None)
        if (
            not token or not plan or plan.get("kind") != str(kind)
            or plan.get("ids") != list(ids)
            or not plan.get("expires_at") or plan["expires_at"] < _now()
        ):
            raise ValueError("Deletion scope changed or expired. Review it again.")
        return plan

    def preview_run_deletion(self, run_ids):
        run_ids = list(dict.fromkeys(str(value) for value in (run_ids or []) if value))
        with self._lock:
            selected = [item for item in self.data.get("runs", []) if str(item.get("id")) in run_ids]
            if len(selected) != len(run_ids):
                raise ValueError("Learning run not found")
            if any(run.get("status") in {"queued", "running"} for run in selected):
                raise ValueError("A running learning activity cannot be deleted")
            memory_ids = self._learner_memory_ids(run_ids=run_ids)
            memory_preview = (
                self.knowledge_base.preview_memory_deletion(memory_ids, reason="learner_run_delete")
                if self.knowledge_base else {
                    "confirmation_token": str(uuid.uuid4()), "delete_count": 0,
                    "rewired_links": [], "warnings": [], "cleared_reference_count": 0,
                }
            )
            token = memory_preview.get("confirmation_token")
            self._remember_delete_plan(token, "runs", run_ids, memory_ids, memory_preview)
            return {
                "status": "confirmation_required",
                "run_ids": run_ids,
                "run_count": len(selected),
                "memory_count": len(memory_ids),
                "memory_preview": memory_preview,
                "confirmation_token": token,
            }

    def delete_runs(self, run_ids, confirmation_token=None):
        run_ids = list(dict.fromkeys(str(value) for value in (run_ids or []) if value))
        with self._lock:
            plan = self._consume_delete_plan(confirmation_token, "runs", run_ids)
            selected = [item for item in self.data.get("runs", []) if str(item.get("id")) in run_ids]
            if len(selected) != len(run_ids):
                raise ValueError("Learning run not found")
            if any(run.get("status") in {"queued", "running"} for run in selected):
                raise ValueError("A running learning activity cannot be deleted")
            memory_ids = self._learner_memory_ids(run_ids=run_ids)
            if memory_ids != plan.get("memory_ids"):
                raise ValueError("Linked memories changed. Review deletion again.")
            if plan.get("blocked_memory_ids"):
                return {
                    "status": "blocked",
                    "message": "One or more linked memories are protected. Unlock them and review deletion again.",
                    "blocked_memory_ids": plan["blocked_memory_ids"],
                }
            previous_data = copy.deepcopy(self.data)
            self.data["runs"] = [
                item for item in self.data.get("runs", [])
                if str(item.get("id")) not in set(run_ids)
            ]
            try:
                self._save()
            except Exception:
                self.data = previous_data
                raise
            memory_result = {"status": "success", "deleted_count": 0, "deleted_ids": []}
            if memory_ids and self.knowledge_base:
                memory_result = self.knowledge_base.delete_memories(
                    memory_ids,
                    reason="learner_run_delete",
                    confirmation_token=confirmation_token,
                    require_all=True,
                )
                if memory_result.get("status") != "success":
                    self.data = previous_data
                    self._save()
                    return {"status": memory_result.get("status"), "memory_result": memory_result}
                if set(memory_result.get("deleted_ids") or []) != set(memory_ids):
                    self.data = previous_data
                    self._save()
                    return {
                        "status": "blocked",
                        "message": "One or more linked memories are protected. Unlock them and review deletion again.",
                        "memory_result": memory_result,
                    }
            return {"status": "success", "removed": len(selected), "memory_result": memory_result}

    def delete_runs_by_confirmation(self, confirmation_token):
        with self._lock:
            plan = self._delete_plans.get(str(confirmation_token or ""))
            if not plan or plan.get("kind") != "runs":
                raise ValueError("Activity deletion scope changed or expired. Review it again.")
            run_ids = list(plan.get("ids") or [])
        return self.delete_runs(run_ids, confirmation_token=confirmation_token)

    def clear_runs(self, scope="read"):
        scope = str(scope or "read").strip().lower()
        if scope not in {"read", "terminal"}:
            raise ValueError("Activity clear scope must be read or terminal")
        with self._lock:
            run_ids = [
                str(run.get("id")) for run in self.data.get("runs", [])
                if run.get("status") not in {"queued", "running"}
                and (scope == "terminal" or run.get("is_read", False))
            ]
        if not run_ids:
            return 0
        preview = self.preview_run_deletion(run_ids)
        return self.delete_runs(
            run_ids, confirmation_token=preview.get("confirmation_token"),
        ).get("removed", 0)

    def preview_clear_runs(self, scope="read"):
        scope = str(scope or "read").strip().lower()
        if scope not in {"read", "terminal"}:
            raise ValueError("Activity clear scope must be read or terminal")
        with self._lock:
            run_ids = [
                str(run.get("id")) for run in self.data.get("runs", [])
                if run.get("status") not in {"queued", "running"}
                and (scope == "terminal" or run.get("is_read", False))
            ]
        if not run_ids:
            return {
                "status": "confirmation_required", "run_ids": [], "run_count": 0,
                "memory_count": 0, "confirmation_token": "", "memory_preview": {},
            }
        return self.preview_run_deletion(run_ids)

    # -------------------------------------------------------------- tasks
    def create_task(self, payload):
        task = self._normalize_task(payload, creating=True)
        with self._lock:
            self.data.setdefault("tasks", []).append(task)
            self._save()
        return self._public_task(task)

    def update_task(self, task_id, updates):
        with self._lock:
            current = self._find_task(task_id)
            merged = dict(current)
            merged.update(updates if isinstance(updates, dict) else {})
            normalized = self._normalize_task(merged, creating=False)
            normalized["id"] = current["id"]
            normalized["created_at"] = current.get("created_at")
            normalized["processed_source_ids"] = current.get("processed_source_ids", [])
            if not any(key in (updates or {}) for key in ("schedule_type", "run_when", "run_at", "time", "weekdays")):
                normalized["next_run_at"] = current.get("next_run_at")
                normalized["defer_session_id"] = current.get("defer_session_id")
            current.clear()
            current.update(normalized)
            self._save()
            return self._public_task(current)

    def delete_task(self, task_id):
        preview = self.preview_task_deletion(task_id)
        return self.delete_task_with_history(
            task_id, confirmation_token=preview.get("confirmation_token"),
        ).get("removed_tasks", 0) == 1

    def preview_task_deletion(self, task_id):
        with self._lock:
            self._find_task(task_id)
            runs = [item for item in self.data.get("runs", []) if str(item.get("task_id")) == str(task_id)]
            if any(run.get("status") in {"queued", "running"} for run in runs):
                raise ValueError("Stop the running learning activity before deleting its task")
            memory_ids = self._learner_memory_ids(task_ids=[task_id])
            memory_preview = (
                self.knowledge_base.preview_memory_deletion(memory_ids, reason="learner_task_delete")
                if self.knowledge_base else {
                    "confirmation_token": str(uuid.uuid4()), "delete_count": 0,
                    "rewired_links": [], "warnings": [], "cleared_reference_count": 0,
                }
            )
            token = memory_preview.get("confirmation_token")
            self._remember_delete_plan(
                token, "task", [str(task_id)], memory_ids, memory_preview,
            )
            return {
                "status": "confirmation_required",
                "task_id": str(task_id),
                "run_count": len(runs),
                "memory_count": len(memory_ids),
                "memory_preview": memory_preview,
                "confirmation_token": token,
            }

    def delete_task_with_history(self, task_id, confirmation_token=None):
        with self._lock:
            plan = self._consume_delete_plan(
                confirmation_token, "task", [str(task_id)],
            )
            self._find_task(task_id)
            runs = [item for item in self.data.get("runs", []) if str(item.get("task_id")) == str(task_id)]
            if any(run.get("status") in {"queued", "running"} for run in runs):
                raise ValueError("Stop the running learning activity before deleting its task")
            memory_ids = self._learner_memory_ids(task_ids=[task_id])
            if memory_ids != plan.get("memory_ids"):
                raise ValueError("Linked memories changed. Review task deletion again.")
            if plan.get("blocked_memory_ids"):
                return {
                    "status": "blocked",
                    "message": "One or more linked memories are protected. Unlock them and review deletion again.",
                    "blocked_memory_ids": plan["blocked_memory_ids"],
                }
            previous_data = copy.deepcopy(self.data)
            self.data["runs"] = [
                item for item in self.data.get("runs", [])
                if str(item.get("task_id")) != str(task_id)
            ]
            self.data["tasks"] = [
                item for item in self.data.get("tasks", [])
                if str(item.get("id")) != str(task_id)
            ]
            try:
                self._save()
            except Exception:
                self.data = previous_data
                raise
            memory_result = {"status": "success", "deleted_count": 0, "deleted_ids": []}
            if memory_ids and self.knowledge_base:
                memory_result = self.knowledge_base.delete_memories(
                    memory_ids,
                    reason="learner_task_delete",
                    confirmation_token=confirmation_token,
                    require_all=True,
                )
                if memory_result.get("status") != "success":
                    self.data = previous_data
                    self._save()
                    return {"status": memory_result.get("status"), "memory_result": memory_result}
                if set(memory_result.get("deleted_ids") or []) != set(memory_ids):
                    self.data = previous_data
                    self._save()
                    return {
                        "status": "blocked",
                        "message": "One or more linked memories are protected. Unlock them and review deletion again.",
                        "memory_result": memory_result,
                    }
            return {
                "status": "success", "removed_tasks": 1, "removed_runs": len(runs),
                "memory_result": memory_result,
            }

    def _find_task(self, task_id):
        task = next((item for item in self.data.get("tasks", []) if item.get("id") == task_id), None)
        if not task:
            raise ValueError("Learning task not found")
        return task

    def _normalize_task(self, payload, creating=False):
        payload = payload if isinstance(payload, dict) else {}
        source_type = str(payload.get("source_type") or "web").strip().lower()
        if source_type not in {"web", "video"}:
            raise ValueError("Source must be authoritative web research or selected creators")
        title = _clean(payload.get("title"), 120)
        topic = _clean(payload.get("topic"), 500)
        if not title:
            title = topic[:80] or "Learner task"
        if source_type == "web":
            if not topic:
                raise ValueError("A learning topic is required")
            if _URL_RE.search(topic):
                raise ValueError("Learner accepts a topic, not a URL. Saya selects readable authoritative sources.")
        creator_ids = []
        video_ids = []
        video_selection_mode = None
        if source_type == "video":
            creator_ids = list(dict.fromkeys(str(item) for item in payload.get("creator_ids", []) if item))[:20]
            if not creator_ids:
                raise ValueError("Select at least one creator already added to Video Recommendations")
            video_ids = list(dict.fromkeys(str(item) for item in payload.get("video_ids", []) if item))[:50]

        schedule_type = str(payload.get("schedule_type") or "once").lower()
        if schedule_type not in {"once", "daily", "weekly"}:
            raise ValueError("Schedule must be once, daily, or weekly")
        if source_type == "video":
            if schedule_type == "once":
                # Keep old API-created tasks runnable, but every current UI
                # submission includes video_ids and must explicitly choose.
                if "video_ids" in payload:
                    if not video_ids:
                        raise ValueError("Select one or more recommended videos for a one-time task")
                    video_selection_mode = "selected"
                else:
                    video_selection_mode = "legacy_creator_latest"
            else:
                video_selection_mode = str(payload.get("video_selection_mode") or "new").strip().lower()
                if video_selection_mode not in {"new", "latest", "most_viewed"}:
                    video_selection_mode = "new"
        time_text = str(payload.get("time") or "09:00")[:5]
        if not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", time_text):
            raise ValueError("Time must use HH:MM")
        weekdays = []
        for value in payload.get("weekdays", []):
            try:
                day = int(value)
            except (TypeError, ValueError):
                continue
            if 0 <= day <= 6 and day not in weekdays:
                weekdays.append(day)
        if schedule_type == "weekly" and not weekdays:
            weekdays = [_now().weekday()]
        run_when = str(payload.get("run_when") or "scheduled").strip().lower()
        if run_when not in {"now", "next_launch", "scheduled"}:
            run_when = "scheduled"
        if schedule_type != "once":
            run_when = "scheduled"
        run_at = _parse_datetime(payload.get("run_at"))
        if schedule_type == "once" and run_when == "now":
            run_at = _now()
        elif schedule_type == "once" and run_when == "next_launch":
            run_at = None
        elif schedule_type == "once" and run_at is None:
            run_at = _now()

        def bounded_int(value, default, minimum, maximum):
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = default
            return max(minimum, min(maximum, parsed))

        web_max_pages = bounded_int(payload.get("web_max_pages"), 15, 3, 30)
        web_reference_count = bounded_int(payload.get("web_reference_count"), 6, 1, 10)
        web_reference_count = min(web_reference_count, web_max_pages)
        web_min_references = bounded_int(payload.get("web_min_references"), 1, 1, 6)
        web_min_references = min(web_min_references, web_reference_count)
        web_source_policy = str(payload.get("web_source_policy") or "balanced").strip().lower()
        if web_source_policy not in {"balanced", "official_only", "flexible"}:
            web_source_policy = "balanced"

        created_at = str(payload.get("created_at") or _iso())
        task = {
            "id": str(payload.get("id") or uuid.uuid4()),
            "title": title,
            "source_type": source_type,
            "topic": topic if source_type == "web" else "",
            "creator_ids": creator_ids,
            "video_ids": video_ids,
            "video_selection_mode": video_selection_mode,
            "schedule_type": schedule_type,
            "run_when": run_when,
            "run_at": _iso(run_at) if run_at else None,
            "time": time_text,
            "weekdays": sorted(weekdays),
            "read_memory": bool(payload.get("read_memory", False)),
            "manage_memory": bool(payload.get("manage_memory", False)),
            "web_max_pages": web_max_pages,
            "web_reference_count": web_reference_count,
            "web_min_references": web_min_references,
            "web_source_policy": web_source_policy,
            "enabled": bool(payload.get("enabled", True)),
            "created_at": created_at,
            "video_tracking_since": (
                str(payload.get("video_tracking_since") or created_at)
                if source_type == "video" and schedule_type != "once"
                else None
            ),
            "updated_at": _iso(),
            "last_run_at": payload.get("last_run_at"),
            "last_status": payload.get("last_status"),
            "processed_source_ids": list(payload.get("processed_source_ids") or [])[-500:],
            "defer_session_id": (
                str(payload.get("defer_session_id") or self.session_id)
                if schedule_type == "once" and run_when == "next_launch"
                else None
            ),
        }
        task["next_run_at"] = self._next_schedule(task, from_time=_now(), include_now=True)
        return task

    def _next_schedule(self, task, from_time=None, include_now=False):
        now = from_time or _now()
        schedule = task.get("schedule_type")
        if schedule == "once":
            if task.get("run_when") == "next_launch":
                return _iso(from_time or _now())
            run_at = _parse_datetime(task.get("run_at"))
            if not run_at or (
                task.get("last_run_at")
                and task.get("last_status") in {"completed", "completed_caution"}
            ):
                return None
            return _iso(run_at)
        hour, minute = (int(item) for item in str(task.get("time") or "09:00").split(":", 1))
        if schedule == "daily":
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate < now or (candidate == now and not include_now):
                candidate += timedelta(days=1)
            return _iso(candidate)
        weekdays = set(task.get("weekdays") or [now.weekday()])
        for offset in range(8):
            candidate = (now + timedelta(days=offset)).replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate.weekday() in weekdays and (candidate > now or (include_now and candidate == now)):
                return _iso(candidate)
        return None

    def _task_due(self, task, now=None):
        next_run = _parse_datetime(task.get("next_run_at"))
        if task.get("run_when") == "next_launch" and task.get("defer_session_id") == self.session_id:
            return False
        return bool(task.get("enabled", True) and next_run and next_run <= (now or _now()))

    # ----------------------------------------------------------- execution
    def start_due_tasks(self, credentials=None):
        started = []
        with self._lock:
            task_ids = [item.get("id") for item in self.data.get("tasks", []) if self._task_due(item)]
        for task_id in task_ids:
            run = self.start_task(task_id, credentials=credentials, force=False)
            if run:
                started.append(run)
        return {"started": started, **self.get_state()}

    def start_task(self, task_id, credentials=None, force=True):
        if self._shutdown_event.is_set():
            raise RuntimeError("Learner service is shutting down")
        with self._lock:
            task = self._find_task(task_id)
            if not force and not self._task_due(task):
                return None
            if any(
                item.get("task_id") == task_id and item.get("status") in {"queued", "running"}
                for item in self.data.get("runs", [])
            ):
                return None
            run = {
                "id": str(uuid.uuid4()),
                "task_id": task_id,
                "task_title": task.get("title"),
                "source_type": task.get("source_type"),
                "status": "queued",
                "stage": "queued",
                "progress": 0,
                "created_at": _iso(),
                "started_at": None,
                "finished_at": None,
                "summary": "",
                "sources": [],
                "memory_result": None,
                "error": "",
                "is_read": False,
                "read_at": None,
                "response_language": self._response_language(task, credentials or {}),
            }
            self.data.setdefault("runs", []).append(run)
            self._save()
            task_snapshot = dict(task)
        future = self._executor.submit(self._execute_run, task_snapshot, run["id"], dict(credentials or {}))
        if not getattr(future, "done", lambda: False)():
            with self._lock:
                self._futures[run["id"]] = future
            callback = getattr(future, "add_done_callback", None)
            if callback:
                callback(lambda completed, run_id=run["id"]: self._forget_future(run_id, completed))
        return self.get_run(run["id"])

    def _forget_future(self, run_id, completed=None):
        with self._lock:
            current = self._futures.get(run_id)
            if completed is None or current is completed:
                self._futures.pop(run_id, None)

    def shutdown(self):
        """Stop accepting work and cancel tasks that have not started yet."""
        self._shutdown_event.set()
        with self._lock:
            futures = list(self._futures.values())
        for future in futures:
            try:
                future.cancel()
            except Exception:
                pass
        shutdown = getattr(self._executor, "shutdown", None)
        if shutdown:
            shutdown(wait=False, cancel_futures=True)

    def _ensure_active(self):
        if self._shutdown_event.is_set():
            raise RuntimeError("Learning stopped because the application is shutting down")

    def _update_run(self, run_id, **updates):
        with self._lock:
            run = next(item for item in self.data.get("runs", []) if item.get("id") == run_id)
            run.update(updates)
            self._save()
            return run

    def _execute_run(self, task, run_id, credentials):
        self._update_run(run_id, status="running", stage="preparing", progress=5, started_at=_iso())
        try:
            self._ensure_active()
            memory_context, memory_ids = self._read_memories(task)
            self._update_run(run_id, stage="reading_sources", progress=20)
            self._ensure_active()
            if task.get("source_type") == "web":
                material = self._learn_from_web(task, credentials)
            else:
                material = self._learn_from_videos(task, credentials)
            self._ensure_active()
            self._update_run(run_id, stage="summarizing", progress=55, sources=material["sources"])
            if material.get("no_update"):
                # "No new video" is a complete, deterministic outcome and
                # must not consume local/API model tokens merely to restate it.
                summary, used_model = self._extractive_summary(task, material, credentials), False
                material["summary_method"] = "no_update"
                material["summary_diagnostic"] = "No material update was found in the accepted evidence."
            else:
                summary, used_model = self._summarize(task, material, memory_context, credentials)
            citation_check = None
            if material.get("raw_sources"):
                citation_check = validate_response_citations(summary, material["raw_sources"])
            self._update_run(run_id, stage="managing_memory", progress=78, summary=summary)
            memory_result = None
            if task.get("manage_memory") and not material.get("no_update") and self.knowledge_base:
                if used_model:
                    self._ensure_active()
                    memory_result = self._manage_memories(
                        task, run_id, summary, material, credentials,
                        use_model=True,
                    )
                else:
                    memory_result = {
                        "created_ids": [], "merged_ids": [],
                        "archived_duplicate_ids": [], "preserved_important_ids": [],
                        "graph_rebuilt": False, "skipped": True,
                        "reason": "Summary model did not return a usable synthesized report; source excerpts were not saved as memory.",
                    }
            if self.knowledge_base and memory_result and not memory_result.get("skipped"):
                self.knowledge_base.ensure_memory_graph(force=True)

            if material.get("no_update"):
                completion_status = "completed"
            elif not used_model:
                completion_status = "failed"
            else:
                completion_status = "completed_caution" if material.get("degraded") else "completed"
            with self._lock:
                live_task = self._find_task(task["id"])
                live_task["last_run_at"] = _iso()
                live_task["last_status"] = completion_status
                processed = list(live_task.get("processed_source_ids") or [])
                processed.extend(material.get("source_ids") or [])
                live_task["processed_source_ids"] = list(dict.fromkeys(processed))[-500:]
                if live_task.get("schedule_type") == "once":
                    live_task["enabled"] = False
                    live_task["next_run_at"] = None
                else:
                    live_task["next_run_at"] = self._next_schedule(live_task, from_time=_now() + timedelta(seconds=1))
                live_task["updated_at"] = _iso()
                self._save()
            self._update_run(
                run_id,
                status=completion_status,
                stage=completion_status,
                progress=100,
                finished_at=_iso(),
                summary=summary,
                used_model=used_model,
                error=(
                    "Source collection finished, but no usable synthesized report was generated. Run the task again."
                    if completion_status == "failed" else ""
                ),
                summary_method=material.get("summary_method"),
                summary_diagnostic=material.get("summary_diagnostic", ""),
                model_name=(
                    str(credentials.get("api_model_name") or "")
                    if str(credentials.get("model") or "").strip().lower() == "api"
                    else str(credentials.get("model") or "")
                ),
                evidence_chars=len(material.get("evidence") or ""),
                readable_source_count=sum(
                    1 for source in material.get("sources") or []
                    if source.get("status") == "read" or source.get("evidence_type") == "full_page"
                ),
                recalled_memory_ids=memory_ids,
                memory_result=memory_result,
                citation_check=citation_check,
                quality_tier=material.get("quality_tier"),
                quality_notice=material.get("quality_notice", ""),
                requested_source_policy=material.get("requested_source_policy"),
                is_read=False,
                read_at=None,
            )
        except Exception as exc:
            with self._lock:
                try:
                    live_task = self._find_task(task["id"])
                    live_task["last_run_at"] = _iso()
                    live_task["last_status"] = "failed"
                    if live_task.get("schedule_type") == "once":
                        # A completed failure is terminal for an automatic
                        # one-time run.  Repeated retries can burn search/model
                        # quota without new user intent; Run now remains available.
                        live_task["enabled"] = False
                        live_task["next_run_at"] = None
                    else:
                        live_task["next_run_at"] = self._next_schedule(
                            live_task, from_time=_now() + timedelta(minutes=5)
                        )
                    self._save()
                except ValueError:
                    pass
            self._update_run(
                run_id,
                status="failed",
                stage="failed",
                finished_at=_iso(),
                error=_clean(exc, 800),
                is_read=False,
                read_at=None,
            )

    # -------------------------------------------------------------- sources
    def _read_memories(self, task):
        if not task.get("read_memory") or not self.knowledge_base:
            return "", []
        query = task.get("topic") or task.get("title") or "learning"
        memories = self.knowledge_base.get_relevant_memories(
            query, mode="normal", limit=8, mark_accessed=False
        )
        lines, ids = [], []
        for item in memories:
            ids.append(str(item.get("id")))
            lines.append(f"- {item.get('title') or 'Memory'}: {_clean(item.get('content'), 900)}")
        return "\n".join(lines), ids

    @staticmethod
    def _authoritative(source):
        return bool(
            source.status == "read"
            and source.text
            and (
                source.source_type in _PRIMARY_SOURCE_TYPES
                or (source.source_type in _SECONDARY_AUTHORITY_TYPES and source.authority in {"medium", "high", "primary"})
            )
        )

    @classmethod
    def _select_reliable_sources(cls, sources):
        """Prefer authority, then permit strong independent corroboration.

        A single unknown page never passes.  The fallback requires at least
        two readable, independently hosted results with healthy ranking and
        excludes every source class already marked low-quality or community.
        """
        authoritative = [source for source in sources if cls._authoritative(source)]
        if authoritative:
            return authoritative, "authoritative"
        candidates = [
            source for source in sources
            if source.status == "read"
            and bool(source.text)
            and source.source_type in _CROSS_CHECKABLE_TYPES
            and source.authority not in {"low", "community"}
            and float(source.quality_score or 0) >= 50
        ]
        domains = {str(source.domain or "").lower() for source in candidates if source.domain}
        if len(domains) >= 2:
            return candidates, "cross_checked"
        return [], "none"

    @staticmethod
    def _dedupe_sources(sources):
        unique, seen_urls = [], set()
        for source in sources or []:
            canonical = str(source.url or "").strip().lower()
            if not canonical or canonical in seen_urls:
                continue
            seen_urls.add(canonical)
            unique.append(source)
        return unique

    @classmethod
    def _select_flexible_readable_sources(cls, sources):
        """Accept a wider but still bounded set of readable trusted pages."""
        trusted, generic = [], []
        for source in cls._dedupe_sources(sources):
            if source.status != "read" or not source.text:
                continue
            if source.source_type in {"community", "aggregator"}:
                continue
            if source.authority in {"low", "community"}:
                continue
            score = float(source.quality_score or 0)
            trusted_type = (
                source.source_type in _PRIMARY_SOURCE_TYPES
                or source.source_type in _SECONDARY_AUTHORITY_TYPES
                or source.source_type == "repository"
                or source.authority in {"medium", "high", "primary"}
            )
            if trusted_type and (score >= 25 or source.authority in {"high", "primary"}):
                trusted.append(source)
            elif source.source_type == "web" and score >= 40:
                generic.append(source)
        generic_domains = {str(item.domain or "").lower() for item in generic if item.domain}
        if len(generic_domains) < 2:
            generic = []
        return cls._dedupe_sources(trusted + generic)

    @classmethod
    def _select_trusted_evidence_sources(cls, sources):
        """Add trustworthy search snippets when guarded page reading fails."""
        selected = cls._select_flexible_readable_sources(sources)
        generic_snippets = []
        for source in cls._dedupe_sources(sources):
            snippet = _clean_evidence(source.snippet, 1600)
            if source.status != "snippet_only" or not cls._informative_snippet(snippet):
                continue
            if source.source_type in {"community", "aggregator"}:
                continue
            if source.authority in {"low", "community"}:
                continue
            score = float(source.quality_score or 0)
            trusted_type = (
                source.source_type in _PRIMARY_SOURCE_TYPES
                or source.source_type in _SECONDARY_AUTHORITY_TYPES
                or source.source_type == "repository"
                or source.authority in {"medium", "high", "primary"}
            )
            if trusted_type and (score >= 20 or source.authority in {"high", "primary"}):
                selected.append(source)
            elif source.source_type == "web" and score >= 50:
                generic_snippets.append(source)
        snippet_domains = {str(item.domain or "").lower() for item in generic_snippets if item.domain}
        if len(snippet_domains) >= 2:
            selected.extend(generic_snippets)
        return cls._dedupe_sources(selected)

    @classmethod
    def _topic_terms(cls, topic):
        return {
            token for token in cls._tokens(topic)
            if token not in _TOPIC_STOPWORDS and len(token) >= 2
        }

    @classmethod
    def _source_topic_relevant(cls, source, topic):
        """Reject obvious search drift without demanding exact phrasing.

        Primary/official material remains eligible because policy pages can use
        formal vocabulary unlike the user's request. Unknown web snippets must
        share at least one meaningful topic term with their title or excerpt.
        """
        if (
            source.source_type in _PRIMARY_SOURCE_TYPES
            or source.source_type in _SECONDARY_AUTHORITY_TYPES
            or source.authority in {"high", "primary"}
        ):
            return True
        topic_terms = cls._topic_terms(topic)
        if not topic_terms:
            return True
        haystack = " ".join((str(source.title or ""), str(source.snippet or ""), str(source.text or "")))
        source_terms = cls._tokens(_clean_evidence(haystack, 5000))
        matches = topic_terms & source_terms
        if matches:
            return True
        # A readable page selected by the generic cross-check tier is kept
        # unless the topic contains a strong domain anchor. This prevents a
        # short or paraphrased body from being discarded solely by keywords.
        strong_anchors = {
            token for token in topic_terms
            if re.search(r"[\u4e00-\u9fff]", token)
            or token in {"ai", "artificial", "intelligence", "medical", "healthcare", "japan", "japanese", "留学", "日本", "医疗"}
        }
        return bool(source.status == "read" and source.text and not strong_anchors)

    @staticmethod
    def _informative_snippet(snippet):
        text = _clean_evidence(snippet, 1800)
        if len(text) < 45:
            return False
        fact_signals = len(_FACT_SIGNAL_RE.findall(text))
        numbers = len(re.findall(r"(?:\b(?:19|20)\d{2}\b|\d+(?:\.\d+)?%|[$€£¥]\s?\d|\d{2,})", text))
        generic_signals = len(_GENERIC_SNIPPET_RE.findall(text))
        if generic_signals and not fact_signals and not numbers:
            return False
        return bool(fact_signals or numbers or len(text) >= 150)

    @staticmethod
    def _source_recency_key(source):
        published = _parse_datetime(getattr(source, "published_at", ""))
        return published or datetime.min

    @classmethod
    def _sort_evidence_sources(cls, sources):
        return sorted(
            cls._dedupe_sources(sources),
            key=lambda source: (
                cls._source_recency_key(source),
                source.status == "read" and bool(source.text),
                float(source.quality_score or 0),
            ),
            reverse=True,
        )

    @classmethod
    def _focused_evidence_text(cls, value, topic, limit=4200):
        text = _clean_evidence(value, 24000)
        if len(text) <= limit:
            return text
        segments = [
            segment.strip()
            for segment in re.split(r"\n+|(?<=[。！？.!?])\s+", text)
            if len(segment.strip()) >= 35
        ]
        if not segments:
            return text[:limit]
        topic_tokens = cls._tokens(topic)
        ranked = []
        for index, segment in enumerate(segments):
            tokens = cls._tokens(segment)
            relevance = len(tokens & topic_tokens)
            fact_signals = len(_FACT_SIGNAL_RE.findall(segment))
            numeric_signals = len(re.findall(
                r"(?:\b(?:19|20)\d{2}\b|\d+(?:\.\d+)?%|[$€£¥]\s?\d|\d{2,})",
                segment,
            ))
            ranked.append((relevance * 4 + fact_signals * 2 + min(numeric_signals, 3), index, segment))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        selected, used = [], 0
        for score, index, segment in ranked:
            if score <= 0 and selected:
                continue
            clipped = segment[:1200]
            if used + len(clipped) + 2 > limit:
                continue
            selected.append((index, clipped))
            used += len(clipped) + 2
            if used >= limit * 0.86:
                break
        if not selected:
            return text[:limit]
        return "\n\n".join(segment for _, segment in sorted(selected))[:limit]

    @classmethod
    def _source_tiers(cls, sources, policy):
        tiers = []
        if policy == "official_only":
            tiers.append((
                "authoritative",
                [source for source in cls._dedupe_sources(sources) if cls._authoritative(source)],
            ))
        if policy in {"official_only", "balanced"}:
            reliable, acceptance = cls._select_reliable_sources(sources)
            tiers.append((acceptance if acceptance != "none" else "reliable", cls._dedupe_sources(reliable)))
        tiers.append(("flexible_readable", cls._select_flexible_readable_sources(sources)))
        tiers.append(("trusted_snippet", cls._select_trusted_evidence_sources(sources)))
        return tiers

    def _quality_notice(self, task, credentials, tier, source_count, minimum_references):
        language = self._response_language(task, credentials)
        below_minimum = source_count < minimum_references
        if language in {"zh-cn", "zh-tw"}:
            if tier == "trusted_snippet":
                notice = (
                    f"严格来源层级没有足够的可读正文，Learner 已改用 {source_count} 个可信来源的搜索摘要完成。"
                    "该结果适合了解线索和初步变化，请打开来源核对关键结论。"
                )
            else:
                notice = (
                    f"严格来源层级没有足够结果，Learner 已降级使用 {source_count} 个可信且可读的来源。"
                    "结果可用，但建议核对关键结论。"
                )
            if below_minimum:
                notice += f" 设置要求至少 {minimum_references} 个参考来源，本次实际找到 {source_count} 个。"
            return notice
        if tier == "trusted_snippet":
            notice = (
                f"The stricter tiers did not provide enough readable page text, so Learner completed with "
                f"trusted search snippets from {source_count} source(s). Open the sources before relying on key claims."
            )
        else:
            notice = (
                f"The stricter tiers did not provide enough results, so Learner completed with {source_count} "
                "broader trusted readable source(s). Verify key claims before relying on them."
            )
        if below_minimum:
            notice += f" The task requested at least {minimum_references} references; {source_count} were available."
        return notice

    def _learn_from_web(self, task, credentials):
        if not self.web_research:
            raise RuntimeError("Web research service is unavailable")
        brave_key = str(credentials.get("brave_search_api_key") or BRAVE_SEARCH_API_KEY or "").strip()
        if not brave_key:
            raise RuntimeError("Brave Search API key is required for an authoritative web learning task")
        year = _now().year
        topic = task.get("topic") or task.get("title") or ""
        query = (
            f"{topic} latest reliable evidence {year} official institutional "
            "or well-established source"
        )
        search_count = int(task.get("web_max_pages", 15) or 15)
        reference_count = int(task.get("web_reference_count", 6) or 6)
        minimum_references = int(task.get("web_min_references", 1) or 1)
        source_policy = str(task.get("web_source_policy") or "balanced").strip().lower()
        if source_policy not in {"official_only", "balanced", "flexible"}:
            source_policy = "balanced"
        bundles = [self._research_web_query(query, brave_key, search_count, reference_count)]
        terminal_warning = self._terminal_search_warning(bundles[0])
        if terminal_warning:
            reason = {
                "search_auth_error": "the Brave Search key was rejected",
                "search_rate_limited": "Brave Search is rate limited",
                "search_network_error": "the search network request failed",
                "search_server_error": "Brave Search reported a server error",
                "search_invalid_json": "Brave Search returned an invalid response",
                "search_missing_key": "the Brave Search key is missing",
            }.get(terminal_warning, "the search service is unavailable")
            raise RuntimeError(
                f"Learning search stopped because {reason}. No fallback API request was made."
            )
        initial_tiers = self._source_tiers(bundles[0].sources, source_policy)
        initial_selected = initial_tiers[0][1] if initial_tiers else []
        if len(initial_selected) < minimum_references:
            # A breaking-news search can hide still-current policy pages.  The
            # second pass removes news wording and targets the responsible
            # authorities while keeping the same reader and quality gate. It
            # also tries to satisfy an explicit minimum-reference setting.
            fallback_query = self._authority_fallback_query(topic)
            if fallback_query and fallback_query.strip().lower() != query.strip().lower():
                bundles.append(self._research_web_query(fallback_query, brave_key, search_count, reference_count))
        all_sources = self._dedupe_sources([
            source for bundle in bundles for source in bundle.sources
        ])
        all_sources = [
            source for source in all_sources
            if self._source_topic_relevant(source, topic)
        ]
        tiers = self._source_tiers(all_sources, source_policy)
        chosen_index, acceptance_level, accepted = None, "none", []
        for index, (tier_name, tier_sources) in enumerate(tiers):
            if len(tier_sources) >= minimum_references:
                chosen_index, acceptance_level, accepted = index, tier_name, tier_sources
                break
        if chosen_index is None:
            for index in range(len(tiers) - 1, -1, -1):
                tier_name, tier_sources = tiers[index]
                if tier_sources:
                    chosen_index, acceptance_level, accepted = index, tier_name, tier_sources
                    break
        if not accepted:
            readable = sum(source.status == "read" and bool(source.text) for source in all_sources)
            official_candidates = sum(
                source.source_type in _PRIMARY_SOURCE_TYPES for source in all_sources
            )
            independent_domains = len({
                str(source.domain or "").lower()
                for source in all_sources
                if source.status == "read" and source.text and source.domain
            })
            raise RuntimeError(
                "No sufficiently reliable readable source passed the Learner quality gate "
                f"after {len(bundles)} search pass(es): {len(all_sources)} source(s), "
                f"{readable} readable, {official_candidates} classified authoritative, "
                f"{independent_domains} independent readable domain(s)."
            )
        accepted = self._sort_evidence_sources(accepted)[:reference_count]
        if acceptance_level == "trusted_snippet" and not any(
            source.status == "snippet_only" for source in accepted
        ):
            acceptance_level = "flexible_readable"
        degraded = bool(chosen_index and chosen_index > 0) or len(accepted) < minimum_references
        quality_notice = (
            self._quality_notice(task, credentials, acceptance_level, len(accepted), minimum_references)
            if degraded else ""
        )
        evidence = []
        public = []
        # Local models use an 8K context window. Keep the complete evidence
        # package compact enough for the report prompt plus a useful answer.
        api_mode = str(credentials.get("model") or "").strip().lower() == "api"
        evidence_budget = 14000 if api_mode else 4000
        per_source_limit = max(500, min(3000 if api_mode else 1600, evidence_budget // max(1, len(accepted))))
        for index, source in enumerate(accepted, 1):
            source.id = index
            evidence_type = "search_snippet" if source.status == "snippet_only" else "full_page"
            source_content = _clean_evidence(
                source.snippet if evidence_type == "search_snippet" else source.text,
                5500,
            )
            source_content = self._focused_evidence_text(
                source_content,
                task.get("topic") or task.get("title") or "",
                limit=per_source_limit,
            )
            public.append({
                "id": index, "kind": "web", "title": source.title,
                "url": source.url, "domain": source.domain,
                "source_type": source.source_type, "authority": source.authority,
                "acceptance": acceptance_level,
                "published_at": source.published_at, "status": source.status,
                "evidence_type": evidence_type,
                "excerpt": _clean(source_content, 900),
            })
            evidence.append(
                f"[Source {index}]\nTitle: {source.title}\nURL: {source.url}\n"
                f"Published: {source.published_at or 'unknown'}\n"
                f"Type: {source.source_type}; authority: {source.authority}; "
                f"acceptance: {acceptance_level}\n"
                f"Evidence type: {evidence_type}\n"
                f"Content:\n{_clean(source_content, 5500)}"
            )
        return {
            "evidence": "\n\n".join(evidence)[:evidence_budget],
            "sources": public,
            "source_ids": [item["url"] for item in public],
            "raw_sources": accepted,
            "degraded": degraded,
            "quality_tier": acceptance_level,
            "quality_notice": quality_notice,
            "requested_source_policy": source_policy,
        }

    @staticmethod
    def _terminal_search_warning(bundle):
        warnings = set(getattr(bundle, "warnings", []) or [])
        return next((item for item in _TERMINAL_SEARCH_WARNINGS if item in warnings), "")

    def _research_web_query(self, query, brave_key, search_count=15, reference_count=6):
        return self.web_research.research(
            "force", query, brave_key, local_model=False,
            search_count=search_count, max_final_sources=reference_count,
            source_strategy="official_first", safe_search="strict",
        )

    @staticmethod
    def _authority_fallback_query(topic):
        base = _clean(_NEWS_WORD_RE.sub(" ", topic), 300)
        base = re.sub(r"\s+", " ", base).strip()
        if _JAPAN_STUDY_RE.search(topic or ""):
            return (
                f"{base} 日本 留学生 现行有效 政策 入学 在留资格 "
                "文部科学省 出入国在留管理庁 JASSO 公式"
            ).strip()[:500]
        return (
            f"{base} current reliable information official institution university "
            "academic organization regulator professional body reputable news primary source"
        ).strip()[:500]

    def _learn_from_videos(self, task, credentials=None):
        if not self.video_service:
            raise RuntimeError("Video recommendation service is unavailable")
        state = self.video_service.get_recommendations(refresh=True)
        creator_ids = set(task.get("creator_ids") or [])
        processed = set(task.get("processed_source_ids") or [])
        all_videos = list(state.get("management_videos", []) or [])
        video_by_id = {str(item.get("id")): item for item in all_videos if item.get("id")}
        schedule = str(task.get("schedule_type") or "once")
        selection_mode = str(task.get("video_selection_mode") or "")

        if schedule == "once" and selection_mode == "selected":
            selected_ids = [str(item) for item in (task.get("video_ids") or []) if item]
            missing = [item for item in selected_ids if item not in video_by_id]
            if missing:
                raise RuntimeError(
                    "One or more selected videos are no longer in the recommendation cache; "
                    "refresh Video Recommendations and choose them again."
                )
            wrong_creator = [
                item for item in selected_ids
                if video_by_id[item].get("creator_id") not in creator_ids
            ]
            if wrong_creator:
                raise RuntimeError("A selected video does not belong to one of the selected creators")
            selected = [video_by_id[item] for item in selected_ids]
        else:
            candidates = [
                item for item in all_videos
                if item.get("creator_id") in creator_ids and item.get("id") not in processed
            ]
            tracking_since = _parse_datetime(task.get("video_tracking_since"))
            if schedule != "once" and tracking_since:
                candidates = [
                    item for item in candidates
                    if self._video_published_at(item) and self._video_published_at(item) >= tracking_since
                ]
            candidates.sort(key=lambda item: item.get("published_at") or "", reverse=True)
            if selection_mode == "most_viewed":
                for item in candidates:
                    if item.get("platform") == "youtube" and item.get("view_count") is None:
                        getter = getattr(self.video_service, "get_video_view_count", None)
                        if getter:
                            try:
                                item["view_count"] = getter(item.get("id"))
                            except Exception:
                                item["view_count"] = None
                candidates.sort(
                    key=lambda item: (
                        self._numeric_video_value(item.get("view_count"), -1),
                        item.get("published_at") or "",
                    ),
                    reverse=True,
                )
            if selection_mode == "new":
                # A recurring "every new video" task consumes all new
                # candidates in the current recommendation window.
                selected = candidates[:8]
            else:
                selected, used_creators = [], set()
                for item in candidates:
                    if item.get("creator_id") in used_creators:
                        continue
                    selected.append(item)
                    used_creators.add(item.get("creator_id"))
                selected = selected[:8]
        if not selected:
            if schedule != "once" and selection_mode == "new":
                message = "No new video published after this recurring task started was found."
            else:
                message = "The selected creators have no unlearned recent video in the recommendation window."
            return {
                "evidence": message,
                "sources": [],
                "source_ids": [],
                "no_update": True,
            }
        evidence, public, source_ids = [], [], []
        for index, video in enumerate(selected[:8], 1):
            context = self.video_service.get_video_learning_context(video.get("id"))
            transcript = _clean(context.get("transcript"), 9000)
            description = _clean(context.get("description"), 1800)
            if video.get("platform") == "youtube" and not transcript:
                continue
            if not transcript and not description:
                continue
            source_ids.append(video.get("id"))
            public.append({
                "id": index, "kind": "video", "title": video.get("title"),
                "url": video.get("url"), "creator": video.get("creator_name"),
                "published_at": video.get("published_at"),
                "transcript_status": context.get("transcript_status"),
                "transcript_language": context.get("transcript_language"),
                "view_count": video.get("view_count"),
                "excerpt": _clean(transcript or description, 900),
            })
            evidence.append(
                f"[Video {index}]\nCreator: {video.get('creator_name')}\nTitle: {video.get('title')}\n"
                f"URL: {video.get('url')}\nPublished: {video.get('published_at')}\n"
                f"Description:\n{description or '(unavailable)'}\nTranscript:\n{transcript or '(unavailable)'}"
            )
        if not public:
            raise RuntimeError("The selected YouTube videos had no readable captions; Learner did not substitute descriptions")
        evidence_budget = 14000 if str((credentials or {}).get("model") or "").strip().lower() == "api" else 4000
        return {
            "evidence": "\n\n".join(evidence)[:evidence_budget],
            "sources": public,
            "source_ids": source_ids,
            "selection_mode": selection_mode,
        }

    @staticmethod
    def _numeric_video_value(value, default=0):
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _video_published_at(video):
        return _parse_datetime(video.get("published_at"))

    # ------------------------------------------------------------ summarize
    def _summarize(self, task, material, memory_context, credentials):
        evidence = material.get("evidence") or ""
        language = self._response_language(task, credentials)
        language_label = _LANGUAGE_LABELS.get(language, "简体中文")
        prompt = (
            f"你正在为 Saya 的 Learner 任务生成高信息密度的最新动态汇报。全文必须使用{language_label}；"
            "先理解并翻译外语证据，再用自己的话归纳。读者打开报告后应立即看到最新事实，而不是流程说明。\n"
            "输出目标：\n"
            "- 把证据中每一条有价值的最新事件、政策、研究结果、数字、发布日期或机构动作尽可能保留下来。\n"
            "- 按发布日期从新到旧排列；日期未知的放后面。相同事件合并，不能把多个不同事件压成一句空泛结论。\n"
            "- 对每条动态写清：日期/时期、发生了什么、涉及谁、关键数字、为何与任务有关，并就近标注 [1] 或 [Video 1]。\n"
            "- 区分“网页正文已读取”和“仅搜索摘要”。仅摘要仍可作为新闻索引，但必须用“线索/据摘要”措辞，不能扩写。\n"
            "严格规则：\n"
            "1. 只依据证据，不补写；事实与推断分开。不得复制长句或逐段改写来源。\n"
            "2. 正文至少 70% 应是具体动态与其影响；状态声明、方法说明、免责声明和格式性文字合计不超过 15%。\n"
            "3. 不写泛泛的“技术发展迅速”“值得关注”等句子，除非后面紧跟具体证据。\n"
            "4. 使用紧凑 Markdown：先写 2–4 句“最新结论”，再写“最新动态索引”（每个事件一条），最后写最多 3 条“对任务的影响/下一步”。\n"
            "5. 不要单独写大段“局限”。如有必要，只在末尾用一行“证据边界”说明正文不可读或来源不足。\n"
            "6. 每条具体事实都要有引用；不要把来源 URL 写进正文，来源链接由界面另行展示。\n"
            "7. 目标长度 800–1800 字；信息很多时可以更长，信息少时不得用声明凑字数。\n"
            "8. 只输出最终汇报，不输出分析过程。"
        )
        if material.get("degraded"):
            prompt += (
                "\n本次含有 search_snippet 证据。不要让质量说明占据报告主体；应把有事实内容的摘要逐条整理为“最新动态线索”，"
                "保留摘要明确出现的日期、机构、数字和动作，并为每条加上“仅据搜索摘要，正文待核对”的短标记。"
            )
        if memory_context:
            prompt += (
                "\nUse recalled memory only to explain continuity or differences. Do not let memory override current evidence."
                f"\n\n[Recalled memory]\n{memory_context[:6000]}"
            )
        prompt += (
            f"\n\n[Learning task]\nTitle: {task.get('title')}\n"
            f"Goal: {task.get('topic') or task.get('title')}\n"
            f"Current date: {_now().date().isoformat()}\n\n[Evidence]\n{evidence}"
        )
        max_tokens = 3200 if str(credentials.get("model") or "").strip().lower() == "api" else 1400
        output = self._llm_completion(prompt, credentials, max_tokens=max_tokens)
        if output and self._summary_is_usable(output, material, language):
            material["summary_method"] = "model"
            material["summary_diagnostic"] = (
                "The model report passed language, citation, coverage, and originality checks."
            )
            return _clean(output, 24000), True
        material["summary_method"] = "source_index_fallback"
        if output:
            material["summary_diagnostic"] = (
                "The model returned a report that did not pass the Learner summary quality checks."
            )
        else:
            detail = _clean(self._last_completion_error, 500)
            friendly_detail = {
                "reasoning_without_visible_content:length": (
                    "the API model used its output budget for reasoning and returned no visible report"
                ),
                "response_has_no_supported_text_field": (
                    "the API response contained no supported visible-text field"
                ),
            }.get(detail, detail)
            material["summary_diagnostic"] = (
                f"The selected model could not generate the report: {friendly_detail}"
                if friendly_detail else "The selected model was unavailable or returned an empty report."
            )
        return self._failed_report(task, material, credentials), False

    def _response_language(self, task, credentials):
        configured = str((credentials or {}).get("response_language") or "auto").strip().lower()
        if configured in _LANGUAGE_LABELS:
            return configured
        if self.response_language_provider:
            try:
                persisted = str(self.response_language_provider() or "auto").strip().lower()
                if persisted in _LANGUAGE_LABELS:
                    return persisted
            except Exception:
                pass
        subject = f"{task.get('title') or ''} {task.get('topic') or ''}"
        if re.search(r"[\u3040-\u30ff]", subject):
            return "ja"
        if re.search(r"[\u4e00-\u9fff]", subject):
            return "zh-cn"
        return "en"

    @staticmethod
    def _source_texts(material):
        texts = []
        for source in material.get("raw_sources") or []:
            text = _clean(getattr(source, "text", ""), 8000)
            if text:
                texts.append(text)
        for source in material.get("sources") or []:
            excerpt = _clean(source.get("excerpt"), 3000)
            if excerpt:
                texts.append(excerpt)
        return texts

    @classmethod
    def _summary_is_usable(cls, output, material, language):
        text = _clean(output, 24000)
        if len(text) < 120:
            return False
        sources = material.get("sources") or []
        if sources:
            cited_ids = {
                int(value)
                for value in re.findall(r"\[(?:Video\s+)?(\d+)\]", text, re.I)
            }
            required_coverage = min(5, max(1, ((len(sources) * 2) + 2) // 3))
            if len(cited_ids) < required_coverage:
                return False
        if language in {"zh-cn", "zh-tw"}:
            han = len(re.findall(r"[\u4e00-\u9fff]", text))
            kana = len(re.findall(r"[\u3040-\u30ff]", text))
            minimum_han = min(650, 160 + 80 * len(sources))
            if han < minimum_han or kana > max(24, int(han * 0.18)):
                return False
        elif language == "ja" and len(re.findall(r"[\u3040-\u30ff]", text)) < 30:
            return False
        elif language == "en":
            words = len(re.findall(r"\b[A-Za-z]{2,}\b", text))
            if words < min(280, 70 + 35 * len(sources)):
                return False

        normalized = re.sub(r"\s+", "", text).lower()
        for source_text in cls._source_texts(material):
            source_normalized = re.sub(r"\s+", "", source_text).lower()
            if not source_normalized:
                continue
            # Exact long windows are enough to catch pasted source passages
            # and avoid the quadratic cost of a full sequence comparison.
            if any(
                normalized[start:start + 90] in source_normalized
                for start in range(0, max(1, len(normalized) - 89), 24)
                if len(normalized[start:start + 90]) == 90
            ):
                return False
        return True

    def _llm_completion(self, prompt, credentials, max_tokens=1600):
        self._last_completion_error = ""
        api_key = str(credentials.get("api_key") or os.environ.get("API_KEY", "") or "").strip()
        use_api = str(credentials.get("model") or "").strip().lower() == "api"
        if not use_api:
            if self.local_completion:
                try:
                    return str(self.local_completion(prompt, credentials, max_tokens) or "").strip()
                except Exception as exc:
                    self._last_completion_error = _clean(exc, 500) or exc.__class__.__name__
                    return ""
            self._last_completion_error = "No local completion function is configured."
            return ""
        if not api_key:
            raise RuntimeError("Learner API mode requires an API key. Add one in API settings or select a local model.")
        api_url = str(credentials.get("api_url") or os.environ.get("API_URL", DEFAULT_API_URL) or DEFAULT_API_URL).strip()
        model = str(credentials.get("api_model_name") or os.environ.get("API_MODEL", DEFAULT_API_MODEL) or DEFAULT_API_MODEL).strip()
        url = self._chat_url(api_url)
        request_payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": "You are Saya's cautious Learner role. Use supplied evidence only."},
                {"role": "user", "content": prompt},
            ],
            "stream": False,
            "max_tokens": max_tokens,
        }
        # DeepSeek V4 enables thinking by default and counts reasoning tokens
        # against max_tokens. Learner already receives selected evidence and
        # needs a visible synthesis, so reserve the budget for the report.
        if model.lower().startswith("deepseek-v4"):
            request_payload["thinking"] = {"type": "disabled"}
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
            json=request_payload,
            timeout=(30, 240),
        )
        if response.status_code != 200:
            raise RuntimeError(f"Learner model request failed: HTTP {response.status_code}")
        payload = response.json()
        content, diagnostic = _completion_content(payload)
        if not content and diagnostic:
            self._last_completion_error = diagnostic
        return content

    @staticmethod
    def _chat_url(api_url):
        value = str(api_url or "").rstrip("/")
        if value.lower().endswith("/chat/completions"):
            return value
        return value + "/chat/completions"

    def _extractive_summary(self, task, material, credentials=None):
        language = self._response_language(task, credentials or {})
        if material.get("no_update"):
            if language in {"zh-cn", "zh-tw"}:
                return f"# {task.get('title')}\n\n## 概览\n当前没有发现尚未学习的新视频。本次任务正常完成，未生成新的长期记忆。\n\n## 局限\n结果受推荐视频的时间窗口与平台可访问性影响。"
            return f"# {task.get('title')}\n\n## Overview\nNo unlearned recent video was found. The task completed without creating a new long-term memory.\n\n## Limitations\nThe result depends on the recommendation window and platform availability."
        chinese = language in {"zh-cn", "zh-tw"}
        sources = list(material.get("sources") or [])
        sources.sort(
            key=lambda source: (
                _parse_datetime(source.get("published_at")) or datetime.min,
                int(source.get("id") or 0),
            ),
            reverse=True,
        )
        lines = [
            f"# {task.get('title')}", "",
            "## 最新动态索引" if chinese else "## Latest development index",
        ]
        for source in sources:
            source_id = source.get("id") or "?"
            citation = f"Video {source_id}" if source.get("kind") == "video" else str(source_id)
            date = str(source.get("published_at") or "")[:10]
            date_label = date or ("日期未标明" if chinese else "Date not stated")
            title = _clean_evidence(source.get("title") or source.get("creator") or "Untitled", 220)
            excerpt = _clean_evidence(source.get("excerpt"), 620)
            evidence_type = source.get("evidence_type") or (
                "search_snippet" if source.get("status") == "snippet_only" else "full_page"
            )
            lines.extend(["", f"### {date_label} · {title}"])
            if excerpt:
                if chinese:
                    label = "摘要线索" if evidence_type == "search_snippet" else "已读正文要点"
                else:
                    label = "Search lead" if evidence_type == "search_snippet" else "Readable source detail"
                lines.append(f"- **{label}：** {excerpt} [{citation}]")
            else:
                lines.append(
                    f"- **{'来源索引' if chinese else 'Source index'}：** {title} [{citation}]"
                )
            domain = source.get("domain") or source.get("creator")
            if domain:
                lines.append(f"- **{'来源' if chinese else 'Source'}：** {domain}")
        if chinese:
            boundary = (
                "证据边界：总结模型本次未返回通过质量检查的归纳，以上保留了各来源中的具体信息；"
                "标为“摘要线索”的条目应打开来源核对正文，且本次内容不会自动写入长期记忆。"
            )
        else:
            boundary = (
                "Evidence boundary: the model did not return a synthesis that passed the quality checks, so this "
                "index preserves concrete source details. Open search-lead sources to verify their full text; this "
                "fallback is not saved to long-term memory."
            )
        lines.extend(["", f"> {boundary}"])
        return "\n".join(lines)

    def _failed_report(self, task, material, credentials=None):
        """Return an honest failure notice instead of presenting excerpts as a report."""
        language = self._response_language(task, credentials or {})
        if language in {"zh-cn", "zh-tw"}:
            return (
                f"# {task.get('title')}\n\n"
                "## 本次没有生成任务汇报\n\n"
                "资料搜索已经完成，但负责理解、翻译和归纳资料的模型本次没有成功生成可用的中文汇报。"
                "为避免把外语原文摘录或搜索线索冒充成任务结论，系统没有再输出引用堆叠式内容。\n\n"
                "请在模型可用后点击“再次运行”。现有来源仍保留在本次记录中供查看；本次结果不会写入长期记忆。"
            )
        return (
            f"# {task.get('title')}\n\n## No task report was generated\n\n"
            "Source collection completed, but the selected model did not produce a usable translated synthesis. "
            "The application did not present raw excerpts as a completed report. Run the task again when the model is available."
        )

    # --------------------------------------------------------------- memory
    def _manage_memories(self, task, run_id, summary, material, credentials, use_model=True):
        candidates = self._memory_candidates(task, summary, credentials, use_model=use_model)
        created, merged, archived, preserved = [], [], [], []
        for candidate in candidates[:6]:
            result = self._upsert_learning_memory(task, run_id, candidate, material)
            created.extend(result.get("created", []))
            merged.extend(result.get("merged", []))
            archived.extend(result.get("archived", []))
            preserved.extend(result.get("preserved", []))
        return {
            "created_ids": created,
            "merged_ids": merged,
            "archived_duplicate_ids": archived,
            "preserved_important_ids": preserved,
            "graph_rebuilt": True,
        }

    def _memory_candidates(self, task, summary, credentials, use_model=True):
        language = self._response_language(task, credentials)
        language_label = _LANGUAGE_LABELS.get(language, "简体中文")
        prompt = (
            "Turn this learning report into at most 4 durable knowledge memories. Return only JSON: "
            '{"memories":[{"title":"...","content":"standalone factual memory","tags":["..."],'
            '"importance":0.0,"important":false}]}. '
            f"Write every title and content field in {language_label}. "
            "Do not store task status, vague commentary, unsupported claims, or copied source passages. "
            "Preserve citations/URLs only in provenance, not content.\n\n"
            + summary[:16000]
        )
        if use_model:
            try:
                output = self._llm_completion(prompt, credentials, max_tokens=1300)
            except Exception:
                output = ""
        else:
            output = ""
        if output:
            try:
                start, end = output.find("{"), output.rfind("}")
                payload = json.loads(output[start:end + 1])
                values = payload.get("memories", [])
                if isinstance(values, list):
                    normalized = []
                    for item in values:
                        if not isinstance(item, dict) or not _clean(item.get("content"), 5000):
                            continue
                        normalized.append({
                            "title": _clean(item.get("title"), 120) or task.get("title"),
                            "content": _clean(item.get("content"), 5000),
                            "tags": [_clean(tag, 50) for tag in item.get("tags", []) if _clean(tag, 50)][:10],
                            "importance": max(0.0, min(1.0, float(item.get("importance", 0.65) or 0.65))),
                            "important": bool(item.get("important", False)),
                        })
                    if normalized:
                        return normalized
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        safe_summary = re.sub(r"\[([^\]]+)\]\(https?://[^)]+\)", r"\1", summary)
        safe_summary = re.sub(r"https?://\S+", "", safe_summary)
        return [{
            "title": task.get("title"),
            "content": _clean(safe_summary, 3600),
            "tags": ["learner", task.get("source_type"), _clean(task.get("topic"), 40)],
            "importance": 0.6,
            "important": False,
        }]

    def _upsert_learning_memory(self, task, run_id, candidate, material):
        kb = self.knowledge_base
        if not kb:
            return {"created": [], "merged": [], "archived": [], "preserved": []}
        content = candidate["content"]
        related = [
            item for item in kb.search_memories(query=content[:800], include_archived=False, limit=30)
            if item.get("source") == "learner"
            and str((item.get("source_detail") or {}).get("learner_run_id") or "") == str(run_id)
        ]
        related.extend(
            item for item in kb.memories
            if item.get("source") == "learner"
            and str((item.get("source_detail") or {}).get("learner_run_id") or "") == str(run_id)
            and item not in related and not item.get("is_archived")
        )
        duplicates = [item for item in related if self._similarity(content, item.get("content")) >= 0.76]
        source_detail = {
            "type": "learner_summary",
            "learner_task_id": task.get("id"),
            "learner_run_id": run_id,
            "captured_at": _iso(),
            "quality_tier": material.get("quality_tier"),
            "quality_notice": material.get("quality_notice", ""),
            "sources": [
                {key: source.get(key) for key in ("title", "url", "kind", "published_at", "source_type")}
                for source in material.get("sources", [])[:10]
            ],
        }
        if not duplicates:
            memory = kb.add_memory(
                content,
                title=candidate.get("title"),
                source="learner",
                is_important=candidate.get("important", False),
                category="knowledge",
                tags=list(dict.fromkeys(["learner", task.get("source_type")] + candidate.get("tags", []))),
                confidence=0.66 if material.get("degraded") else 0.84,
                source_detail=source_detail,
                metadata={
                    "memory_layer": "semantic",
                    "memory_kind": "knowledge",
                    "retention_policy": "permanent" if candidate.get("important") else "compressible",
                    "importance_score": candidate.get("importance", 0.6),
                },
            )
            return {"created": [memory["id"]], "merged": [], "archived": [], "preserved": []}

        duplicates.sort(
            key=lambda item: (
                bool(item.get("is_important") or item.get("is_locked")),
                float(item.get("importance_score") or 0),
                str(item.get("date") or ""),
            ),
            reverse=True,
        )
        primary = duplicates[0]
        primary_id = primary.get("id")
        combined = self._merge_text(primary.get("content"), content)
        supersedes = list(primary.get("supersedes") or [])
        archived, preserved = [], []
        for duplicate in duplicates[1:]:
            duplicate_id = duplicate.get("id")
            combined = self._merge_text(combined, duplicate.get("content"))
            if duplicate.get("is_important") or duplicate.get("is_locked"):
                preserved.append(duplicate_id)
                continue
            kb.update_memory(duplicate_id, {"is_archived": True, "superseded_by": primary_id})
            supersedes.append(duplicate_id)
            archived.append(duplicate_id)
        kb.update_memory(primary_id, {
            "content": combined,
            "source_detail": source_detail,
            "tags": list(dict.fromkeys(list(primary.get("tags") or []) + ["learner"] + candidate.get("tags", [])))[:12],
            "is_important": bool(primary.get("is_important") or candidate.get("important")),
            "importance_score": max(float(primary.get("importance_score") or 0), candidate.get("importance", 0.6)),
            "supersedes": list(dict.fromkeys(supersedes)),
        })
        return {"created": [], "merged": [primary_id], "archived": archived, "preserved": preserved}

    @staticmethod
    def _tokens(text):
        value = str(text or "")
        tokens = set(token.lower() for token in re.findall(r"[A-Za-z0-9_+-]{2,}", value))
        for run in re.findall(r"[\u4e00-\u9fff]{2,}", value):
            tokens.update(run[index:index + 2] for index in range(len(run) - 1))
        return tokens

    @classmethod
    def _similarity(cls, left, right):
        left_clean, right_clean = _clean(left, 8000).lower(), _clean(right, 8000).lower()
        if not left_clean or not right_clean:
            return 0.0
        left_tokens, right_tokens = cls._tokens(left_clean), cls._tokens(right_clean)
        jaccard = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
        sequence = SequenceMatcher(None, left_clean[:3000], right_clean[:3000]).ratio()
        containment = min(len(left_tokens & right_tokens) / max(1, min(len(left_tokens), len(right_tokens))), 1.0)
        return max(sequence, jaccard * 0.75 + containment * 0.25)

    @staticmethod
    def _merge_text(left, right):
        left, right = _clean(left, 6000), _clean(right, 6000)
        if not left:
            return right
        if not right or right.lower() in left.lower():
            return left
        if left.lower() in right.lower():
            return right
        left_lines = [item.strip() for item in left.splitlines() if item.strip()]
        additions = [item.strip() for item in right.splitlines() if item.strip()]
        known = {re.sub(r"\W+", "", item).lower() for item in left_lines}
        novel = [item for item in additions if re.sub(r"\W+", "", item).lower() not in known]
        return _clean("\n".join(left_lines + novel), 8000)


learner_service = LearnerService()
