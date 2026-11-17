import os
import json
import codecs
import base64
import asyncio
import random
import sys
import re
import hashlib
import uuid
import shutil
import threading
import copy
from functools import wraps
from datetime import datetime, timedelta
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote, urlparse

from saya_app.config import (
    CHAT_MEMORY_DIR,
    KNOWLEDGE_BASE_DIR,
    LLAVA_MMPROJ_PATH,
    LLAVA_MODEL_PATH,
    MMD_DIRECTORY,
    PROJECT_ROOT,
)
from saya_app.services.memory_store import SQLiteMemoryStore
from saya_app.services.memory_graph import MemoryGraphStore
from saya_app.services.embedding_provider import (
    EmbeddingProviderError,
    create_embedding_provider,
)
from saya_app.services.time_context import TimeContextService


def memory_write_locked(method):
    """Serialize mutations across API requests and concurrent Learner workers."""
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        with self._memory_write_lock:
            return method(self, *args, **kwargs)
    return wrapped

class SafeStream:
    def __init__(self, original):
        self.original = original
        
    def write(self, msg):
        try:
            if self.original:
                self.original.write(msg)
        except OSError:
            pass # Ignore invalid handle errors
            
    def flush(self):
        try:
            if self.original:
                self.original.flush()
        except OSError:
            pass
            
    def fileno(self):
        try:
            if self.original:
                return self.original.fileno()
        except OSError:
            pass
        return -1

    def isatty(self):
        try:
            return bool(self.original and self.original.isatty())
        except OSError:
            return False

def log_to_file(msg):
    try:
        print(f"[LOG] {msg}")
    except OSError:
        pass

@contextmanager
def safe_c_execution():
    # Robust context manager to save and restore FDs
    # This protects against C-libraries (llama.cpp) closing stdout/stderr (FD 1/2)
    original_stdout_fd = 1
    original_stderr_fd = 2
    
    saved_stdout_fd = None
    saved_stderr_fd = None
    
    try:
        # Try to save original FDs
        try:
            saved_stdout_fd = os.dup(original_stdout_fd)
        except Exception:
            pass
            
        try:
            saved_stderr_fd = os.dup(original_stderr_fd)
        except Exception:
            pass
            
        yield
        
    finally:
        # Restore FDs if they were closed/changed
        if saved_stdout_fd is not None:
            try:
                os.dup2(saved_stdout_fd, original_stdout_fd)
                os.close(saved_stdout_fd)
            except Exception:
                pass
                
        if saved_stderr_fd is not None:
            try:
                os.dup2(saved_stderr_fd, original_stderr_fd)
                os.close(saved_stderr_fd)
            except Exception:
                pass

class MemoryManager:
    def __init__(self, base_dir=str(CHAT_MEMORY_DIR)):
        self.base_dir = base_dir
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        # Keep time metadata beside chat history so alternate/test memory
        # roots remain isolated.  The ledger intentionally stores no message
        # content.
        self.time_events = TimeContextService(os.path.join(self.base_dir, "time_events.jsonl"))
        self.current_session_file = None
        self.pinned_file = os.path.join(self.base_dir, "pinned.json")
        self.legacy_sessions_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "knowledge_base",
            "sessions.json"
        )
        self._delete_plans = {}
        self._recover_staged_session_deletions()
        self.initialize_session()

    def _recover_staged_session_deletions(self):
        """Restore session files left staged by an interrupted cascade delete."""
        try:
            for name in os.listdir(self.base_dir):
                marker = ".jsonl.deleting-"
                if marker not in name:
                    continue
                staged_path = os.path.join(self.base_dir, name)
                if name.endswith(".committed"):
                    os.remove(staged_path)
                    continue
                original_name = name.split(marker, 1)[0] + ".jsonl"
                original_path = self._safe_session_path(original_name)
                if original_path and not os.path.exists(original_path):
                    os.replace(staged_path, original_path)
        except OSError as exc:
            print(f"Error recovering staged session deletion: {exc}")

    def _safe_session_path(self, session_id):
        if not session_id or session_id != os.path.basename(session_id):
            return None
        if not session_id.startswith("session_") or not session_id.endswith(".jsonl"):
            return None
        path = os.path.abspath(os.path.join(self.base_dir, session_id))
        base = os.path.abspath(self.base_dir)
        if os.path.commonpath([path, base]) != base:
            return None
        return path

    def _load_legacy_sessions(self):
        if not os.path.exists(self.legacy_sessions_file):
            return []
        try:
            with codecs.open(self.legacy_sessions_file, "r", "utf-8") as f:
                data = json.load(f)
            return data.get("sessions", []) if isinstance(data, dict) else []
        except Exception as e:
            print(f"Error loading legacy sessions: {e}")
            return []

    def _find_legacy_session(self, session_id):
        if not isinstance(session_id, str) or not session_id.startswith("legacy:"):
            return None
        legacy_id = session_id.split(":", 1)[1]
        for session in self._load_legacy_sessions():
            if str(session.get("id")) == legacy_id:
                return session
        return None

    def initialize_session(self):
        """Start a new legacy/current session and return its stable id."""
        session_id = self.create_session(make_current=True)
        return session_id

    def create_session(self, make_current=False):
        """Create a collision-resistant session id without changing global state by default."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S-%f")
        session_id = f"session_{timestamp}-{uuid.uuid4().hex[:8]}.jsonl"
        path = self._safe_session_path(session_id)
        if not path:
            raise ValueError("Unable to create a safe session path")
        if make_current:
            self.current_session_file = path
        return session_id

    def get_pinned_set(self):
        if not os.path.exists(self.pinned_file):
            return set()
        try:
            with codecs.open(self.pinned_file, "r", "utf-8") as f:
                return set(json.load(f))
        except:
            return set()

    def save_pinned_set(self, pinned_set):
        try:
            with codecs.open(self.pinned_file, "w", "utf-8") as f:
                json.dump(list(pinned_set), f)
        except Exception as e:
            print(f"Error saving pinned: {e}")

    def pin_session(self, session_id):
        if isinstance(session_id, str) and session_id.startswith("legacy:"):
            return False
        pinned = self.get_pinned_set()
        pinned.add(session_id)
        self.save_pinned_set(pinned)
        return True

    def unpin_session(self, session_id):
        if isinstance(session_id, str) and session_id.startswith("legacy:"):
            return False
        pinned = self.get_pinned_set()
        if session_id in pinned:
            pinned.remove(session_id)
            self.save_pinned_set(pinned)
        return True

    def _last_completed_qa_time(self, entries):
        """Return the latest completed exchange time, or pending-turn time.

        A user message is now persisted before long Agent work. If generation
        is interrupted, the pending session must remain near the top of the
        sidebar instead of sorting to the bottom and appearing to disappear.
        """
        waiting_for_answer = False
        last_qa_time = ""
        last_activity_time = ""
        for entry in entries or []:
            if not isinstance(entry, dict):
                continue
            role = str(entry.get("role", "") or "").strip().lower()
            if role in {"user", "ai", "assistant"}:
                last_activity_time = str(entry.get("time", "") or "").strip() or last_activity_time
            if role == "user":
                waiting_for_answer = True
            elif role in {"ai", "assistant"} and waiting_for_answer:
                last_qa_time = str(entry.get("time", "") or "").strip()
                waiting_for_answer = False
        return last_qa_time or last_activity_time

    def build_archive_exchanges(self, entries):
        """Return completed user/assistant pairs with stable source-index IDs."""
        exchanges = []
        pending_questions = []
        question_indices = []
        for index, entry in enumerate(entries or []):
            if isinstance(entry, dict):
                role = str(entry.get("role") or "").strip().lower()
                content = str(entry.get("content") or "").strip()
            else:
                content = str(entry or "").strip()
                lowered = content.lower()
                if lowered.startswith("doctor:"):
                    role, content = "user", content.split(":", 1)[1].strip()
                elif lowered.startswith("saya:"):
                    role, content = "assistant", content.split(":", 1)[1].strip()
                else:
                    role = "system"
            if not content:
                continue
            if role == "user":
                pending_questions.append(content)
                question_indices.append(index)
            elif role in {"ai", "assistant"} and pending_questions:
                exchanges.append({
                    "id": f"qa-{question_indices[0]}-{index}",
                    "question": "\n".join(pending_questions).strip(),
                    "answer": content,
                    "question_index": question_indices[0],
                    "answer_index": index,
                })
                pending_questions = []
                question_indices = []
        return exchanges

    def _session_time_sort_key(self, value):
        raw = str(value or "").strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d_%H-%M-%S",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%d",
        ):
            try:
                return datetime.strptime(raw[:19], fmt).timestamp()
            except (TypeError, ValueError):
                continue
        return 0.0

    @staticmethod
    def _is_web_session_file(filename):
        """Return whether a session file belongs in the web history.

        Minecraft sessions use the ``session_mc_`` namespace.  The old
        ``session_saya_unified.jsonl`` file was written by the former shared
        web/game bridge and contains mixed Minecraft, desktop and test rows,
        so exposing it as a normal web chat would reintroduce game dialogue
        into the sidebar.
        """
        name = str(filename or "")
        return (
            name.startswith("session_")
            and name.endswith(".jsonl")
            and not name.startswith("session_mc_")
            and name != "session_saya_unified.jsonl"
        )

    def get_session_list(self):
        """Get list of available sessions with preview"""
        sessions = []
        files = [f for f in os.listdir(self.base_dir) if self._is_web_session_file(f)]
        pinned_set = self.get_pinned_set()
        
        for file in files:
            path = os.path.join(self.base_dir, file)
            try:
                with codecs.open(path, "r", "utf-8") as f:
                    entries = [json.loads(line) for line in f if line.strip()]
                role_mode = next(
                    (str(entry.get("role_mode", "") or "") for entry in entries if entry.get("role_mode")),
                    ""
                )
                last_qa_time = self._last_completed_qa_time(entries)
                if entries:
                    first = entries[0]
                    first_content = str(first.get("content", ""))
                    preview_entry = first
                    if first.get("role") == "user" and "[IMAGE:" in first_content:
                        ai_entry = next((item for item in entries if item.get("role") == "ai"), None)
                        if ai_entry:
                            preview_entry = ai_entry
                    preview_content = self._clean_session_preview(preview_entry.get("content", ""))
                    preview = preview_content[:30] + ("..." if len(preview_content) > 30 else "")
                else:
                    preview = "Empty session"
                
                sessions.append({
                    "id": file,
                    "name": file.replace("session_", "").replace(".jsonl", ""),
                    "preview": preview,
                    "time": last_qa_time,
                    "last_qa_time": last_qa_time,
                    "pinned": file in pinned_set,
                    "role_mode": role_mode
                })
            except:
                continue

        for legacy in self._load_legacy_sessions():
            history = legacy.get("history") or []
            preview = legacy.get("preview") or (history[0] if history else "Legacy session")
            sessions.append({
                "id": f"legacy:{legacy.get('id')}",
                "name": legacy.get("time", "Legacy session"),
                "preview": str(preview)[:30] + ("..." if len(str(preview)) > 30 else ""),
                "time": legacy.get("time", ""),
                "last_qa_time": legacy.get("time", ""),
                "pinned": False,
                "legacy": True
            })
        sessions.sort(
            key=lambda item: (
                self._session_time_sort_key(item.get("last_qa_time") or item.get("time")),
                str(item.get("id", "")),
            ),
            reverse=True,
        )
        return sessions

    def _clean_session_preview(self, content):
        text = str(content or "")
        text = re.sub(r"\[IMAGE:.*?\]", "[Image]", text, flags=re.DOTALL)
        text = re.sub(r"\[FILE:.*?\]", "[File]", text, flags=re.DOTALL)
        text = re.sub(r"\(System Note:.*?\)", "", text, flags=re.DOTALL)
        text = re.sub(r"\[FILE_CONTEXT\].*?\[/FILE_CONTEXT\]", "", text, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"\s+", " ", text).strip()
        return text or "Untitled session"

    def _search_terms(self, query):
        query = re.sub(r"\s+", " ", str(query or "")).strip().lower()
        words = [w for w in re.findall(r"[a-z0-9_\-\u4e00-\u9fff]+", query) if len(w) >= 1]
        terms = set(words)
        compact = query.replace(" ", "")
        if len(compact) >= 2:
            terms.add(compact)
            for i in range(len(compact) - 1):
                chunk = compact[i:i + 2]
                if re.search(r"[\u4e00-\u9fff]", chunk):
                    terms.add(chunk)
        return [term for term in terms if term]

    def _score_search_match(self, content, terms, query):
        text = str(content or "").lower()
        if not text:
            return 0
        score = 0
        if query and query in text:
            score += 10 + len(query)
        for term in terms:
            if term in text:
                score += 4 + min(len(term), 8)
            elif len(term) >= 3:
                # Partial stem-ish matching for English/number terms.
                for size in range(min(len(term), 6), 2, -1):
                    if term[:size] in text:
                        score += 2
                        break
        return score

    def get_chat_activity_stats(self, year=None, month=None):
        now = datetime.now()
        try:
            year = int(year) if year else now.year
        except (TypeError, ValueError):
            year = now.year
        try:
            month = int(month) if month else now.month
        except (TypeError, ValueError):
            month = now.month
        month = max(1, min(12, month))
        first = datetime(year, month, 1)
        if month == 12:
            next_month = datetime(year + 1, 1, 1)
        else:
            next_month = datetime(year, month + 1, 1)
        month_dates = []
        cursor = first
        while cursor < next_month:
            month_dates.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)

        active_dates = set()
        all_dates = set()
        total_sessions = 0
        total_messages = 0
        files = [f for f in os.listdir(self.base_dir) if self._is_web_session_file(f)]
        for file in files:
            total_sessions += 1
            path = os.path.join(self.base_dir, file)
            try:
                with codecs.open(path, "r", "utf-8") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        total_messages += 1
                        date_str = str(data.get("time", ""))[:10]
                        if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                            all_dates.add(date_str)
                            if date_str in month_dates:
                                active_dates.add(date_str)
            except Exception:
                continue

        for legacy in self._load_legacy_sessions():
            total_sessions += 1
            history = legacy.get("history") or []
            total_messages += len(history)
            date_str = str(legacy.get("time", ""))[:10]
            if re.match(r"\d{4}-\d{2}-\d{2}", date_str):
                all_dates.add(date_str)
                if date_str in month_dates:
                    active_dates.add(date_str)

        today = now.strftime("%Y-%m-%d")
        days = []
        for date_str in month_dates:
            if date_str in active_dates:
                status = "active"
            elif date_str <= today:
                status = "inactive"
            else:
                status = "future"
            days.append({
                "date": date_str,
                "day": int(date_str[-2:]),
                "status": status
            })

        return {
            "total_sessions": total_sessions,
            "total_messages": total_messages,
            "active_days_total": len(all_dates),
            "active_days_month": len(active_dates),
            "year": year,
            "month": month,
            "month_days": days
        }

    def get_daily_conversation_stats(self, date_str=None):
        """Count completed conversations and messages for one calendar day."""
        try:
            target_date = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            target_date = datetime.now().strftime("%Y-%m-%d")

        user_messages = 0
        assistant_messages = 0
        completed_conversations = 0
        active_sessions = 0
        files = [name for name in os.listdir(self.base_dir) if self._is_web_session_file(name)]
        for name in files:
            path = os.path.join(self.base_dir, name)
            session_active = False
            pending_user = False
            try:
                with codecs.open(path, "r", "utf-8") as handle:
                    for line in handle:
                        if not line.strip():
                            continue
                        entry = json.loads(line)
                        role = str(entry.get("role") or "").strip().lower()
                        entry_date = str(entry.get("time") or "")[:10]
                        if role == "user":
                            pending_user = True
                            if entry_date == target_date:
                                user_messages += 1
                                session_active = True
                        elif role in {"ai", "assistant"}:
                            if entry_date == target_date:
                                assistant_messages += 1
                                session_active = True
                                if pending_user:
                                    completed_conversations += 1
                            pending_user = False
                if session_active:
                    active_sessions += 1
            except (OSError, ValueError, json.JSONDecodeError):
                continue

        for legacy in self._load_legacy_sessions():
            if str(legacy.get("time") or "")[:10] != target_date:
                continue
            active_sessions += 1
            exchanges = self.build_archive_exchanges(legacy.get("history") or [])
            completed_conversations += len(exchanges)
            for item in legacy.get("history") or []:
                text = str(item or "").strip().lower()
                if text.startswith("doctor:"):
                    user_messages += 1
                elif text.startswith("saya:"):
                    assistant_messages += 1

        return {
            "date": target_date,
            "conversation_count": completed_conversations,
            "user_message_count": user_messages,
            "assistant_message_count": assistant_messages,
            "session_count": active_sessions,
        }

    def search_sessions(self, query, limit=50):
        query = re.sub(r"\s+", " ", str(query or "")).strip().lower()
        if not query:
            return []
        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 50

        terms = self._search_terms(query)
        results = []
        files = sorted(
            [
                f for f in os.listdir(self.base_dir)
                if self._is_web_session_file(f)
            ],
            reverse=True
        )
        for file in files:
            path = os.path.join(self.base_dir, file)
            try:
                matches = []
                entries = []
                with codecs.open(path, "r", "utf-8") as f:
                    for index, line in enumerate(f):
                        data = json.loads(line)
                        entries.append(data)
                        content = str(data.get("content", ""))
                        score = self._score_search_match(content, terms, query)
                        if score > 0:
                            snippet = content[:220] + ("..." if len(content) > 220 else "")
                            matches.append({
                                "index": index,
                                "role": data.get("role", ""),
                                "time": data.get("time", ""),
                                "snippet": self._clean_session_preview(snippet),
                                "score": score
                            })
                if matches:
                    last_qa_time = self._last_completed_qa_time(entries)
                    matches.sort(key=lambda item: item.get("score", 0), reverse=True)
                    results.append({
                        "id": file,
                        "name": file.replace("session_", "").replace(".jsonl", ""),
                        "time": last_qa_time,
                        "last_qa_time": last_qa_time,
                        "matches": matches[:5],
                        "match_count": len(matches),
                        "score": sum(item.get("score", 0) for item in matches)
                    })
            except Exception:
                continue
            if len(results) >= limit:
                break

        for legacy in self._load_legacy_sessions():
            history = [str(item) for item in legacy.get("history", [])]
            matches = []
            for index, content in enumerate(history):
                score = self._score_search_match(content, terms, query)
                if score > 0:
                    matches.append({
                        "index": index,
                        "role": "legacy",
                        "time": legacy.get("time", ""),
                        "snippet": self._clean_session_preview(content[:220] + ("..." if len(content) > 220 else "")),
                        "score": score
                    })
            if matches and len(results) < limit:
                matches.sort(key=lambda item: item.get("score", 0), reverse=True)
                results.append({
                    "id": f"legacy:{legacy.get('id')}",
                    "name": legacy.get("time", "Legacy session"),
                    "time": legacy.get("time", ""),
                    "last_qa_time": legacy.get("time", ""),
                    "legacy": True,
                    "matches": matches[:5],
                    "match_count": len(matches),
                    "score": sum(item.get("score", 0) for item in matches)
                })
        results.sort(key=lambda item: (item.get("score", 0), item.get("time", "")), reverse=True)
        return results[:limit]

    def load_session(self, session_id, make_current=True):
        """Load a specific session completely"""
        legacy = self._find_legacy_session(session_id)
        if legacy:
            # Legacy sessions are read-only. A continued conversation will be saved
            # into a fresh JSONL session instead of overwriting another active file.
            if make_current:
                self.current_session_file = None
            return [str(item) for item in legacy.get("history", [])]

        path = self._safe_session_path(session_id)
        if not path:
            return []
        if not os.path.exists(path):
            return []
        
        if make_current:
            self.current_session_file = path
        history_items = []
        try:
            with codecs.open(path, "r", "utf-8") as f:
                for line in f:
                    data = json.loads(line)
                    if data['role'] == 'user':
                        history_items.append(f"Doctor: {data['content']}")
                    elif data['role'] == 'system':
                        history_items.append(f"[System]: {data['content']}")
                    else:
                        history_items.append(f"Saya: {data['content']}")
        except Exception as e:
            print(f"Error loading session {session_id}: {e}")
            
        return history_items

    def delete_session(self, session_id):
        """Delete a session file"""
        if isinstance(session_id, str) and session_id.startswith("legacy:"):
            return False
        path = self._safe_session_path(session_id)
        if not path:
            return False
        if os.path.exists(path):
            os.remove(path)
            return True
        return False

    def _session_delete_signature(self, session_id):
        path = self._safe_session_path(session_id)
        if not path or not os.path.isfile(path):
            return ""
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as session_file:
                while True:
                    chunk = session_file.read(1024 * 1024)
                    if not chunk:
                        break
                    digest.update(chunk)
            return digest.hexdigest()
        except OSError:
            return ""

    def preview_session_deletion(self, session_id):
        if isinstance(session_id, str) and session_id.startswith("legacy:"):
            raise ValueError("Legacy sessions cannot be deleted here")
        signature = self._session_delete_signature(session_id)
        if not signature:
            raise ValueError("Session not found")
        now = datetime.now()
        self._delete_plans = {
            token: plan for token, plan in self._delete_plans.items()
            if plan.get("expires_at") and plan["expires_at"] >= now
        }
        token = str(uuid.uuid4())
        self._delete_plans[token] = {
            "session_id": str(session_id), "signature": signature,
            "expires_at": now + timedelta(minutes=10),
        }
        return {"confirmation_token": token, "session_id": str(session_id)}

    def stage_session_deletion(self, session_id, confirmation_token=None, expected_signature=None):
        if expected_signature is None:
            plan = self._delete_plans.pop(str(confirmation_token or ""), None)
            if not plan or plan.get("session_id") != str(session_id):
                raise ValueError("Session deletion confirmation expired. Review it again.")
            expected_signature = plan.get("signature")
            if plan.get("expires_at") < datetime.now():
                raise ValueError("Session deletion confirmation expired. Review it again.")
        if expected_signature != self._session_delete_signature(session_id):
            raise ValueError("Session changed. Review deletion again.")
        original_path = self._safe_session_path(session_id)
        staged_path = original_path + f".deleting-{uuid.uuid4().hex}"
        os.replace(original_path, staged_path)
        return {
            "session_id": str(session_id), "original_path": original_path,
            "staged_path": staged_path,
        }

    @staticmethod
    def rollback_staged_session(stage):
        try:
            if stage and os.path.exists(stage.get("staged_path", "")):
                os.replace(stage["staged_path"], stage["original_path"])
            return True
        except OSError:
            return False

    @staticmethod
    def commit_staged_session(stage):
        try:
            if stage and os.path.exists(stage.get("staged_path", "")):
                committed_path = stage["staged_path"] + ".committed"
                os.replace(stage["staged_path"], committed_path)
                try:
                    os.remove(committed_path)
                except OSError:
                    # The committed suffix tells startup recovery to finish
                    # cleanup instead of restoring a successfully deleted chat.
                    pass
            return True
        except OSError:
            return False

    def save_message(self, role, content, translation=None, metadata=None, session_id=None):
        """Save one message, preferably to an explicitly selected session."""
        target_path = self._safe_session_path(session_id) if session_id else self.current_session_file
        if not target_path:
            session_id = self.create_session(make_current=session_id is None)
            target_path = self._safe_session_path(session_id)
            
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "content": content
        }
        if translation:
            entry["translation"] = translation
        if isinstance(metadata, dict):
            entry.update(metadata)
            
        with codecs.open(target_path, "a", "utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            
    def update_message_translation(self, index, translation):
        """Update translation for a message at specific index (0-based)"""
        if not self.current_session_file or not os.path.exists(self.current_session_file):
            return False
            
        lines = []
        try:
            with codecs.open(self.current_session_file, "r", "utf-8") as f:
                lines = f.readlines()
        except:
            return False
            
        if index < 0 or index >= len(lines):
            return False
            
        try:
            data = json.loads(lines[index])
            data["translation"] = translation
            lines[index] = json.dumps(data, ensure_ascii=False) + "\n"
            
            with codecs.open(self.current_session_file, "w", "utf-8") as f:
                f.writelines(lines)
            return True
        except:
            return False

    def load_session_full(self, session_id, make_current=True):
        """Load full session data including translations"""
        legacy = self._find_legacy_session(session_id)
        if legacy:
            items = []
            for line in legacy.get("history", []):
                text = str(line)
                if text.startswith("Doctor:"):
                    role, content = "user", text.replace("Doctor:", "", 1).strip()
                elif text.startswith("Saya:"):
                    role, content = "ai", text.replace("Saya:", "", 1).strip()
                else:
                    role, content = "system", text
                items.append({
                    "time": legacy.get("time", ""),
                    "role": role,
                    "content": content,
                    "legacy": True
                })
            return items

        path = self._safe_session_path(session_id)
        if not path:
            return []
        if not os.path.exists(path):
            return []
        
        if make_current:
            self.current_session_file = path
        items = []
        try:
            with codecs.open(path, "r", "utf-8") as f:
                for line in f:
                    items.append(json.loads(line))
        except Exception as e:
            print(f"Error loading session {session_id}: {e}")
            
        return items

    def truncate_session(self, count, session_id=None):
        """Truncate an explicit session, falling back to the legacy current one."""
        target_path = self._safe_session_path(session_id) if session_id else self.current_session_file
        if not target_path or not os.path.exists(target_path):
            return False
            
        try:
            # Read all lines
            with codecs.open(target_path, "r", "utf-8") as f:
                lines = f.readlines()
            
            # Truncate
            if count < len(lines):
                lines = lines[:count]
                
                # Write back
                with codecs.open(target_path, "w", "utf-8") as f:
                    f.writelines(lines)
            return True
        except Exception as e:
            print(f"Error truncating session: {e}")
            return False

memory_manager = MemoryManager()

class ProjectManager:
    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "projects.json")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        self.projects = self.load_projects()
        self._delete_plans = {}

    def load_projects(self):
        if os.path.exists(self.file_path):
            try:
                with codecs.open(self.file_path, "r", "utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    return data
            except Exception as e:
                print(f"Error loading projects: {e}")
        return []

    def save_projects(self):
        try:
            temp_path = self.file_path + f".tmp-{uuid.uuid4().hex}"
            with codecs.open(temp_path, "w", "utf-8") as f:
                json.dump(self.projects, f, ensure_ascii=False, indent=4)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, self.file_path)
            return True
        except Exception as e:
            print(f"Error saving projects: {e}")
            try:
                if 'temp_path' in locals() and os.path.exists(temp_path):
                    os.remove(temp_path)
            except OSError:
                pass
            return False

    def list_projects(self):
        return sorted(self.projects, key=lambda p: p.get("updated_at", p.get("created_at", "")), reverse=True)

    def add_project(self, name):
        name = re.sub(r"\s+", " ", str(name or "")).strip()
        if not name:
            return None
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        project = {
            "id": str(uuid.uuid4()),
            "name": name[:80],
            "created_at": now,
            "updated_at": now,
            "session_ids": [],
            "notes": ""
        }
        self.projects.append(project)
        self.save_projects()
        return project

    def get_project(self, project_id):
        for project in self.projects:
            if project.get("id") == project_id:
                return project
        return None

    def add_session(self, project_id, session_id):
        if not project_id or not session_id:
            return False
        project = self.get_project(project_id)
        if not project:
            return False
        sessions = project.setdefault("session_ids", [])
        if session_id not in sessions:
            sessions.append(session_id)
        project["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_projects()
        return True

    def remove_session(self, session_id):
        changed = False
        for project in self.projects:
            sessions = project.get("session_ids", [])
            if session_id in sessions:
                project["session_ids"] = [sid for sid in sessions if sid != session_id]
                project["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
        if changed:
            self.save_projects()

    def delete_project(self, project_id):
        # Removes the project record and returns the session_ids it owned so
        # the caller can cascade-delete the associated session files. If the
        # project does not exist, returns None.
        if not project_id:
            return None
        project = self.get_project(project_id)
        if not project:
            return None
        session_ids = list(project.get("session_ids", []))
        self.projects = [p for p in self.projects if p.get("id") != project_id]
        self.save_projects()
        return session_ids

    def _project_delete_signature(self, project, memory_manager):
        session_signatures = {
            str(session_id): memory_manager._session_delete_signature(session_id)
            for session_id in project.get("session_ids", [])
        }
        raw = json.dumps(
            {"project": project, "sessions": session_signatures},
            ensure_ascii=False, sort_keys=True, default=str,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def preview_project_deletion(self, project_id, memory_manager):
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        now = datetime.now()
        self._delete_plans = {
            token: plan for token, plan in self._delete_plans.items()
            if plan.get("expires_at") and plan["expires_at"] >= now
        }
        token = str(uuid.uuid4())
        session_ids = list(project.get("session_ids", []))
        self._delete_plans[token] = {
            "project_id": str(project_id),
            "signature": self._project_delete_signature(project, memory_manager),
            "session_ids": session_ids,
            "session_signatures": {
                session_id: memory_manager._session_delete_signature(session_id)
                for session_id in session_ids
            },
            "expires_at": now + timedelta(minutes=10),
        }
        return {
            "confirmation_token": token, "project_id": str(project_id),
            "session_ids": session_ids,
        }

    def consume_project_deletion_plan(self, project_id, confirmation_token, memory_manager):
        plan = self._delete_plans.pop(str(confirmation_token or ""), None)
        project = self.get_project(project_id)
        if (
            not plan or not project or plan.get("project_id") != str(project_id)
            or plan.get("expires_at") < datetime.now()
            or plan.get("signature") != self._project_delete_signature(project, memory_manager)
        ):
            raise ValueError("Project changed or deletion confirmation expired. Review it again.")
        return plan

    def stage_project_deletion(self, project_id):
        project = self.get_project(project_id)
        if not project:
            raise ValueError("Project not found")
        project_copy = copy.deepcopy(project)
        original_index = self.projects.index(project)
        self.projects.pop(original_index)
        if not self.save_projects():
            self.projects.insert(original_index, project_copy)
            raise RuntimeError("Unable to persist project deletion")
        return {"project": project_copy, "index": original_index}

    def rollback_staged_project(self, stage):
        if not stage or not isinstance(stage.get("project"), dict):
            return False
        self.projects.insert(min(int(stage.get("index", 0)), len(self.projects)), stage["project"])
        if self.save_projects():
            return True
        self.projects = [project for project in self.projects if project is not stage["project"]]
        return False

    def find_project_for_session(self, session_id):
        for project in self.projects:
            if session_id in project.get("session_ids", []):
                return project
        return None

    def build_project_context(self, project_id, memory_manager, current_session_id=None, max_sessions=10, max_chars=6000):
        project = self.get_project(project_id)
        if not project:
            return ""
        session_ids = [sid for sid in project.get("session_ids", []) if sid != current_session_id]
        session_ids = session_ids[-max_sessions:]
        parts = []
        total = 0
        for session_id in reversed(session_ids):
            path = memory_manager._safe_session_path(session_id)
            if not path or not os.path.exists(path):
                continue
            try:
                messages = []
                with codecs.open(path, "r", "utf-8") as f:
                    for line in f:
                        data = json.loads(line)
                        role = "Doctor" if data.get("role") == "user" else "Saya"
                        content = re.sub(r"\s+", " ", str(data.get("content", ""))).strip()
                        if content:
                            messages.append(f"{role}: {content[:360]}")
                if not messages:
                    continue
                excerpt = "\n".join(messages[-12:])
                block = f"[Session {session_id}]\n{excerpt}\n"
                if total + len(block) > max_chars:
                    break
                parts.append(block)
                total += len(block)
            except Exception:
                continue
        if not parts:
            return ""
        return (
            f"[System: Project Memory]\n"
            f"Current project: {project.get('name', 'Untitled Project')}.\n"
            "This is the active project context and has high priority for project-specific continuity. Use it before general long-term memory for project-specific decisions, terminology, preferences, names, files, and implementation details. Keep it separate from unrelated global chat history, and do not overrule the current user message.\n"
            + "\n".join(parts) + "\n"
        )

project_manager = ProjectManager()

class KnowledgeBase:
    VECTOR_DIMENSIONS = 384
    VECTOR_MATCH_THRESHOLD = 0.08
    MEMORY_LAYER_ORDER = {"core": 0, "semantic": 1, "episodic": 2, "archive": 3}
    DAILY_ACTIVITY_MEMORY_ID = "system-daily-activity-current"
    DAILY_ACTIVITY_MANAGED_SLOT = "daily_activity:today"
    ANKI_CHECKIN_MEMORY_ID = "system-anki-checkin-status"
    ANKI_CHECKIN_MANAGED_SLOT = "anki:checkin_status"
    ABOUT_YOU_PERSONALITY_ID = "about-you-personality"
    ABOUT_YOU_POSSESSION_PREFIX = "about-you-possession-"
    ABOUT_YOU_POSSESSION_SUGGESTED_LENGTH = 600
    RETENTION_POLICIES = {"permanent", "durable", "compressible", "temporary"}

    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "knowledge.json")
        self.settings_path = os.path.join(self.base_dir, "memory_settings.json")
        self.candidates_path = os.path.join(self.base_dir, "memory_candidates.json")
        self.deletions_path = os.path.join(self.base_dir, "memory_deletions.json")
        self.maintenance_path = os.path.join(self.base_dir, "memory_maintenance.json")
        self.cold_archive_path = os.path.join(self.base_dir, "memory_cold_archive.jsonl")
        self.sqlite_path = os.path.join(self.base_dir, "memory3.sqlite3")
        self.memory_v4_store = None
        self.backup_dir = os.path.join(self.base_dir, "backups")
        self.sqlite_store = None
        self.embedding_provider = None
        self.embedding_last_error = ""
        self._vector_cache = {}
        self._last_memory_backup_at = None
        self._last_access_flush_at = datetime.now()
        self._access_metadata_dirty = False
        self._dirty_access_ids = set()
        self._maintenance_lock = threading.RLock()
        self._memory_write_lock = threading.RLock()
        self._maintenance_running = False
        self._ai_compression_plans = {}
        self._memory_delete_plans = {}
        self.last_maintenance_result = None
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        
        self.memories = []
        self.memory_candidates = []
        self.deleted_memory_ids = {}
        self.load_memory_deletions()
        self.load_memories()
        # Settings must exist before candidate loading: the loader triggers
        # auto-adoption, which reads the adoption settings.
        self.settings = self.load_settings()
        self.load_memory_candidates()
        self.memory_maintenance = self.load_memory_maintenance_state()
        self.import_legacy_memories()
        self.normalize_all_memories()
        self.initialize_sqlite_store()
        if self.settings.get("memory_v4_write_enabled", False):
            self.initialize_memory_v4_store()
        self.initialize_embedding_provider()
        self.memory_graph = MemoryGraphStore(self.base_dir)
        self._memory_graph_dirty = True
        self.last_maintenance_result = self.run_memory_capacity_maintenance(
            trigger="startup",
        )

    def load_settings(self):
        defaults = {
            "auto_save_memories": True,
            "auto_merge_enabled": False,
            "auto_merge_days": 7,
            "last_auto_merge": None,
            "memory_2_enabled": True,
            "relevant_memory_count": 8,
            "include_memory_metadata": True,
            "local_vector_retrieval_enabled": True,
            "autonomous_memory_enabled": False,
            "layered_memory_enabled": True,
            # V4 plan section 3.3: age alone must never promote a candidate.
            # Default OFF; enable explicitly if ever needed.
            "candidate_auto_adopt_enabled": False,
            "candidate_auto_adopt_days": 3,
            "candidate_auto_adopt_min_confidence": 0.8,
            "api_context_window_tokens": 1000000,
            "local_context_window_tokens": 8192,
            "memory_prompt_budget_tokens": 12000,
            "memory_prompt_budget_ratio": 0.15,
            "memory_item_budget_tokens": 700,
            "memory_max_chars_core": 1200,
            "memory_max_chars_semantic": 2400,
            "memory_max_chars_episodic": 3600,
            "memory_max_chars_knowledge_simple": 1200,
            "memory_max_chars_knowledge_complex": 3600,
            "memory_core_limit": 10,
            "memory_candidate_pool_size": 80,
            "memory_active_soft_limit": 5000,
            "memory_archive_soft_limit": 50000,
            "memory_active_hard_limit": 6000,
            "memory_archive_hard_limit": 60000,
            "memory_capacity_warning_ratio": 0.80,
            "memory_capacity_auto_compact_enabled": True,
            "memory_cold_archive_enabled": True,
            "memory_cold_archive_batch_size": 200,
            "memory_retrieval_threshold": 0.08,
            "memory_access_flush_seconds": 120,
            "memory_compression_enabled": False,
            "memory_compression_age_days": 30,
            "memory_compression_min_group": 3,
            "memory_compression_group_size": 20,
            "memory_compression_summary_chars": 1800,
            "memory_compression_max_generations": 2,
            "memory_ai_compression_enabled": False,
            "memory_ai_compression_max_memories": 200,
            "memory_ai_compression_max_generations": 2,
            "memory_ai_compression_input_token_limit": 32000,
            "sqlite_memory_enabled": True,
            "sqlite_fts_enabled": True,
            "embedding_enabled": False,
            "embedding_provider": "sentence_transformers",
            "embedding_model": "BAAI/bge-m3",
            "embedding_device": "cpu",
            "embedding_api_url": "",
            "embedding_api_key_env": "EMBEDDING_API_KEY",
            "embedding_batch_size": 8,
            "embedding_candidate_count": 80,
            "embedding_min_similarity": 0.30,
            "embedding_index_batch_limit": 500,
            "embedding_api_timeout_seconds": 120,
            "embedding_max_chars": 12000,
            "embedding_auto_index_new": False,
            # V4 rollout flags remain off until the corresponding stage has
            # independent acceptance evidence. Shadow read is observational.
            "memory_v4_write_enabled": False,
            "memory_v4_read_enabled": False,
            "memory_v4_shadow_read_enabled": True,
            "memory_background_synthesis_enabled": False,
            "prompt_compiler_v2_enabled": False,
            "daily_state_v2_enabled": False,
            "agent_loop_enabled": False,
            # Explicit user consent is required before a remote Agent model
            # may receive private memory/daily tool results.
            "memory_v4_agent_allow_private": False,
            "web_tools_v2_enabled": False,
            "connector_discord_enabled": False,
            "connector_x_enabled": False,
            "external_write_tools_enabled": False,
            "memory_graph_auto_expansion": 4,
            "memory_graph_deep_expansion": 10,
            "memory_graph_candidate_limit": 10000,
        }
        if os.path.exists(self.settings_path):
            try:
                with codecs.open(self.settings_path, "r", "utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    defaults.update(loaded)
            except Exception as e:
                print(f"Error loading memory settings: {e}")
        return defaults

    def save_settings(self):
        self._atomic_write_json(self.settings_path, self.settings)

    def load_memory_maintenance_state(self):
        defaults = {
            "version": 1,
            "last_band": "normal",
            "last_run_at": None,
            "last_result": None,
            "cold_archive_count": 0,
            "events": [],
        }
        if os.path.exists(self.maintenance_path):
            try:
                loaded = self._load_json_file(self.maintenance_path)
                if isinstance(loaded, dict):
                    defaults.update(loaded)
            except Exception as exc:
                print(f"Error loading memory maintenance state: {exc}")
        if not isinstance(defaults.get("events"), list):
            defaults["events"] = []
        defaults["events"] = [
            event for event in defaults["events"][-60:]
            if isinstance(event, dict) and event.get("id")
        ]
        try:
            defaults["cold_archive_count"] = max(0, int(defaults.get("cold_archive_count") or 0))
        except (TypeError, ValueError):
            defaults["cold_archive_count"] = 0
        if defaults["cold_archive_count"] == 0 and os.path.exists(self.cold_archive_path):
            try:
                with open(self.cold_archive_path, "r", encoding="utf-8") as archive_file:
                    defaults["cold_archive_count"] = sum(1 for line in archive_file if line.strip())
            except OSError:
                pass
        return defaults

    def save_memory_maintenance_state(self):
        self.memory_maintenance["events"] = list(self.memory_maintenance.get("events") or [])[-60:]
        return self._atomic_write_json(self.maintenance_path, self.memory_maintenance)

    def _record_memory_maintenance_event(
        self,
        code,
        level,
        message,
        details=None,
        compression_engine="deterministic_local",
        token_usage=0,
    ):
        event = {
            "id": str(uuid.uuid4()),
            "code": str(code or "memory_maintenance"),
            "level": str(level or "info"),
            "message": str(message or "Memory maintenance completed."),
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "acknowledged": False,
            "compression_engine": str(compression_engine or "deterministic_local"),
            "token_usage": max(0, int(token_usage or 0)),
            "details": details if isinstance(details, dict) else {},
        }
        events = self.memory_maintenance.setdefault("events", [])
        events.append(event)
        self.memory_maintenance["events"] = events[-60:]
        self.save_memory_maintenance_state()
        return event

    def acknowledge_memory_maintenance(self, event_id=None):
        changed = False
        event_id = str(event_id or "").strip()
        for event in self.memory_maintenance.get("events") or []:
            if event.get("acknowledged"):
                continue
            if not event_id or str(event.get("id")) == event_id:
                event["acknowledged"] = True
                event["acknowledged_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                changed = True
        if changed:
            self.save_memory_maintenance_state()
        return {"status": "success", "acknowledged": changed}

    def update_settings(self, updates):
        boolean_keys = {
            "auto_save_memories",
            "auto_merge_enabled",
            "memory_2_enabled",
            "include_memory_metadata",
            "local_vector_retrieval_enabled",
            "autonomous_memory_enabled",
            "layered_memory_enabled",
            "memory_compression_enabled",
            "memory_capacity_auto_compact_enabled",
            "memory_cold_archive_enabled",
            "memory_ai_compression_enabled",
            "sqlite_memory_enabled",
            "sqlite_fts_enabled",
            "embedding_enabled",
            "embedding_auto_index_new",
            "memory_v4_write_enabled",
            "memory_v4_read_enabled",
            "memory_v4_shadow_read_enabled",
            "memory_background_synthesis_enabled",
            "prompt_compiler_v2_enabled",
            "daily_state_v2_enabled",
            "agent_loop_enabled",
            "memory_v4_agent_allow_private",
            "web_tools_v2_enabled",
            "connector_discord_enabled",
            "connector_x_enabled",
            "external_write_tools_enabled",
        }
        integer_limits = {
            "auto_merge_days": (1, 365),
            "relevant_memory_count": (1, 50),
            "api_context_window_tokens": (8192, 2000000),
            "local_context_window_tokens": (2048, 262144),
            "memory_prompt_budget_tokens": (512, 200000),
            "memory_item_budget_tokens": (64, 12000),
            "memory_max_chars_core": (200, 12000),
            "memory_max_chars_semantic": (400, 30000),
            "memory_max_chars_episodic": (600, 50000),
            "memory_max_chars_knowledge_simple": (400, 12000),
            "memory_max_chars_knowledge_complex": (800, 30000),
            "memory_core_limit": (0, 100),
            "memory_candidate_pool_size": (10, 1000),
            "memory_active_soft_limit": (100, 1000000),
            "memory_archive_soft_limit": (100, 5000000),
            "memory_active_hard_limit": (100, 1200000),
            "memory_archive_hard_limit": (100, 6000000),
            "memory_cold_archive_batch_size": (2, 2000),
            "memory_access_flush_seconds": (10, 3600),
            "memory_compression_age_days": (1, 3650),
            "memory_compression_min_group": (2, 100),
            "memory_compression_group_size": (2, 500),
            "memory_compression_summary_chars": (300, 20000),
            "memory_compression_max_generations": (1, 5),
            "memory_ai_compression_max_memories": (2, 500),
            "memory_ai_compression_max_generations": (1, 5),
            "memory_ai_compression_input_token_limit": (2000, 120000),
            "embedding_batch_size": (1, 256),
            "embedding_candidate_count": (5, 1000),
            "embedding_index_batch_limit": (1, 1000000),
            "embedding_api_timeout_seconds": (10, 600),
            "embedding_max_chars": (200, 100000),
            "memory_graph_auto_expansion": (1, 20),
            "memory_graph_deep_expansion": (1, 50),
            "memory_graph_candidate_limit": (100, 10000),
        }
        float_limits = {
            "memory_prompt_budget_ratio": (0.001, 0.5),
            "memory_capacity_warning_ratio": (0.50, 0.95),
            "memory_retrieval_threshold": (0.0, 1.0),
            "embedding_min_similarity": (-1.0, 1.0),
        }
        string_limits = {
            "embedding_provider": 40,
            "embedding_model": 300,
            "embedding_device": 30,
            "embedding_api_url": 600,
            "embedding_api_key_env": 100,
        }
        for key, value in (updates or {}).items():
            if key in boolean_keys:
                if isinstance(value, str):
                    self.settings[key] = value.strip().lower() not in {"", "0", "false", "no", "off"}
                else:
                    self.settings[key] = bool(value)
            elif key in integer_limits:
                try:
                    lower, upper = integer_limits[key]
                    self.settings[key] = max(lower, min(upper, int(value)))
                except (TypeError, ValueError):
                    continue
            elif key in float_limits:
                try:
                    lower, upper = float_limits[key]
                    self.settings[key] = max(lower, min(upper, float(value)))
                except (TypeError, ValueError):
                    continue
            elif key in string_limits:
                text = str(value or "").strip()[:string_limits[key]]
                if key == "embedding_provider" and text not in {"sentence_transformers", "openai_compatible"}:
                    continue
                if key == "embedding_api_key_env" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", text):
                    continue
                self.settings[key] = text
        self.settings["memory_active_hard_limit"] = max(
            int(self.settings.get("memory_active_soft_limit", 5000)),
            int(self.settings.get("memory_active_hard_limit", 6000)),
        )
        self.settings["memory_archive_hard_limit"] = max(
            int(self.settings.get("memory_archive_soft_limit", 50000)),
            int(self.settings.get("memory_archive_hard_limit", 60000)),
        )
        self.save_settings()
        if self.settings.get("sqlite_memory_enabled", True) and not self.sqlite_store:
            self.initialize_sqlite_store()
        if self.settings.get("memory_v4_write_enabled", False) and not self.memory_v4_store:
            self.initialize_memory_v4_store()
        elif not self.settings.get("memory_v4_write_enabled", False):
            self.memory_v4_store = None
        self.initialize_embedding_provider()
        if hasattr(self, "memory_maintenance"):
            self.last_maintenance_result = self.run_memory_capacity_maintenance(
                trigger="settings_update",
            )
        return self.settings

    def initialize_sqlite_store(self):
        if not self.settings.get("sqlite_memory_enabled", True):
            self.sqlite_store = None
            return {"status": "disabled"}
        store = SQLiteMemoryStore(self.sqlite_path)
        self.sqlite_store = store
        if not store.available:
            return {"status": "fallback_json", "error": store.last_error}

        status = store.integrity_check()
        migration_complete = str((status.get("meta") or {}).get("migration_complete") or "") == "1"
        sqlite_tombstones = store.load_deleted_ids()
        if sqlite_tombstones:
            merged = set(self.deleted_memory_ids) | sqlite_tombstones
            if merged != set(self.deleted_memory_ids):
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                for memory_id in sqlite_tombstones:
                    self.deleted_memory_ids.setdefault(memory_id, {"deleted_at": now, "reason": "sqlite_migration"})
                self.save_memory_deletions()
            before_count = len(self.memories)
            self.memories = [
                memory for memory in self.memories
                if str(memory.get("id") or "") not in merged
            ]
            if len(self.memories) != before_count:
                self._atomic_write_json(self.file_path, self.memories)
        if not migration_complete:
            self._backup_existing_file(
                self.file_path,
                "knowledge.pre-sqlite-migration",
                force=True,
                min_interval_seconds=0,
            )
        sync_result = store.upsert_memories(
            self.memories,
            migration_source=self.file_path if not migration_complete else "",
        )
        if sync_result.get("status") != "success":
            return {"status": "fallback_json", **sync_result}
        for memory_id in self.deleted_memory_ids:
            store.mark_deleted(memory_id)

        # Preserve database-only records after an interrupted JSON export. JSON
        # records always win on ID conflicts, while missing IDs are restored.
        json_ids = {str(memory.get("id")) for memory in self.memories if memory.get("id")}
        recovered = [
            memory for memory in store.load_active_memories()
            if str(memory.get("id") or "") not in json_ids
        ]
        if recovered:
            self.memories.extend(recovered)
            self.normalize_all_memories()
        return {
            "status": "success",
            "migrated": not migration_complete,
            "synced_count": sync_result.get("count", 0),
            "recovered_count": len(recovered),
        }

    def rebuild_sqlite_store(self):
        if not self.settings.get("sqlite_memory_enabled", True):
            return {"status": "disabled", "count": 0}
        if not self.sqlite_store or not self.sqlite_store.available:
            init_result = self.initialize_sqlite_store()
            if init_result.get("status") != "success":
                return init_result
        return self.sqlite_store.rebuild(self.memories)

    def get_sqlite_store_status(self):
        if not self.settings.get("sqlite_memory_enabled", True):
            return {"enabled": False, "available": False, "status": "disabled"}
        if not self.sqlite_store:
            return {"enabled": True, "available": False, "status": "not_initialized"}
        status = self.sqlite_store.integrity_check()
        status.update({
            "enabled": True,
            "available": bool(self.sqlite_store.available and status.get("ok")),
            "status": "ready" if status.get("ok") else "fallback_json",
        })
        return status

    def initialize_embedding_provider(self):
        if not self.settings.get("embedding_enabled", False):
            self.embedding_provider = None
            self.embedding_last_error = ""
            return {"status": "disabled", "ready": False}
        try:
            self.embedding_provider = create_embedding_provider(self.settings)
            self.embedding_last_error = ""
            status = self.embedding_provider.status()
            return {"status": "ready" if status.get("ready") else "dependency_missing", **status}
        except Exception as exc:
            self.embedding_provider = None
            self.embedding_last_error = str(exc)
            return {"status": "unavailable", "ready": False, "error": self.embedding_last_error}

    def _embedding_text(self, memory):
        text = self._memory_text(memory)
        try:
            max_chars = max(200, int(self.settings.get("embedding_max_chars", 12000)))
        except (TypeError, ValueError):
            max_chars = 12000
        return text[:max_chars]

    def _embedding_content_hash(self, memory):
        text = self._embedding_text(memory)
        return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()

    def get_embedding_status(self):
        configured = bool(self.settings.get("embedding_enabled", False))
        provider_status = {
            "ready": False,
            "provider": self.settings.get("embedding_provider", "sentence_transformers"),
            "model": self.settings.get("embedding_model", "BAAI/bge-m3"),
        }
        if self.embedding_provider:
            provider_status = self.embedding_provider.status()
        provider_key = str(provider_status.get("provider_key") or "")
        index_status = (
            self.sqlite_store.embedding_status(provider_key)
            if self.sqlite_store and self.sqlite_store.available
            else {"available": False, "count": 0}
        )
        stale_count = 0
        missing_count = 0
        if provider_key and self.sqlite_store and self.sqlite_store.available:
            hashes = self.sqlite_store.embedding_hashes(provider_key)
            for memory in self.memories:
                if memory.get("is_archived"):
                    continue
                memory_id = str(memory.get("id") or "")
                if memory_id not in hashes:
                    missing_count += 1
                elif hashes[memory_id] != self._embedding_content_hash(memory):
                    stale_count += 1
        return {
            "enabled": configured,
            "ready": bool(configured and provider_status.get("ready") and index_status.get("available")),
            "provider": provider_status,
            "index": index_status,
            "missing_count": missing_count,
            "stale_count": stale_count,
            "last_error": self.embedding_last_error or provider_status.get("last_error", ""),
        }

    def build_embedding_index(self, force=False, limit=None):
        if not self.settings.get("embedding_enabled", False):
            return {"status": "disabled", "indexed_count": 0, "remaining_count": 0}
        if not self.sqlite_store or not self.sqlite_store.available:
            return {"status": "sqlite_unavailable", "indexed_count": 0, "error": "SQLite is required for embeddings."}
        if not self.embedding_provider:
            init_result = self.initialize_embedding_provider()
            if not self.embedding_provider:
                return {"status": "provider_unavailable", "indexed_count": 0, **init_result}
        provider_status = self.embedding_provider.status()
        if not provider_status.get("ready"):
            return {"status": "provider_unavailable", "indexed_count": 0, **provider_status}
        provider_key = self.embedding_provider.key
        existing_hashes = self.sqlite_store.embedding_hashes(provider_key)
        pending = []
        for memory in self.memories:
            if memory.get("is_archived") or not memory.get("id"):
                continue
            content_hash = self._embedding_content_hash(memory)
            if force or existing_hashes.get(str(memory.get("id"))) != content_hash:
                pending.append((memory, content_hash))
        try:
            requested_limit = int(
                limit if limit is not None else self.settings.get("embedding_index_batch_limit", 500)
            )
            requested_limit = max(1, min(1000000, requested_limit))
        except (TypeError, ValueError):
            requested_limit = 500
        batch_targets = pending[:requested_limit]
        try:
            batch_size = max(1, min(256, int(self.settings.get("embedding_batch_size", 8))))
        except (TypeError, ValueError):
            batch_size = 8
        indexed_count = 0
        dimensions = set()
        try:
            for start in range(0, len(batch_targets), batch_size):
                batch = batch_targets[start:start + batch_size]
                vectors = self.embedding_provider.embed([
                    self._embedding_text(memory) for memory, _ in batch
                ])
                if len(vectors) != len(batch):
                    raise EmbeddingProviderError("Embedding provider returned an unexpected batch size.")
                entries = []
                for (memory, content_hash), vector in zip(batch, vectors):
                    dimensions.add(len(vector))
                    entries.append({
                        "memory_id": str(memory.get("id")),
                        "content_hash": content_hash,
                        "vector": vector,
                    })
                result = self.sqlite_store.upsert_embeddings(provider_key, entries)
                if result.get("status") != "success":
                    raise EmbeddingProviderError(result.get("error") or "Unable to save embedding vectors.")
                indexed_count += result.get("count", 0)
            self.embedding_last_error = ""
            return {
                "status": "success",
                "indexed_count": indexed_count,
                "remaining_count": max(0, len(pending) - indexed_count),
                "provider_key": provider_key,
                "dimensions": sorted(dimensions),
            }
        except Exception as exc:
            self.embedding_last_error = str(exc)
            return {
                "status": "error",
                "indexed_count": indexed_count,
                "remaining_count": max(0, len(pending) - indexed_count),
                "error": self.embedding_last_error,
            }

    def maybe_index_new_memories(self, memories):
        if not (
            self.settings.get("embedding_enabled", False)
            and self.settings.get("embedding_auto_index_new", False)
            and self.embedding_provider
        ):
            return {"status": "skipped", "indexed_count": 0}
        provider_status = self.embedding_provider.status()
        if not provider_status.get("ready") or not self.sqlite_store or not self.sqlite_store.available:
            return {"status": "provider_unavailable", "indexed_count": 0}
        provider_key = self.embedding_provider.key
        existing_hashes = self.sqlite_store.embedding_hashes(provider_key)
        targets = []
        for memory in memories or []:
            if not isinstance(memory, dict) or memory.get("is_archived") or not memory.get("id"):
                continue
            content_hash = self._embedding_content_hash(memory)
            if existing_hashes.get(str(memory.get("id"))) != content_hash:
                targets.append((memory, content_hash))
        if not targets:
            return {"status": "success", "indexed_count": 0}
        try:
            vectors = self.embedding_provider.embed([
                self._embedding_text(memory) for memory, _ in targets
            ])
            entries = [
                {
                    "memory_id": str(memory.get("id")),
                    "content_hash": content_hash,
                    "vector": vector,
                }
                for (memory, content_hash), vector in zip(targets, vectors)
            ]
            result = self.sqlite_store.upsert_embeddings(provider_key, entries)
            return {
                "status": result.get("status", "error"),
                "indexed_count": result.get("count", 0),
                "error": result.get("error", ""),
            }
        except Exception as exc:
            self.embedding_last_error = str(exc)
            return {"status": "error", "indexed_count": 0, "error": self.embedding_last_error}

    def import_legacy_memories(self):
        """Copy legacy memory records into the active store without changing source files."""
        existing_refs = {
            m.get("legacy_ref") for m in self.memories if m.get("legacy_ref")
        }
        existing_contents = {
            re.sub(r"\s+", " ", str(m.get("content", ""))).strip().lower()
            for m in self.memories
        }
        changed = False

        for filename in ("long_term_memory.json", "rag_memory.json"):
            path = os.path.join(self.base_dir, filename)
            if not os.path.exists(path):
                continue
            try:
                with codecs.open(path, "r", "utf-8") as f:
                    records = json.load(f)
            except Exception as e:
                print(f"Error importing {filename}: {e}")
                continue

            if not isinstance(records, list):
                continue

            for record in records:
                if not isinstance(record, dict) or not record.get("content"):
                    continue
                normalized_content = re.sub(
                    r"\s+", " ", str(record["content"])
                ).strip().lower()
                if normalized_content in existing_contents:
                    continue
                raw_id = str(record.get("id") or hashlib.sha256(
                    record["content"].encode("utf-8")
                ).hexdigest()[:16])
                legacy_ref = f"{filename}:{raw_id}"
                if legacy_ref in existing_refs:
                    continue

                timestamp = record.get("timestamp") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                date = str(timestamp)[:10]
                title = record.get("title") or f"Imported memory · {date}"
                stable_id = hashlib.sha256(legacy_ref.encode("utf-8")).hexdigest()[:20]
                self.memories.append({
                    "id": f"legacy-{stable_id}",
                    "creation_time": timestamp,
                    "date": date,
                    "title": title,
                    "content": record["content"],
                    "source": f"legacy:{record.get('source', filename)}",
                    "is_locked": bool(record.get("is_locked", False)),
                    "is_important": bool(record.get("is_important", False)),
                    "legacy_ref": legacy_ref
                })
                existing_refs.add(legacy_ref)
                existing_contents.add(normalized_content)
                changed = True

        if changed:
            self.save_memories()

    def _backup_existing_file(self, path, label, force=False, min_interval_seconds=300):
        if not os.path.exists(path):
            return None
        try:
            now = datetime.now()
            if not force and self._last_memory_backup_at:
                elapsed = (now - self._last_memory_backup_at).total_seconds()
                if elapsed < min_interval_seconds:
                    return None
            os.makedirs(self.backup_dir, exist_ok=True)
            stamp = now.strftime("%Y%m%d-%H%M%S-%f")
            backup_path = os.path.join(self.backup_dir, f"{label}.{stamp}.json")
            shutil.copy2(path, backup_path)
            self._last_memory_backup_at = now
            self._prune_backups(label, keep=40)
            return backup_path
        except Exception as e:
            print(f"Error backing up {path}: {e}")
            return None

    def _prune_backups(self, label, keep=40):
        try:
            if not os.path.isdir(self.backup_dir):
                return
            prefix = f"{label}."
            files = [
                os.path.join(self.backup_dir, name)
                for name in os.listdir(self.backup_dir)
                if name.startswith(prefix) and name.endswith(".json")
            ]
            files.sort(key=lambda p: os.path.getmtime(p), reverse=True)
            for old_path in files[keep:]:
                try:
                    os.remove(old_path)
                except OSError:
                    pass
        except Exception as e:
            print(f"Error pruning memory backups: {e}")

    def _atomic_write_json(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        text = json.dumps(data, ensure_ascii=False, indent=4)
        json.loads(text)
        tmp_path = f"{path}.tmp-{os.getpid()}-{uuid.uuid4().hex}"
        try:
            with codecs.open(tmp_path, "w", "utf-8") as f:
                f.write(text)
                f.write("\n")
                try:
                    f.flush()
                    os.fsync(f.fileno())
                except OSError:
                    pass
            os.replace(tmp_path, path)
        finally:
            if os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass

    def _load_json_file(self, path):
        with codecs.open(path, "r", "utf-8-sig") as f:
            return json.load(f)

    def _load_latest_valid_backup(self):
        if not os.path.isdir(self.backup_dir):
            return None
        backup_paths = [
            os.path.join(self.backup_dir, name)
            for name in os.listdir(self.backup_dir)
            if name.startswith("knowledge.") and name.endswith(".json")
        ]
        backup_paths.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        for backup_path in backup_paths:
            try:
                data = self._load_json_file(backup_path)
                if isinstance(data, list):
                    print(f"Loaded KB from backup: {backup_path}")
                    return data
            except Exception:
                continue
        return None

    def load_memory_deletions(self):
        self.deleted_memory_ids = {}
        if not os.path.exists(self.deletions_path):
            return self.deleted_memory_ids
        try:
            loaded = self._load_json_file(self.deletions_path)
            deleted = loaded.get("deleted", {}) if isinstance(loaded, dict) else {}
            if isinstance(deleted, list):
                deleted = {str(memory_id): {"deleted_at": ""} for memory_id in deleted if memory_id}
            if isinstance(deleted, dict):
                self.deleted_memory_ids = {
                    str(memory_id): (details if isinstance(details, dict) else {"deleted_at": str(details or "")})
                    for memory_id, details in deleted.items()
                    if memory_id
                }
        except Exception as exc:
            print(f"Error loading memory deletion ledger: {exc}")
        return self.deleted_memory_ids

    def save_memory_deletions(self):
        try:
            self._atomic_write_json(self.deletions_path, {
                "schema_version": 1,
                "deleted": self.deleted_memory_ids,
            })
            return True
        except Exception as exc:
            print(f"Error saving memory deletion ledger: {exc}")
            return False

    def _record_memory_deletions(self, memory_ids, reason="user_delete"):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        previous = dict(self.deleted_memory_ids)
        for memory_id in memory_ids:
            memory_id = str(memory_id or "").strip()
            if memory_id:
                self.deleted_memory_ids[memory_id] = {"deleted_at": now, "reason": str(reason or "user_delete")}
        if self.save_memory_deletions():
            return previous
        self.deleted_memory_ids = previous
        return None

    def _restore_memory_deletion_ledger(self, previous):
        self.deleted_memory_ids = dict(previous or {})
        return self.save_memory_deletions()

    def revive_deleted_memory(self, memory_id):
        """Clear a tombstone only for an explicit user-driven recreation."""
        memory_id = str(memory_id or "").strip()
        if not memory_id:
            return False
        if memory_id not in self.deleted_memory_ids:
            if self.sqlite_store and self.sqlite_store.available:
                return self.sqlite_store.revive_memory(memory_id)
            return True
        previous = dict(self.deleted_memory_ids)
        self.deleted_memory_ids.pop(memory_id, None)
        if self.save_memory_deletions():
            if not self.sqlite_store or not self.sqlite_store.available:
                return True
            if self.sqlite_store.revive_memory(memory_id):
                return True
            self.deleted_memory_ids = previous
            self.save_memory_deletions()
            return False
        self.deleted_memory_ids = previous
        return False

    def load_memories(self):
        if os.path.exists(self.file_path):
            try:
                loaded = self._load_json_file(self.file_path)
                self.memories = loaded if isinstance(loaded, list) else []
            except Exception as e:
                print(f"Error loading KB: {e}")
                self._backup_existing_file(self.file_path, "knowledge.corrupt", force=True)
                self.memories = self._load_latest_valid_backup() or []
        else:
            self.memories = []
        if self.deleted_memory_ids:
            tombstones = set(self.deleted_memory_ids)
            self.memories = [
                memory for memory in self.memories
                if str(memory.get("id") or "") not in tombstones
            ]

    def save_memories(self, invalidate_vectors=True, sqlite_subset=None, graph_dirty=True, sync_memory_v4=True):
        try:
            self._backup_existing_file(self.file_path, "knowledge.before-save")
            self._atomic_write_json(self.file_path, self.memories)
            if self.sqlite_store and self.sqlite_store.available:
                sync_targets = self.memories if sqlite_subset is None else sqlite_subset
                sync_result = self.sqlite_store.upsert_memories(sync_targets)
                if sync_result.get("status") != "success":
                    print(f"SQLite memory sync fallback: {sync_result.get('error', 'unknown error')}")
            if sync_memory_v4 and self.settings.get("memory_v4_write_enabled", False):
                self._sync_memory_v4(sqlite_subset if sqlite_subset is not None else self.memories)
            if invalidate_vectors:
                self._vector_cache = {}
            self._access_metadata_dirty = False
            self._dirty_access_ids = set()
            self._last_access_flush_at = datetime.now()
            if graph_dirty and hasattr(self, "memory_graph"):
                self._memory_graph_dirty = True
            return True
        except Exception as e:
            print(f"Error saving KB: {e}")
            return False

    def initialize_memory_v4_store(self):
        """Create the V4 store only after its explicit write flag is enabled."""
        if not self.settings.get("memory_v4_write_enabled", False):
            self.memory_v4_store = None
            return {"status": "disabled", "available": False}
        try:
            from saya_app.services.memory_v4 import MemoryV4Store
            self.memory_v4_store = MemoryV4Store(os.path.join(self.base_dir, "memory4.sqlite3"))
            return {"status": "ready", "available": True, "path": self.memory_v4_store.path}
        except Exception as exc:
            self.memory_v4_store = None
            return {"status": "error", "available": False, "error": str(exc)}

    def _sync_memory_v4(self, memories):
        """Best-effort V4 dual write; legacy JSON remains the compatibility source."""
        if not self.memory_v4_store:
            result = self.initialize_memory_v4_store()
            if result.get("status") != "ready":
                return result
        try:
            from saya_app.services.memory_v4 import MemoryV4Migration
            migration = MemoryV4Migration(self.base_dir, self.memory_v4_store.path)
            changed = 0
            skipped = 0
            for memory in memories or []:
                if not isinstance(memory, dict):
                    continue
                item, _, _ = migration._memory_item(memory)
                if item is None:
                    continue
                result = self.memory_v4_store.upsert_memory(
                    item, migration._evidence_for(memory, item),
                    operation="dual_write", reason="legacy KnowledgeBase write", actor="system",
                )
                if result.get("status") == "unchanged":
                    skipped += 1
                else:
                    changed += 1
            for memory_id, details in self.deleted_memory_ids.items():
                self.memory_v4_store.tombstone_memory(
                    memory_id, reason=(details or {}).get("reason", "legacy_deleted"),
                    actor="system", purge=True, allow_missing=True,
                )
            return {"status": "success", "changed_count": changed, "skipped_count": skipped}
        except Exception as exc:
            print(f"V4 memory sync fallback: {exc}")
            return {"status": "error", "error": str(exc)}

    def get_memory_v4_status(self):
        write_enabled = bool(self.settings.get("memory_v4_write_enabled", False))
        read_enabled = bool(self.settings.get("memory_v4_read_enabled", False))
        store = self.memory_v4_store or (self._open_memory_v4_store_for_read() if read_enabled else None)
        if not store:
            return {
                "enabled": write_enabled or read_enabled,
                "write_enabled": write_enabled,
                "read_enabled": read_enabled,
                "available": False,
                "status": "disabled" if not (write_enabled or read_enabled) else "not_initialized",
            }
        status = store.integrity_check()
        status.update({
            "enabled": True, "write_enabled": write_enabled, "read_enabled": read_enabled,
            "available": bool(status.get("ok")), "status": "ready" if status.get("ok") else "error",
        })
        return status

    def preview_memory_v4_migration(self):
        from saya_app.services.memory_v4 import MemoryV4Migration
        return MemoryV4Migration(self.base_dir).preview()

    def apply_memory_v4_migration(self, report=None):
        from saya_app.services.memory_v4 import MemoryV4Migration
        return MemoryV4Migration(self.base_dir).apply(report)

    def export_memory_v4(self):
        """Export V4 without creating a database or enabling V4 main read."""
        v4_path = os.path.join(self.base_dir, "memory4.sqlite3")
        if not os.path.exists(v4_path):
            return {"status": "unavailable", "reason": "V4 store has not been created"}
        from saya_app.services.memory_v4 import MemoryV4Store
        return MemoryV4Store(v4_path).export_state()

    def _open_memory_v4_store_for_read(self):
        if self.memory_v4_store:
            return self.memory_v4_store
        v4_path = os.path.join(self.base_dir, "memory4.sqlite3")
        if not os.path.exists(v4_path):
            return None
        try:
            from saya_app.services.memory_v4 import MemoryV4Store
            self.memory_v4_store = MemoryV4Store(v4_path)
            return self.memory_v4_store
        except Exception as exc:
            print(f"V4 memory read fallback: {exc}")
            return None

    def query_memory_v4(
        self,
        user_query,
        *,
        depth="normal",
        scope_type=None,
        scope_id=None,
        turn_id=None,
        bypass_need_planner=False,
        **filters,
    ):
        """Query V4 only when read is enabled; shadow results never enter prompts."""
        read_enabled = bool(self.settings.get("memory_v4_read_enabled", False))
        shadow_enabled = bool(self.settings.get("memory_v4_shadow_read_enabled", True))
        store = self._open_memory_v4_store_for_read()
        if store is None or (not read_enabled and not shadow_enabled):
            return {"status": "disabled", "items": [], "candidates": []}
        from saya_app.services.memory_v4 import MemoryNeedPlanner, MemoryQuery, MemoryRetriever
        planner = MemoryNeedPlanner()
        query = (
            MemoryQuery(
                query=str(user_query or ""),
                depth=depth,
                scope_type=scope_type,
                scope_id=scope_id,
            )
            if bypass_need_planner
            else planner.plan(user_query, depth=depth, scope_type=scope_type, scope_id=scope_id)
        )
        query_data = query.__dict__.copy()
        query_data.update({key: value for key, value in filters.items() if key in query_data})
        result = MemoryRetriever(store).search(query_data, turn_id=turn_id)
        payload = result.to_dict()
        payload["status"] = "ready" if read_enabled else "shadow"
        if not read_enabled:
            # Shadow retrieval is diagnostic only; callers must opt into the
            # actual item list explicitly by enabling V4 main read.
            payload["shadow_items"] = payload.pop("items", [])
            payload["items"] = []
        return payload

    def acknowledge_memory_v4_usage(self, memory_ids, *, turn_id=None, used=True, feedback=None):
        if not self.settings.get("memory_v4_read_enabled", False):
            return {"status": "disabled", "receipts": []}
        store = self._open_memory_v4_store_for_read()
        if store is None:
            return {"status": "unavailable", "receipts": []}
        from saya_app.services.memory_v4 import MemoryRetriever
        receipts = MemoryRetriever(store).acknowledge_used(memory_ids, turn_id=turn_id, used=used, feedback=feedback)
        return {"status": "success", "receipts": receipts}

    def compile_memory_prompt_v4(self, user_request, *, memory_items=None, daily_ring=None, first_contact_today=False, **kwargs):
        """Compile an isolated V4 prompt; default callers still use legacy prompts."""
        from saya_app.services.memory_v4 import PromptCompiler
        return PromptCompiler().compile(
            user_request,
            memory_items=memory_items or [],
            daily_ring=daily_ring or [],
            first_contact_today=first_contact_today,
            **kwargs,
        ).to_dict()

    def build_memory_v4_context_with_audit(
        self,
        user_request,
        *,
        depth="normal",
        scope_type=None,
        scope_id=None,
        turn_id=None,
        token_budget=2400,
    ):
        """Build the V4 memory-only prompt block behind the main-read flag.

        The legacy chat builder owns persona, history, current-user text, and
        external context. This adapter contributes only sanitized V4 memory
        blocks, so enabling it cannot duplicate those system sections. When
        the flag or V4 database is unavailable, callers can keep the legacy
        context path unchanged.
        """
        if not self.settings.get("memory_v4_read_enabled", False):
            return {"status": "disabled", "context": "", "audit": {"status": "disabled"}}
        result = self.query_memory_v4(
            user_request,
            depth=depth,
            scope_type=scope_type,
            scope_id=scope_id,
            turn_id=turn_id,
        )
        if result.get("status") != "ready":
            return {
                "status": result.get("status", "unavailable"),
                "context": "",
                "audit": {"status": result.get("status", "unavailable")},
            }
        compiled = self.compile_memory_prompt_v4(
            user_request,
            memory_items=result.get("items") or [],
            token_budget=token_budget,
        )
        memory_blocks = [
            block for block in compiled.get("blocks", [])
            if isinstance(block, dict) and block.get("kind") == "memory"
        ]
        context = ""
        if memory_blocks:
            context = "[System: V4 Memory Context]\n" + "\n".join(
                str(block.get("content") or "") for block in memory_blocks
            ) + "\n\n"
        candidates = result.get("candidates") or []
        audit = {
            "version": "v4",
            "status": "ready",
            "mode": depth,
            "query": str(user_request or "")[:240],
            "selected_count": len(memory_blocks),
            "selected": [
                {
                    "id": str(block.get("id") or "")[len("memory:"):],
                    "kind": block.get("kind", "memory"),
                    "trigger_reason": block.get("trigger_reason", "retriever_selected_memory"),
                }
                for block in memory_blocks
            ],
            "candidate_count": len(candidates),
            "omitted_count": len(result.get("omitted") or []),
            "prompt_block_ids": [str(block.get("id") or "") for block in memory_blocks],
        }
        return {"status": "ready", "context": context, "audit": audit}

    def memory_v4_controls_summary(self, *, limit=100):
        """Return V4 user-control rows only when a read/write rollout is active."""
        if not (self.settings.get("memory_v4_read_enabled", False) or self.settings.get("memory_v4_write_enabled", False)):
            return {"status": "disabled", "memories": []}
        store = self._open_memory_v4_store_for_read()
        if store is None:
            return {"status": "unavailable", "memories": []}
        from saya_app.services.memory_v4 import MemoryControls
        return {"status": "ready", "memories": MemoryControls(store).summary(limit=max(1, min(500, int(limit))))}

    def memory_v4_control(self, operation, *, memory_id=None, updates=None, revision=None, turn_id=None, actor="user", enabled=None):
        """Apply one auditable V4 user control behind the write rollout flag."""
        operation = str(operation or "").strip().lower()
        read_operations = {"summary", "why_used"}
        mutation_operations = {"correct", "pin", "unpin", "do_not_mention", "forget", "restore"}
        if operation not in read_operations | mutation_operations:
            return {"status": "rejected", "reason": "unsupported_memory_control"}
        if operation in read_operations:
            allowed = bool(self.settings.get("memory_v4_read_enabled", False) or self.settings.get("memory_v4_write_enabled", False))
        else:
            allowed = bool(self.settings.get("memory_v4_write_enabled", False))
        if not allowed:
            return {"status": "disabled", "reason": "memory_v4_control_flag_disabled"}
        store = self._open_memory_v4_store_for_read()
        if store is None:
            return {"status": "unavailable", "reason": "memory4.sqlite3 is not available"}
        from saya_app.services.memory_v4 import MemoryControls
        controls = MemoryControls(store)
        try:
            if operation == "summary":
                return {"status": "ready", "memories": controls.summary(limit=max(1, min(500, int((updates or {}).get("limit", 100)))))}
            if operation == "why_used":
                return {"status": "ready", **controls.why_used(str(memory_id or ""), turn_id=turn_id)}
            if operation == "correct":
                result = controls.correct(str(memory_id or ""), updates or {}, actor=actor)
                self._mirror_v4_write_to_legacy({"committed": [{"memory": result.get("memory")}]} )
                return {**result, "status": "success"}
            if operation == "pin":
                result = controls.pin(str(memory_id or ""), actor=actor)
                self._mirror_v4_write_to_legacy({"committed": [{"memory": result.get("memory")}]} )
                return {**result, "status": "success"}
            if operation == "unpin":
                result = controls.unpin(str(memory_id or ""), actor=actor)
                self._mirror_v4_write_to_legacy({"committed": [{"memory": result.get("memory")}]} )
                return {**result, "status": "success"}
            if operation == "do_not_mention":
                result = controls.do_not_mention(str(memory_id or ""), bool(enabled), actor=actor)
                self._mirror_v4_write_to_legacy({"committed": [{"memory": result.get("memory")}]} )
                return {**result, "status": "success"}
            if operation == "forget":
                forgotten = controls.forget(str(memory_id or ""), actor=actor)
                if forgotten:
                    self._mirror_v4_write_to_legacy({"forgotten": [str(memory_id)]})
                return {"status": "success" if forgotten else "no_op", "memory_id": str(memory_id or "")}
            try:
                restore_revision = int(revision)
            except (TypeError, ValueError):
                return {"status": "rejected", "reason": "restore revision is required"}
            restored = controls.restore(str(memory_id or ""), restore_revision, actor=actor)
            self._mirror_v4_write_to_legacy({"committed": [{"memory": restored.get("memory")}]} )
            return {**restored, "status": "success"}
        except KeyError as exc:
            return {"status": "not_found", "memory_id": str(exc.args[0] if exc.args else memory_id or "")}
        except (TypeError, ValueError) as exc:
            return {"status": "rejected", "reason": str(exc)[:240]}

    def memory_v4_daily_state(self, action="conversation", *, day=None, tasks=None, timezone="Asia/Shanghai"):
        """Expose the D0-D3 Daily State ring behind its independent flag."""
        if not self.settings.get("daily_state_v2_enabled", False):
            return {"status": "disabled", "reason": "daily_state_v2_enabled is false", "ring": []}
        path = os.path.join(self.base_dir, "memory4.sqlite3")
        try:
            from saya_app.services.memory_v4 import DayStateManager, MemoryV4Store
            store = self.memory_v4_store or (MemoryV4Store(path) if os.path.exists(path) else MemoryV4Store(path))
            self.memory_v4_store = store
            manager = DayStateManager(store, timezone=timezone)
            action = str(action or "conversation").strip().lower()
            current_day = str(day or datetime.now().strftime("%Y-%m-%d"))[:10]
            first_contact = None
            if action == "ring":
                state = manager.ensure(current_day)
            elif action == "page_load":
                state = manager.record_page_load(current_day)
            elif action == "backend_start":
                state = manager.record_backend_start(current_day)
            elif action == "first_contact":
                first_contact = manager.first_contact_today(current_day)
                state = manager.ensure(current_day)
            elif action == "rollover":
                return manager.rollover(current_day, tasks=tasks or [])
            else:
                first_contact = manager.first_contact_today(current_day)
                state = manager.record_conversation(current_day)
            return {
                "status": "ready", "action": action, "date": current_day,
                "first_contact_today": bool((first_contact or {}).get("first_contact_today", False)),
                "state": state.to_dict(), "ring": manager.ring(current_day),
            }
        except Exception as exc:
            return {"status": "error", "reason": str(exc)[:240], "ring": []}

    def _memory_v4_capability_registry(self, web_tools=None, allow_legacy_memory=False):
        """Build the V4 registry without exposing private handlers in public metadata."""
        from saya_app.services.memory_v4 import CapabilityRegistry, register_memory_capabilities

        memory_handler = None
        memory_read_handler = None
        daily_handler = None
        time_query_handler = None
        weather_query_handler = None
        web_search_handler = None
        web_read_handler = None
        connector_read_feed_handler = None
        connector_mark_read_handler = None
        if self.settings.get("memory_v4_read_enabled", False) or allow_legacy_memory:
            def memory_handler(arguments):
                if self.settings.get("memory_v4_read_enabled", False):
                    payload = self.query_memory_v4(
                        arguments.get("query", ""),
                        depth=arguments.get("depth", "normal"),
                        kinds=arguments.get("kinds") if isinstance(arguments.get("kinds"), list) else [],
                        scope_type=arguments.get("scope_type"),
                        scope_id=arguments.get("scope_id"),
                        time_from=arguments.get("time_from"),
                        time_to=arguments.get("time_to"),
                        max_candidates=arguments.get("max_candidates", 40),
                        max_items=arguments.get("max_items", 5),
                        token_budget=arguments.get("token_budget", 1200),
                        bypass_need_planner=True,
                    )
                else:
                    payload = {"status": "disabled"}
                if payload.get("status") != "ready" and allow_legacy_memory:
                    legacy_items = self.search_memories(
                        arguments.get("query", ""),
                        limit=max(1, min(20, int(arguments.get("max_items", 5) or 5))),
                    )
                    payload = {
                        "status": "ready",
                        "items": [
                            {
                                "id": str(item.get("id") or ""),
                                "canonical_text": str(self._memory_text(item) or "")[:240],
                                "kind": str(item.get("memory_layer") or item.get("category") or "semantic"),
                                "scope_type": "project" if item.get("project_id") else "global",
                            }
                            for item in legacy_items if isinstance(item, dict) and item.get("id")
                        ],
                        "candidates": [],
                    }
                payload["items"] = [
                    {
                        "id": item.get("id"), "canonical_text": item.get("canonical_text"),
                        "kind": item.get("kind"), "scope_type": item.get("scope_type"),
                    }
                    for item in payload.get("items", []) if isinstance(item, dict)
                ]
                return payload

            def memory_read_handler(arguments):
                memory_ids = arguments.get("memory_ids", arguments.get("ids", []))
                if isinstance(memory_ids, str):
                    memory_ids = [memory_ids]
                memory_ids = [str(item).strip() for item in (memory_ids or []) if str(item).strip()][:10]
                store = self._open_memory_v4_store_for_read()
                if store is None and allow_legacy_memory:
                    wanted = set(memory_ids)
                    legacy_items = [
                        item for item in self.get_memories(include_archived=False)
                        if str(item.get("id") or "") in wanted
                    ]
                    return {
                        "status": "ready",
                        "items": [
                            {
                                "id": str(item.get("id")),
                                "canonical_text": str(self._memory_text(item) or ""),
                                "kind": str(item.get("memory_layer") or item.get("category") or "semantic"),
                                "scope_type": "project" if item.get("project_id") else "global",
                            }
                            for item in legacy_items
                        ],
                    }
                if store is None:
                    return {"status": "unavailable", "items": []}
                from saya_app.services.memory_v4 import MemoryRetriever
                items = MemoryRetriever(store).fetch_full(memory_ids)
                return {
                    "status": "ready",
                    "items": [
                        {
                            "id": item.id,
                            "canonical_text": item.canonical_text,
                            "kind": item.kind,
                            "scope_type": item.scope_type,
                            "scope_id": item.scope_id,
                            "valid_from": item.valid_from,
                            "valid_to": item.valid_to,
                        }
                        for item in items
                    ],
                }
        if self.settings.get("daily_state_v2_enabled", False):
            def daily_handler(arguments):
                return self.memory_v4_daily_state(
                    action="ring", day=arguments.get("day"), timezone=arguments.get("timezone", "Asia/Shanghai")
                )
        configured_time_query = getattr(self, "memory_v4_time_query", None)
        if callable(configured_time_query):
            def time_query_handler(arguments):
                return configured_time_query(arguments)
        configured_weather_query = getattr(self, "memory_v4_weather_query", None)
        if callable(configured_weather_query):
            def weather_query_handler(arguments):
                return configured_weather_query(arguments)
        explicit_web_tools = web_tools is not None
        configured_web_tools = web_tools if explicit_web_tools else getattr(self, "memory_v4_web_tools", None)
        # Passing a request-scoped WebToolsV2 instance is an explicit opt-in;
        # it must not rely on mutable global settings or a shared API key.
        if (self.settings.get("web_tools_v2_enabled", False) or explicit_web_tools) and configured_web_tools is not None:
            search_method = getattr(configured_web_tools, "search", None)
            read_method = getattr(configured_web_tools, "read", None)
            if callable(search_method):
                def web_search_handler(arguments):
                    return search_method(
                        arguments.get("query", ""),
                        freshness=arguments.get("freshness", ""),
                        domains=arguments.get("domains") if isinstance(arguments.get("domains"), list) else None,
                        max_results=arguments.get("max_results", 5),
                        private_context=arguments.get("private_context", ""),
                    )
            if callable(read_method):
                def web_read_handler(arguments):
                    urls = arguments.get("source_ids", arguments.get("urls", arguments.get("url", [])))
                    return read_method(urls, max_chars_each=arguments.get("max_chars_each"))
        configured_connector = getattr(self, "memory_v4_connector", None)
        if configured_connector is None and (
            self.settings.get("connector_discord_enabled", False)
            or self.settings.get("connector_x_enabled", False)
        ):
            self.configure_memory_v4_connectors_from_env()
            configured_connector = getattr(self, "memory_v4_connector", None)
        connector_source = str(getattr(configured_connector, "source", "") or "").strip().lower()
        connector_flag = f"connector_{connector_source}_enabled" if connector_source else ""
        if (
            configured_connector is not None
            and callable(getattr(configured_connector, "poll", None))
            and connector_flag
            and self.settings.get(connector_flag, False)
        ):
            def connector_read_feed_handler(arguments):
                store = self._open_memory_v4_store_for_read()
                if store is None:
                    return {"status": "unavailable", "reason": "memory4.sqlite3 is not available", "items": []}
                from saya_app.services.memory_v4 import AttentionInbox
                inbox = AttentionInbox(store, policy=getattr(configured_connector, "policy", None))
                polled = inbox.poll(configured_connector)
                polled["items"] = inbox.list_attention(limit=max(1, min(20, int(arguments.get("limit", 5)))))
                return polled

            def connector_mark_read_handler(arguments):
                store = self._open_memory_v4_store_for_read()
                if store is None:
                    return {"status": "unavailable", "reason": "memory4.sqlite3 is not available", "marked": 0}
                from saya_app.services.memory_v4 import AttentionInbox
                inbox = AttentionInbox(store, policy=getattr(configured_connector, "policy", None))
                event_ids = arguments.get("event_ids")
                if isinstance(event_ids, str):
                    event_ids = [event_ids]
                return {"status": "success", "marked": inbox.mark_read(event_ids or [])}
        registry = register_memory_capabilities(
            CapabilityRegistry(), memory_query=memory_handler, memory_read=memory_read_handler, daily_query=daily_handler,
            time_query=time_query_handler, weather_query=weather_query_handler,
            web_search=web_search_handler, web_read=web_read_handler,
            connector_read_feed=connector_read_feed_handler,
            connector_mark_read=connector_mark_read_handler,
        )
        return registry

    def memory_v4_capabilities(self):
        """Return the safe, discoverable read capability manifest for V4."""
        return {"status": "ready", "capabilities": self._memory_v4_capability_registry().list_public()}

    def memory_v4_agent_policy(
        self,
        policy_options=None,
        model_step=None,
        web_tools=None,
        allow_legacy_memory=False,
        request_opt_in=False,
    ):
        """Build one server-owned policy for all HTTP Agent Loop entrypoints.

        Private memory/daily tools stay blocked for external model providers
        unless the host explicitly opts in through an environment setting.
        A server-owned LocalLlamaAgentStep may use them by default because its
        inference remains local. Request JSON may narrow this policy, but
        cannot widen it.
        """
        from saya_app.services.memory_v4 import CapabilityPolicy, LocalLlamaAgentStep
        options = policy_options if isinstance(policy_options, dict) else {}
        selected_step = model_step or getattr(self, "memory_v4_model_step", None)
        local_agent = isinstance(selected_step, LocalLlamaAgentStep)
        loopback_api = False
        if not local_agent and selected_step is not None:
            try:
                loopback_api = urlparse(str(getattr(selected_step, "api_url", ""))).hostname in {"localhost", "127.0.0.1", "::1"}
            except Exception:
                loopback_api = False
        private_opt_in = bool(self.settings.get("memory_v4_agent_allow_private", False))
        private_opt_in = private_opt_in or os.environ.get("SAYA_MEMORY_V4_AGENT_ALLOW_PRIVATE", "").strip().lower() in {"1", "true", "yes", "on"}
        configured_names = options.get("allowed_names") if isinstance(options.get("allowed_names"), list) else None
        if configured_names is None:
            configured_names = [
                item["name"] for item in self._memory_v4_capability_registry(
                    web_tools=web_tools,
                    allow_legacy_memory=allow_legacy_memory,
                ).list_public()
                if item.get("availability") == "available"
            ]
        if not local_agent and not loopback_api and not private_opt_in:
            configured_names = [
                name for name in configured_names
                if name not in {"memory.query", "memory.read", "daily.query", "time.query"}
            ]
        return CapabilityPolicy(
            mode=options.get("mode", "allow"),
            max_steps=options.get("max_steps", 6),
            max_total_cost=options.get("max_total_cost", 1.0),
            max_total_latency_ms=options.get("max_total_latency_ms", 30000),
            allowed_names=configured_names,
            denied_names=options.get("denied_names") if isinstance(options.get("denied_names"), list) else set(),
        )

    def memory_v4_events(self, turn_id, *, after_sequence=-1, limit=100):
        """Return the public event page used by SSE reconnect/resume clients."""
        if not str(turn_id or "").strip():
            return {"status": "invalid", "reason": "turn_id is required", "events": [], "next_sequence": -1}
        store = self._open_memory_v4_store_for_read()
        if store is None:
            return {"status": "unavailable", "reason": "memory4.sqlite3 is not available", "events": [], "next_sequence": -1}
        return store.list_events_after(str(turn_id), after_sequence=after_sequence, limit=limit)

    def configure_memory_v4_connectors_from_env(self):
        """Load optional read-only provider adapters without polling them."""
        from saya_app.services.memory_v4 import DiscordReadOnlyConnector, XReadOnlyConnector
        configured = []
        if self.settings.get("connector_discord_enabled", False):
            connector = DiscordReadOnlyConnector.from_env()
            if connector is not None:
                self.memory_v4_connector = connector
                configured.append("discord")
        if self.settings.get("connector_x_enabled", False):
            connector = XReadOnlyConnector.from_env()
            if connector is not None:
                if not getattr(self, "memory_v4_connector", None):
                    self.memory_v4_connector = connector
                configured.append("x")
        return {"status": "ready", "configured": configured}

    def configure_memory_v4_model_from_env(self):
        """Register an optional server-owned OpenAI-compatible model step.

        No request can override these values. The adapter is instantiated only
        when all three dedicated environment variables are present.
        """
        api_url = str(os.environ.get("SAYA_MEMORY_V4_AGENT_API_URL", "")).strip()
        api_key = str(os.environ.get("SAYA_MEMORY_V4_AGENT_API_KEY", "")).strip()
        model = str(os.environ.get("SAYA_MEMORY_V4_AGENT_MODEL", "")).strip()
        if not (api_url and api_key and model):
            local_model = str(os.environ.get("SAYA_MEMORY_V4_AGENT_LOCAL_MODEL", "")).strip()
            if not local_model:
                return {"status": "unconfigured"}
            try:
                from saya_app.model_runtime import get_model
                from saya_app.services.memory_v4 import AgentModelSettings, LocalLlamaAgentStep
                self.memory_v4_model_step = LocalLlamaAgentStep(
                    get_model(local_model), AgentModelSettings(local_model),
                )
            except Exception as exc:
                return {"status": "error", "provider": "local", "model": local_model, "reason": str(exc)[:240]}
            return {"status": "ready", "provider": "local", "model": local_model}
        from saya_app.services.memory_v4 import AgentModelSettings, OpenAICompatibleAgentStep
        self.memory_v4_model_step = OpenAICompatibleAgentStep(api_url, api_key, AgentModelSettings(model))
        return {"status": "ready", "provider": "openai_compatible", "model": model}

    def memory_v4_agent_is_configured(self):
        """Return whether the host can provide an Agent model without loading it."""
        if callable(getattr(self, "memory_v4_model_step", None)):
            return True
        api_configured = all(
            str(os.environ.get(name, "")).strip()
            for name in (
                "SAYA_MEMORY_V4_AGENT_API_URL",
                "SAYA_MEMORY_V4_AGENT_API_KEY",
                "SAYA_MEMORY_V4_AGENT_MODEL",
            )
        )
        return api_configured or bool(str(os.environ.get("SAYA_MEMORY_V4_AGENT_LOCAL_MODEL", "")).strip())

    def run_memory_v4_agent_loop(
        self,
        turn_id,
        model_step,
        *,
        initial_context=None,
        policy=None,
        cancel_event=None,
        event_sink=None,
        web_tools=None,
        allow_legacy_memory=False,
        request_opt_in=False,
    ):
        """Run the bounded Agent Loop through an application-owned model callback.

        This is an internal integration seam, not an HTTP code-execution
        surface. The model callback must be registered by the host application;
        the public API can only discover and inspect capability metadata.
        """
        # Internal HTTP endpoints remain feature-flag gated.  The chat
        # endpoint may pass request_opt_in=True only after the user explicitly
        # enabled Agent Tools for that turn.
        if not self.settings.get("agent_loop_enabled", False) and not request_opt_in:
            return {"status": "disabled", "reason": "agent_loop_enabled is false"}
        if not callable(model_step):
            return {"status": "unconfigured", "reason": "model_step is not configured"}
        store = self._open_memory_v4_store_for_read()
        from saya_app.services.memory_v4 import AgentLoop
        resolved_turn_id = str(turn_id or "memory-v4-turn")
        active_cancel_event = cancel_event or threading.Event()
        cancel_registry = getattr(self, "_memory_v4_cancel_events", None)
        if cancel_registry is None:
            cancel_registry = self._memory_v4_cancel_events = {}
        cancel_registry[resolved_turn_id] = active_cancel_event
        try:
            result = AgentLoop(
                self._memory_v4_capability_registry(
                    web_tools=web_tools,
                    allow_legacy_memory=allow_legacy_memory,
                ),
                store=store,
            ).run(
                resolved_turn_id, model_step,
                initial_context=initial_context,
                policy=policy,
                cancel_event=active_cancel_event,
                event_sink=event_sink,
            )
        finally:
            cancel_registry.pop(resolved_turn_id, None)
        return result.to_dict()

    def cancel_memory_v4_agent(self, turn_id):
        event = getattr(self, "_memory_v4_cancel_events", {}).get(str(turn_id or ""))
        if event is None:
            return {"status": "not_found", "turn_id": str(turn_id or "")}
        event.set()
        return {"status": "cancel_requested", "turn_id": str(turn_id)}

    def run_memory_v4_agent_once(
        self,
        turn_id,
        *,
        initial_context=None,
        policy_options=None,
        model_step=None,
        web_tools=None,
        cancel_event=None,
        event_sink=None,
        allow_legacy_memory=False,
        request_opt_in=False,
    ):
        """HTTP-safe adapter using only a model step registered by the host."""
        selected_model_step = model_step if callable(model_step) else getattr(self, "memory_v4_model_step", None)
        if not callable(selected_model_step):
            self.configure_memory_v4_model_from_env()
            selected_model_step = getattr(self, "memory_v4_model_step", None)
        if not callable(selected_model_step):
            return {"status": "unconfigured", "reason": "memory_v4_model_step is not configured"}
        policy = self.memory_v4_agent_policy(
            policy_options,
            model_step=selected_model_step,
            web_tools=web_tools,
            allow_legacy_memory=allow_legacy_memory,
        )
        return self.run_memory_v4_agent_loop(
            turn_id,
            selected_model_step,
            initial_context=initial_context,
            policy=policy,
            web_tools=web_tools,
            cancel_event=cancel_event,
            event_sink=event_sink,
            allow_legacy_memory=allow_legacy_memory,
            request_opt_in=request_opt_in,
        )

    def run_memory_v4_synthesis_once(self, producer=None, *, limit=10, actor="background_synthesis"):
        """Run one bounded synthesis pass through the safe scheduler seam.

        The application may register a producer callable on the KnowledgeBase
        instance.  HTTP callers can trigger only that configured producer; they
        cannot submit executable synthesis logic or bypass evidence validation.
        """
        if not self.settings.get("memory_background_synthesis_enabled", False):
            return {"status": "disabled", "processed": 0, "applied": 0, "rejected": 0}
        if not self.settings.get("memory_v4_write_enabled", False):
            return {
                "status": "disabled", "reason": "memory_v4_write_enabled is false",
                "processed": 0, "applied": 0, "rejected": 0,
            }
        result = self.initialize_memory_v4_store()
        if result.get("status") != "ready" or not self.memory_v4_store:
            return {**result, "processed": 0, "applied": 0, "rejected": 0}
        configured_producer = producer if producer is not None else getattr(self, "memory_v4_synthesis_producer", None)
        from saya_app.services.memory_v4 import BackgroundSynthesisScheduler
        scheduler = BackgroundSynthesisScheduler(
            self.memory_v4_store, configured_producer, enabled=True,
        )
        return scheduler.run_once(limit=limit, actor=actor)

    def schedule_memory_v4_synthesis(self, *, limit=10, actor="background_synthesis"):
        """Schedule one bounded synthesis pass without blocking the request."""
        if not self.settings.get("memory_background_synthesis_enabled", False):
            return {"status": "disabled", "processed": 0, "applied": 0, "rejected": 0}
        if not self.settings.get("memory_v4_write_enabled", False):
            return {"status": "disabled", "reason": "memory_v4_write_enabled is false"}
        if not callable(getattr(self, "memory_v4_synthesis_producer", None)):
            return {"status": "unconfigured", "reason": "memory_v4_synthesis_producer is not configured"}
        thread = getattr(self, "_memory_v4_synthesis_thread", None)
        if thread is not None and thread.is_alive():
            return {"status": "already_running"}

        def worker():
            try:
                self._memory_v4_synthesis_last_result = self.run_memory_v4_synthesis_once(limit=limit, actor=actor)
            except Exception as exc:
                self._memory_v4_synthesis_last_result = {"status": "error", "reason": str(exc)[:240]}

        thread = threading.Thread(target=worker, name="saya-memory-v4-synthesis", daemon=True)
        self._memory_v4_synthesis_thread = thread
        thread.start()
        return {"status": "scheduled", "limit": max(1, min(50, int(limit))), "actor": str(actor or "background_synthesis")[:80]}

    def memory_v4_synthesis_status(self):
        thread = getattr(self, "_memory_v4_synthesis_thread", None)
        return {
            "status": "running" if thread is not None and thread.is_alive() else "idle",
            "last_result": getattr(self, "_memory_v4_synthesis_last_result", None),
        }

    def process_memory_write_v4(
        self,
        user_input,
        *,
        context=None,
        action=None,
        intent=None,
        turn_id=None,
        message_id=None,
        sequence=0,
        actor="user",
        mirror_legacy=True,
    ):
        """Run the V4 write pipeline behind an explicit rollout flag.

        The legacy JSON store remains the compatibility projection during the
        rollout.  V4 is authoritative for this call, while the projection is
        written with the same logical id and without triggering a second V4
        conversion pass.
        """
        if not self.settings.get("memory_v4_write_enabled", False):
            return {"status": "disabled", "reason": "memory_v4_write_enabled is false"}
        result = self.initialize_memory_v4_store()
        if result.get("status") != "ready" or not self.memory_v4_store:
            return result
        try:
            from saya_app.services.memory_v4 import WritePipeline
            pipeline = WritePipeline(self.memory_v4_store)
            response = pipeline.process(
                user_input,
                context=context,
                action=action,
                intent=intent,
                turn_id=turn_id,
                message_id=message_id,
                sequence=sequence,
                actor=actor,
            )
            if mirror_legacy:
                self._mirror_v4_write_to_legacy(response)
            if response.get("status") == "success":
                self.schedule_memory_v4_synthesis(limit=10, actor="post_write_background_synthesis")
            return response
        except Exception as exc:
            print(f"V4 write pipeline fallback: {exc}")
            return {"status": "error", "error": str(exc)}

    def _mirror_v4_write_to_legacy(self, response):
        """Keep the old UI/API projection synchronized for V4 commits."""
        committed = response.get("committed") if isinstance(response, dict) else []
        changed = False
        for entry in committed or []:
            memory = entry.get("memory") if isinstance(entry, dict) else None
            if not isinstance(memory, dict) or not memory.get("id"):
                continue
            memory_id = str(memory["id"])
            legacy = next((item for item in self.memories if str(item.get("id")) == memory_id), None)
            now = datetime.now()
            if legacy is None:
                legacy = {
                    "id": memory_id,
                    "creation_time": str(memory.get("created_at") or now.strftime("%Y-%m-%d %H:%M:%S")),
                    "date": str(memory.get("valid_from") or now.strftime("%Y-%m-%d"))[:10],
                    "title": str(memory.get("predicate") or "Memory")[:120],
                    "content": memory.get("canonical_text", ""),
                    "source": "write_pipeline",
                    "is_locked": bool(memory.get("locked")),
                    "is_important": bool(memory.get("pinned")),
                }
                self.memories.append(legacy)
            else:
                legacy.update({
                    "content": memory.get("canonical_text", legacy.get("content", "")),
                    "is_locked": bool(memory.get("locked")),
                    "is_important": bool(memory.get("pinned")),
                    "last_updated": memory.get("updated_at"),
                })
            legacy.update({
                "source": "write_pipeline",
                "category": memory.get("kind", legacy.get("category", "general")),
                "confidence": memory.get("confidence", legacy.get("confidence", 0.0)),
                "importance_score": memory.get("salience", legacy.get("importance_score", 0.0)),
                "schema_version": 3,
                "v4_revision": memory.get("revision"),
                "source_detail": {"type": "memory_v4_write_pipeline", "memory_id": memory_id},
            })
            changed = True
        forgotten = response.get("forgotten") if isinstance(response, dict) else []
        if forgotten:
            self._record_memory_deletions(forgotten, reason="explicit_forget")
            deleted = set(str(item) for item in forgotten)
            self.memories = [item for item in self.memories if str(item.get("id")) not in deleted]
            changed = True
        if changed:
            self.save_memories(graph_dirty=False, sync_memory_v4=False)

    def load_memory_candidates(self):
        if os.path.exists(self.candidates_path):
            try:
                loaded = self._load_json_file(self.candidates_path)
                self.memory_candidates = loaded if isinstance(loaded, list) else []
            except Exception as e:
                print(f"Error loading memory candidates: {e}")
                self.memory_candidates = []
        else:
            self.memory_candidates = []
        self.memory_candidates = [
            self._normalize_memory_candidate(candidate)
            for candidate in self.memory_candidates
            if isinstance(candidate, dict)
        ]
        self.auto_adopt_pending_candidates()

    def auto_adopt_pending_candidates(self, now=None):
        """Adopt aged, confident pending candidates into long-term memory.

        Disabled by default (V4 plan section 3.3: age must never promote a
        candidate on its own). When explicitly enabled, aged candidates above
        the confidence bar adopt themselves through the SAME confirm path
        (so content dedupe and capacity checks still apply); low-confidence
        or young ones stay pending for review.
        Fail-silent: adoption must never block startup.
        """
        try:
            if not self.settings.get("candidate_auto_adopt_enabled", False):
                return {"adopted": 0, "reason": "disabled"}
            try:
                ttl_days = max(0.0, float(self.settings.get("candidate_auto_adopt_days", 3)))
            except (TypeError, ValueError):
                ttl_days = 3.0
            try:
                min_confidence = float(self.settings.get("candidate_auto_adopt_min_confidence", 0.8))
            except (TypeError, ValueError):
                min_confidence = 0.8
            from datetime import datetime as _dt

            now = now or _dt.now()
            adopted = 0
            for candidate in list(self.memory_candidates):
                if candidate.get("status") != "pending":
                    continue
                try:
                    created = _dt.strptime(
                        str(candidate.get("creation_time") or ""), "%Y-%m-%d %H:%M:%S")
                except ValueError:
                    continue
                age_days = (now - created).total_seconds() / 86400.0
                try:
                    confidence = float(candidate.get("confidence") or 0.0)
                except (TypeError, ValueError):
                    confidence = 0.0
                if age_days >= ttl_days and confidence >= min_confidence:
                    result = self.confirm_memory_candidate(str(candidate.get("id")))
                    if result and result.get("status") == "success":
                        adopted += 1
            if adopted:
                print(f"[memory] auto-adopted {adopted} aged candidates into long-term memory")
            return {"adopted": adopted}
        except Exception as exc:
            print(f"[memory] auto-adopt failed (continuing): {exc}")
            return {"adopted": 0, "error": str(exc)}

    def save_memory_candidates(self):
        try:
            self._atomic_write_json(self.candidates_path, self.memory_candidates)
        except Exception as e:
            print(f"Error saving memory candidates: {e}")

    def _normalize_memory_candidate(self, candidate):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        content = self._normalize_text(candidate.get("content"))
        title = self._normalize_text(candidate.get("title"))[:120]
        if not title:
            title = f"Memory candidate {now[:10]}"
        category = self._normalize_text(candidate.get("category")) or self.infer_category(content, title, "auto_autonomous")
        tags = candidate.get("tags")
        if not isinstance(tags, list):
            tags = self.infer_tags(content, category)
        tags = [self._normalize_text(tag) for tag in tags if self._normalize_text(tag)][:8]

        normalized = dict(candidate)
        normalized.update({
            "id": self._normalize_text(candidate.get("id")) or str(datetime.now().timestamp()),
            "creation_time": self._normalize_text(candidate.get("creation_time")) or now,
            "date": self._normalize_text(candidate.get("date"))[:10] or now[:10],
            "title": title,
            "content": content,
            "category": category,
            "tags": tags,
            "confidence": self._coerce_float(candidate.get("confidence"), 0.7),
            "importance_score": self._coerce_float(candidate.get("importance_score"), 0.45),
            "status": self._normalize_text(candidate.get("status")) or "pending",
            "source": "auto_autonomous",
        })
        source_detail = normalized.get("source_detail")
        if not isinstance(source_detail, dict):
            source_detail = {}
        source_detail.setdefault("type", "autonomous_memory_candidate")
        source_detail.setdefault("captured_at", normalized["creation_time"])
        normalized["source_detail"] = source_detail
        return normalized

    def _memory_content_key(self, content):
        return re.sub(r"\s+", " ", str(content or "")).strip().lower()

    def _has_matching_memory_content(self, content, project_id=""):
        target = self._memory_content_key(content)
        active_project_id = str(project_id or "")
        if not target:
            return False
        for memory in self.memories:
            existing = self._memory_content_key(memory.get("content"))
            existing_project_id = str(memory.get("project_id") or "")
            if existing == target and existing_project_id == active_project_id:
                return True
        return False

    def _find_pending_candidate_by_content(self, content, project_id=""):
        target = self._memory_content_key(content)
        active_project_id = str(project_id or "")
        if not target:
            return None
        for candidate in self.memory_candidates:
            if candidate.get("status") != "pending":
                continue
            existing = self._memory_content_key(candidate.get("content"))
            existing_project_id = str(candidate.get("project_id") or "")
            if existing == target and existing_project_id == active_project_id:
                return candidate
        return None

    def get_memory_candidates(self, status="pending", limit=20):
        status = self._normalize_text(status)
        try:
            limit = max(1, min(100, int(limit)))
        except (TypeError, ValueError):
            limit = 20
        candidates = [
            candidate for candidate in self.memory_candidates
            if not status or candidate.get("status") == status
        ]
        candidates.sort(key=lambda item: (item.get("creation_time", ""), item.get("id", "")), reverse=True)
        return candidates[:limit]

    def add_memory_candidate(
        self,
        content,
        title=None,
        category=None,
        tags=None,
        confidence=None,
        importance_score=None,
        source_detail=None,
        metadata=None
    ):
        content = self._normalize_text(content)
        if len(content) < 4:
            return None
        metadata = metadata if isinstance(metadata, dict) else {}
        project_id = str(metadata.get("project_id") or "")
        if self._has_matching_memory_content(content, project_id=project_id):
            return None
        existing = self._find_pending_candidate_by_content(content, project_id=project_id)
        if existing:
            return existing

        now = datetime.now()
        candidate = {
            "id": str(now.timestamp()),
            "creation_time": now.strftime("%Y-%m-%d %H:%M:%S"),
            "date": now.strftime("%Y-%m-%d"),
            "title": title or f"Memory candidate {now.strftime('%Y-%m-%d')}",
            "content": content,
            "category": category,
            "tags": tags,
            "confidence": confidence,
            "importance_score": importance_score,
            "status": "pending",
            "source": "auto_autonomous",
            "source_detail": source_detail if isinstance(source_detail, dict) else {
                "type": "autonomous_memory_candidate",
                "captured_at": now.strftime("%Y-%m-%d %H:%M:%S")
            }
        }
        candidate.update(metadata)
        candidate = self._normalize_memory_candidate(candidate)
        self.memory_candidates.append(candidate)
        self.save_memory_candidates()
        return candidate

    def _find_memory_candidate(self, candidate_id):
        candidate_id = str(candidate_id or "")
        for candidate in self.memory_candidates:
            if str(candidate.get("id")) == candidate_id:
                return candidate
        return None

    def confirm_memory_candidate(self, candidate_id, updates=None):
        candidate = self._find_memory_candidate(candidate_id)
        if not candidate or candidate.get("status") != "pending":
            return None
        updates = updates if isinstance(updates, dict) else {}

        content = self._normalize_text(updates.get("content", candidate.get("content")))
        if not content:
            return None
        title = self._normalize_text(updates.get("title", candidate.get("title"))) or None
        category = self._normalize_text(updates.get("category", candidate.get("category"))) or None
        tags = updates.get("tags", candidate.get("tags"))
        if isinstance(tags, str):
            tags = [tag.strip() for tag in tags.split(",") if tag.strip()]
        if not isinstance(tags, list):
            tags = None
        is_important = bool(updates.get("is_important", False))
        is_locked = bool(updates.get("is_locked", False))
        project_id = str(candidate.get("project_id") or "")

        if self._has_matching_memory_content(content, project_id=project_id):
            candidate["status"] = "duplicate"
            candidate["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_memory_candidates()
            return {"status": "duplicate", "memory": None}

        source_detail = candidate.get("source_detail") if isinstance(candidate.get("source_detail"), dict) else {}
        source_detail = dict(source_detail)
        source_detail["type"] = "autonomous_memory_confirmed"
        source_detail["confirmed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        metadata = {}
        for key in ("project_id", "project_name", "project_scoped"):
            if key in candidate:
                metadata[key] = candidate[key]
        if is_locked:
            metadata["is_locked"] = True

        memory = self.add_memory(
            content,
            title=title,
            source="auto_autonomous",
            is_important=is_important,
            category=category,
            tags=tags,
            confidence=updates.get("confidence", candidate.get("confidence")),
            source_detail=source_detail,
            metadata=metadata
        )
        candidate["status"] = "confirmed"
        candidate["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        candidate["memory_id"] = memory.get("id")
        self.save_memory_candidates()
        return {"status": "success", "memory": memory}

    def ignore_memory_candidate(self, candidate_id):
        candidate = self._find_memory_candidate(candidate_id)
        if not candidate:
            return False
        candidate["status"] = "ignored"
        candidate["resolved_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_memory_candidates()
        return True

    def _normalize_text(self, value):
        return re.sub(r"\s+", " ", str(value or "")).strip()

    def _tokenize(self, text):
        text = str(text or "").lower()
        english = re.findall(r"[a-z0-9_]{2,}", text)
        chinese_keywords = [
            "记忆", "喜欢", "讨厌", "不喜欢", "名字", "生日", "目标", "计划", "项目",
            "代码", "产品", "模型", "提示词", "记住", "关系", "沙耶", "Saya", "Doctor",
            "开心", "难过", "焦虑", "压力", "健康", "睡眠", "提醒", "明天", "今天"
        ]
        chinese = [kw.lower() for kw in chinese_keywords if kw in text]
        return set(english + chinese)

    def infer_category(self, content, title=None, source=None):
        text = f"{title or ''} {content or ''} {source or ''}".lower()
        rules = [
            ("conversation_summary", ["archive", "session_archive", "auto_merge", "summary", "总结"]),
            ("identity", ["我的名字", "叫我", "我是", "生日", "name", "birthday", "call me"]),
            ("preference", ["喜欢", "不喜欢", "讨厌", "偏好", "prefer", "favorite", "like", "dislike", "hate"]),
            ("project", ["项目", "代码", "产品", "功能", "模型", "接口", "bug", "api", "prompt", "feature", "project"]),
            ("goal", ["目标", "计划", "想要", "quest", "goal", "plan", "todo"]),
            ("schedule", ["提醒", "明天", "后天", "今天", "日期", "时间", "deadline", "schedule", "remind"]),
            ("health", ["健康", "睡眠", "焦虑", "压力", "疼", "生病", "health", "sleep", "stress", "anxiety"]),
            ("relationship", ["我们", "关系", "doctor", "saya", "沙耶", "陪伴", "信任", "love", "trust"]),
            ("emotion", ["开心", "难过", "伤心", "害怕", "生气", "孤独", "happy", "sad", "angry", "lonely"])
        ]
        for category, keywords in rules:
            if any(keyword in text for keyword in keywords):
                return category
        return "general"

    def infer_tags(self, content, category=None):
        tags = []
        if category and category != "general":
            tags.append(category)

        text = str(content or "")
        keyword_tags = {
            "api": ["api", "key", "接口"],
            "prompt": ["prompt", "提示词", "system prompt"],
            "memory": ["memory", "记忆", "记住"],
            "model": ["模型", "model", "qwen", "mistral", "saya r1"],
            "product": ["产品", "功能", "体验"],
            "coding": ["代码", "bug", "报错", "实现", "修复"],
            "relationship": ["关系", "陪伴", "信任", "doctor", "saya", "沙耶"],
            "preference": ["喜欢", "不喜欢", "偏好", "prefer", "favorite"]
        }
        lowered = text.lower()
        for tag, keywords in keyword_tags.items():
            if any(keyword in lowered for keyword in keywords) and tag not in tags:
                tags.append(tag)

        for word in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text):
            tag = word.lower()
            if tag not in tags and len(tags) < 8:
                tags.append(tag)

        return tags[:8]

    def _coerce_float(self, value, default, minimum=0.0, maximum=1.0):
        try:
            value = float(value)
        except (TypeError, ValueError):
            value = default
        return max(minimum, min(maximum, value))

    def _infer_memory_kind(self, memory):
        category = str(memory.get("category") or "general")
        source = str(memory.get("source") or "").lower()
        managed_slot = str(memory.get("managed_slot") or "").lower()
        if managed_slot == "life_quests:summary":
            return "goal_summary"
        if managed_slot == "assistant_role:learning_map:index":
            return "knowledge_index"
        if managed_slot.startswith("assistant_role:learning_area:"):
            return "knowledge_area"
        if managed_slot.startswith("about_you:") or source == "about_you":
            return "profile"
        if category == "conversation_summary" or source in {"archive", "session_archive", "auto_merge"}:
            return "summary"
        if category in {"identity", "preference", "goal", "relationship", "project", "health"}:
            return category
        if category in {"emotion", "schedule"}:
            return "event"
        return "fact"

    def _infer_memory_layer(self, memory):
        if memory.get("is_archived"):
            return "archive"
        source = str(memory.get("source") or "").lower()
        category = str(memory.get("category") or "general").lower()
        title = str(memory.get("title") or "").lower()
        managed_slot = str(memory.get("managed_slot") or "").lower()
        if managed_slot.startswith("assistant_role:learning_"):
            return "semantic"
        if managed_slot.startswith("about_you:") or managed_slot == "life_quests:summary" or source == "about_you":
            return "core"
        if "life quests" in title and memory.get("is_locked"):
            return "core"
        if category == "identity" and memory.get("is_locked") and source in {"manual", "system"}:
            return "core"
        if category == "conversation_summary" or source in {"archive", "session_archive", "auto_merge"}:
            return "episodic"
        if category in {"emotion", "schedule"}:
            return "episodic"
        # Minecraft task events are dated happenings -> episodic; they used to
        # fall through to semantic where timestamped events pile up and crowd
        # out durable knowledge.
        if source == "game_task" or category == "event":
            return "episodic"
        return "semantic"

    def _memory_layer(self, memory):
        layer = str(memory.get("memory_layer") or "").lower()
        if memory.get("is_archived"):
            return "archive"
        if layer not in self.MEMORY_LAYER_ORDER:
            return self._infer_memory_layer(memory)
        return layer

    def _infer_retention_policy(self, memory):
        current = str(memory.get("retention_policy") or "").strip().lower()
        if current in self.RETENTION_POLICIES:
            return current
        managed_slot = str(memory.get("managed_slot") or "").strip().lower()
        source = str(memory.get("source") or "").strip().lower()
        layer = self._memory_layer(memory)
        if managed_slot or source in {"about_you", "system_activity"} or layer == "core":
            return "permanent"
        if memory.get("expires_at"):
            return "temporary"
        if (
            layer == "episodic"
            or source in {"archive", "session_archive", "auto_merge", "capacity_compaction", "auto_autonomous"}
            or str(memory.get("memory_kind") or "").lower() in {"event", "summary", "conversation_summary"}
        ):
            return "compressible"
        if memory.get("is_locked") or memory.get("is_important"):
            return "durable"
        return "durable"

    def _memory_tier(self, memory):
        source = str(memory.get("source", "") or "").lower()
        title = str(memory.get("title", "") or "").lower()
        category = str(memory.get("category", "") or "").lower()
        managed_slot = str(memory.get("managed_slot", "") or "").lower()
        source_type = ""
        if isinstance(memory.get("source_detail"), dict):
            source_type = str(memory["source_detail"].get("type", "") or "").lower()

        if managed_slot.startswith("about_you:") or source == "about_you":
            return "about_you"

        if (
            source in {"manual", "archive", "session_archive", "system", "auto_merge"}
            or "life quests" in title
            or category == "goal"
        ):
            return "curated"

        if (
            source == "auto_explicit"
            or source_type == "explicit_user_request"
            or "llm_extraction" in source
            or "rag_auto" in source
            or source.startswith("legacy:")
        ):
            return "auto"

        # Unknown memories are treated as curated when they look deliberately edited,
        # otherwise as auto-like notes with a lower ceiling.
        if memory.get("is_locked") or memory.get("is_important"):
            return "curated"
        return "auto"

    def _memory_score_band(self, memory):
        tier = self._memory_tier(memory)
        if tier == "about_you":
            return 0.90, 1.00
        if tier == "curated":
            return 0.70, 0.89
        return 0.35, 0.70

    def _recency_score(self, memory):
        date_text = str(memory.get("date") or memory.get("creation_time") or "")[:10]
        try:
            memory_date = datetime.strptime(date_text, "%Y-%m-%d").date()
            days = max(0, (datetime.now().date() - memory_date).days)
        except (TypeError, ValueError):
            return 0.35
        if days <= 7:
            return 1.0
        if days <= 30:
            return 0.82
        if days <= 180:
            return 0.58
        if days <= 730:
            return 0.36
        return 0.22

    def _access_score(self, memory):
        try:
            count = max(0, int(memory.get("access_count", 0) or 0))
        except (TypeError, ValueError):
            count = 0
        return min(1.0, count / 12.0)

    def _query_relevance_score(self, memory, query, embedding_score=None):
        query = str(query or "").strip()
        if not query:
            return 0.0
        query_tokens = self._tokenize(query)
        memory_tokens = self._tokenize(self._memory_text(memory))
        overlap_score = 0.0
        if query_tokens:
            overlap_score = len(query_tokens & memory_tokens) / max(1, len(query_tokens))
        semantic_score = self.semantic_memory_score(memory, query)
        exact_score = 1.0 if query.lower() in self._memory_text(memory).lower() else 0.0
        if embedding_score is not None:
            dense_score = self._coerce_float((float(embedding_score) + 1.0) / 2.0, 0.0)
            return self._coerce_float(
                dense_score * 0.62 + overlap_score * 0.15 + semantic_score * 0.13 + exact_score * 0.10,
                0.0,
            )
        return self._coerce_float(
            overlap_score * 0.35 + semantic_score * 0.45 + exact_score * 0.20,
            0.0
        )

    def memory_priority_score(self, memory, query="", embedding_score=None):
        low, high = self._memory_score_band(memory)
        confidence = self._coerce_float(memory.get("confidence"), 0.75)
        importance = self._coerce_float(memory.get("importance_score"), 0.4)
        recency = self._recency_score(memory)
        access = self._access_score(memory)
        relevance = self._query_relevance_score(memory, query, embedding_score=embedding_score)
        locked = 1.0 if memory.get("is_locked") else 0.0
        starred = 1.0 if memory.get("is_important") else 0.0

        tier = self._memory_tier(memory)
        if tier == "about_you":
            quality = (
                confidence * 0.22 + importance * 0.22 + locked * 0.18 +
                starred * 0.12 + relevance * 0.16 + recency * 0.06 + access * 0.04
            )
        elif tier == "curated":
            quality = (
                confidence * 0.24 + importance * 0.22 + locked * 0.12 +
                starred * 0.12 + relevance * 0.16 + recency * 0.08 + access * 0.06
            )
        else:
            # Auto-saved and inferred notes are intentionally dynamic: relevance,
            # recency, confidence, and repeated access can lift them, but only up
            # to the curated tier floor.
            quality = (
                relevance * 0.34 + confidence * 0.22 + importance * 0.14 +
                recency * 0.14 + access * 0.10 + starred * 0.04 + locked * 0.02
            )

        if memory.get("conflicts_with"):
            quality -= 0.08
        if self._is_expired(memory):
            quality -= 0.25

        quality = self._coerce_float(quality, 0.0)
        return round(low + quality * (high - low), 4)

    def _looks_like_correction(self, content):
        text = str(content or "").lower()
        markers = [
            "不再", "不是", "改成", "改为", "更新", "纠正", "以后", "现在",
            "no longer", "not anymore", "instead", "changed to", "correction", "update this"
        ]
        return any(marker in text for marker in markers)

    def _find_related_memory_ids(self, memory, min_overlap=0.45):
        category = memory.get("category")
        if category not in {"identity", "preference", "relationship", "project", "goal", "schedule"}:
            return []
        new_tokens = self._tokenize(memory.get("content", ""))
        if not new_tokens:
            return []
        related = []
        for existing in self.memories:
            if existing.get("id") == memory.get("id") or existing.get("is_archived"):
                continue
            if existing.get("category") != category:
                continue
            old_tokens = self._tokenize(existing.get("content", ""))
            if not old_tokens:
                continue
            overlap = len(new_tokens & old_tokens) / max(1, min(len(new_tokens), len(old_tokens)))
            if overlap >= min_overlap:
                related.append(existing.get("id"))
        return [rid for rid in related if rid]

    def normalize_memory(self, memory):
        """Upgrade any old memory dict to the Memory 2.0 schema without removing old fields."""
        if not isinstance(memory, dict):
            return memory, False

        changed = False
        timestamp = datetime.now()

        def ensure(key, value, allow_none=False):
            nonlocal changed
            if key not in memory or (not allow_none and memory.get(key) in (None, "")):
                memory[key] = value
                changed = True

        ensure("id", str(timestamp.timestamp()))
        ensure("creation_time", timestamp.strftime("%Y-%m-%d %H:%M:%S"))
        ensure("date", str(memory.get("creation_time", ""))[:10] or timestamp.strftime("%Y-%m-%d"))
        ensure("title", f"Memory {memory.get('date', timestamp.strftime('%Y-%m-%d'))}")
        ensure("content", "")
        ensure("source", "manual")
        ensure("is_locked", False)
        ensure("is_important", False)
        ensure("is_archived", False)

        title_key = str(memory.get("title") or "").strip().lower()
        source_key = str(memory.get("source") or "").strip().lower()
        if title_key in {"life quests summary", "achievements log"} and source_key == "system":
            managed_defaults = {
                "managed_slot": "life_quests:summary",
                "is_locked": True,
                "is_important": True,
                "memory_layer": "core",
                "memory_kind": "goal_summary",
                "category": "goal",
            }
            for key, value in managed_defaults.items():
                if memory.get(key) != value:
                    memory[key] = value
                    changed = True

        if not isinstance(memory.get("content"), str):
            memory["content"] = str(memory.get("content", ""))
            changed = True

        category = memory.get("category")
        if not category:
            memory["category"] = self.infer_category(
                memory.get("content", ""),
                memory.get("title", ""),
                memory.get("source", "")
            )
            changed = True

        tags = memory.get("tags")
        if not isinstance(tags, list):
            if isinstance(tags, str):
                tags = [t.strip() for t in tags.split(",") if t.strip()]
            else:
                tags = self.infer_tags(memory.get("content", ""), memory.get("category"))
            memory["tags"] = tags[:8]
            changed = True

        if "schema_version" not in memory or memory.get("schema_version") != 3:
            memory["schema_version"] = 3
            changed = True

        if "confidence" not in memory:
            source = str(memory.get("source", ""))
            default_confidence = 0.9 if source in {"manual", "auto_explicit"} else 0.75
            if source.startswith("legacy:"):
                default_confidence = 0.65
            memory["confidence"] = default_confidence
            changed = True
        else:
            confidence = self._coerce_float(memory.get("confidence"), 0.75)
            if confidence != memory.get("confidence"):
                memory["confidence"] = confidence
                changed = True

        if "importance_score" not in memory:
            score = 0.4
            if memory.get("is_important"):
                score += 0.25
            if memory.get("is_locked"):
                score += 0.25
            if memory.get("category") in {"identity", "preference", "relationship"}:
                score += 0.1
            memory["importance_score"] = self._coerce_float(score, 0.5)
            changed = True

        tier = self._memory_tier(memory)
        base_priority_score = self.memory_priority_score(memory)
        if memory.get("memory_tier") != tier:
            memory["memory_tier"] = tier
            changed = True
        if memory.get("base_priority_score") != base_priority_score:
            memory["base_priority_score"] = base_priority_score
            changed = True

        ensure("access_count", 0)
        ensure("last_accessed", None, allow_none=True)
        ensure("expires_at", None, allow_none=True)
        ensure("supersedes", [])
        ensure("conflicts_with", [])
        ensure("source_detail", {
            "type": memory.get("source", "manual"),
            "captured_at": memory.get("creation_time")
        })
        ensure("evidence", [{
            "type": "text",
            "quote": self._normalize_text(memory.get("content", ""))[:180],
            "created_at": memory.get("creation_time")
        }])

        # "Archive conversation" historically meant "create an active
        # conversation summary". Migrate those summaries to explicit Episodic
        # semantics so they cannot be mistaken for retired Archive records.
        if (
            not memory.get("is_archived")
            and str(memory.get("category") or "").lower() == "conversation_summary"
            and str(memory.get("source") or "").lower() in {"archive", "session_archive"}
        ):
            if memory.get("memory_layer") != "episodic":
                memory["memory_layer"] = "episodic"
                changed = True
            if memory.get("archive_semantics") != "active_summary_not_archived_record":
                memory["archive_semantics"] = "active_summary_not_archived_record"
                changed = True
            tags = [
                str(tag) for tag in memory.get("tags", [])
                if str(tag).strip().lower() != "archive"
            ]
            for tag in ("conversation", "summary", "episodic"):
                if tag not in tags:
                    tags.append(tag)
            if memory.get("tags") != tags[:8]:
                memory["tags"] = tags[:8]
                changed = True
            detail = memory.get("source_detail")
            if not isinstance(detail, dict):
                detail = {"type": "session_archive"}
            detail_updates = {
                "action": "summarize_selected_exchanges",
                "target_memory_layer": "episodic",
            }
            for key, value in detail_updates.items():
                if detail.get(key) != value:
                    detail[key] = value
                    changed = True
            memory["source_detail"] = detail

        status = "archived" if memory.get("is_archived") else "active"
        if memory.get("status") != status:
            memory["status"] = status
            changed = True

        layer = self._memory_layer(memory)
        if memory.get("memory_layer") != layer:
            memory["memory_layer"] = layer
            changed = True
        kind = str(memory.get("memory_kind") or "").strip().lower()
        if not kind:
            memory["memory_kind"] = self._infer_memory_kind(memory)
            changed = True
        retention_policy = self._infer_retention_policy(memory)
        if memory.get("retention_policy") != retention_policy:
            memory["retention_policy"] = retention_policy
            changed = True
        if "compressed_from" not in memory or not isinstance(memory.get("compressed_from"), list):
            memory["compressed_from"] = []
            changed = True
        inferred_generation = 1 if memory.get("compressed_from") else 0
        try:
            compression_generation = max(0, int(memory.get("compression_generation", inferred_generation)))
        except (TypeError, ValueError):
            compression_generation = inferred_generation
        if memory.get("compression_generation") != compression_generation:
            memory["compression_generation"] = compression_generation
            changed = True
        try:
            ai_generation = max(0, int(memory.get("ai_compression_generation", 0)))
        except (TypeError, ValueError):
            ai_generation = 0
        if memory.get("ai_compression_generation") != ai_generation:
            memory["ai_compression_generation"] = ai_generation
            changed = True
        if "parent_memory_id" not in memory:
            memory["parent_memory_id"] = None
            changed = True

        return memory, changed

    def normalize_all_memories(self):
        changed = False
        normalized = []
        for memory in self.memories:
            if not isinstance(memory, dict):
                continue
            item, item_changed = self.normalize_memory(memory)
            normalized.append(item)
            changed = changed or item_changed
        self.memories = normalized
        if changed:
            self.save_memories()

    @memory_write_locked
    def add_memory(
        self,
        content,
        title=None,
        source="manual",
        is_important=False,
        category=None,
        tags=None,
        confidence=None,
        source_detail=None,
        metadata=None,
        run_capacity_check=True,
    ):
        timestamp = datetime.now()
        if not title:
            # Simple fallback title if none provided
            title = f"Memory {timestamp.strftime('%Y-%m-%d %H:%M')}"
            
        memory = {
            "id": str(uuid.uuid4()),
            "creation_time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "date": timestamp.strftime("%Y-%m-%d"),
            "title": title,
            "content": content,
            "source": source,
            "is_locked": False,
            "is_important": is_important
        }
        if category:
            memory["category"] = category
        if tags:
            memory["tags"] = tags
        if confidence is not None:
            memory["confidence"] = confidence
        if source_detail:
            memory["source_detail"] = source_detail
        if isinstance(metadata, dict):
            memory.update(metadata)

        memory["id"] = str(memory.get("id") or uuid.uuid4())
        if self._find_memory(memory["id"]):
            raise ValueError("A memory with this ID already exists")
        previous_memories = copy.deepcopy(self.memories)
        previous_deletions = copy.deepcopy(self.deleted_memory_ids)
        if memory["id"] in self.deleted_memory_ids and not self.revive_deleted_memory(memory["id"]):
            raise RuntimeError("Unable to clear the previous deletion marker")

        memory, _ = self.normalize_memory(memory)
        if self._looks_like_correction(memory.get("content")):
            related_ids = self._find_related_memory_ids(memory)
            if related_ids:
                memory["supersedes"] = related_ids
                memory["conflicts_with"] = related_ids
                for existing in self.memories:
                    if existing.get("id") in related_ids:
                        conflicts = existing.setdefault("conflicts_with", [])
                        if memory["id"] not in conflicts:
                            conflicts.append(memory["id"])
                        existing["superseded_by"] = memory["id"]

        self.memories.append(memory)
        try:
            self._validate_relationship_graph()
        except ValueError:
            self.memories = previous_memories
            self.deleted_memory_ids = previous_deletions
            self.save_memory_deletions()
            raise
        if not self.save_memories():
            self.memories = previous_memories
            self.deleted_memory_ids = previous_deletions
            self.save_memory_deletions()
            raise RuntimeError("Unable to persist the new memory")
        self.maybe_index_new_memories([memory])
        if run_capacity_check and not self._maintenance_running:
            self.last_maintenance_result = self.run_memory_capacity_maintenance(
                trigger="memory_added",
            )
        return memory

    def upsert_daily_activity_memory(self, date_str, checkin_record, app_entries, chat_stats):
        """Maintain one protected, high-priority memory describing today.

        The same managed slot is refreshed throughout the day and reused after
        midnight, so daily operational state does not create an ever-growing
        pile of important memories.
        """
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        date_str = str(date_str or now[:10])[:10]
        checkin_record = checkin_record if isinstance(checkin_record, dict) else {}
        chat_stats = chat_stats if isinstance(chat_stats, dict) else {}
        try:
            app_entries = max(0, int(app_entries or 0))
        except (TypeError, ValueError):
            app_entries = 0

        done = int(checkin_record.get("done") or 0)
        total = int(checkin_record.get("total") or 0)
        status = str(checkin_record.get("status") or "no_tasks")
        status_labels = {
            "completed": "全部完成",
            "partial": "部分完成",
            "pending": "尚未完成",
            "missed": "未完成",
            "no_tasks": "今日没有打卡任务",
            "future": "尚未开始",
        }
        task_lines = []
        for task in checkin_record.get("task_details") or []:
            if not isinstance(task, dict):
                continue
            title = re.sub(r"\s+", " ", str(task.get("title") or "")).strip()
            if title:
                task_lines.append(f"  - {'已完成' if task.get('done') else '未完成'}：{title[:80]}")
        if not task_lines:
            task_lines.append("  - 今日没有可记录的打卡任务")

        self_ratings = []
        for key, label in (("mood", "心情"), ("energy", "精力"), ("difficulty", "难度")):
            value = checkin_record.get(key)
            if value not in (None, ""):
                self_ratings.append(f"{label} {value}/5")
        rating_text = "、".join(self_ratings) if self_ratings else "未填写"
        note = re.sub(r"\s+", " ", str(checkin_record.get("note") or "")).strip()

        content_lines = [
            f"这是 Saya 自动维护的今日活动状态，日期为 {date_str}，数据会在进入页面、完成对话或修改打卡后更新。",
            f"- 今日打卡：{done}/{total}，状态为{status_labels.get(status, status)}。",
            f"- 今日自评：{rating_text}。",
            f"- 今日进入 Saya 页面：{app_entries} 次。",
            f"- 今日已完成对话：{int(chat_stats.get('conversation_count') or 0)} 轮；用户消息 {int(chat_stats.get('user_message_count') or 0)} 条，Saya 回复 {int(chat_stats.get('assistant_message_count') or 0)} 条，涉及 {int(chat_stats.get('session_count') or 0)} 个会话。",
            "- 今日打卡明细：",
            *task_lines,
        ]
        if note:
            content_lines.append(f"- 今日备注：{note[:500]}")
        content = "\n".join(content_lines)

        memory = self._find_memory(self.DAILY_ACTIVITY_MEMORY_ID)
        if memory is None:
            memory = {
                "id": self.DAILY_ACTIVITY_MEMORY_ID,
                "creation_time": now,
            }
            self.memories.append(memory)
        memory.update({
            "date": date_str,
            "title": f"今日活动状态 · {date_str}",
            "content": content,
            "source": "system_activity",
            "source_detail": {
                "type": "daily_activity_snapshot",
                "captured_at": now,
                "date": date_str,
            },
            "category": "schedule",
            "tags": ["today", "checkin", "activity", "conversation"],
            "confidence": 1.0,
            "importance_score": 0.9,
            "is_locked": True,
            "is_important": True,
            "is_archived": False,
            "status": "active",
            "managed_slot": self.DAILY_ACTIVITY_MANAGED_SLOT,
            "memory_layer": "core",
            "memory_kind": "daily_activity",
            "last_updated": now,
            "evidence": [{
                "type": "system_activity_snapshot",
                "quote": content[:180],
                "created_at": now,
            }],
        })
        memory.pop("archived_from_layer", None)
        memory, _ = self.normalize_memory(memory)
        self.save_memories()
        return memory

    def upsert_anki_checkin_memory(self, anki_state):
        """Maintain one fixed memory containing the latest Anki check-in state."""
        anki_state = anki_state if isinstance(anki_state, dict) else {}
        summary = anki_state.get("summary")
        if not isinstance(summary, dict):
            return None

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        completed_decks = [
            re.sub(r"\s+", " ", str(name or "")).strip()
            for name in summary.get("completed_decks_today") or []
            if str(name or "").strip()
        ]
        completed = bool(summary.get("daily_task_completed"))
        recent_activity = []
        for item in anki_state.get("daily_activity") or []:
            if not isinstance(item, dict) or int(item.get("reviews") or 0) <= 0:
                continue
            recent_activity.append(
                f"{str(item.get('date') or '')[:10]}：{int(item.get('reviews') or 0)} 次复习"
            )
        recent_text = "；".join(recent_activity[-14:]) or "最近 30 天暂无复习记录"
        minutes = round(int(summary.get("today_time_ms") or 0) / 60000, 1)
        completed_text = "、".join(completed_decks) if completed_decks else "暂无"
        status_text = "已完成" if completed else "尚未完成"
        snapshot = {
            "date": now[:10],
            "profile": str(anki_state.get("profile") or ""),
            "source_modified_at": str(anki_state.get("source_modified_at") or ""),
            "completed": completed,
            "completed_decks": completed_decks,
            "today_reviews": int(summary.get("today_reviews") or 0),
            "today_cards": int(summary.get("today_cards") or 0),
            "today_time_ms": int(summary.get("today_time_ms") or 0),
            "today_accuracy": float(summary.get("today_accuracy") or 0),
            "current_streak": int(summary.get("current_streak") or 0),
            "longest_streak": int(summary.get("longest_streak") or 0),
            "recent_activity": recent_activity[-14:],
        }
        memory = self._find_memory(self.ANKI_CHECKIN_MEMORY_ID)
        if memory is not None and memory.get("anki_checkin") == snapshot:
            return memory

        content = "\n".join([
            "这是 Saya 自动维护的 Anki 学习打卡记忆；每次读取 Anki 后会原位更新，不会为每天重复创建新记忆。",
            f"- 当前档案：{str(anki_state.get('profile') or '未命名档案')}。",
            f"- 今日每日任务：{status_text}。判定规则为至少一个实际牌组今天有学习记录，且当前没有到期的复习或学习卡。",
            f"- 今日满足条件的牌组：{completed_text}。",
            f"- 今日学习：{int(summary.get('today_reviews') or 0)} 次复习，{int(summary.get('today_cards') or 0)} 张不同卡片，用时约 {minutes:g} 分钟，正确率 {float(summary.get('today_accuracy') or 0):.1f}%。",
            f"- 连续学习：当前 {int(summary.get('current_streak') or 0)} 天，最长 {int(summary.get('longest_streak') or 0)} 天。",
            f"- 最近活动：{recent_text}。",
            f"- Anki 数据源最后变动：{str(anki_state.get('source_modified_at') or '未知')}。",
        ])

        if memory is None:
            memory = {
                "id": self.ANKI_CHECKIN_MEMORY_ID,
                "creation_time": now,
            }
            self.memories.append(memory)
        memory.update({
            "date": now[:10],
            "title": "Anki 学习打卡状态（自动更新）",
            "content": content,
            "source": "system_activity",
            "source_detail": {
                "type": "anki_checkin_snapshot",
                "captured_at": now,
                "source_modified_at": anki_state.get("source_modified_at"),
                "profile": anki_state.get("profile"),
            },
            "category": "schedule",
            "tags": ["anki", "study", "checkin", "spaced_repetition"],
            "confidence": 1.0,
            "importance_score": 0.9,
            "is_locked": True,
            "is_important": True,
            "is_archived": False,
            "status": "active",
            "managed_slot": self.ANKI_CHECKIN_MANAGED_SLOT,
            "memory_layer": "core",
            "memory_kind": "study_tracker",
            "retention_policy": "permanent",
            "last_updated": now,
            "anki_checkin": snapshot,
            "evidence": [{
                "type": "anki_statistics_snapshot",
                "quote": content[:180],
                "created_at": now,
            }],
        })
        memory.pop("archived_from_layer", None)
        memory, _ = self.normalize_memory(memory)
        self.save_memories()
        return memory

    def _find_memory(self, memory_id):
        for memory in self.memories:
            if memory.get("id") == memory_id:
                return memory
        return None

    def _upsert_managed_memory(
        self,
        memory_id,
        title,
        content,
        managed_slot,
        category="identity",
        tags=None,
        payload=None,
        archived=False,
        importance_score=0.9
    ):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory = self._find_memory(memory_id)
        if not memory:
            memory = {
                "id": memory_id,
                "creation_time": now,
                "date": now[:10],
                "source": "about_you",
                "source_detail": {
                    "type": "about_you_managed_slot",
                    "captured_at": now
                },
            }
            self.memories.append(memory)

        memory.update({
            "title": title,
            "content": content,
            "source": "about_you",
            "category": category,
            "tags": tags or ["about_you", category],
            "confidence": 0.95,
            "importance_score": self._coerce_float(importance_score, 0.9),
            "is_locked": True,
            "is_important": True,
            "is_archived": bool(archived),
            "managed_slot": managed_slot,
            "last_updated": now,
        })
        if payload is not None:
            memory["about_you_payload"] = payload
        memory["source_detail"] = {
            "type": "about_you_managed_slot",
            "captured_at": memory.get("creation_time", now),
            "updated_at": now
        }
        memory["evidence"] = [{
            "type": "text",
            "quote": self._normalize_text(content)[:180],
            "created_at": now
        }]
        self.normalize_memory(memory)
        return memory

    def _safe_about_you_date(self, value):
        text = str(value or "").strip()[:10]
        try:
            datetime.strptime(text, "%Y-%m-%d")
            return text
        except (TypeError, ValueError):
            return datetime.now().strftime("%Y-%m-%d")

    def _personality_memory(self):
        memory = self._find_memory(self.ABOUT_YOU_PERSONALITY_ID)
        profile = {
            "mode": "simple",
            "entries": [],
            "full_entry": None
        }
        if memory and isinstance(memory.get("about_you_payload"), dict):
            payload = memory.get("about_you_payload")
            entries = payload.get("entries") if isinstance(payload.get("entries"), list) else []
            if payload.get("mode") == "full" and isinstance(payload.get("full_entry"), dict):
                profile["mode"] = "full"
                profile["full_entry"] = payload.get("full_entry")
            else:
                profile["entries"] = [entry for entry in entries if isinstance(entry, dict)][-2:]
            return memory, profile
        return memory, profile

    def _personality_labels(self):
        return {
            "energy": "energy source",
            "information": "information style",
            "decisions": "decision style",
            "structure": "structure style",
            "social": "social pace",
            "stress": "stress response",
            "support": "preferred support",
            "communication": "communication preference",
            "feedback": "feedback preference",
            "memory": "memory preference",
            "initiative": "agent initiative",
            "detail": "detail level",
            "emotion_tone": "emotional tone",
            "boundaries": "conversation boundaries",
            "work_style": "work style",
            "motivation": "motivation style",
            "conflict": "conflict style",
            "learning": "learning style",
            "trust": "trust building",
            "humor": "humor preference",
            "decision_speed": "decision speed",
            "planning": "planning preference",
            "creativity": "creative collaboration",
            "privacy": "privacy preference",
            "autonomy": "autonomy preference",
            "rituals": "continuity rituals",
            "mbti_energy": "MBTI-like energy",
            "mbti_perception": "MBTI-like perception",
            "mbti_judgment": "MBTI-like judgment",
            "mbti_lifestyle": "MBTI-like lifestyle",
            "attachment": "attachment and reassurance",
            "failure_support": "failure support",
            "focus": "focus support",
            "change": "change handling",
        }

    def _format_personality_parts(self, answers):
        labels = self._personality_labels()
        parts = []
        for key, label in labels.items():
            value = self._normalize_text((answers or {}).get(key))
            if value:
                parts.append(f"{label}: {value}")
        return parts

    def _format_personality_content(self, profile):
        if isinstance(profile, list):
            profile = {"mode": "simple", "entries": profile[-2:], "full_entry": None}

        mode = (profile or {}).get("mode", "simple")
        if mode == "full" and isinstance((profile or {}).get("full_entry"), dict):
            entry = profile.get("full_entry")
            parts = self._format_personality_parts(entry.get("answers") if isinstance(entry.get("answers"), dict) else {})
            note = self._normalize_text(entry.get("note"))
            if note:
                parts.append(f"note: {note}")
            summary = "; ".join(parts) if parts else "detailed questionnaire saved without detailed answers"
            return "\n".join([
                "User personality profile from one detailed MBTI-like self-assessment.",
                "This single protected memory slot replaces older dated simple versions. Treat it as self-reported, updateable context rather than a diagnosis.",
                f"- {entry.get('date', '')}: {summary}"
            ])

        active_entries = [
            entry for entry in (profile or {}).get("entries", [])
            if isinstance(entry, dict)
        ][-2:]
        if not active_entries:
            return (
                "User personality profile has no active self-assessment yet. "
                "This managed memory slot is reserved for dated personality survey results."
            )
        lines = [
            "User personality profile from up to two dated self-assessments.",
            "Treat these notes as self-reported, updateable context rather than a permanent diagnosis."
        ]
        for entry in active_entries:
            answers = entry.get("answers") if isinstance(entry.get("answers"), dict) else {}
            parts = self._format_personality_parts(answers)
            note = self._normalize_text(entry.get("note"))
            if note:
                parts.append(f"note: {note}")
            summary = "; ".join(parts) if parts else "survey saved without detailed answers"
            lines.append(f"- {entry.get('date', '')}: {summary}")
        return "\n".join(lines)

    def get_about_you_personality(self):
        memory, profile = self._personality_memory()
        if not memory:
            memory = self._upsert_managed_memory(
                self.ABOUT_YOU_PERSONALITY_ID,
                "About You · Personality Profile",
                self._format_personality_content(profile),
                "about_you:personality",
                category="identity",
                tags=["about_you", "personality", "identity"],
                payload=profile,
                archived=True
            )
            self.save_memories()
        return {
            "memory_id": self.ABOUT_YOU_PERSONALITY_ID,
            "mode": profile.get("mode", "simple"),
            "entries": profile.get("entries", []),
            "full_entry": profile.get("full_entry"),
            "memory": memory
        }

    def save_about_you_personality_entry(self, entry):
        _, profile = self._personality_memory()
        entry = entry if isinstance(entry, dict) else {}
        mode = "full" if entry.get("mode") == "full" else "simple"
        entry_id = str(entry.get("id") or uuid.uuid4())
        clean_entry = {
            "id": entry_id,
            "date": self._safe_about_you_date(entry.get("date")),
            "answers": entry.get("answers") if isinstance(entry.get("answers"), dict) else {},
            "note": self._normalize_text(entry.get("note"))[:1000],
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode
        }

        if mode == "full":
            next_profile = {
                "mode": "full",
                "entries": [],
                "full_entry": clean_entry
            }
            content = self._format_personality_content(next_profile)
            self._upsert_managed_memory(
                self.ABOUT_YOU_PERSONALITY_ID,
                "About You · Personality Profile",
                content,
                "about_you:personality",
                category="identity",
                tags=["about_you", "personality", "identity", "detailed_profile"],
                payload=next_profile,
                archived=False
            )
            self.save_memories()
            return self.get_about_you_personality()

        entries = [] if profile.get("mode") == "full" else profile.get("entries", [])
        replaced = False
        clean_entries = []
        for existing in entries:
            if not isinstance(existing, dict):
                continue
            if str(existing.get("id")) == entry_id:
                clean_entries.append(clean_entry)
                replaced = True
            else:
                clean_entries.append(existing)
        if not replaced:
            clean_entries.append(clean_entry)

        clean_entries.sort(key=lambda item: (item.get("date", ""), item.get("updated_at", "")))
        clean_entries = clean_entries[-2:]
        next_profile = {
            "mode": "simple",
            "entries": clean_entries,
            "full_entry": None
        }
        content = self._format_personality_content(next_profile)
        self._upsert_managed_memory(
            self.ABOUT_YOU_PERSONALITY_ID,
            "About You · Personality Profile",
            content,
            "about_you:personality",
            category="identity",
            tags=["about_you", "personality", "identity"],
            payload=next_profile,
            archived=not bool(clean_entries)
        )
        self.save_memories()
        return self.get_about_you_personality()

    def delete_about_you_personality_entry(self, entry_id):
        _, profile = self._personality_memory()
        entry_id = str(entry_id or "")
        if profile.get("mode") == "full":
            full_entry = profile.get("full_entry") if isinstance(profile.get("full_entry"), dict) else {}
            if not entry_id or str(full_entry.get("id")) == entry_id:
                clean_profile = {"mode": "simple", "entries": [], "full_entry": None}
                self._upsert_managed_memory(
                    self.ABOUT_YOU_PERSONALITY_ID,
                    "About You · Personality Profile",
                    self._format_personality_content(clean_profile),
                    "about_you:personality",
                    category="identity",
                    tags=["about_you", "personality", "identity"],
                    payload=clean_profile,
                    archived=True
                )
                self.save_memories()
            return self.get_about_you_personality()

        entries = profile.get("entries", [])
        clean_entries = [
            entry for entry in entries
            if isinstance(entry, dict) and str(entry.get("id")) != entry_id
        ][-2:]
        clean_profile = {"mode": "simple", "entries": clean_entries, "full_entry": None}
        content = self._format_personality_content(clean_profile)
        self._upsert_managed_memory(
            self.ABOUT_YOU_PERSONALITY_ID,
            "About You · Personality Profile",
            content,
            "about_you:personality",
            category="identity",
            tags=["about_you", "personality", "identity"],
            payload=clean_profile,
            archived=not bool(clean_entries)
        )
        self.save_memories()
        return self.get_about_you_personality()

    def _possession_memory_id(self, slot, part=1):
        safe_slot = re.sub(r"[^a-zA-Z0-9_-]", "", str(slot or ""))[:24] or "1"
        suffix = f"-part-{part}" if int(part or 1) > 1 else ""
        return f"{self.ABOUT_YOU_POSSESSION_PREFIX}{safe_slot}{suffix}"

    def _chunk_text(self, text, size):
        text = str(text or "")
        if len(text) <= size:
            return [text]
        chunks = []
        remaining = text
        while remaining:
            chunks.append(remaining[:size])
            remaining = remaining[size:]
        return chunks

    def get_about_you_possessions(self):
        slots = {}
        for memory in self.memories:
            if not str(memory.get("managed_slot", "")).startswith("about_you:possession:"):
                continue
            payload = memory.get("about_you_payload") if isinstance(memory.get("about_you_payload"), dict) else {}
            slot = str(payload.get("slot") or "").strip()
            if not slot:
                continue
            part = int(payload.get("part") or 1)
            current = slots.setdefault(slot, {
                "slot": slot,
                "title": payload.get("title", ""),
                "content": "",
                "active": False,
                "updated_at": payload.get("updated_at", ""),
                "parts": 0
            })
            if part == 1:
                current["title"] = payload.get("title", current.get("title", ""))
                current["content"] = payload.get("full_content", "")
                current["active"] = not memory.get("is_archived", False) and bool(payload.get("active", False))
                current["updated_at"] = payload.get("updated_at", current.get("updated_at", ""))
            current["parts"] = max(current.get("parts", 0), part)

        return {
            "suggested_length": self.ABOUT_YOU_POSSESSION_SUGGESTED_LENGTH,
            "slots": sorted(slots.values(), key=lambda item: str(item.get("slot", "")))
        }

    def save_about_you_possession(self, slot, title, content):
        slot = re.sub(r"[^a-zA-Z0-9_-]", "", str(slot or ""))[:24] or "1"
        title = self._normalize_text(title)[:120] or f"Important possession {slot}"
        content = str(content or "").strip()
        if not content:
            return self.delete_about_you_possession(slot)

        chunks = self._chunk_text(content, self.ABOUT_YOU_POSSESSION_SUGGESTED_LENGTH)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for index, chunk in enumerate(chunks, start=1):
            memory_id = self._possession_memory_id(slot, index)
            chunk_title = (
                f"About You · Important Possession {slot}: {title}"
                if index == 1
                else f"About You · Important Possession {slot}: {title} ({index})"
            )
            payload = {
                "slot": slot,
                "title": title,
                "part": index,
                "total_parts": len(chunks),
                "active": True,
                "updated_at": now
            }
            if index == 1:
                payload["full_content"] = content
            managed_slot = f"about_you:possession:{slot}"
            memory_content = (
                f"User important possession slot {slot}.\n"
                f"Title: {title}\n"
                f"Details: {chunk}"
            )
            if len(chunks) > 1:
                memory_content += f"\nPart {index} of {len(chunks)}."
            self._upsert_managed_memory(
                memory_id,
                chunk_title,
                memory_content,
                managed_slot,
                category="preference",
                tags=["about_you", "important_possession", "preference"],
                payload=payload,
                archived=False,
                importance_score=0.85
            )

        # Retire stale overflow parts if the slot became shorter.
        stale_part = len(chunks) + 1
        while True:
            stale_memory = self._find_memory(self._possession_memory_id(slot, stale_part))
            if not stale_memory:
                break
            stale_memory["content"] = f"User important possession slot {slot} part {stale_part} is no longer active."
            stale_memory["is_archived"] = True
            stale_memory["about_you_payload"] = {
                "slot": slot,
                "title": title,
                "part": stale_part,
                "active": False,
                "updated_at": now
            }
            stale_part += 1

        self.save_memories()
        return self.get_about_you_possessions()

    def delete_about_you_possession(self, slot):
        slot = re.sub(r"[^a-zA-Z0-9_-]", "", str(slot or ""))[:24] or "1"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        found = False
        part = 1
        while True:
            memory = self._find_memory(self._possession_memory_id(slot, part))
            if not memory:
                break
            found = True
            title = ""
            payload = memory.get("about_you_payload") if isinstance(memory.get("about_you_payload"), dict) else {}
            if payload:
                title = payload.get("title", "")
            memory["content"] = f"User important possession slot {slot} was cleared on {now[:10]}."
            memory["is_archived"] = True
            memory["about_you_payload"] = {
                "slot": slot,
                "title": title,
                "part": part,
                "active": False,
                "updated_at": now,
                "full_content": "" if part == 1 else None
            }
            memory["last_updated"] = now
            part += 1
        if not found:
            self._upsert_managed_memory(
                self._possession_memory_id(slot, 1),
                f"About You · Important Possession {slot}",
                f"User important possession slot {slot} is empty.",
                f"about_you:possession:{slot}",
                category="preference",
                tags=["about_you", "important_possession", "preference"],
                payload={
                    "slot": slot,
                    "title": "",
                    "part": 1,
                    "active": False,
                    "updated_at": now,
                    "full_content": ""
                },
                archived=True,
                importance_score=0.85
            )
        self.save_memories()
        return self.get_about_you_possessions()

    def memory_delete_policy(self, memory):
        if not isinstance(memory, dict):
            return False, "Memory not found"
        managed_slot = str(memory.get("managed_slot") or "")
        retired_assistant_memory = bool(
            memory.get("is_archived")
            and managed_slot.startswith((
                "assistant_role:learning_area:",
                "assistant_role:learning_note:",
                "assistant_role:curated:",
            ))
        )
        if retired_assistant_memory:
            return True, "Archived Assistant record can be permanently deleted"
        if managed_slot:
            return False, "This memory is managed by another feature"
        if memory.get("is_locked", False):
            return False, "Unlock this memory before permanently deleting it"
        return True, "Memory can be permanently deleted"

    def _validate_relationship_graph(self, memories=None):
        """Reject dangling, self-referential, or cyclic supersession metadata."""
        records = memories if memories is not None else self.memories
        by_id = {str(item.get("id")): item for item in records if isinstance(item, dict) and item.get("id")}
        adjacency = {memory_id: set() for memory_id in by_id}
        for memory_id, memory in by_id.items():
            for field in ("supersedes", "conflicts_with"):
                values = memory.get(field) or []
                if not isinstance(values, list):
                    raise ValueError(f"{field} must be a list")
                for related_id in values:
                    related_id = str(related_id or "").strip()
                    if not related_id or related_id not in by_id:
                        raise ValueError(f"{field} references a missing memory")
                    if related_id == memory_id:
                        raise ValueError("A memory cannot relate to itself")
                    if field == "supersedes":
                        adjacency[memory_id].add(related_id)
                        if str(by_id[related_id].get("superseded_by") or "") != memory_id:
                            raise ValueError("Supersession relationships must be reciprocal")
                    elif memory_id not in {
                        str(value) for value in (by_id[related_id].get("conflicts_with") or [])
                    }:
                        raise ValueError("Conflict relationships must be reciprocal")
            newer_id = str(memory.get("superseded_by") or "").strip()
            if newer_id:
                if newer_id not in by_id:
                    raise ValueError("superseded_by references a missing memory")
                if newer_id == memory_id:
                    raise ValueError("A memory cannot supersede itself")
                if memory_id not in {
                    str(value) for value in (by_id[newer_id].get("supersedes") or [])
                }:
                    raise ValueError("Supersession relationships must be reciprocal")
                adjacency[newer_id].add(memory_id)

        visiting, visited = set(), set()

        def visit(memory_id):
            if memory_id in visiting:
                raise ValueError("The supersession relationship would form a cycle")
            if memory_id in visited:
                return
            visiting.add(memory_id)
            for related_id in adjacency.get(memory_id, ()):
                visit(related_id)
            visiting.remove(memory_id)
            visited.add(memory_id)

        for memory_id in by_id:
            visit(memory_id)
        return True

    def _memory_delete_signature(self, memory_ids):
        target_ids = {str(memory_id) for memory_id in memory_ids}
        payload = []
        relation_fields = (
            "id", "is_archived", "is_locked", "managed_slot", "supersedes",
            "superseded_by", "conflicts_with", "merged_into", "parent_memory_id",
            "compressed_from",
        )
        for memory in self.memories:
            related = bool(str(memory.get("id")) in target_ids)
            if not related:
                related_values = []
                for field in ("supersedes", "conflicts_with", "compressed_from"):
                    value = memory.get(field) or []
                    related_values.extend(value if isinstance(value, list) else [value])
                related_values.extend([
                    memory.get("superseded_by"), memory.get("merged_into"), memory.get("parent_memory_id")
                ])
                related = bool(target_ids & {str(value) for value in related_values if value})
            if related:
                payload.append({field: memory.get(field) for field in relation_fields})
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if self.memory_graph:
            target_nodes = {f"memory:{memory_id}" for memory_id in target_ids}
            manual_edges = [
                edge for edge in (self.memory_graph.graph.get("edges") or [])
                if edge.get("source_kind") == "manual"
                and (edge.get("source") in target_nodes or edge.get("target") in target_nodes)
            ]
            raw += json.dumps(manual_edges, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _relationship_cleanup_plan(self, memory_ids):
        target_ids = {str(memory_id) for memory_id in memory_ids}
        by_id = {str(memory.get("id")): memory for memory in self.memories if memory.get("id")}
        remaining_ids = set(by_id) - target_ids
        changes = {}
        cleared = []
        rewired = []
        warnings = []
        target_nodes = {f"memory:{memory_id}" for memory_id in target_ids}
        removed_manual_edges = [
            {
                "id": str(edge.get("id") or ""),
                "source": str(edge.get("source") or ""),
                "target": str(edge.get("target") or ""),
                "relation": str(edge.get("relation") or "related_to"),
            }
            for edge in ((self.memory_graph.graph.get("edges") or []) if self.memory_graph else [])
            if edge.get("source_kind") == "manual"
            and (edge.get("source") in target_nodes or edge.get("target") in target_nodes)
        ]

        def change(memory_id):
            return changes.setdefault(memory_id, copy.deepcopy(by_id[memory_id]))

        for memory_id in remaining_ids:
            memory = by_id[memory_id]
            for field in ("supersedes", "conflicts_with", "compressed_from"):
                values = memory.get(field) or []
                values = values if isinstance(values, list) else [values]
                kept = [value for value in values if str(value) not in target_ids]
                if len(kept) != len(values):
                    change(memory_id)[field] = kept
                    cleared.append({"memory_id": memory_id, "field": field})
            for field in ("superseded_by", "merged_into", "parent_memory_id"):
                if str(memory.get(field) or "") in target_ids:
                    change(memory_id)[field] = None
                    cleared.append({"memory_id": memory_id, "field": field})

        # Only supersession has a safe transitive meaning. Treat adjacent
        # deleted nodes as one component so deleting B+C from A->B->C->D
        # safely reconnects A->D. Other relationship types are never guessed.
        newer_to_older = {memory_id: set() for memory_id in by_id}
        for memory_id, memory in by_id.items():
            newer_to_older[memory_id].update(
                str(value) for value in (memory.get("supersedes") or [])
                if str(value) in by_id
            )
            older_id = memory_id
            newer_id = str(memory.get("superseded_by") or "").strip()
            if newer_id in by_id:
                newer_to_older[newer_id].add(older_id)
        older_to_newer = {memory_id: set() for memory_id in by_id}
        for newer_id, older_ids in newer_to_older.items():
            for older_id in older_ids:
                older_to_newer[older_id].add(newer_id)

        pending_targets = set(target_ids & set(by_id))
        while pending_targets:
            seed = pending_targets.pop()
            component = {seed}
            queue = [seed]
            while queue:
                current = queue.pop()
                adjacent_targets = (
                    newer_to_older.get(current, set()) | older_to_newer.get(current, set())
                ) & target_ids
                for related_id in adjacent_targets - component:
                    component.add(related_id)
                    pending_targets.discard(related_id)
                    queue.append(related_id)
            newer_ids = {
                newer_id for target_id in component
                for newer_id in older_to_newer.get(target_id, set())
                if newer_id in remaining_ids
            }
            older_ids = {
                older_id for target_id in component
                for older_id in newer_to_older.get(target_id, set())
                if older_id in remaining_ids
            }
            if older_ids and len(newer_ids) == 1:
                bridge_from = next(iter(newer_ids))
                newer = change(bridge_from)
                supersedes = [
                    str(value) for value in (newer.get("supersedes") or [])
                    if str(value) not in target_ids
                ]
                for older_id in sorted(older_ids):
                    if older_id != bridge_from and older_id not in supersedes:
                        supersedes.append(older_id)
                    change(older_id)["superseded_by"] = bridge_from
                    rewired.append({
                        "from": bridge_from, "to": older_id,
                        "relation": "supersedes", "via": sorted(component),
                    })
                newer["supersedes"] = supersedes
            elif older_ids and len(newer_ids) > 1:
                warnings.append({
                    "memory_ids": sorted(component),
                    "code": "ambiguous_supersession",
                    "message": "Multiple newer memories point to these records; links will be cleared instead of guessed.",
                })

        tentative = [changes.get(str(memory.get("id")), memory) for memory in self.memories if str(memory.get("id")) not in target_ids]
        self._validate_relationship_graph(tentative)
        return {
            "changes": changes, "cleared": cleared, "rewired": rewired,
            "warnings": warnings, "removed_manual_edges": removed_manual_edges,
        }

    @memory_write_locked
    def preview_memory_deletion(self, memory_ids, reason="user_delete"):
        unique_ids = list(dict.fromkeys(
            str(memory_id).strip() for memory_id in (memory_ids or []) if str(memory_id).strip()
        ))[:500]
        deletable_ids, blocked = [], []
        for memory_id in unique_ids:
            memory = self._find_memory(memory_id)
            allowed, policy_reason = self.memory_delete_policy(memory)
            if allowed:
                deletable_ids.append(memory_id)
            else:
                blocked.append({"id": memory_id, "reason": policy_reason})
        cleanup = self._relationship_cleanup_plan(deletable_ids)
        now = datetime.now()
        self._memory_delete_plans = {
            token: plan for token, plan in self._memory_delete_plans.items()
            if plan.get("expires_at") and plan["expires_at"] >= now
        }
        token = str(uuid.uuid4())
        expires_at = now + timedelta(minutes=10)
        self._memory_delete_plans[token] = {
            "ids": deletable_ids,
            "reason": str(reason or "user_delete"),
            "signature": self._memory_delete_signature(deletable_ids),
            "expires_at": expires_at,
        }
        return {
            "status": "confirmation_required",
            "confirmation_token": token,
            "expires_at": expires_at.isoformat(timespec="seconds"),
            "delete_count": len(deletable_ids),
            "memory_ids": deletable_ids,
            "titles": [str(self._find_memory(memory_id).get("title") or "Untitled")[:160] for memory_id in deletable_ids],
            "blocked": blocked,
            "cleared_reference_count": len(cleanup["cleared"]),
            "removed_manual_edge_count": len(cleanup["removed_manual_edges"]),
            "removed_manual_edges": cleanup["removed_manual_edges"],
            "rewired_links": cleanup["rewired"],
            "warnings": cleanup["warnings"],
        }

    def _consume_memory_delete_plan(self, confirmation_token, memory_ids, reason):
        plan = self._memory_delete_plans.pop(str(confirmation_token or ""), None)
        if not plan or plan["expires_at"] < datetime.now():
            raise ValueError("Deletion confirmation expired. Review the impact again.")
        expected = list(dict.fromkeys(str(memory_id) for memory_id in memory_ids))
        if plan["ids"] != expected or plan["reason"] != str(reason or "user_delete"):
            raise ValueError("Deletion scope changed. Review the impact again.")
        if plan["signature"] != self._memory_delete_signature(expected):
            raise ValueError("Memory relationships changed. Review the impact again.")
        return plan

    @memory_write_locked
    def delete_memory(self, memory_id, confirmation_token=None):
        target = self._find_memory(memory_id)
        allowed, reason = self.memory_delete_policy(target)
        if not allowed:
            print(f"Memory deletion blocked for {memory_id}: {reason}")
            return False

        result = self.delete_memories(
            [memory_id], reason="user_delete", confirmation_token=confirmation_token,
        )
        return result.get("status") == "success" and result.get("deleted_count") == 1

    @memory_write_locked
    def delete_memories(
        self, memory_ids, reason="user_bulk_delete", confirmation_token=None,
        require_all=False,
    ):
        unique_ids = []
        seen = set()
        for memory_id in memory_ids if isinstance(memory_ids, (list, tuple, set)) else []:
            memory_id = str(memory_id or "").strip()
            if memory_id and memory_id not in seen:
                unique_ids.append(memory_id)
                seen.add(memory_id)
        unique_ids = unique_ids[:500]

        deletable_ids = []
        blocked = []
        for memory_id in unique_ids:
            memory = self._find_memory(memory_id)
            if not memory:
                blocked.append({"id": memory_id, "reason": "Memory not found"})
                continue
            allowed, policy_reason = self.memory_delete_policy(memory)
            if not allowed:
                blocked.append({"id": memory_id, "reason": policy_reason})
                continue
            deletable_ids.append(memory_id)

        if require_all and blocked:
            return {
                "status": "blocked",
                "message": "Every linked memory must be deletable before this cascade can continue.",
                "deleted_count": 0,
                "deleted_ids": [],
                "blocked": blocked,
            }
        if not deletable_ids:
            return {
                "status": "blocked" if blocked else "success",
                "message": blocked[0]["reason"] if blocked else "",
                "deleted_count": 0,
                "deleted_ids": [],
                "blocked": blocked,
            }

        cleanup = self._relationship_cleanup_plan(deletable_ids)
        needs_confirmation = bool(
            cleanup["cleared"] or cleanup["rewired"] or cleanup["warnings"]
            or cleanup["removed_manual_edges"]
        )
        confirmed = bool(confirmation_token)
        if confirmed:
            try:
                self._consume_memory_delete_plan(confirmation_token, deletable_ids, reason)
            except ValueError as exc:
                return {
                    "status": "confirmation_required",
                    "message": str(exc),
                    **self.preview_memory_deletion(deletable_ids, reason=reason),
                }
        elif needs_confirmation:
            return self.preview_memory_deletion(deletable_ids, reason=reason)

        deletable_set = set(deletable_ids)
        previous_memories = copy.deepcopy(self.memories)
        previous_deletions = self._record_memory_deletions(deletable_ids, reason=reason)
        if previous_deletions is None:
            return {
                "status": "error",
                "message": "Unable to persist memory deletion ledger",
                "deleted_count": 0,
                "deleted_ids": [],
                "blocked": blocked,
            }
        for memory_id, updated in cleanup["changes"].items():
            for index, memory in enumerate(self.memories):
                if str(memory.get("id")) == memory_id:
                    self.memories[index] = updated
                    break
        self.memories = [memory for memory in self.memories if str(memory.get("id")) not in deletable_set]
        if not self.save_memories():
            self.memories = previous_memories
            self._restore_memory_deletion_ledger(previous_deletions)
            return {
                "status": "error",
                "message": "Unable to persist memory deletion",
                "deleted_count": 0,
                "deleted_ids": [],
                "blocked": blocked,
            }
        if self.sqlite_store and self.sqlite_store.available:
            for memory_id in deletable_ids:
                deleted = (
                    self.sqlite_store.purge_memory(memory_id, reason=reason)
                    if confirmed else self.sqlite_store.mark_deleted(memory_id)
                )
                if not deleted:
                    # JSON plus the file tombstone remain authoritative and
                    # prevent resurrection. Surface the degraded secondary
                    # cleanup so maintenance can retry it later.
                    blocked.append({"id": memory_id, "reason": "SQLite cleanup pending"})
        self.ensure_memory_graph(force=True)
        return {
            "status": "success",
            "deleted_count": len(deletable_ids),
            "deleted_ids": deletable_ids,
            "blocked": blocked,
            "cleared_reference_count": len(cleanup["cleared"]),
            "removed_manual_edge_count": len(cleanup["removed_manual_edges"]),
            "removed_manual_edges": cleanup["removed_manual_edges"],
            "rewired_links": cleanup["rewired"],
            "warnings": cleanup["warnings"],
        }

    def _auto_memories_for_session(self, session_id):
        """Return automatic memories whose provenance is this exact chat session."""
        session_id = str(session_id or "").strip()
        if not session_id:
            return []

        matches = []
        for memory in self.memories:
            if self._memory_tier(memory) != "auto":
                continue
            if memory.get("is_locked") or memory.get("managed_slot"):
                continue
            source_detail = memory.get("source_detail")
            if not isinstance(source_detail, dict):
                continue
            if str(source_detail.get("chat_session_id") or "") == session_id:
                matches.append(memory)
        return matches

    def get_auto_memory_delete_preview(self, session_id):
        """Provide a small, display-safe preview before deleting a chat's auto memories."""
        preview = []
        for memory in self._auto_memories_for_session(session_id):
            content = self._normalize_text(memory.get("content"))
            preview.append({
                "id": memory.get("id"),
                "title": self._normalize_text(memory.get("title")) or "Untitled automatic memory",
                "content": content[:180] + ("..." if len(content) > 180 else "")
            })
        return preview

    def preview_auto_memory_deletion(self, session_ids, reason="session_delete"):
        session_ids = list(dict.fromkeys(
            str(session_id) for session_id in (session_ids or []) if session_id
        ))
        memories = []
        for session_id in session_ids:
            for memory in self._auto_memories_for_session(session_id):
                if memory not in memories:
                    memories.append(memory)
        memory_preview = self.preview_memory_deletion(
            [memory.get("id") for memory in memories], reason=reason,
        )
        return {
            "auto_memories": [
                {
                    "id": memory.get("id"),
                    "title": self._normalize_text(memory.get("title")) or "Untitled automatic memory",
                    "content": (
                        self._normalize_text(memory.get("content"))[:180]
                        + ("..." if len(self._normalize_text(memory.get("content"))) > 180 else "")
                    ),
                }
                for memory in memories
            ],
            "auto_memory_count": len(memories),
            "memory_preview": memory_preview,
            "confirmation_token": memory_preview.get("confirmation_token"),
        }

    def delete_auto_memories_for_session(self, session_id):
        """Delete only unlocked automatic memories explicitly linked to one chat session."""
        related = self._auto_memories_for_session(session_id)
        related_ids = {memory.get("id") for memory in related}
        if not related_ids:
            return {"deleted_count": 0}

        previous = self.memories
        previous_deletions = self._record_memory_deletions(related_ids, reason="session_delete")
        if previous_deletions is None:
            return {"deleted_count": 0, "error": "Unable to persist memory deletion ledger"}
        self.memories = [memory for memory in self.memories if memory.get("id") not in related_ids]
        if not self.save_memories():
            self.memories = previous
            self._restore_memory_deletion_ledger(previous_deletions)
            return {"deleted_count": 0, "error": "Unable to persist memory deletion"}
        if self.sqlite_store and self.sqlite_store.available:
            for memory_id in related_ids:
                self.sqlite_store.mark_deleted(memory_id)
        return {"deleted_count": len(related)}

    def _synchronize_relationship_update(self, memory_id, previous, current, updated_fields):
        by_id = {str(memory.get("id")): memory for memory in self.memories if memory.get("id")}
        memory_id = str(memory_id)
        if "supersedes" in updated_fields:
            before = {str(value) for value in (previous.get("supersedes") or [])}
            after = list(dict.fromkeys(str(value) for value in (current.get("supersedes") or [])))
            current["supersedes"] = after
            for related_id in before - set(after):
                related = by_id.get(related_id)
                if related and str(related.get("superseded_by") or "") == memory_id:
                    related["superseded_by"] = None
            for related_id in after:
                related = by_id.get(related_id)
                if related:
                    prior_newer_id = str(related.get("superseded_by") or "")
                    if prior_newer_id and prior_newer_id != memory_id and prior_newer_id in by_id:
                        by_id[prior_newer_id]["supersedes"] = [
                            value for value in (by_id[prior_newer_id].get("supersedes") or [])
                            if str(value) != related_id
                        ]
                    related["superseded_by"] = memory_id
        if "superseded_by" in updated_fields:
            before = str(previous.get("superseded_by") or "")
            after = str(current.get("superseded_by") or "")
            if before and before != after and before in by_id:
                by_id[before]["supersedes"] = [
                    value for value in (by_id[before].get("supersedes") or [])
                    if str(value) != memory_id
                ]
            if after and after in by_id:
                values = [str(value) for value in (by_id[after].get("supersedes") or [])]
                if memory_id not in values:
                    values.append(memory_id)
                by_id[after]["supersedes"] = values
        if "conflicts_with" in updated_fields:
            before = {str(value) for value in (previous.get("conflicts_with") or [])}
            after = set(str(value) for value in (current.get("conflicts_with") or []))
            current["conflicts_with"] = sorted(after)
            for related_id in before - after:
                related = by_id.get(related_id)
                if related:
                    related["conflicts_with"] = [
                        value for value in (related.get("conflicts_with") or [])
                        if str(value) != memory_id
                    ]
            for related_id in after:
                related = by_id.get(related_id)
                if related:
                    values = [str(value) for value in (related.get("conflicts_with") or [])]
                    if memory_id not in values:
                        values.append(memory_id)
                    related["conflicts_with"] = sorted(values)

    @memory_write_locked
    def update_memory(self, memory_id, updates):
        if not isinstance(updates, dict):
            return False
        for m in self.memories:
            if m["id"] == memory_id:
                previous_memories = copy.deepcopy(self.memories)
                previous = copy.deepcopy(m)
                allowed = {
                    "title", "content", "source", "is_locked", "is_important", "is_archived",
                    "category", "tags", "confidence", "importance_score", "expires_at",
                    "status", "supersedes", "conflicts_with", "superseded_by",
                    "memory_layer", "memory_kind", "retention_policy", "source_detail"
                }
                clean_updates = {k: v for k, v in updates.items() if k in allowed}
                if "memory_layer" in clean_updates:
                    requested_layer = str(clean_updates.get("memory_layer") or "").lower()
                    if requested_layer not in self.MEMORY_LAYER_ORDER:
                        clean_updates.pop("memory_layer", None)
                if "retention_policy" in clean_updates:
                    requested_policy = str(clean_updates.get("retention_policy") or "").lower()
                    if requested_policy not in self.RETENTION_POLICIES:
                        clean_updates.pop("retention_policy", None)
                managed_slot = str(m.get("managed_slot") or "")
                if managed_slot:
                    blocked = {"title", "content", "is_locked", "is_archived", "source", "category", "tags", "supersedes", "conflicts_with", "memory_layer", "memory_kind", "retention_policy"}
                    clean_updates = {k: v for k, v in clean_updates.items() if k not in blocked}
                    clean_updates["is_locked"] = True
                    if managed_slot.startswith("about_you:"):
                        clean_updates["source"] = "about_you"
                        clean_updates["memory_layer"] = "core"
                if clean_updates.get("is_archived") is True and not m.get("is_archived"):
                    m["archived_from_layer"] = self._memory_layer(m)
                m.update(clean_updates)
                if "is_archived" in clean_updates:
                    if clean_updates.get("is_archived"):
                        m["memory_layer"] = "archive"
                    elif m.get("memory_layer") == "archive":
                        restored = str(m.get("archived_from_layer") or "")
                        m["memory_layer"] = restored if restored in self.MEMORY_LAYER_ORDER and restored != "archive" else self._infer_memory_layer(m)
                if "content" in clean_updates and "tags" not in clean_updates:
                    m["category"] = self.infer_category(m.get("content", ""), m.get("title", ""), m.get("source", ""))
                    m["tags"] = self.infer_tags(m.get("content", ""), m.get("category"))
                self.normalize_memory(m)
                try:
                    self._synchronize_relationship_update(memory_id, previous, m, set(clean_updates))
                    self._validate_relationship_graph()
                except ValueError:
                    self.memories = previous_memories
                    raise
                if self.save_memories():
                    self.maybe_index_new_memories([m])
                    if not self._maintenance_running:
                        self.last_maintenance_result = self.run_memory_capacity_maintenance(
                            trigger="memory_updated",
                        )
                    return True
                self.memories = previous_memories
                return False
        return False

    def get_memories(self, include_archived=False):
        memories = self.memories
        if not include_archived:
            memories = [m for m in memories if not m.get("is_archived", False)]
        by_date = sorted(
            memories,
            key=lambda x: (x.get("date", ""), x.get("creation_time", "")),
            reverse=True,
        )
        return sorted(by_date, key=lambda x: self.MEMORY_LAYER_ORDER.get(self._memory_layer(x), 2))

    def _memory_capacity_snapshot(self):
        active = sum(1 for memory in self.memories if not memory.get("is_archived"))
        archived = len(self.memories) - active
        active_soft = max(1, int(self.settings.get("memory_active_soft_limit", 5000)))
        archive_soft = max(1, int(self.settings.get("memory_archive_soft_limit", 50000)))
        active_hard = max(active_soft, int(self.settings.get("memory_active_hard_limit", 6000)))
        archive_hard = max(archive_soft, int(self.settings.get("memory_archive_hard_limit", 60000)))
        warning_ratio = self._coerce_float(
            self.settings.get("memory_capacity_warning_ratio"),
            0.80,
            minimum=0.50,
            maximum=0.95,
        )
        active_warning = max(1, min(active_soft, int(active_hard * warning_ratio)))
        archive_warning = max(1, min(archive_soft, int(archive_hard * warning_ratio)))
        active_ratio = active / active_hard
        archive_ratio = archived / archive_hard
        if active >= active_hard or archived >= archive_hard:
            band = "hard"
        elif active >= active_soft or archived >= archive_soft:
            band = "auto"
        elif active >= active_warning or archived >= archive_warning:
            band = "warning"
        else:
            band = "normal"
        return {
            "active": active,
            "archived": archived,
            "active_soft_limit": active_soft,
            "archive_soft_limit": archive_soft,
            "active_hard_limit": active_hard,
            "archive_hard_limit": archive_hard,
            "active_warning_at": active_warning,
            "archive_warning_at": archive_warning,
            "warning_ratio": warning_ratio,
            "active_ratio": round(active_ratio, 4),
            "archive_ratio": round(archive_ratio, 4),
            "band": band,
        }

    def get_memory_maintenance_status(self):
        snapshot = self._memory_capacity_snapshot()
        compression_pressure = self._local_compression_pressure_state()
        pending = [
            dict(event) for event in self.memory_maintenance.get("events") or []
            if not event.get("acknowledged")
        ][-8:]
        return {
            "status": snapshot["band"],
            "capacity": snapshot,
            "auto_compact_enabled": bool(
                self.settings.get("memory_capacity_auto_compact_enabled", True)
            ),
            "hard_limit_forced": True,
            "cold_archive_enabled": bool(self.settings.get("memory_cold_archive_enabled", True)),
            "cold_archive_count": int(self.memory_maintenance.get("cold_archive_count") or 0),
            "ai_compression_enabled": bool(self.settings.get("memory_ai_compression_enabled", False)),
            "compression_pressure": compression_pressure,
            "pending_events": pending,
            "last_result": self.memory_maintenance.get("last_result"),
            "compression_engine": "deterministic_local",
            "token_usage": 0,
        }

    def _is_capacity_managed_memory(self, memory):
        managed_slot = str(memory.get("managed_slot") or "").strip()
        source = str(memory.get("source") or "").strip().lower()
        return bool(managed_slot) or source in {"about_you", "system_activity"}

    @memory_write_locked
    def _cold_store_memories(self, memories, reason, force=False):
        records = [
            memory for memory in memories
            if isinstance(memory, dict) and memory.get("id") and memory in self.memories
        ]
        if not records or (
            not force and not self.settings.get("memory_cold_archive_enabled", True)
        ):
            return {"moved_count": 0, "memory_ids": []}
        os.makedirs(os.path.dirname(self.cold_archive_path), exist_ok=True)
        moved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            bundle_size = max(2, min(2000, int(self.settings.get("memory_cold_archive_batch_size", 200))))
        except (TypeError, ValueError):
            bundle_size = 200
        bundle_id = None
        moved_ids = []
        archive_start = os.path.getsize(self.cold_archive_path) if os.path.exists(self.cold_archive_path) else 0
        with open(self.cold_archive_path, "a", encoding="utf-8") as archive_file:
            for index, memory in enumerate(records):
                if index % bundle_size == 0:
                    bundle_id = str(uuid.uuid4())
                memory_id = str(memory.get("id"))
                wrapper = {
                    "version": 1,
                    "bundle_id": bundle_id,
                    "cold_archived_at": moved_at,
                    "reason": str(reason or "capacity_maintenance"),
                    "summary": {
                        "id": memory_id,
                        "title": str(memory.get("title") or "Untitled")[:180],
                        "date": str(memory.get("date") or "")[:10],
                        "layer": self._memory_layer(memory),
                        "project_id": str(memory.get("project_id") or ""),
                        "parent_memory_id": memory.get("parent_memory_id"),
                    },
                    "memory": memory,
                }
                archive_file.write(json.dumps(wrapper, ensure_ascii=False) + "\n")
                moved_ids.append(memory_id)
            archive_file.flush()
            os.fsync(archive_file.fileno())

        previous_ledger = dict(self.deleted_memory_ids)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        for memory_id in moved_ids:
            self.deleted_memory_ids[memory_id] = {
                "deleted_at": now,
                "reason": "cold_archive",
            }
        if not self.save_memory_deletions():
            self.deleted_memory_ids = previous_ledger
            try:
                with open(self.cold_archive_path, "r+b") as archive_file:
                    archive_file.truncate(archive_start)
            except OSError:
                pass
            return {"moved_count": 0, "memory_ids": [], "error": "deletion_ledger_failed"}

        moved_id_set = set(moved_ids)
        previous_memories = copy.deepcopy(self.memories)
        self.memories = [
            memory for memory in self.memories
            if str(memory.get("id") or "") not in moved_id_set
        ]
        if not self.save_memories():
            self.memories = previous_memories
            self._restore_memory_deletion_ledger(previous_ledger)
            try:
                with open(self.cold_archive_path, "r+b") as archive_file:
                    archive_file.truncate(archive_start)
            except OSError:
                pass
            return {"moved_count": 0, "memory_ids": [], "error": "memory_store_failed"}
        if self.sqlite_store and self.sqlite_store.available:
            for memory_id in moved_ids:
                self.sqlite_store.mark_deleted(memory_id)
        self.memory_maintenance["cold_archive_count"] = (
            int(self.memory_maintenance.get("cold_archive_count") or 0) + len(moved_ids)
        )
        self.save_memory_maintenance_state()
        return {"moved_count": len(moved_ids), "memory_ids": moved_ids}

    def list_cold_archive_memories(self, limit=50, search=""):
        try:
            limit = max(1, min(500, int(limit)))
        except (TypeError, ValueError):
            limit = 50
        query = str(search or "").strip().lower()
        if not os.path.exists(self.cold_archive_path):
            return []
        items = []
        try:
            with open(self.cold_archive_path, "r", encoding="utf-8") as archive_file:
                for line in archive_file:
                    try:
                        wrapper = json.loads(line)
                    except (TypeError, ValueError):
                        continue
                    summary = wrapper.get("summary") if isinstance(wrapper, dict) else None
                    if not isinstance(summary, dict):
                        continue
                    haystack = " ".join([
                        str(summary.get("title") or ""),
                        str(summary.get("date") or ""),
                        str(summary.get("project_id") or ""),
                    ]).lower()
                    if query and query not in haystack:
                        continue
                    items.append({
                        **summary,
                        "cold_archived_at": wrapper.get("cold_archived_at"),
                        "reason": wrapper.get("reason"),
                    })
        except OSError as exc:
            print(f"Error reading cold memory archive: {exc}")
            return []
        return items[-limit:][::-1]

    @memory_write_locked
    def restore_cold_archive_memory(self, memory_id):
        memory_id = str(memory_id or "").strip()
        if not memory_id or not os.path.exists(self.cold_archive_path):
            return None
        restored = None
        kept_lines = []
        try:
            with open(self.cold_archive_path, "r", encoding="utf-8") as archive_file:
                for line in archive_file:
                    try:
                        wrapper = json.loads(line)
                    except (TypeError, ValueError):
                        kept_lines.append(line)
                        continue
                    summary = wrapper.get("summary") if isinstance(wrapper, dict) else {}
                    if restored is None and str(summary.get("id") or "") == memory_id:
                        candidate = wrapper.get("memory")
                        if isinstance(candidate, dict):
                            restored = candidate
                            continue
                    kept_lines.append(line)
            if restored is None:
                return None
        except OSError as exc:
            print(f"Error restoring cold memory archive: {exc}")
            return None

        if self._find_memory(memory_id):
            print(f"Cold restore blocked because memory {memory_id} already exists")
            return None
        if not self.revive_deleted_memory(memory_id):
            return None
        restored["is_archived"] = True
        restored["memory_layer"] = "archive"
        restored["status"] = "archived"
        restored["cold_restored_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        restored, _ = self.normalize_memory(restored)
        self.memories.append(restored)
        if not self.save_memories():
            self.memories = [memory for memory in self.memories if memory is not restored]
            self._record_memory_deletions([memory_id], reason="cold_archive_restore_rollback")
            if self.sqlite_store and self.sqlite_store.available:
                self.sqlite_store.mark_deleted(memory_id)
            return None
        try:
            temp_path = self.cold_archive_path + ".tmp"
            with open(temp_path, "w", encoding="utf-8") as archive_file:
                archive_file.writelines(kept_lines)
                archive_file.flush()
                os.fsync(archive_file.fileno())
            os.replace(temp_path, self.cold_archive_path)
        except OSError as exc:
            try:
                if os.path.exists(self.cold_archive_path + ".tmp"):
                    os.remove(self.cold_archive_path + ".tmp")
            except OSError:
                pass
            self.memories = [memory for memory in self.memories if memory is not restored]
            rollback_recorded = self._record_memory_deletions(
                [memory_id], reason="cold_archive_restore_rollback",
            )
            if rollback_recorded is not None:
                self.save_memories()
                if self.sqlite_store and self.sqlite_store.available:
                    self.sqlite_store.mark_deleted(memory_id)
            else:
                self.memories.append(restored)
            print(f"Error finalizing cold memory restore: {exc}")
            return restored if rollback_recorded is None else None
        self.memory_maintenance["cold_archive_count"] = max(
            0,
            int(self.memory_maintenance.get("cold_archive_count") or 0) - 1,
        )
        self.save_memory_maintenance_state()
        self.last_maintenance_result = self.run_memory_capacity_maintenance(
            trigger="cold_restore",
        )
        return restored

    def _cold_archive_to_target(self, target, force=False, reason="capacity_archive"):
        archived = [memory for memory in self.memories if memory.get("is_archived")]
        overflow = max(0, len(archived) - max(0, int(target)))
        if not overflow:
            return {"moved_count": 0, "memory_ids": []}
        retention_rank = {"temporary": 0, "compressible": 1, "durable": 2, "permanent": 3}
        candidates = [
            memory for memory in archived
            if not self._is_capacity_managed_memory(memory)
            and self._infer_retention_policy(memory) != "permanent"
            and (
                force
                or self._infer_retention_policy(memory) in {"temporary", "compressible"}
            )
        ]
        candidates.sort(key=lambda memory: (
            0 if memory.get("parent_memory_id") or memory.get("merged_into") else 1,
            retention_rank.get(self._infer_retention_policy(memory), 2),
            1 if memory.get("is_important") else 0,
            1 if memory.get("is_locked") else 0,
            str(memory.get("date") or ""),
            self._coerce_float(memory.get("importance_score"), 0.4),
        ))
        return self._cold_store_memories(candidates[:overflow], reason=reason, force=force)

    def _cold_active_to_target(self, target, reason="active_hard_overflow"):
        active = [memory for memory in self.memories if not memory.get("is_archived")]
        overflow = max(0, len(active) - max(0, int(target)))
        if not overflow:
            return {"moved_count": 0, "memory_ids": []}
        retention_rank = {"temporary": 0, "compressible": 1, "durable": 2, "permanent": 3}
        candidates = [
            memory for memory in active
            if not self._is_capacity_managed_memory(memory)
            and self._infer_retention_policy(memory) != "permanent"
        ]
        candidates.sort(key=lambda memory: (
            retention_rank.get(self._infer_retention_policy(memory), 2),
            1 if memory.get("is_important") else 0,
            1 if memory.get("is_locked") else 0,
            self._coerce_float(memory.get("importance_score"), 0.4),
            self._coerce_float(memory.get("base_priority_score"), 0.5),
            str(memory.get("date") or ""),
        ))
        return self._cold_store_memories(candidates[:overflow], reason=reason, force=True)

    def run_memory_capacity_maintenance(self, trigger="manual"):
        with self._maintenance_lock:
            if self._maintenance_running:
                return {"status": "already_running", "notify_user": False}
            self._maintenance_running = True
            try:
                before = self._memory_capacity_snapshot()
                previous_band = str(self.memory_maintenance.get("last_band") or "normal")
                auto_enabled = bool(
                    self.settings.get("memory_capacity_auto_compact_enabled", True)
                )
                forced = before["band"] == "hard"
                merged_count = 0
                summary_count = 0
                cold_count = 0
                merge_result = {}

                if before["active"] >= before["active_hard_limit"]:
                    target = max(1, int(before["active_soft_limit"] * 0.90))
                    merge_result = self.merge_memories(
                        days=0,
                        force=True,
                        target_active=target,
                        reason="hard_limit",
                    )
                    merged_count += int(merge_result.get("merged_count") or 0)
                    summary_count += int(merge_result.get("summary_count") or 0)
                    cold_result = self._cold_active_to_target(target, reason="active_hard_limit")
                    cold_count += int(cold_result.get("moved_count") or 0)
                elif before["active"] >= before["active_soft_limit"] and auto_enabled:
                    target = max(1, int(before["active_soft_limit"] * 0.90))
                    merge_result = self.merge_memories(
                        days=self.settings.get("memory_compression_age_days", 30),
                        force=False,
                        target_active=target,
                        reason="soft_limit",
                    )
                    merged_count += int(merge_result.get("merged_count") or 0)
                    summary_count += int(merge_result.get("summary_count") or 0)

                middle = self._memory_capacity_snapshot()
                if middle["archived"] >= middle["archive_hard_limit"]:
                    forced = True
                    target = max(0, int(middle["archive_soft_limit"] * 0.90))
                    cold_result = self._cold_archive_to_target(
                        target,
                        force=True,
                        reason="archive_hard_limit",
                    )
                    cold_count += int(cold_result.get("moved_count") or 0)
                elif middle["archived"] >= middle["archive_soft_limit"] and auto_enabled:
                    target = max(0, int(middle["archive_soft_limit"] * 0.90))
                    cold_result = self._cold_archive_to_target(
                        target,
                        force=False,
                        reason="archive_soft_limit",
                    )
                    cold_count += int(cold_result.get("moved_count") or 0)

                after = self._memory_capacity_snapshot()
                changed_count = merged_count + cold_count
                compression_pressure = self._local_compression_pressure_state()
                ai_recommended = bool(
                    after["band"] in {"warning", "auto"}
                    and (
                        compression_pressure.get("all_at_generation_limit")
                        or merge_result.get("all_candidates_at_generation_limit")
                    )
                )
                ai_offer_signature = (
                    f"{after['band']}:{after['active'] // max(10, int(after['active_soft_limit'] * 0.02))}:"
                    f"{compression_pressure.get('generation_capped_count', 0) // 10}"
                ) if ai_recommended else ""
                offer_ai_now = bool(
                    ai_recommended
                    and ai_offer_signature != str(self.memory_maintenance.get("last_ai_offer_signature") or "")
                )
                notify_user = False
                event = None
                if forced:
                    notify_user = True
                    if after["active"] >= after["active_hard_limit"] or after["archived"] >= after["archive_hard_limit"]:
                        event = self._record_memory_maintenance_event(
                            "hard_limit_blocked",
                            "critical",
                            "Memory reached its hard limit. Forced compaction ran, but protected managed memories still prevent the store from returning below the limit.",
                            {"before": before, "after": after, "merged_count": merged_count, "cold_count": cold_count},
                        )
                    else:
                        event = self._record_memory_maintenance_event(
                            "hard_limit_forced_compaction",
                            "critical",
                            f"Memory reached its hard limit. Saya forcibly compacted {merged_count} memories and moved {cold_count} full originals to recoverable cold storage.",
                            {"before": before, "after": after, "summary_count": summary_count},
                        )
                elif offer_ai_now:
                    notify_user = True
                    event = self._record_memory_maintenance_event(
                        "ai_compression_recommended",
                        "warning",
                        "Memory pressure remains, but every safely repeatable local summary has reached its generation limit. Choose a scoped DeepSeek compression plan or skip AI compression for now.",
                        {
                            "before": before,
                            "after": after,
                            "ai_recommended": True,
                            "ai_compression_enabled": bool(
                                self.settings.get("memory_ai_compression_enabled", False)
                            ),
                            "compression_pressure": compression_pressure,
                            "local_result": merge_result,
                            "choices": ["review_ai_compression", "skip_ai_compression"],
                        },
                    )
                    self.memory_maintenance["last_ai_offer_signature"] = ai_offer_signature
                elif changed_count:
                    notify_user = True
                    event = self._record_memory_maintenance_event(
                        "automatic_capacity_compaction",
                        "warning",
                        f"Memory approached its capacity target. Saya automatically compacted {merged_count} memories and moved {cold_count} archived originals to recoverable cold storage.",
                        {"before": before, "after": after, "summary_count": summary_count},
                    )
                elif before["band"] in {"warning", "auto"} and previous_band == "normal":
                    notify_user = True
                    event = self._record_memory_maintenance_event(
                        "capacity_warning",
                        "warning",
                        "Memory is approaching its automatic-compaction threshold. Saya will preserve permanent memories and notify you when capacity maintenance runs.",
                        {"before": before, "after": after},
                    )

                result = {
                    "status": (
                        "forced_compaction" if forced
                        else "auto_compaction" if changed_count
                        else "warning" if before["band"] in {"warning", "auto"}
                        else "healthy"
                    ),
                    "trigger": str(trigger or "manual"),
                    "notify_user": notify_user,
                    "event": event,
                    "before": before,
                    "after": after,
                    "merged_count": merged_count,
                    "summary_count": summary_count,
                    "cold_archived_count": cold_count,
                    "ai_compression_recommended": ai_recommended,
                    "compression_pressure": compression_pressure,
                    "compression_engine": "deterministic_local",
                    "token_usage": 0,
                }
                self.memory_maintenance["last_band"] = after["band"]
                if after["band"] == "normal":
                    self.memory_maintenance["last_ai_offer_signature"] = None
                self.memory_maintenance["last_run_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self.memory_maintenance["last_result"] = result
                self.save_memory_maintenance_state()
                self.last_maintenance_result = result
                return result
            finally:
                self._maintenance_running = False

    def get_memory_stats(self, context_window_tokens=None):
        counts = {layer: 0 for layer in self.MEMORY_LAYER_ORDER}
        over_content_limit = {layer: 0 for layer in self.MEMORY_LAYER_ORDER}
        active = 0
        archived = 0
        total_content_chars = 0
        for memory in self.memories:
            layer = self._memory_layer(memory)
            counts[layer] = counts.get(layer, 0) + 1
            content_chars = len(str(memory.get("content", "")))
            total_content_chars += content_chars
            if layer != "archive" and content_chars > self.memory_content_limit(memory.get("memory_kind", ""), layer):
                over_content_limit[layer] = over_content_limit.get(layer, 0) + 1
            if layer == "archive" or memory.get("is_archived"):
                archived += 1
            else:
                active += 1
        capacity = self._memory_capacity_snapshot()
        active_limit = capacity["active_soft_limit"]
        archive_limit = capacity["archive_soft_limit"]
        warnings = []
        if active >= active_limit:
            warnings.append("active_soft_limit_reached")
        if archived >= archive_limit:
            warnings.append("archive_soft_limit_reached")
        if active >= capacity["active_hard_limit"]:
            warnings.append("active_hard_limit_reached")
        if archived >= capacity["archive_hard_limit"]:
            warnings.append("archive_hard_limit_reached")
        if counts.get("core", 0) > int(self.settings.get("memory_core_limit", 10)):
            warnings.append("core_limit_exceeded")
        if sum(over_content_limit.values()):
            warnings.append("existing_content_over_limit")
        return {
            "schema_version": 3,
            "total": len(self.memories),
            "active": active,
            "archived": archived,
            "layers": counts,
            "over_content_limit": over_content_limit,
            "total_content_chars": total_content_chars,
            "active_soft_limit": active_limit,
            "archive_soft_limit": archive_limit,
            "active_hard_limit": capacity["active_hard_limit"],
            "archive_hard_limit": capacity["archive_hard_limit"],
            "active_warning_at": capacity["active_warning_at"],
            "archive_warning_at": capacity["archive_warning_at"],
            "prompt_budget_tokens": self._memory_prompt_budget(context_window_tokens),
            "content_limits": {
                "core": self.memory_content_limit("", "core"),
                "semantic": self.memory_content_limit("", "semantic"),
                "episodic": self.memory_content_limit("", "episodic"),
                "knowledge_simple": self.memory_content_limit("knowledge_extract_simple", "semantic"),
                "knowledge_complex": self.memory_content_limit("knowledge_extract_complex", "semantic"),
            },
            "warnings": warnings,
            "sqlite": self.get_sqlite_store_status(),
            "embedding": self.get_embedding_status(),
            "maintenance": self.get_memory_maintenance_status(),
        }

    def _is_expired(self, memory):
        expires_at = memory.get("expires_at")
        if not expires_at:
            return False
        try:
            return datetime.strptime(str(expires_at)[:10], "%Y-%m-%d").date() < datetime.now().date()
        except (TypeError, ValueError):
            return False

    def _memory_text(self, memory):
        tags = memory.get("tags", [])
        tag_text = " ".join(tags if isinstance(tags, list) else [])
        return " ".join([
            str(memory.get("title", "")),
            str(memory.get("content", "")),
            tag_text,
            str(memory.get("category", "")),
        ])

    def _vector_terms(self, text):
        normalized = self._normalize_text(text).lower()
        if not normalized:
            return []

        terms = re.findall(r"[a-z0-9_]{2,}", normalized)
        cjk_runs = re.findall(r"[\u3400-\u9fff]+", normalized)
        for run in cjk_runs:
            terms.extend(list(run))
            for size in (2, 3):
                if len(run) >= size:
                    terms.extend(run[i:i + size] for i in range(len(run) - size + 1))

        compact = re.sub(r"\s+", "", normalized)
        compact = compact[:6000]
        for size in (3, 4):
            if len(compact) >= size:
                terms.extend(compact[i:i + size] for i in range(len(compact) - size + 1))
        return terms

    def _hash_vector(self, text):
        vector = {}
        for term in self._vector_terms(text):
            digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
            value = int.from_bytes(digest, "big")
            bucket = value % self.VECTOR_DIMENSIONS
            sign = 1.0 if (value >> 8) & 1 else -1.0
            weight = 1.0
            if len(term) >= 4:
                weight = 1.25
            vector[bucket] = vector.get(bucket, 0.0) + sign * weight
        norm = sum(v * v for v in vector.values()) ** 0.5
        if not norm:
            return {}
        return {bucket: value / norm for bucket, value in vector.items()}

    def _memory_vector(self, memory):
        text = self._memory_text(memory)
        cache_key = (
            memory.get("id"),
            hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        )
        cached = self._vector_cache.get(cache_key)
        if cached is not None:
            return cached
        vector = self._hash_vector(text)
        self._vector_cache[cache_key] = vector
        return vector

    def _cosine_from_vectors(self, left, right):
        if not left or not right:
            return 0.0
        if len(left) > len(right):
            left, right = right, left
        score = sum(value * right.get(bucket, 0.0) for bucket, value in left.items())
        return max(0.0, min(1.0, score))

    def semantic_memory_score(self, memory, query):
        if not self.settings.get("local_vector_retrieval_enabled", True):
            return 0.0
        query = str(query or "").strip()
        if not query:
            return 0.0
        return self._cosine_from_vectors(
            self._hash_vector(query),
            self._memory_vector(memory)
        )

    def score_memory(self, memory, query, project_id=None, embedding_score=None):
        memory_project_id = str(memory.get("project_id") or "")
        active_project_id = str(project_id or "")
        relevance = self._query_relevance_score(memory, query, embedding_score=embedding_score)
        confidence = self._coerce_float(memory.get("confidence"), 0.75)
        importance = self._coerce_float(memory.get("importance_score"), 0.4)
        recency = self._recency_score(memory)
        access = self._access_score(memory)
        layer = self._memory_layer(memory)
        layer_bias = {"core": 1.0, "semantic": 0.65, "episodic": 0.35, "archive": 0.0}.get(layer, 0.4)
        score = (
            relevance * 0.58
            + importance * 0.13
            + confidence * 0.10
            + recency * 0.07
            + access * 0.04
            + layer_bias * 0.08
        )
        if active_project_id and memory_project_id == active_project_id:
            score += 0.12
        elif memory_project_id and memory_project_id != active_project_id:
            score -= 0.18
        if memory.get("is_locked"):
            score += 0.025
        if memory.get("is_important"):
            score += 0.035
        if memory.get("conflicts_with"):
            score -= 0.10
        if memory.get("superseded_by") or self._is_expired(memory):
            score -= 0.25
        return round(max(0.0, min(1.0, score)), 4)

    def _memory_allowed_for_project(self, memory, project_id=None):
        memory_project_id = str(memory.get("project_id") or "")
        active_project_id = str(project_id or "")
        if not memory_project_id:
            return True
        return bool(active_project_id and memory_project_id == active_project_id)

    def search_memories(self, query="", category=None, memory_layer=None, include_archived=False, limit=50):
        query = str(query or "").strip()
        memory_layer = str(memory_layer or "").strip().lower()
        if memory_layer not in self.MEMORY_LAYER_ORDER:
            memory_layer = ""
        results = []
        source_memories = self.get_memories(include_archived=include_archived)
        if (
            query
            and not include_archived
            and self.settings.get("sqlite_fts_enabled", True)
            and self.sqlite_store
            and self.sqlite_store.available
        ):
            fts_ids = self.sqlite_store.search_candidate_ids(
                query,
                limit=max(100, min(2000, int(limit or 50) * 4)),
            )
            if fts_ids:
                fts_id_set = set(fts_ids)
                source_memories = [
                    memory for memory in source_memories
                    if str(memory.get("id")) in fts_id_set
                ]
        for memory in source_memories:
            if category and memory.get("category") != category:
                continue
            if memory_layer and self._memory_layer(memory) != memory_layer:
                continue
            if self._is_expired(memory) and not include_archived:
                continue
            semantic_score = self.semantic_memory_score(memory, query) if query else 0.0
            if query:
                query_tokens = self._tokenize(query)
                memory_text = self._memory_text(memory).lower()
                memory_tokens = self._tokenize(memory_text)
                has_text_match = query.lower() in memory_text or bool(query_tokens & memory_tokens)
                if not has_text_match and semantic_score < self.VECTOR_MATCH_THRESHOLD:
                    continue
            score = self.score_memory(memory, query) if query else 0
            item = dict(memory)
            item["relevance_score"] = score
            item["memory_tier"] = self._memory_tier(memory)
            item["priority_score"] = self.memory_priority_score(memory, query)
            item["semantic_score"] = round(semantic_score, 4)
            results.append(item)

        if query:
            results.sort(key=lambda m: (m.get("relevance_score", 0), m.get("date", "")), reverse=True)
        else:
            results.sort(key=lambda m: (m.get("date", ""), m.get("creation_time", "")), reverse=True)
            results.sort(key=lambda m: self.MEMORY_LAYER_ORDER.get(self._memory_layer(m), 2))

        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 50
        return results[:limit]

    def get_relevant_memories(self, user_input, mode="normal", limit=None, mark_accessed=False, project_id=None, exclude_daily_activity=False):
        if limit is None:
            limit = self.settings.get("relevant_memory_count", 8)
        try:
            limit = max(1, min(50, int(limit)))
        except (TypeError, ValueError):
            limit = 8

        if not self.settings.get("layered_memory_enabled", True):
            return self._get_relevant_memories_legacy(
                user_input,
                mode=mode,
                limit=limit,
                mark_accessed=mark_accessed,
                project_id=project_id,
                exclude_daily_activity=exclude_daily_activity,
            )

        active_by_id = {
            str(memory.get("id")): memory
            for memory in self.memories
            if memory.get("id") and not memory.get("is_archived") and not self._is_expired(memory)
        }
        candidates = [
            m for m in self.memories
            if not m.get("is_archived")
            and not self._is_expired(m)
            and self._memory_allowed_for_project(m, project_id=project_id)
            and not (
                exclude_daily_activity
                and str(m.get("id")) == self.DAILY_ACTIVITY_MEMORY_ID
            )
            and not (
                m.get("superseded_by")
                and str(m.get("superseded_by")) in active_by_id
            )
        ]

        try:
            core_limit = max(0, min(100, int(self.settings.get("memory_core_limit", 10))))
        except (TypeError, ValueError):
            core_limit = 10
        try:
            pool_limit = max(limit, min(1000, int(self.settings.get("memory_candidate_pool_size", 80))))
        except (TypeError, ValueError):
            pool_limit = 80
        fts_candidate_ids = set()
        embedding_scores = {}
        if (
            self.settings.get("sqlite_fts_enabled", True)
            and self.sqlite_store
            and self.sqlite_store.available
        ):
            fts_ids = self.sqlite_store.search_candidate_ids(
                user_input,
                limit=pool_limit,
                project_id=project_id,
            )
            if fts_ids:
                fts_candidate_ids = set(fts_ids)
        if (
            self.settings.get("embedding_enabled", False)
            and self.embedding_provider
            and self.sqlite_store
            and self.sqlite_store.available
        ):
            try:
                provider_key = self.embedding_provider.key
                indexed = self.sqlite_store.embedding_status(provider_key).get("count", 0)
                if indexed:
                    query_vectors = self.embedding_provider.embed([str(user_input or "")])
                    if query_vectors:
                        vector_limit = max(
                            limit,
                            min(1000, int(self.settings.get("embedding_candidate_count", 80))),
                        )
                        vector_results = self.sqlite_store.search_embedding(
                            query_vectors[0],
                            provider_key,
                            limit=vector_limit,
                            min_similarity=self.settings.get("embedding_min_similarity", 0.30),
                            project_id=project_id,
                        )
                        embedding_scores = {
                            str(item.get("id")): float(item.get("score", 0.0))
                            for item in vector_results
                        }
                self.embedding_last_error = ""
            except Exception as exc:
                self.embedding_last_error = str(exc)

        retrieval_ids = set(fts_candidate_ids) | set(embedding_scores)
        if retrieval_ids:
            safety_candidates = sorted(
                (
                    memory for memory in candidates
                    if self._memory_layer(memory) != "core"
                    and (memory.get("is_locked") or memory.get("is_important"))
                ),
                key=lambda memory: (
                    self._coerce_float(memory.get("importance_score"), 0.4),
                    memory.get("date", ""),
                ),
                reverse=True,
            )[:max(10, limit)]
            retrieval_ids.update(str(memory.get("id")) for memory in safety_candidates)
            candidates = [
                memory for memory in candidates
                if self._memory_layer(memory) == "core"
                or str(memory.get("id")) in retrieval_ids
            ]
        threshold = self._coerce_float(
            self.settings.get("memory_retrieval_threshold"),
            self.VECTOR_MATCH_THRESHOLD,
        )
        query_text = str(user_input or "").strip().lower()
        query_tokens = self._tokenize(query_text)
        core = []
        relevant = []
        for memory in candidates:
            item = dict(memory)
            item["_layer"] = self._memory_layer(memory)
            item["_tier"] = self._memory_tier(memory)
            embedding_score = embedding_scores.get(str(memory.get("id")))
            item["_embedding_score"] = embedding_score
            semantic_score = self.semantic_memory_score(memory, user_input)
            item["_semantic_score"] = round(semantic_score, 4)
            item["_score"] = self.score_memory(
                memory,
                user_input,
                project_id=project_id,
                embedding_score=embedding_score,
            )
            item["_priority_score"] = self.memory_priority_score(
                memory,
                user_input,
                embedding_score=embedding_score,
            )
            if item["_layer"] == "core":
                core.append(item)
                continue
            memory_text = self._memory_text(memory).lower()
            memory_tokens = self._tokenize(memory_text)
            has_query_signal = bool(
                query_text and (
                    query_text in memory_text
                    or bool(query_tokens & memory_tokens)
                    or semantic_score >= threshold
                    or str(memory.get("id")) in fts_candidate_ids
                    or embedding_score is not None
                )
            )
            if has_query_signal:
                relevant.append(item)

        core.sort(key=lambda m: (m.get("_score", 0), m.get("date", "")), reverse=True)
        relevant.sort(key=lambda m: (m.get("_score", 0), m.get("date", "")), reverse=True)
        relevant = relevant[:pool_limit]
        relevant_limit = max(limit, 20) if mode == "deep" else limit
        selected = core[:core_limit] + relevant[:relevant_limit]
        selected.sort(
            key=lambda m: (
                -self.MEMORY_LAYER_ORDER.get(m.get("_layer", "semantic"), 2),
                m.get("_score", 0),
                m.get("date", ""),
            ),
            reverse=True,
        )

        if mark_accessed and selected:
            selected_ids = {m.get("id") for m in selected}
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            changed = False
            for memory in self.memories:
                if memory.get("id") in selected_ids:
                    memory["access_count"] = int(memory.get("access_count", 0) or 0) + 1
                    memory["last_accessed"] = now
                    changed = True
            if changed:
                self._access_metadata_dirty = True
                self._dirty_access_ids.update(selected_ids)
                try:
                    flush_seconds = max(10, int(self.settings.get("memory_access_flush_seconds", 120)))
                except (TypeError, ValueError):
                    flush_seconds = 120
                elapsed = (datetime.now() - self._last_access_flush_at).total_seconds()
                if elapsed >= flush_seconds:
                    dirty_memories = [
                        memory for memory in self.memories
                        if memory.get("id") in self._dirty_access_ids
                    ]
                    self.save_memories(
                        invalidate_vectors=False,
                        sqlite_subset=dirty_memories,
                        graph_dirty=False,
                    )

        return selected

    def _get_relevant_memories_legacy(self, user_input, mode="normal", limit=8, mark_accessed=False, project_id=None, exclude_daily_activity=False):
        candidates = [
            m for m in self.memories
            if not m.get("is_archived")
            and not self._is_expired(m)
            and self._memory_allowed_for_project(m, project_id=project_id)
            and not (
                exclude_daily_activity
                and str(m.get("id")) == self.DAILY_ACTIVITY_MEMORY_ID
            )
        ]
        scored = []
        for memory in candidates:
            item = dict(memory)
            item["_score"] = self.score_memory(memory, user_input, project_id=project_id)
            item["_layer"] = self._memory_layer(memory)
            scored.append(item)
        scored.sort(key=lambda m: (m.get("_score", 0), m.get("date", "")), reverse=True)
        selected = scored[:(max(limit, 20) if mode == "deep" else limit)]
        if mark_accessed and selected:
            selected_ids = {m.get("id") for m in selected}
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            for memory in self.memories:
                if memory.get("id") in selected_ids:
                    memory["access_count"] = int(memory.get("access_count", 0) or 0) + 1
                    memory["last_accessed"] = now
            self._access_metadata_dirty = True
            self._dirty_access_ids.update(selected_ids)
        return selected

    def estimate_token_count(self, text):
        text = str(text or "")
        cjk_count = len(re.findall(r"[\u3400-\u9fff]", text))
        non_cjk = re.sub(r"[\u3400-\u9fff]", "", text)
        latin_like = len(re.findall(r"[A-Za-z0-9_]+|[^\sA-Za-z0-9_]", non_cjk))
        return max(1, cjk_count + int(len(non_cjk) / 4) + int(latin_like * 0.25))

    def _truncate_to_token_budget(self, text, max_tokens):
        text = str(text or "").strip()
        try:
            max_tokens = max(1, int(max_tokens))
        except (TypeError, ValueError):
            max_tokens = 700
        if self.estimate_token_count(text) <= max_tokens:
            return text
        low, high = 1, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self.estimate_token_count(text[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return text[:low].rstrip() + "…"

    def _query_focused_excerpt(self, content, query, max_tokens):
        content = str(content or "").strip()
        if self.estimate_token_count(content) <= max_tokens:
            return content
        lowered = content.lower()
        query_text = str(query or "").strip().lower()
        anchors = []
        if len(query_text) >= 2:
            anchors.append(query_text)
        anchors.extend(re.findall(r"[a-z0-9_]{2,}", query_text))
        cjk = "".join(re.findall(r"[\u3400-\u9fff]", query_text))
        anchors.extend(cjk[index:index + 2] for index in range(max(0, len(cjk) - 1)))
        positions = [lowered.find(anchor) for anchor in anchors if anchor and lowered.find(anchor) >= 0]
        if not positions:
            return self._truncate_to_token_budget(content, max_tokens)
        anchor_position = min(positions)
        approximate_chars = max(80, int(max_tokens * 1.8))
        start = max(0, anchor_position - approximate_chars // 3)
        excerpt = content[start:start + approximate_chars]
        excerpt = self._truncate_to_token_budget(excerpt, max_tokens)
        if start > 0:
            excerpt = "…" + excerpt
        if start + approximate_chars < len(content) and not excerpt.endswith("…"):
            excerpt += "…"
        return excerpt

    def format_memory_for_prompt(self, memory, include_metadata=True, max_tokens=None, query=""):
        try:
            max_tokens = max(64, int(max_tokens or self.settings.get("memory_item_budget_tokens", 700)))
        except (TypeError, ValueError):
            max_tokens = 700
        metadata_reserve = 90 if include_metadata else 30
        content = self._query_focused_excerpt(
            memory.get("content", ""),
            query,
            max(32, max_tokens - metadata_reserve),
        )

        if not include_metadata:
            return f"- {memory.get('title', 'Untitled')}: {content}"

        tags = memory.get("tags", [])
        tag_text = ", ".join(tags[:6]) if isinstance(tags, list) and tags else "none"
        flags = []
        if memory.get("is_locked"):
            flags.append("locked")
        if memory.get("is_important"):
            flags.append("starred")
        if memory.get("supersedes"):
            flags.append("updates older memory")
        if memory.get("conflicts_with"):
            flags.append("may be superseded/conflicted")
        if memory.get("_graph_expanded"):
            relation_text = "/".join(str(value) for value in list(memory.get("_graph_relations") or [])[:2])
            via_text = ", ".join(str(value) for value in list(memory.get("_graph_via") or [])[:2])
            graph_flag = "graph-linked"
            if relation_text:
                graph_flag += f" by {relation_text}"
            if via_text:
                graph_flag += f" via {via_text}"
            flags.append(graph_flag)
        flag_text = f"; flags={', '.join(flags)}" if flags else ""
        project_text = ""
        if memory.get("project_name"):
            project_text = f"; project={memory.get('project_name')}"
        return (
            f"- {memory.get('title', 'Untitled')} "
            f"[layer={self._memory_layer(memory)}; category={memory.get('category', 'general')}; date={memory.get('date', '')}; "
            f"confidence={self._coerce_float(memory.get('confidence'), 0.75):.2f}; "
            f"source={memory.get('source', 'manual')}; tags={tag_text}{project_text}{flag_text}]\n"
            f"  {content}"
        )

    def _memory_prompt_budget(self, context_window_tokens=None):
        if context_window_tokens is None:
            context_window_tokens = self.settings.get("api_context_window_tokens", 1000000)
        try:
            context_window_tokens = max(2048, int(context_window_tokens))
        except (TypeError, ValueError):
            context_window_tokens = 1000000
        try:
            absolute_budget = max(512, int(self.settings.get("memory_prompt_budget_tokens", 12000)))
        except (TypeError, ValueError):
            absolute_budget = 12000
        ratio = self._coerce_float(
            self.settings.get("memory_prompt_budget_ratio"),
            0.15,
            minimum=0.001,
            maximum=0.5,
        )
        return min(absolute_budget, max(512, int(context_window_tokens * ratio)))

    def memory_content_limit(self, memory_kind="", memory_layer="semantic"):
        kind = str(memory_kind or "").strip().lower()
        layer = str(memory_layer or "semantic").strip().lower()
        if kind == "knowledge_extract_simple":
            key, fallback = "memory_max_chars_knowledge_simple", 1200
        elif kind == "knowledge_extract_complex":
            key, fallback = "memory_max_chars_knowledge_complex", 3600
        elif layer == "core":
            key, fallback = "memory_max_chars_core", 1200
        elif layer == "episodic":
            key, fallback = "memory_max_chars_episodic", 3600
        else:
            key, fallback = "memory_max_chars_semantic", 2400
        try:
            return max(200, int(self.settings.get(key, fallback)))
        except (TypeError, ValueError):
            return fallback

    def bound_generated_memory_content(self, content, memory_kind="", memory_layer="semantic"):
        text = str(content or "").strip()
        limit = self.memory_content_limit(memory_kind, memory_layer)
        if len(text) <= limit:
            return text, False, len(text)
        clipped = text[:limit].rstrip()
        last_break = max(clipped.rfind("\n"), clipped.rfind("。"), clipped.rfind("."))
        if last_break >= int(limit * 0.65):
            clipped = clipped[:last_break + 1].rstrip()
        return clipped, True, len(text)

    def _memory_conflict_audit(self, memory):
        """Return a bounded, public explanation of a memory's conflict state."""
        by_id = {str(item.get("id")): item for item in self.memories if item.get("id")}
        relations = []

        def add_relation(relation, related_id):
            related = by_id.get(str(related_id))
            if not related:
                return
            relations.append({
                "relation": relation,
                "title": str(related.get("title") or "Untitled")[:120],
                "date": str(related.get("date") or "")[:10],
                "active": (
                    not bool(related.get("is_archived"))
                    and not self._is_expired(related)
                    and str(related.get("superseded_by") or "") != str(memory.get("id") or "")
                ),
            })

        for related_id in list(memory.get("conflicts_with") or [])[:5]:
            add_relation("conflicts_with", related_id)
        for related_id in list(memory.get("supersedes") or [])[:5]:
            add_relation("supersedes", related_id)
        superseded_by = memory.get("superseded_by")
        if superseded_by:
            add_relation("superseded_by", superseded_by)

        if any(item["relation"] == "superseded_by" and item["active"] for item in relations):
            status = "superseded"
        elif any(item["relation"] == "conflicts_with" and item["active"] for item in relations):
            status = "needs_review"
        elif any(item["relation"] == "supersedes" for item in relations):
            status = "updates_older"
        else:
            status = "clear"
        return {"status": status, "relations": relations}

    @staticmethod
    def normalize_memory_graph_mode(mode):
        value = str(mode or "auto").strip().lower()
        return value if value in {"off", "auto", "deep"} else "auto"

    def ensure_memory_graph(self, force=False):
        if not force and not getattr(self, "_memory_graph_dirty", True):
            return {"changed": False, "sync_mode": "cached", **self.memory_graph.status()}
        result = self.memory_graph.ensure_synced(self.memories, force=force)
        self._memory_graph_dirty = False
        return result

    def get_memory_graph_status(self):
        sync = self.ensure_memory_graph(force=False)
        return {
            **self.memory_graph.status(),
            "sync_changed": bool(sync.get("changed")),
            "file": os.path.basename(str(self.memory_graph.path)),
        }

    def get_memory_graph_neighbors(self, memory_id):
        self.ensure_memory_graph(force=False)
        return self.memory_graph.neighbors(memory_id)

    def get_memory_graph_view(
        self,
        search="",
        memory_layer="",
        include_archived=False,
        limit=80,
        center_memory_id="",
        relation="",
    ):
        """Return a bounded, enriched subgraph for the memory management UI."""
        self.ensure_memory_graph(force=False)
        try:
            limit = max(20, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 80
        search_text = str(search or "").strip().lower()
        memory_layer = str(memory_layer or "").strip().lower()
        relation = str(relation or "").strip().lower()
        center_memory_id = str(center_memory_id or "").strip()

        active_by_id = {
            str(memory.get("id")): memory
            for memory in self.memories
            if memory.get("id")
        }

        def memory_allowed(memory, ignore_search=False):
            if not include_archived and (memory.get("is_archived") or self._memory_layer(memory) == "archive"):
                return False
            if memory_layer and self._memory_layer(memory) != memory_layer:
                return False
            if search_text and not ignore_search:
                haystack = " ".join([
                    self._memory_text(memory),
                    str(memory.get("project_name") or ""),
                    str(memory.get("source") or ""),
                ]).lower()
                if search_text not in haystack and not bool(self._tokenize(search_text) & self._tokenize(haystack)):
                    return False
            return True

        graph_nodes = self.memory_graph.graph.get("nodes", {})
        def edge_visible(edge):
            if relation:
                return edge.get("relation") == relation
            # Dates are available as an explicit filter, but hiding them from
            # the default overview prevents a single busy day becoming one
            # enormous hub that obscures stronger semantic relationships.
            return edge.get("relation") != "occurred_on"

        def adjacent(node_id):
            return self.memory_graph.adjacent(
                node_id,
                relation=relation,
                exclude_relations=() if relation else {"occurred_on"},
            )

        selected_memory_node_ids = []
        center_node_id = f"memory:{center_memory_id}" if center_memory_id else ""
        if center_node_id and center_node_id in graph_nodes:
            queue = [(center_node_id, 0)]
            visited = {center_node_id}
            while queue and len(selected_memory_node_ids) < limit:
                node_id, depth = queue.pop(0)
                node = graph_nodes.get(node_id, {})
                if node.get("type") == "memory":
                    memory = active_by_id.get(str(node.get("ref_id") or ""))
                    if memory and memory_allowed(memory, ignore_search=(node_id == center_node_id)):
                        selected_memory_node_ids.append(node_id)
                if depth >= 2:
                    continue
                for neighbor_id, _edge in adjacent(node_id):
                    if neighbor_id not in visited:
                        visited.add(neighbor_id)
                        queue.append((neighbor_id, depth + 1))
        else:
            candidates = [memory for memory in self.memories if memory_allowed(memory)]
            candidates.sort(
                key=lambda memory: (
                    1 if self._memory_layer(memory) == "core" else 0,
                    1 if memory.get("is_locked") else 0,
                    1 if memory.get("is_important") else 0,
                    str(memory.get("date") or ""),
                    str(memory.get("creation_time") or ""),
                ),
                reverse=True,
            )
            selected_memory_node_ids = [
                f"memory:{memory.get('id')}" for memory in candidates[:limit]
                if f"memory:{memory.get('id')}" in graph_nodes
            ]

        selected_memory_set = set(selected_memory_node_ids)
        included_node_ids = set(selected_memory_node_ids)
        included_edge_by_id = {}
        # Add connector nodes used by the selected memories. Direct memory links
        # are kept only when both memories are inside the bounded selection.
        for memory_node_id in selected_memory_set:
            for _neighbor_id, edge in adjacent(memory_node_id):
                if not edge_visible(edge):
                    continue
                source = edge.get("source")
                target = edge.get("target")
                source_type = (graph_nodes.get(source) or {}).get("type")
                target_type = (graph_nodes.get(target) or {}).get("type")
                include_edge = False
                if source in selected_memory_set and target in selected_memory_set:
                    include_edge = True
                elif source in selected_memory_set and target_type != "memory":
                    include_edge = True
                elif target in selected_memory_set and source_type != "memory":
                    include_edge = True
                if include_edge:
                    included_node_ids.update((source, target))
                    included_edge_by_id[str(edge.get("id") or "")] = dict(edge)
        included_edges = list(included_edge_by_id.values())

        connector_degree = {}
        for edge in included_edges:
            for endpoint in (edge.get("source"), edge.get("target")):
                if (graph_nodes.get(endpoint) or {}).get("type") != "memory":
                    connector_degree[endpoint] = connector_degree.get(endpoint, 0) + 1
        connector_priority = {"project": 5, "session": 4, "entity": 3, "tag": 2, "date": 1}
        useful_connectors = [
            node_id for node_id, degree in connector_degree.items()
            if degree >= 2
        ]
        useful_connectors.sort(
            key=lambda node_id: (
                connector_degree.get(node_id, 0),
                connector_priority.get((graph_nodes.get(node_id) or {}).get("type"), 0),
                str((graph_nodes.get(node_id) or {}).get("label") or ""),
            ),
            reverse=True,
        )
        useful_connector_set = set(useful_connectors[:max(30, limit)])
        included_node_ids = selected_memory_set | useful_connector_set
        included_edges = [
            edge for edge in included_edges
            if edge.get("source") in included_node_ids and edge.get("target") in included_node_ids
        ]

        memory_nodes = []
        connector_nodes = []
        for node_id in included_node_ids:
            node = dict(graph_nodes.get(node_id) or {})
            if not node:
                continue
            if node.get("type") == "memory":
                memory = active_by_id.get(str(node.get("ref_id") or ""))
                if not memory:
                    continue
                detail = memory.get("source_detail") if isinstance(memory.get("source_detail"), dict) else {}
                node.update({
                    "label": str(memory.get("title") or "Untitled")[:180],
                    "preview": str(memory.get("content") or "").strip()[:500],
                    "category": str(memory.get("category") or "general")[:80],
                    "source": str(memory.get("source") or "manual")[:80],
                    "project_name": str(memory.get("project_name") or "")[:140],
                    "locked": bool(memory.get("is_locked")),
                    "starred": bool(memory.get("is_important")),
                    "archived": bool(memory.get("is_archived")),
                    "session_id": str(detail.get("chat_session_id") or detail.get("session_id") or ""),
                    "conflict": self._memory_conflict_audit(memory),
                })
                memory_nodes.append(node)
            else:
                connector_nodes.append(node)

        nodes = memory_nodes + connector_nodes
        included_ids = {node.get("id") for node in nodes}
        included_edges = [
            edge for edge in included_edges
            if edge.get("source") in included_ids and edge.get("target") in included_ids
        ]
        connected_memory_ids = {
            endpoint
            for edge in included_edges
            for endpoint in (edge.get("source"), edge.get("target"))
            if str(endpoint).startswith("memory:")
        }
        return {
            "status": self.memory_graph.status(),
            "filters": {
                "search": search_text,
                "memory_layer": memory_layer,
                "include_archived": bool(include_archived),
                "limit": limit,
                "center_memory_id": center_memory_id,
                "relation": relation,
            },
            "nodes": nodes,
            "edges": included_edges,
            "summary": {
                "memory_nodes": len(memory_nodes),
                "connector_nodes": len(connector_nodes),
                "edges": len(included_edges),
                "orphan_memories": sum(
                    1 for node in memory_nodes if node.get("id") not in connected_memory_ids
                ),
                "truncated": len(selected_memory_node_ids) >= limit,
            },
        }

    @memory_write_locked
    def add_memory_graph_edge(self, source_memory_id, target_memory_id, relation="related_to", confidence=0.9):
        self.ensure_memory_graph(force=False)
        return self.memory_graph.add_manual_edge(
            source_memory_id,
            target_memory_id,
            relation=relation,
            confidence=confidence,
        )

    @memory_write_locked
    def remove_memory_graph_edge(self, edge_id):
        return self.memory_graph.remove_manual_edge(edge_id)

    def _expand_graph_memories(self, seed_memories, user_input, project_id=None, graph_mode="auto"):
        graph_mode = self.normalize_memory_graph_mode(graph_mode)
        trace = {
            "mode": graph_mode,
            "status": "disabled" if graph_mode == "off" else "no_seed",
            "seed_count": 0,
            "expanded_count": 0,
        }
        if graph_mode == "off" or not seed_memories:
            return list(seed_memories or []), trace

        threshold = self._coerce_float(
            self.settings.get("memory_retrieval_threshold"),
            self.VECTOR_MATCH_THRESHOLD,
        )
        query_text = str(user_input or "").strip().lower()
        graph_stopwords = {
            "the", "a", "an", "is", "are", "was", "were", "to", "of", "and", "or",
            "what", "when", "where", "who", "how", "please", "tell", "me",
            "我", "的", "是", "了", "吗", "呢", "什么", "怎么", "如何", "请", "一下",
        }
        query_tokens = {
            token for token in self._tokenize(query_text)
            if token not in graph_stopwords and len(token.strip()) >= 2
        }
        graph_seeds = []
        for memory in seed_memories:
            layer = memory.get("_layer") or self._memory_layer(memory)
            memory_text = self._memory_text(memory).lower()
            memory_tokens = self._tokenize(memory_text)
            semantic_score = self._coerce_float(memory.get("_semantic_score"), 0)
            has_query_signal = bool(
                query_text and (
                    query_text in memory_text
                    or bool(query_tokens & memory_tokens)
                    or semantic_score >= threshold
                )
            )
            # Core memories are always supplied for safety, but an unrelated
            # core note must not become a graph traversal starting point.
            if layer != "core" or has_query_signal:
                graph_seeds.append(memory)
        if not graph_seeds:
            return list(seed_memories), trace

        self.ensure_memory_graph(force=False)
        expansion_limit = int(self.settings.get(
            "memory_graph_deep_expansion" if graph_mode == "deep" else "memory_graph_auto_expansion",
            10 if graph_mode == "deep" else 4,
        ))
        raw_paths = self.memory_graph.expand_memory_ids(
            [memory.get("id") for memory in graph_seeds],
            mode=graph_mode,
            max_results=max(
                expansion_limit * 4,
                int(self.settings.get("memory_graph_candidate_limit", 10000)),
            ),
        )
        by_id = {str(memory.get("id")): memory for memory in self.memories if memory.get("id")}
        active_by_id = {
            str(memory.get("id")): memory
            for memory in self.memories
            if memory.get("id") and not memory.get("is_archived") and not self._is_expired(memory)
        }
        selected_ids = {str(memory.get("id")) for memory in seed_memories}
        additions = []
        for path in raw_paths:
            memory = by_id.get(str(path.get("memory_id") or ""))
            if not memory or str(memory.get("id")) in selected_ids:
                continue
            if memory.get("is_archived") or self._is_expired(memory):
                continue
            if not self._memory_allowed_for_project(memory, project_id=project_id):
                continue
            if memory.get("superseded_by") and str(memory.get("superseded_by")) in active_by_id:
                continue

            relations = list(dict.fromkeys(str(value) for value in path.get("relations", []) if value))
            memory_text = self._memory_text(memory).lower()
            memory_tokens = self._tokenize(memory_text)
            semantic_score = self.semantic_memory_score(memory, user_input)
            has_query_signal = bool(
                query_text and (
                    query_text in memory_text
                    or bool(query_tokens & memory_tokens)
                    or semantic_score >= threshold
                )
            )
            graph_score = self._coerce_float(path.get("graph_score"), 0)
            strong_context = bool(
                set(relations)
                & {
                    "conflicts_with", "supersedes", "supports", "caused_by",
                    "part_of", "follows", "related_to", "belongs_to_project", "from_session",
                }
            )
            if not has_query_signal and not (strong_context and graph_score >= 0.8):
                continue

            item = dict(memory)
            item["_layer"] = self._memory_layer(memory)
            item["_tier"] = self._memory_tier(memory)
            item["_semantic_score"] = round(semantic_score, 4)
            item["_embedding_score"] = None
            item["_score"] = self.score_memory(memory, user_input, project_id=project_id)
            item["_priority_score"] = self.memory_priority_score(memory, user_input)
            item["_graph_expanded"] = True
            item["_graph_score"] = round(graph_score, 4)
            item["_graph_relations"] = relations
            path_nodes = [self.memory_graph.graph.get("nodes", {}).get(node_id, {}) for node_id in path.get("path", [])]
            item["_graph_via"] = [
                str(node.get("label") or "")[:120]
                for node in path_nodes[1:-1]
                if node.get("label")
            ]
            additions.append(item)

        additions.sort(
            key=lambda item: (
                item.get("_graph_score", 0) * 2.0 + item.get("_semantic_score", 0),
                item.get("_priority_score", 0),
                item.get("date", ""),
            ),
            reverse=True,
        )
        additions = additions[:expansion_limit]
        trace.update({
            "status": "expanded" if additions else "no_related_match",
            "seed_count": len(graph_seeds),
            "expanded_count": len(additions),
        })
        return list(seed_memories) + additions, trace

    def _memory_recall_audit_item(self, memory, prompt_text=""):
        layer = memory.get("_layer") or self._memory_layer(memory)
        embedding_score = memory.get("_embedding_score")
        semantic_score = memory.get("_semantic_score")
        score = memory.get("_score", memory.get("relevance_score", 0))
        if memory.get("_graph_expanded"):
            reason = "graph_related"
        elif layer == "core":
            reason = "core_memory"
        elif memory.get("is_locked") or memory.get("is_important"):
            reason = "protected_priority"
        elif embedding_score is not None:
            reason = "embedding_match"
        elif semantic_score:
            reason = "semantic_match"
        else:
            reason = "text_relevance"
        content = str(memory.get("content") or "").strip()
        # The audit is user-facing, so include enough of the exact memory to
        # make a generic title (for example, "Important memory") traceable.
        # Keep this bounded because deep recall can contain many records.
        content_preview_limit = 1400
        content_preview = content[:content_preview_limit].rstrip()
        if len(content) > content_preview_limit:
            content_preview += "…"
        prompt_excerpt = str(prompt_text or "").strip()
        prompt_excerpt_limit = 1800
        if len(prompt_excerpt) > prompt_excerpt_limit:
            prompt_excerpt = prompt_excerpt[:prompt_excerpt_limit].rstrip() + "…"
        item = {
            "memory_id": str(memory.get("id") or ""),
            "title": str(memory.get("title") or "Untitled")[:160],
            "content_preview": content_preview,
            "prompt_excerpt": prompt_excerpt,
            "included_in_prompt": True,
            "layer": layer,
            "category": str(memory.get("category") or "general")[:80],
            "source": str(memory.get("source") or "manual")[:80],
            "project_name": str(memory.get("project_name") or "")[:120],
            "reason": reason,
            "locked": bool(memory.get("is_locked")),
            "starred": bool(memory.get("is_important")),
            "scores": {
                "relevance": round(self._coerce_float(score, 0), 4),
                "priority": round(self._coerce_float(memory.get("_priority_score"), 0), 4),
                "semantic": round(self._coerce_float(semantic_score, 0), 4),
                "embedding": None if embedding_score is None else round(self._coerce_float(embedding_score, 0), 4),
            },
            "conflict": self._memory_conflict_audit(memory),
        }
        if memory.get("_graph_expanded"):
            item["graph"] = {
                "score": round(self._coerce_float(memory.get("_graph_score"), 0), 4),
                "relations": list(memory.get("_graph_relations") or [])[:4],
                "via": list(memory.get("_graph_via") or [])[:3],
            }
        return item

    def _empty_recall_audit(self, user_input, mode, project_id=None, status="no_match"):
        active = [memory for memory in self.memories if not memory.get("is_archived") and not self._is_expired(memory)]
        return {
            "version": 1,
            "status": status,
            "mode": str(mode or "normal"),
            "query": str(user_input or "")[:240],
            "project_id": str(project_id or ""),
            "selected_count": 0,
            "selected": [],
            "conflict_count": 0,
            "candidate_summary": {
                "active": len(active),
                "archived": sum(1 for memory in self.memories if memory.get("is_archived")),
                "superseded_filtered": sum(
                    1 for memory in active
                    if memory.get("superseded_by")
                    and any(
                        str(other.get("id")) == str(memory.get("superseded_by"))
                        and not other.get("is_archived")
                        and not self._is_expired(other)
                        for other in self.memories
                    )
                ),
                "project_filtered": sum(
                    1 for memory in active
                    if not self._memory_allowed_for_project(memory, project_id=project_id)
                ),
            },
        }

    def enrich_memory_recall_audit(self, audit):
        """Backfill display identity for audits saved before content was included.

        Older chat records only persisted memory titles.  Resolve those titles
        only when they map to one current memory; ambiguous titles are left
        untouched so a historical audit is never made to point at the wrong
        record.
        """
        if not isinstance(audit, dict) or not isinstance(audit.get("selected"), list):
            return audit

        enriched = copy.deepcopy(audit)
        memories_by_title = {}
        for memory in self.memories:
            title = str(memory.get("title") or "Untitled").strip()
            memories_by_title.setdefault(title, []).append(memory)

        selected = []
        for raw_item in enriched.get("selected") or []:
            item = dict(raw_item) if isinstance(raw_item, dict) else {}
            memory_id = str(item.get("memory_id") or item.get("id") or "").strip()
            memory = self._find_memory(memory_id) if memory_id else None
            if memory is None:
                title = str(item.get("title") or "Untitled").strip()
                matches = memories_by_title.get(title, [])
                memory = matches[0] if len(matches) == 1 else None
            if memory is not None:
                display_item = self._memory_recall_audit_item(
                    memory,
                    prompt_text=str(item.get("prompt_excerpt") or ""),
                )
                # Preserve the historical scores/reason/conflict result, while
                # filling only the identity/content fields that old audits lack.
                for key, value in display_item.items():
                    if key not in item or item.get(key) in (None, "", []):
                        item[key] = value
                item.setdefault("included_in_prompt", True)
            selected.append(item)
        enriched["selected"] = selected
        enriched["selected_count"] = len(selected)
        return enriched

    def build_prompt_context_with_audit(self, user_input, mode="normal", project_id=None, context_window_tokens=None, limit=None, graph_mode="off", mark_accessed=False, exclude_daily_activity=False):
        if not self.settings.get("memory_2_enabled", True):
            memories = [
                m for m in self.get_memories()
                if self._memory_allowed_for_project(m, project_id=project_id)
            ]
            if not memories:
                return "", self._empty_recall_audit(user_input, mode, project_id)
            target = [m for m in memories if m.get("is_locked") or m.get("is_important")]
            if mode == "deep":
                target = memories[:25]
            if not target:
                return "", self._empty_recall_audit(user_input, mode, project_id)
            lines = [self.format_memory_for_prompt(m, include_metadata=False, query=user_input) for m in target]
            audit = self._empty_recall_audit(user_input, mode, project_id, status="selected")
            audit["selected"] = [
                self._memory_recall_audit_item(memory, prompt_text=line)
                for memory, line in zip(target, lines)
            ]
            audit["selected_count"] = len(audit["selected"])
            audit["conflict_count"] = sum(1 for item in audit["selected"] if item["conflict"]["status"] != "clear")
            return (
                "[System: Long-term Memory]\n"
                "Use these notes to help answer Doctor. Do not invent details that are not present.\n"
                + "\n".join(lines) + "\n\n"
            ), audit

        memories = self.get_relevant_memories(
            user_input,
            mode=mode,
            project_id=project_id,
            limit=limit,
            mark_accessed=mark_accessed,
            exclude_daily_activity=exclude_daily_activity,
        )
        if not memories:
            audit = self._empty_recall_audit(user_input, mode, project_id)
            audit["graph"] = {
                "mode": self.normalize_memory_graph_mode(graph_mode),
                "status": "no_seed",
                "seed_count": 0,
                "expanded_count": 0,
                "included_count": 0,
            }
            return "", audit

        memories, graph_trace = self._expand_graph_memories(
            memories,
            user_input,
            project_id=project_id,
            graph_mode=graph_mode,
        )

        include_metadata = self.settings.get("include_memory_metadata", True)
        budget = self._memory_prompt_budget(context_window_tokens=context_window_tokens)
        header_reserve = 180
        remaining = max(128, budget - header_reserve)
        lines = []
        included_memories = []
        for memory in memories:
            item_budget = min(
                remaining,
                max(64, int(self.settings.get("memory_item_budget_tokens", 700))),
            )
            if item_budget < 64:
                break
            line = self.format_memory_for_prompt(
                memory,
                include_metadata=include_metadata,
                max_tokens=item_budget,
                query=user_input,
            )
            line_tokens = self.estimate_token_count(line)
            if line_tokens > remaining:
                continue
            lines.append(line)
            included_memories.append(memory)
            remaining -= line_tokens
        if not lines:
            audit = self._empty_recall_audit(user_input, mode, project_id, status="budget_limited")
            graph_trace["included_count"] = 0
            audit["graph"] = graph_trace
            return "", audit
        mode_note = (
            "Deep recall mode is active: search all supplied memories carefully, but say when a detail is missing."
            if mode == "deep"
            else "Normal mode is active: prioritize locked/starred memories and relevant notes."
        )
        graph_note = (
            "Graph recall added linked context. A relationship is supporting context, not proof of causality; "
            "verify it against memory content and dates.\n"
            if any(memory.get("_graph_expanded") for memory in included_memories)
            else ""
        )
        context = (
            "[System: Memory 2.0]\n"
            "The following are long-term memories about Doctor, Saya, projects, preferences, and prior events.\n"
            f"{mode_note}\n"
            "Memory rules: prefer newer locked/starred memories when notes conflict; treat low-confidence notes as hints; "
            "do not fabricate missing details; do not expose internal IDs unless Doctor asks to manage memory.\n"
            + graph_note
            + "\n".join(lines) + "\n\n"
        )
        audit = self._empty_recall_audit(user_input, mode, project_id, status="selected")
        audit["selected"] = [
            self._memory_recall_audit_item(memory, prompt_text=line)
            for memory, line in zip(included_memories, lines)
        ]
        audit["selected_count"] = len(audit["selected"])
        audit["conflict_count"] = sum(
            1 for item in audit["selected"] if item["conflict"]["status"] != "clear"
        )
        audit["budget_tokens"] = budget
        audit["used_tokens"] = budget - header_reserve - remaining
        graph_trace["included_count"] = sum(
            1 for memory in included_memories if memory.get("_graph_expanded")
        )
        audit["graph"] = graph_trace
        return context, audit

    def build_prompt_context(self, user_input, mode="normal", project_id=None, context_window_tokens=None, limit=None, graph_mode="off", mark_accessed=False, exclude_daily_activity=False):
        context, _audit = self.build_prompt_context_with_audit(
            user_input,
            mode=mode,
            project_id=project_id,
            context_window_tokens=context_window_tokens,
            limit=limit,
            graph_mode=graph_mode,
            mark_accessed=mark_accessed,
            exclude_daily_activity=exclude_daily_activity,
        )
        return context

    def build_daily_activity_prompt_context_with_audit(self):
        context = self.build_daily_activity_prompt_context()
        if not context:
            return "", self._empty_recall_audit("", "daily", status="no_daily_snapshot")
        memory = self._find_memory(self.DAILY_ACTIVITY_MEMORY_ID)
        audit = self._empty_recall_audit("", "daily", status="daily_only")
        audit["selected"] = [
            self._memory_recall_audit_item(memory, prompt_text=context)
        ] if memory else []
        audit["selected_count"] = len(audit["selected"])
        return context, audit

    def build_daily_activity_prompt_context(self):
        """Return only the small managed today snapshot for auto recall."""
        memory = self._find_memory(self.DAILY_ACTIVITY_MEMORY_ID)
        if not memory or memory.get("is_archived"):
            return ""
        if str(memory.get("date") or "") != datetime.now().strftime("%Y-%m-%d"):
            return ""
        content = str(memory.get("content") or "").strip()
        if not content:
            return ""
        return (
            "[System: Today's Activity Memory]\n"
            "This is a protected, automatically refreshed current-day memory. "
            "Use it naturally when relevant and do not expose implementation details.\n"
            f"{content}\n\n"
        )

    def _compression_generation(self, memory):
        inferred = 1 if memory.get("compressed_from") else 0
        try:
            return max(0, int(memory.get("compression_generation", inferred)))
        except (TypeError, ValueError):
            return inferred

    def _ai_compression_generation(self, memory):
        try:
            return max(0, int(memory.get("ai_compression_generation", 0)))
        except (TypeError, ValueError):
            return 0

    def _memory_entity_names(self, memory, limit=8):
        names = []
        for entity in memory.get("entities") or []:
            if isinstance(entity, dict):
                value = entity.get("name") or entity.get("label")
            else:
                value = entity
            value = self._normalize_text(value)
            if value and value.lower() not in {item.lower() for item in names}:
                names.append(value[:100])
            if len(names) >= limit:
                break
        return names

    def _memory_topic_names(self, memory, limit=8):
        ignored = {
            "compressed", "summary", "semantic", "episodic", "archive",
            "conversation", "memory", "general",
        }
        topics = []
        for value in list(memory.get("topics") or []) + list(memory.get("tags") or []):
            if isinstance(value, dict):
                value = value.get("name") or value.get("label")
            value = self._normalize_text(value)
            if not value or value.lower() in ignored:
                continue
            if value.lower() not in {item.lower() for item in topics}:
                topics.append(value[:100])
            if len(topics) >= limit:
                break
        return topics

    def _compression_topic_key(self, memory):
        category = self._normalize_text(memory.get("category") or "general").lower()[:60]
        entities = self._memory_entity_names(memory, limit=2)
        topics = self._memory_topic_names(memory, limit=2)
        anchors = entities or topics
        if not anchors:
            return category or "general"
        clean_anchors = [re.sub(r"\s+", " ", item.lower())[:60] for item in anchors]
        return (category or "general") + ":" + "|".join(clean_anchors)

    def _extract_compression_facts(self, memory):
        content = self._normalize_text(memory.get("content"))
        sentences = [
            self._normalize_text(part)[:240]
            for part in re.split(r"(?<=[。！？!?])|[\r\n]+", content)
            if self._normalize_text(part)
        ]

        def matching_sentences(pattern, limit=4):
            regex = re.compile(pattern, flags=re.IGNORECASE)
            matches = []
            for sentence in sentences:
                if regex.search(sentence) and sentence not in matches:
                    matches.append(sentence)
                if len(matches) >= limit:
                    break
            return matches

        date_values = []
        memory_date = self._normalize_text(memory.get("date"))[:10]
        if memory_date:
            date_values.append(memory_date)
        for value in re.findall(
            r"(?:20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|\d{1,2}月\d{1,2}日)",
            content,
        ):
            if value not in date_values:
                date_values.append(value)
            if len(date_values) >= 6:
                break

        source_detail = memory.get("source_detail") if isinstance(memory.get("source_detail"), dict) else {}
        source_refs = {
            key: str(value)[:180]
            for key, value in {
                "source": memory.get("source"),
                "session_id": source_detail.get("session_id") or source_detail.get("chat_session_id"),
                "project_id": memory.get("project_id"),
                "created_at": memory.get("creation_time"),
            }.items()
            if value
        }
        return {
            "dates": date_values,
            "entities": self._memory_entity_names(memory),
            "topics": self._memory_topic_names(memory),
            "decisions": matching_sentences(
                r"决定|计划|改为|改成|取消|停止|完成|下一步|decision|decided|plan(?:ned)?|switch(?:ed)?|will\b"
            ),
            "preferences": matching_sentences(
                r"喜欢|不喜欢|讨厌|偏好|希望|prefer|favorite|like|love|hate|want"
            ),
            "negations": matching_sentences(
                r"不|没|没有|无|取消|停止|禁止|\bnot\b|\bno\b|never|without|don['’]?t|cannot|can['’]?t"
            ),
            "numeric_facts": matching_sentences(
                r"\d+(?:\.\d+)?(?:%|％|次|条|个|天|小时|分钟|元|美元|GB|MB|KB|tokens?)?"
            ),
            "source": source_refs,
        }

    def _compression_fact_preview(self, facts, max_chars=260):
        parts = []
        labels = (
            ("dates", "dates"), ("entities", "entities"),
            ("decisions", "decisions"), ("preferences", "preferences"),
            ("negations", "negations"), ("numeric_facts", "numbers"),
        )
        for key, label in labels:
            values = facts.get(key) or []
            if values:
                parts.append(f"{label}=" + " | ".join(str(value) for value in values[:2]))
        preview = "; ".join(parts)
        return preview[:max_chars].rstrip()

    def _local_compression_pressure_state(self):
        max_generation = max(1, int(self.settings.get("memory_compression_max_generations", 2)))
        eligible = 0
        capped = 0
        considered = 0
        for memory in self.memories:
            if memory.get("is_archived") or self._is_capacity_managed_memory(memory):
                continue
            if self._memory_layer(memory) == "core" or memory.get("source") == "system":
                continue
            if self._infer_retention_policy(memory) not in {"temporary", "compressible"}:
                continue
            considered += 1
            if self._compression_generation(memory) >= max_generation:
                capped += 1
            else:
                eligible += 1
        return {
            "considered_count": considered,
            "eligible_count": eligible,
            "generation_capped_count": capped,
            "max_local_generation": max_generation,
            "all_at_generation_limit": bool(capped and eligible == 0),
        }

    def _memory_compression_fingerprint(self, memory):
        payload = {
            "id": memory.get("id"),
            "title": memory.get("title"),
            "content": memory.get("content"),
            "date": memory.get("date"),
            "is_archived": bool(memory.get("is_archived")),
            "compression_generation": self._compression_generation(memory),
            "ai_compression_generation": self._ai_compression_generation(memory),
        }
        return hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()

    def _prune_ai_compression_plans(self):
        now = datetime.now()
        kept = {}
        for plan_id, plan in self._ai_compression_plans.items():
            try:
                expires_at = datetime.strptime(plan.get("expires_at", ""), "%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError):
                continue
            if expires_at > now:
                kept[plan_id] = plan
        self._ai_compression_plans = kept

    def _ai_compression_candidate_groups(self, scope="pressure", limit=50, project_id=""):
        scope = str(scope or "pressure").strip().lower()
        if scope not in {"pressure", "repeated", "project", "oldest"}:
            scope = "pressure"
        max_memories = max(2, int(self.settings.get("memory_ai_compression_max_memories", 200)))
        try:
            limit = max(2, min(max_memories, int(limit)))
        except (TypeError, ValueError):
            limit = min(50, max_memories)
        project_id = str(project_id or "").strip()
        local_max = max(1, int(self.settings.get("memory_compression_max_generations", 2)))
        ai_max = max(1, int(self.settings.get("memory_ai_compression_max_generations", 2)))
        candidates = []
        skipped_ai_limit = 0
        for memory in self.memories:
            if memory.get("is_archived") or self._is_capacity_managed_memory(memory):
                continue
            if self._memory_layer(memory) == "core" or memory.get("source") == "system":
                continue
            if self._infer_retention_policy(memory) == "permanent":
                continue
            if scope == "project" and str(memory.get("project_id") or "") != project_id:
                continue
            generation = self._compression_generation(memory)
            ai_generation = self._ai_compression_generation(memory)
            if scope == "repeated" and generation < local_max:
                continue
            if ai_generation >= ai_max:
                skipped_ai_limit += 1
                continue
            candidates.append(memory)

        if scope == "oldest":
            candidates.sort(key=lambda memory: (
                str(memory.get("date") or ""),
                1 if memory.get("is_important") else 0,
                1 if memory.get("is_locked") else 0,
                self._coerce_float(memory.get("importance_score"), 0.4),
            ))
        else:
            candidates.sort(key=lambda memory: (
                0 if self._compression_generation(memory) >= local_max else 1,
                0 if self._infer_retention_policy(memory) in {"temporary", "compressible"} else 1,
                1 if memory.get("is_important") else 0,
                1 if memory.get("is_locked") else 0,
                str(memory.get("date") or ""),
                self._coerce_float(memory.get("importance_score"), 0.4),
            ))
        candidates = candidates[:limit]

        grouped = {}
        for memory in candidates:
            key = (
                str(memory.get("project_id") or ""),
                self._memory_layer(memory),
                str(memory.get("category") or "general"),
                self._compression_topic_key(memory),
            )
            grouped.setdefault(key, []).append(memory)

        group_size = max(2, min(30, int(self.settings.get("memory_compression_group_size", 20))))
        groups = []
        for key, memories in grouped.items():
            memories.sort(key=lambda memory: (str(memory.get("date") or ""), str(memory.get("creation_time") or "")))
            for offset in range(0, len(memories), group_size):
                chunk = memories[offset:offset + group_size]
                if len(chunk) < 2:
                    continue
                project_key, layer, category, topic_key = key
                group_id = hashlib.sha256(
                    ("|".join([project_key, layer, category, topic_key]) + f"|{offset}").encode("utf-8")
                ).hexdigest()[:16]
                groups.append({
                    "group_id": group_id,
                    "project_id": project_key,
                    "layer": layer,
                    "category": category,
                    "topic_key": topic_key,
                    "memories": chunk,
                })
        return groups, {
            "scope": scope,
            "requested_limit": limit,
            "candidate_count": sum(len(group["memories"]) for group in groups),
            "skipped_ai_generation_limit": skipped_ai_limit,
            "local_generation_limit": local_max,
            "ai_generation_limit": ai_max,
        }

    def build_ai_compression_preview(self, scope="pressure", limit=50, project_id=""):
        self._prune_ai_compression_plans()
        groups, selection = self._ai_compression_candidate_groups(
            scope=scope,
            limit=limit,
            project_id=project_id,
        )
        input_limit = max(2000, int(self.settings.get("memory_ai_compression_input_token_limit", 32000)))
        selected_groups = []
        source_groups = []
        source_tokens = 0
        for group in groups:
            records = []
            for memory in group["memories"]:
                records.append({
                    "id": str(memory.get("id") or ""),
                    "date": str(memory.get("date") or ""),
                    "title": str(memory.get("title") or "Untitled")[:180],
                    "content": str(memory.get("content") or "")[:12000],
                    "structured_facts": self._extract_compression_facts(memory),
                    "compression_generation": self._compression_generation(memory),
                })
            source_group = {
                "group_id": group["group_id"],
                "project_id": group["project_id"],
                "layer": group["layer"],
                "category": group["category"],
                "topic_key": group["topic_key"],
                "memories": records,
            }
            group_tokens = self.estimate_token_count(json.dumps(source_group, ensure_ascii=False))
            if selected_groups and source_tokens + group_tokens + 900 > input_limit:
                break
            if group_tokens + 900 > input_limit:
                continue
            selected_groups.append(group)
            source_groups.append(source_group)
            source_tokens += group_tokens

        prompt_preamble = (
            "You compress long-term memories conservatively. Use only the supplied facts. "
            "Preserve dates, people, decisions, preference changes, numbers, negations, and source meaning. "
            "Do not merge groups, invent details, resolve contradictions, or silently remove a later correction. "
            "Return one JSON object with a summaries array. Each item must contain group_id, title, summary, "
            "and preserved_facts. Summary must be concise but self-contained.\n"
        )
        response_schema = {
            "summaries": [{
                "group_id": "group id from input",
                "title": "short factual title",
                "summary": "self-contained summary",
                "preserved_facts": {
                    "dates": [], "entities": [], "decisions": [],
                    "preferences": [], "negations": [], "numeric_facts": [],
                },
            }]
        }
        prompt = (
            prompt_preamble
            + "Required JSON shape:\n"
            + json.dumps(response_schema, ensure_ascii=False)
            + "\n\nSource groups:\n"
            + json.dumps(source_groups, ensure_ascii=False)
        )
        input_tokens = self.estimate_token_count(prompt)
        output_tokens = min(8192, max(400, len(selected_groups) * 320, int(input_tokens * 0.22)))
        total_tokens = input_tokens + output_tokens
        plan_id = str(uuid.uuid4())
        expires_at = datetime.now() + timedelta(minutes=15)
        plan = {
            "id": plan_id,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "expires_at": expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            "scope": selection["scope"],
            "project_id": str(project_id or ""),
            "prompt": prompt,
            "input_tokens": input_tokens,
            "max_output_tokens": output_tokens,
            "estimated_total_tokens": total_tokens,
            "groups": [{
                "group_id": group["group_id"],
                "project_id": group["project_id"],
                "layer": group["layer"],
                "category": group["category"],
                "topic_key": group["topic_key"],
                "memory_ids": [str(memory.get("id")) for memory in group["memories"]],
            } for group in selected_groups],
            "fingerprints": {
                str(memory.get("id")): self._memory_compression_fingerprint(memory)
                for group in selected_groups for memory in group["memories"]
            },
        }
        if selected_groups:
            self._ai_compression_plans[plan_id] = plan
        return {
            "status": "ready" if selected_groups else "no_safe_groups",
            "plan_id": plan_id if selected_groups else None,
            "expires_at": plan["expires_at"] if selected_groups else None,
            "scope": selection["scope"],
            "candidate_count": sum(len(group["memory_ids"]) for group in plan["groups"]),
            "group_count": len(plan["groups"]),
            "groups": [{
                "group_id": group["group_id"],
                "topic": group["topic_key"],
                "category": group["category"],
                "memory_count": len(group["memory_ids"]),
            } for group in plan["groups"]],
            "token_estimate": {
                "provider": "DeepSeek-compatible",
                "method": "cjk_aware_estimate",
                "input_tokens": input_tokens,
                "max_output_tokens": output_tokens,
                "estimated_total_tokens": total_tokens,
                "likely_min_tokens": max(1, int(total_tokens * 0.85)),
                "likely_max_tokens": max(1, int(total_tokens * 1.20)),
                "exact": False,
            },
            "limits": {
                "requested_memories": selection["requested_limit"],
                "input_token_limit": input_limit,
                "skipped_ai_generation_limit": selection["skipped_ai_generation_limit"],
            },
        }

    def get_ai_compression_plan(self, plan_id):
        self._prune_ai_compression_plans()
        plan = self._ai_compression_plans.get(str(plan_id or ""))
        if not plan:
            raise ValueError("AI compression plan expired or does not exist. Refresh the estimate.")
        for memory_id, fingerprint in plan.get("fingerprints", {}).items():
            memory = self._find_memory(memory_id)
            if not memory or memory.get("is_archived"):
                raise ValueError("A selected memory changed after preview. Refresh the estimate.")
            if self._memory_compression_fingerprint(memory) != fingerprint:
                raise ValueError("A selected memory changed after preview. Refresh the estimate.")
        return plan

    def apply_ai_compression_plan(self, plan_id, summaries, usage=None, model_name=""):
        if not self.settings.get("memory_ai_compression_enabled", False):
            raise ValueError("AI memory compression is disabled in Mode Setting.")
        plan = self.get_ai_compression_plan(plan_id)
        if not isinstance(summaries, list):
            raise ValueError("AI compression response did not contain a summaries list.")
        outputs = {
            str(item.get("group_id") or ""): item
            for item in summaries
            if isinstance(item, dict) and item.get("group_id")
        }
        usage = usage if isinstance(usage, dict) else {}
        prompt_tokens = max(0, int(usage.get("prompt_tokens") or plan.get("input_tokens") or 0))
        completion_tokens = max(0, int(usage.get("completion_tokens") or 0))
        total_tokens = max(
            prompt_tokens + completion_tokens,
            int(usage.get("total_tokens") or 0),
        )
        created = []
        archived_count = 0
        rejected_groups = []
        for group in plan.get("groups") or []:
            group_id = str(group.get("group_id") or "")
            output = outputs.get(group_id)
            if not output:
                rejected_groups.append({"group_id": group_id, "reason": "missing_output"})
                continue
            memories = [self._find_memory(memory_id) for memory_id in group.get("memory_ids") or []]
            memories = [memory for memory in memories if memory and not memory.get("is_archived")]
            if len(memories) < 2:
                rejected_groups.append({"group_id": group_id, "reason": "sources_changed"})
                continue
            title = self._normalize_text(output.get("title"))[:180]
            summary = self._normalize_text(output.get("summary"))
            if len(summary) < 20:
                rejected_groups.append({"group_id": group_id, "reason": "summary_too_short"})
                continue
            source_text = "\n".join(
                f"{memory.get('date', '')}\n{memory.get('title', '')}\n{memory.get('content', '')}"
                for memory in memories
            )

            def number_tokens(text):
                return {
                    token.replace(",", "")
                    for token in re.findall(r"(?<![A-Za-z0-9_])\d+(?:[.,]\d+)?%?", str(text or ""))
                }

            unsupported_numbers = sorted(
                number_tokens(title + "\n" + summary) - number_tokens(source_text)
            )
            if unsupported_numbers:
                rejected_groups.append({
                    "group_id": group_id,
                    "reason": "unsupported_numeric_fact",
                    "values": unsupported_numbers[:12],
                })
                continue
            preserved_facts = output.get("preserved_facts") if isinstance(output.get("preserved_facts"), dict) else {}
            normalized_source = self._normalize_text(source_text).lower()
            unsupported_anchors = []
            for fact_key in ("dates", "entities"):
                values = preserved_facts.get(fact_key) or []
                if not isinstance(values, list):
                    continue
                for value in values[:20]:
                    normalized_value = self._normalize_text(value).lower()
                    if normalized_value and normalized_value not in normalized_source:
                        unsupported_anchors.append({"type": fact_key, "value": normalized_value[:120]})
            if unsupported_anchors:
                rejected_groups.append({
                    "group_id": group_id,
                    "reason": "unsupported_preserved_fact",
                    "values": unsupported_anchors[:12],
                })
                continue
            bounded_summary, was_truncated, original_chars = self.bound_generated_memory_content(
                summary,
                memory_kind="summary",
                memory_layer=group.get("layer") or "semantic",
            )
            structured_facts = [
                {"memory_id": memory.get("id"), **self._extract_compression_facts(memory)}
                for memory in memories
            ]
            compression_generation = max(
                self._compression_generation(memory) for memory in memories
            ) + 1
            ai_generation = max(
                self._ai_compression_generation(memory) for memory in memories
            ) + 1
            metadata = {
                "memory_layer": "episodic" if group.get("layer") == "episodic" else "semantic",
                "memory_kind": "summary",
                "retention_policy": "compressible",
                "compressed_from": [memory.get("id") for memory in memories],
                "compression_generation": compression_generation,
                "ai_compression_generation": ai_generation,
                "structured_facts": structured_facts,
                "model_preserved_facts": preserved_facts,
                "compression_scope": {
                    "project_id": group.get("project_id") or "",
                    "category": group.get("category") or "general",
                    "topic_key": group.get("topic_key") or "general",
                    "source_layer": group.get("layer") or "semantic",
                },
            }
            if group.get("project_id"):
                metadata.update({
                    "project_id": group.get("project_id"),
                    "project_name": str(memories[0].get("project_name") or "Untitled Project")[:80],
                    "project_scoped": True,
                })
            merged = self.add_memory(
                bounded_summary,
                title=title or f"AI compressed {group.get('topic_key') or 'memory'}",
                source="ai_capacity_compaction",
                is_important=False,
                category=group.get("category") or "general",
                tags=["compressed", "ai_compressed", str(group.get("category") or "general")],
                confidence=min(self._coerce_float(memory.get("confidence"), 0.75) for memory in memories),
                source_detail={
                    "type": "confirmed_ai_memory_compression",
                    "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "compression_engine": "ai_deepseek",
                    "model": str(model_name or "")[:180],
                    "prompt_tokens": prompt_tokens,
                    "completion_tokens": completion_tokens,
                    "token_usage": total_tokens,
                    "content_truncated": was_truncated,
                    "original_generated_chars": original_chars,
                    "protection_version": 2,
                },
                metadata=metadata,
                run_capacity_check=False,
            )
            for memory in memories:
                memory["archived_from_layer"] = self._memory_layer(memory)
                memory["is_archived"] = True
                memory["status"] = "archived"
                memory["memory_layer"] = "archive"
                memory["merged_into"] = merged["id"]
                memory["parent_memory_id"] = merged["id"]
            created.append(merged)
            archived_count += len(memories)

        if not created:
            raise ValueError(
                "AI output did not pass conservative validation; no memories were changed. "
                + json.dumps(rejected_groups[:6], ensure_ascii=False)
            )
        self.save_memories()
        self._ai_compression_plans.pop(str(plan_id), None)
        capacity_result = self.run_memory_capacity_maintenance(trigger="ai_compression_completed")
        event = self._record_memory_maintenance_event(
            "ai_compression_completed",
            "info",
            f"Confirmed AI compression archived {archived_count} source memories into {len(created)} reviewed summaries.",
            {
                "summary_count": len(created),
                "archived_count": archived_count,
                "rejected_groups": rejected_groups,
                "capacity_after": capacity_result.get("after"),
                "model": str(model_name or "")[:180],
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
            },
            compression_engine="ai_deepseek",
            token_usage=total_tokens,
        )
        result = {
            "status": "ai_compression_completed",
            "notify_user": True,
            "event": event,
            "summary_count": len(created),
            "archived_count": archived_count,
            "rejected_groups": rejected_groups,
            "memories": created,
            "compression_engine": "ai_deepseek",
            "model": str(model_name or ""),
            "usage": {
                "prompt_tokens": prompt_tokens,
                "completion_tokens": completion_tokens,
                "total_tokens": total_tokens,
                "actual": bool(usage.get("total_tokens") or usage.get("prompt_tokens")),
            },
            "capacity": capacity_result,
        }
        self.memory_maintenance["last_ai_offer_signature"] = None
        self.memory_maintenance["last_result"] = result
        self.save_memory_maintenance_state()
        self.last_maintenance_result = result
        return result

    def merge_memories(self, days=7, force=False, target_active=None, reason="manual"):
        """Compact compatible memories into bounded summaries and archive the originals."""
        try:
            days = max(0 if force else 1, min(3650, int(days)))
        except (TypeError, ValueError):
            days = int(self.settings.get("memory_compression_age_days", 30))
        cutoff = (datetime.now() - timedelta(days=days)).date()
        groups = {}
        blocked_generation_count = 0
        eligible_candidate_count = 0
        max_generation = max(1, int(self.settings.get("memory_compression_max_generations", 2)))
        for memory in self.memories:
            if memory.get("is_archived") or self._is_capacity_managed_memory(memory):
                continue
            layer = self._memory_layer(memory)
            if layer == "core" or memory.get("source") == "system":
                continue
            if self._infer_retention_policy(memory) == "permanent":
                continue
            if self._compression_generation(memory) >= max_generation:
                blocked_generation_count += 1
                continue
            if not force and (
                memory.get("is_locked")
                or memory.get("is_important")
                or self._infer_retention_policy(memory) not in {"temporary", "compressible"}
            ):
                continue
            try:
                memory_date = datetime.strptime(memory.get("date", ""), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                if not force:
                    continue
                memory_date = cutoff
            if memory_date <= cutoff:
                eligible_candidate_count += 1
                topic_key = self._compression_topic_key(memory)
                group_key = (
                    str(memory.get("project_id") or ""),
                    layer,
                    str(memory.get("category") or "general"),
                    topic_key,
                    "forced" if force else str(memory.get("date") or "")[:7],
                )
                groups.setdefault(group_key, []).append(memory)

        try:
            min_group = max(2, int(self.settings.get("memory_compression_min_group", 3)))
            group_size = max(min_group, int(self.settings.get("memory_compression_group_size", 20)))
            summary_chars = max(300, int(self.settings.get("memory_compression_summary_chars", 1800)))
        except (TypeError, ValueError):
            min_group, group_size, summary_chars = 3, 20, 1800
        if force:
            min_group = 2
        try:
            target_active = None if target_active is None else max(0, int(target_active))
        except (TypeError, ValueError):
            target_active = None

        merged_count = 0
        summaries = []
        for (project_id, layer, category, topic_key, month), group in sorted(groups.items()):
            group.sort(key=lambda item: (item.get("date", ""), item.get("creation_time", "")))
            for offset in range(0, len(group), group_size):
                if target_active is not None:
                    active_count = sum(1 for item in self.memories if not item.get("is_archived"))
                    if active_count <= target_active:
                        break
                chunk = group[offset:offset + group_size]
                if target_active is not None and len(chunk) >= 2:
                    reduction_needed = max(1, active_count - target_active)
                    chunk = chunk[:max(2, min(len(chunk), reduction_needed + 1))]
                if len(chunk) < min_group:
                    continue
                per_item_chars = max(80, int((summary_chars - 160) / len(chunk)))
                content_parts = []
                structured_facts = []
                for memory in chunk:
                    facts = self._extract_compression_facts(memory)
                    structured_facts.append({"memory_id": memory.get("id"), **facts})
                    content = self._normalize_text(memory.get("content"))
                    if len(content) > per_item_chars:
                        content = content[:per_item_chars].rstrip() + "…"
                    content_parts.append(
                        f"- [{memory.get('date', '')}] {memory.get('title', 'Untitled')}: {content}"
                    )
                    facts_preview = self._compression_fact_preview(facts)
                    if facts_preview:
                        content_parts.append(f"  Key facts: {facts_preview}")
                summary_content = "\n".join(content_parts)
                if len(summary_content) > summary_chars:
                    summary_content = summary_content[:summary_chars].rstrip() + "…"
                compression_generation = max(
                    self._compression_generation(memory) for memory in chunk
                ) + 1
                metadata = {
                    "memory_layer": "episodic" if layer == "episodic" else "semantic",
                    "memory_kind": "summary",
                    "retention_policy": "compressible",
                    "compressed_from": [memory.get("id") for memory in chunk if memory.get("id")],
                    "compression_generation": compression_generation,
                    "ai_compression_generation": max(
                        self._ai_compression_generation(memory) for memory in chunk
                    ),
                    "structured_facts": structured_facts,
                    "compression_scope": {
                        "project_id": project_id,
                        "category": category,
                        "topic_key": topic_key,
                        "month": month,
                        "source_layer": layer,
                    },
                }
                if project_id:
                    metadata.update({
                        "project_id": project_id,
                        "project_name": str(chunk[0].get("project_name") or "Untitled Project")[:80],
                        "project_scoped": True,
                    })
                merged = self.add_memory(
                    summary_content,
                    title=f"Compressed {category} · {month}",
                    source="capacity_compaction" if reason in {"soft_limit", "hard_limit"} else "auto_merge",
                    is_important=False,
                    category=category,
                    tags=["compressed", category, layer],
                    confidence=min(self._coerce_float(item.get("confidence"), 0.75) for item in chunk),
                    source_detail={
                        "type": "bounded_memory_compression",
                        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "reason": str(reason or "manual"),
                        "forced": bool(force),
                        "compression_engine": "deterministic_local",
                        "token_usage": 0,
                        "protection_version": 2,
                        "compression_generation": compression_generation,
                    },
                    metadata=metadata,
                    run_capacity_check=False,
                )
                summaries.append(merged)
                for memory in chunk:
                    memory["archived_from_layer"] = layer
                    memory["is_archived"] = True
                    memory["status"] = "archived"
                    memory["memory_layer"] = "archive"
                    memory["merged_into"] = merged["id"]
                    memory["parent_memory_id"] = merged["id"]
                merged_count += len(chunk)
        if not summaries:
            return {
                "status": "skipped",
                "merged_count": 0,
                "summary_count": 0,
                "eligible_candidate_count": eligible_candidate_count,
                "blocked_generation_count": blocked_generation_count,
                "all_candidates_at_generation_limit": bool(
                    blocked_generation_count and eligible_candidate_count == 0
                ),
                "compression_engine": "deterministic_local",
                "token_usage": 0,
            }
        self.save_memories()
        return {
            "status": "success",
            "merged_count": merged_count,
            "summary_count": len(summaries),
            "eligible_candidate_count": eligible_candidate_count,
            "blocked_generation_count": blocked_generation_count,
            "all_candidates_at_generation_limit": bool(
                blocked_generation_count and eligible_candidate_count == 0
            ),
            "memories": summaries,
            "compression_engine": "deterministic_local",
            "token_usage": 0,
        }

    def maybe_auto_merge(self):
        if not (
            self.settings.get("memory_compression_enabled", False)
            or self.settings.get("auto_merge_enabled", False)
        ):
            return {"status": "disabled", "merged_count": 0}
        today = datetime.now().strftime("%Y-%m-%d")
        if self.settings.get("last_auto_merge") == today:
            return {"status": "already_checked", "merged_count": 0}
        result = self.merge_memories(
            self.settings.get(
                "memory_compression_age_days",
                self.settings.get("auto_merge_days", 30),
            )
        )
        self.settings["last_auto_merge"] = today
        self.save_settings()
        return result

    # --- Explicit memory intent gate (V4 plan sections 3.2 / 6.2) ----------
    # Anchored, sentence-initial command regexes are a conservative FALLBACK:
    # the message itself must start with the save command. The previous
    # unanchored search matched ordinary discussion of "记住" mid-sentence and
    # stored fragments like "多少东西？".
    _EXPLICIT_SAVE_COMMANDS = (
        re.compile(
            r"^\s*(?:请|請|帮我|幫我|你要|以后|以後)?\s*"
            r"(?:一定|必須|必须|务必|千萬|千万(?:要)?)?\s*"
            r"(?:记住|記住|记一下|記一下|记下来|記下來)\s*"
            r"(?:这件事|這件事|这一点|這一點|[:：,，])?\s*"
            r"(?P<content>.+)$",
            re.DOTALL,
        ),
        re.compile(
            r"^\s*(?:please\s+)?remember(?:\s+that|\s+this|\s*[:：,，])?\s+(?P<content>.+)$",
            re.IGNORECASE | re.DOTALL,
        ),
        re.compile(
            r"^\s*(?:save|keep)\s+(?:this\s+)?(?:memory|note)\s*(?:[:：,，])?\s+(?P<content>.+)$",
            re.IGNORECASE | re.DOTALL,
        ),
    )
    # Quoted speech is a citation of someone else's words, not a command.
    _EXPLICIT_SAVE_QUOTE_START = re.compile(r"^\s*[「『“\"']")
    # Interrogative/fragment tails left after the command word.
    _EXPLICIT_SAVE_FRAGMENT_TAILS = ("了吗", "了没有", "吗", "呢", "么", "嘛", "了多少", "多少", "是不是", "行吗", "好吗", "没有")
    # Pronouns/demonstratives alone are not a standalone fact subject.
    _EXPLICIT_SAVE_PRONOUN_ONLY = re.compile(
        r"^(?:这件事|这一点|这个|那个|这些|那些|它|他|她|你|我|您的?|他的?|她的?)+$"
    )
    # Credentials never enter long-term memory text (plan section 6.7).
    _EXPLICIT_SAVE_SECRET_HINTS = re.compile(
        r"(密码|口令|pass\s?word|passwd|token|api\s?key|secret|credential|私钥|密钥|助记词|mnemonic)",
        re.IGNORECASE,
    )
    # Auto-star only on explicitly strengthened intent (plan section 7.3).
    _EXPLICIT_SAVE_PIN_WORDS = ("重要", "一定", "永远", "必须", "务必", "千万要")

    def _explicit_memory_reject_reason(self, text, captured):
        """Return a reason code when an anchored match still must not write."""
        if str(text).rstrip().endswith(("?", "？")) or str(captured).rstrip().endswith(("?", "？")):
            return "question"
        tail = str(captured or "").strip().rstrip("。.!！?？ \t")
        if tail in self._EXPLICIT_SAVE_FRAGMENT_TAILS:
            return "question"
        core = re.sub(r"[\s，。,.!！?？：:；;'\"“”‘’()（）\[\]【】、]+", "", str(captured or ""))
        if len(core) < 2:
            return "too_short"
        if self._EXPLICIT_SAVE_PRONOUN_ONLY.match(core):
            return "pronoun_only"
        if self._EXPLICIT_SAVE_SECRET_HINTS.search(str(captured or "")):
            return "secret_content"
        return ""

    def capture_explicit_memory(self, user_input, project_id=None, project_name=None, session_id=None):
        """Save only sentence-initial explicit 'remember this' commands.

        The regex fallback is deliberately conservative (V4 plan section 6.2):
        questions, quoted speech, fragments, pronoun-only content and
        credential-like payloads are rejected even when the command word
        matches. Ordinary chat that merely discusses "记住" never writes.
        """
        if not self.settings.get("auto_save_memories", True):
            return None
        text = str(user_input or "").strip()
        if not text:
            return None
        if self.settings.get("memory_v4_write_enabled", False):
            action = {}
            if project_id:
                action.update({
                    "scope_type": "project",
                    "scope_id": str(project_id),
                    "metadata": {
                        "project_id": str(project_id),
                        "project_name": str(project_name or "Untitled Project")[:80],
                    },
                })
            result = self.process_memory_write_v4(
                text,
                action=action or None,
                turn_id=session_id,
                actor="user",
            )
            committed = result.get("committed") if isinstance(result, dict) else []
            if committed:
                memory_id = committed[0].get("memory", {}).get("id")
                if memory_id:
                    return self._find_memory(memory_id)
            return None
        if self._EXPLICIT_SAVE_QUOTE_START.match(text):
            return None
        captured = None
        for pattern in self._EXPLICIT_SAVE_COMMANDS:
            match = pattern.match(text)
            if match:
                captured = match.group("content").strip()
                break
        if not captured or len(captured) < 2:
            return None
        reject_reason = self._explicit_memory_reject_reason(text, captured)
        if reject_reason:
            print(f"[memory] explicit capture rejected ({reject_reason}): {text[:80]}")
            return None

        normalized = re.sub(r"\s+", " ", captured).strip().lower()
        active_project_id = str(project_id or "")
        for memory in self.memories:
            existing = re.sub(r"\s+", " ", str(memory.get("content", ""))).strip().lower()
            existing_project_id = str(memory.get("project_id") or "")
            if existing == normalized and existing_project_id == active_project_id:
                return memory

        category = self.infer_category(captured, source="auto_explicit")
        tags = self.infer_tags(captured, category)
        metadata = {}
        if active_project_id:
            metadata = {
                "project_id": active_project_id,
                "project_name": str(project_name or "Untitled Project")[:80],
                "project_scoped": True
            }
            if "project" not in tags:
                tags.append("project")
        source_detail = {
            "type": "explicit_user_request",
            "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }
        if session_id:
            source_detail["chat_session_id"] = str(session_id)
        pin_requested = any(word in text for word in self._EXPLICIT_SAVE_PIN_WORDS)

        return self.add_memory(
            captured,
            title=f"Explicit Memory · {datetime.now().strftime('%Y-%m-%d')}",
            source="auto_explicit",
            is_important=pin_requested,
            category=category,
            tags=tags,
            confidence=0.92,
            source_detail=source_detail,
            metadata=metadata
        )

class CheckInManager:
    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "checkins.json")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        self.data = self.load_data()

    def load_data(self):
        defaults = {
            "schema_version": 2,
            "tasks": [],
            "records": {},
            "activity": {},
            "auto_reports": {
                "months": {},
                "years": {}
            }
        }
        if os.path.exists(self.file_path):
            try:
                with codecs.open(self.file_path, "r", "utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    defaults.update(loaded)
            except Exception as e:
                print(f"Error loading check-ins: {e}")
        if not isinstance(defaults.get("tasks"), list):
            defaults["tasks"] = []
        if not isinstance(defaults.get("records"), dict):
            defaults["records"] = {}
        if not isinstance(defaults.get("activity"), dict):
            defaults["activity"] = {}
        if not isinstance(defaults.get("auto_reports"), dict):
            defaults["auto_reports"] = {"months": {}, "years": {}}
        if not isinstance(defaults["auto_reports"].get("months"), dict):
            defaults["auto_reports"]["months"] = {}
        if not isinstance(defaults["auto_reports"].get("years"), dict):
            defaults["auto_reports"]["years"] = {}
        return defaults

    def save_data(self):
        try:
            with codecs.open(self.file_path, "w", "utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving check-ins: {e}")

    def _now(self):
        return datetime.now()

    def _today(self):
        return self._now().strftime("%Y-%m-%d")

    def get_daily_activity(self, date_str=None):
        date_str = self._safe_date(date_str)
        item = self.data.get("activity", {}).get(date_str, {})
        if not isinstance(item, dict):
            item = {}
        try:
            entry_count = max(0, int(item.get("entry_count") or 0))
        except (TypeError, ValueError):
            entry_count = 0
        return {
            "date": date_str,
            "entry_count": entry_count,
            "last_entry_at": item.get("last_entry_at"),
        }

    def record_app_entry(self, date_str=None):
        """Record one completed load of the main Saya page."""
        date_str = self._safe_date(date_str)
        store = self.data.setdefault("activity", {})
        item = self.get_daily_activity(date_str)
        item["entry_count"] += 1
        item["last_entry_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
        store[date_str] = item

        # Keep a little over one year of entry counters. Historical check-in
        # records and chat files remain the source of truth for their own data.
        cutoff = (self._now() - timedelta(days=400)).strftime("%Y-%m-%d")
        for old_date in list(store):
            if re.match(r"^\d{4}-\d{2}-\d{2}$", str(old_date)) and old_date < cutoff:
                store.pop(old_date, None)
        self.save_data()
        return dict(item)

    def _add_months(self, date_obj, delta):
        month_index = date_obj.month - 1 + int(delta)
        year = date_obj.year + month_index // 12
        month = month_index % 12 + 1
        day = min(date_obj.day, 28)
        return datetime(year, month, day)

    def _date_window(self):
        now = self._now()
        current_month = datetime(now.year, now.month, 1)
        min_month = self._add_months(current_month, -12)
        max_month = self._add_months(current_month, 12)
        next_after_max = self._add_months(max_month, 1)
        max_date = next_after_max - timedelta(days=1)
        return {
            "min_month": min_month.strftime("%Y-%m"),
            "max_month": max_month.strftime("%Y-%m"),
            "min_date": min_month.strftime("%Y-%m-%d"),
            "max_date": max_date.strftime("%Y-%m-%d")
        }

    def _clamp_year_month_to_window(self, year, month):
        window = self._date_window()
        current = f"{int(year):04d}-{int(month):02d}"
        if current < window["min_month"]:
            return int(window["min_month"][:4]), int(window["min_month"][5:7])
        if current > window["max_month"]:
            return int(window["max_month"][:4]), int(window["max_month"][5:7])
        return year, month

    def _clamp_date_to_window(self, date_str):
        date_str = self._safe_date(date_str)
        window = self._date_window()
        if date_str < window["min_date"]:
            return window["min_date"]
        if date_str > window["max_date"]:
            return window["max_date"]
        return date_str

    def is_date_in_edit_window(self, date_str):
        try:
            date_str = datetime.strptime(str(date_str)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return False
        window = self._date_window()
        return window["min_date"] <= date_str <= window["max_date"]

    def _safe_date(self, value=None):
        if not value:
            return self._today()
        try:
            return datetime.strptime(str(value)[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return self._today()

    def _safe_year_month(self, year=None, month=None):
        now = self._now()
        try:
            year = int(year) if year else now.year
        except (TypeError, ValueError):
            year = now.year
        try:
            month = int(month) if month else now.month
        except (TypeError, ValueError):
            month = now.month
        year = max(1970, min(2999, year))
        month = max(1, min(12, month))
        return year, month

    def _active_tasks(self):
        tasks = [
            task for task in self.data.get("tasks", [])
            if task.get("active", True)
        ]
        return sorted(tasks, key=lambda task: int(task.get("order", 9999)))

    def _task_created_date(self, task):
        created_at = str(task.get("created_at", ""))
        try:
            return datetime.strptime(created_at[:10], "%Y-%m-%d").strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            return "1970-01-01"

    def _is_task_due_on_date(self, task, date_str):
        date_str = self._safe_date(date_str)
        created = self._task_created_date(task)
        if created > date_str:
            return False
        frequency = str(task.get("frequency", "daily") or "daily").lower()
        try:
            current_dt = datetime.strptime(date_str, "%Y-%m-%d")
            created_dt = datetime.strptime(created, "%Y-%m-%d")
        except ValueError:
            return True
        weekday = current_dt.weekday()
        if frequency == "daily":
            return True
        if frequency == "weekdays":
            return weekday < 5
        if frequency == "weekends":
            return weekday >= 5
        if frequency == "weekly":
            return weekday == created_dt.weekday()
        if frequency == "monthly":
            return current_dt.day == created_dt.day
        return True

    def _tasks_for_date(self, date_str):
        date_str = self._safe_date(date_str)
        return [
            task for task in self._active_tasks()
            if self._is_task_due_on_date(task, date_str)
        ]

    def _has_any_tasks_for_date(self, date_str):
        date_str = self._safe_date(date_str)
        record = self.data.get("records", {}).get(date_str, {})
        return bool(self._tasks_for_date(date_str) or self._daily_tasks_for_record(record))

    def _normalize_record(self, date_str):
        date_str = self._safe_date(date_str)
        record = self.data["records"].get(date_str)
        if not isinstance(record, dict):
            record = {
                "date": date_str,
                "completions": {},
                "note": "",
                "updated_at": None
            }
            self.data["records"][date_str] = record
        if not isinstance(record.get("completions"), dict):
            record["completions"] = {}
        if not isinstance(record.get("daily_tasks"), list):
            record["daily_tasks"] = []
        if not isinstance(record.get("automatic_completions"), dict):
            record["automatic_completions"] = {}
        if "note" not in record:
            record["note"] = ""
        return record

    def _daily_tasks_for_record(self, record):
        tasks = record.get("daily_tasks", []) if isinstance(record, dict) else []
        clean = []
        for task in tasks:
            if not isinstance(task, dict):
                continue
            title = re.sub(r"\s+", " ", str(task.get("title") or "")).strip()
            if not title:
                continue
            clean.append({
                "id": str(task.get("id") or uuid.uuid4()),
                "title": title[:80],
                "done": bool(task.get("done", False)),
                "goal_id": str(task.get("goal_id") or "").strip() or None,
                "created_at": str(task.get("created_at") or self._now().strftime("%Y-%m-%d %H:%M:%S"))
            })
        return clean

    def add_task(self, title, goal_id=None, preset=None):
        preset = str(preset or "").strip().lower()
        if preset:
            if preset != "anki":
                raise ValueError("Unknown Daily task preset")
            for existing in self.data.get("tasks", []):
                if existing.get("active", True) and existing.get("preset") == preset:
                    return existing
            title = "Anki"
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        if not title:
            return None
        now = self._now().strftime("%Y-%m-%d %H:%M:%S")
        task = {
            "id": str(uuid.uuid4()),
            "title": title[:80],
            "frequency": "daily",
            "goal_id": str(goal_id or "").strip() or None,
            "order": len(self.data.get("tasks", [])),
            "active": True,
            "created_at": now
        }
        if preset:
            task.update({
                "preset": preset,
                "completion_mode": "automatic",
                "completion_source": "anki",
            })
        self.data["tasks"].append(task)
        self.save_data()
        return task

    def update_task(self, task_id, updates):
        if not isinstance(updates, dict):
            return False
        for task in self.data.get("tasks", []):
            if task.get("id") == task_id:
                is_preset = bool(task.get("preset"))
                if "title" in updates and not is_preset:
                    title = re.sub(r"\s+", " ", str(updates.get("title") or "")).strip()
                    if title:
                        task["title"] = title[:80]
                if "active" in updates:
                    task["active"] = bool(updates.get("active"))
                if "frequency" in updates and not is_preset:
                    frequency = str(updates.get("frequency") or "daily").lower()
                    if frequency in {"daily", "weekdays", "weekends", "weekly", "monthly"}:
                        task["frequency"] = frequency
                if "goal_id" in updates:
                    task["goal_id"] = str(updates.get("goal_id") or "").strip() or None
                if "order" in updates:
                    try:
                        task["order"] = int(updates.get("order"))
                    except (TypeError, ValueError):
                        pass
                self.save_data()
                return True
        return False

    def sync_preset_completion(self, preset, completed, evidence=None, date_str=None):
        """Persist a read-only task result supplied by its owning integration."""
        preset = str(preset or "").strip().lower()
        date_str = self._safe_date(date_str)
        tasks = [
            task for task in self._tasks_for_date(date_str)
            if str(task.get("preset") or "").strip().lower() == preset
            and str(task.get("completion_mode") or "") == "automatic"
        ]
        if not tasks:
            return {"changed": False, "task_count": 0, "completed": bool(completed)}

        record = self._normalize_record(date_str)
        completions = record.setdefault("completions", {})
        automatic = record.setdefault("automatic_completions", {})
        clean_evidence = evidence if isinstance(evidence, dict) else {}
        changed = False
        for task in tasks:
            task_id = task.get("id")
            if completions.get(task_id) != bool(completed):
                completions[task_id] = bool(completed)
                changed = True
            detail = {
                "preset": preset,
                "completed": bool(completed),
                "source": str(task.get("completion_source") or preset),
                **clean_evidence,
            }
            if automatic.get(task_id) != detail:
                automatic[task_id] = detail
                changed = True
        if changed:
            record["updated_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
            self.save_data()
        return {
            "changed": changed,
            "task_count": len(tasks),
            "completed": bool(completed),
        }

    def reorder_tasks(self, ordered_ids):
        if not isinstance(ordered_ids, list):
            return False
        order_map = {str(task_id): index for index, task_id in enumerate(ordered_ids)}
        for task in self.data.get("tasks", []):
            if task.get("id") in order_map:
                task["order"] = order_map[task.get("id")]
        self.data["tasks"].sort(key=lambda task: int(task.get("order", 9999)))
        self.save_data()
        return True

    def delete_task(self, task_id):
        before = len(self.data.get("tasks", []))
        self.data["tasks"] = [task for task in self.data.get("tasks", []) if task.get("id") != task_id]
        changed = len(self.data["tasks"]) != before
        if changed:
            self.save_data()
        return changed

    def save_day(self, date_str=None, completions=None, note=None, daily_tasks=None, mood=None, energy=None, difficulty=None):
        date_str = self._safe_date(date_str)
        if not self.is_date_in_edit_window(date_str):
            window = self._date_window()
            raise ValueError(
                f"Check-in date must be between {window['min_date']} and {window['max_date']}."
            )
        record = self._normalize_record(date_str)
        active_ids = {task.get("id") for task in self._tasks_for_date(date_str)}
        incoming = completions if isinstance(completions, dict) else {}
        clean = {}
        existing = record.get("completions", {})
        tasks_by_id = {
            task.get("id"): task for task in self._tasks_for_date(date_str)
        }
        for task_id in active_ids:
            task = tasks_by_id.get(task_id, {})
            if str(task.get("completion_mode") or "") == "automatic":
                clean[task_id] = bool(existing.get(task_id, False))
            else:
                clean[task_id] = bool(incoming.get(task_id, False))
        record["completions"] = clean
        if daily_tasks is not None:
            record["daily_tasks"] = self._daily_tasks_for_record({"daily_tasks": daily_tasks})
        if note is not None:
            record["note"] = str(note or "").strip()[:1000]
        for key, value in {"mood": mood, "energy": energy, "difficulty": difficulty}.items():
            if value is None:
                continue
            try:
                record[key] = max(1, min(5, int(value)))
            except (TypeError, ValueError):
                record[key] = None
        record["updated_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data()
        return self.decorate_record(date_str)

    def status_for_date(self, date_str):
        date_str = self._safe_date(date_str)
        tasks = self._tasks_for_date(date_str)
        record = self.data.get("records", {}).get(date_str, {})
        daily_tasks = self._daily_tasks_for_record(record)
        today = self._today()
        if not tasks and not daily_tasks:
            return {
                "status": "future" if date_str > today else "no_tasks",
                "done": 0,
                "total": 0,
                "ratio": 0
            }
        completions = record.get("completions", {}) if isinstance(record, dict) else {}
        done = sum(1 for task in tasks if completions.get(task.get("id")) is True)
        done += sum(1 for task in daily_tasks if task.get("done") is True)
        total = len(tasks) + len(daily_tasks)
        ratio = done / total if total else 0

        if done == total:
            status = "completed"
        elif done > 0:
            status = "partial"
        elif date_str < today:
            status = "missed"
        elif date_str == today:
            status = "pending"
        else:
            status = "future"

        return {
            "status": status,
            "done": done,
            "total": total,
            "ratio": round(ratio, 4)
        }

    def decorate_record(self, date_str):
        date_str = self._safe_date(date_str)
        record = dict(self._normalize_record(date_str))
        record["daily_tasks"] = self._daily_tasks_for_record(record)
        for key in ("mood", "energy", "difficulty", "chat_session_id"):
            record.setdefault(key, None)
        record.update(self.status_for_date(date_str))
        return record

    def _date_range_for_month(self, year, month):
        start = datetime(year, month, 1)
        if month == 12:
            end = datetime(year + 1, 1, 1)
        else:
            end = datetime(year, month + 1, 1)
        days = []
        cursor = start
        while cursor < end:
            days.append(cursor.strftime("%Y-%m-%d"))
            cursor += timedelta(days=1)
        return days

    def month_days(self, year=None, month=None):
        year, month = self._safe_year_month(year, month)
        days = []
        for date_str in self._date_range_for_month(year, month):
            status = self.status_for_date(date_str)
            record = self.data.get("records", {}).get(date_str, {})
            daily_tasks = self._daily_tasks_for_record(record)
            days.append({
                "date": date_str,
                "day": int(date_str[-2:]),
                "status": status["status"],
                "done": status["done"],
                "total": status["total"],
                "ratio": status["ratio"],
                "daily_task_count": len(daily_tasks),
                "note": record.get("note", "") if isinstance(record, dict) else ""
            })
        return days

    def _summary_for_dates(self, dates):
        eligible = [
            date_str for date_str in dates
            if date_str <= self._today() and self._has_any_tasks_for_date(date_str)
        ]
        counts = {
            "completed": 0,
            "partial": 0,
            "missed": 0,
            "pending": 0,
            "future": 0,
            "no_tasks": 0
        }
        total_done = 0
        total_possible = 0
        one_off_done = 0
        one_off_total = 0
        task_totals = {
            task.get("id"): {"title": task.get("title", ""), "done": 0, "possible": 0}
            for task in self._active_tasks()
        }

        current_streak = 0
        longest_streak = 0
        running_streak = 0

        for date_str in eligible:
            status = self.status_for_date(date_str)
            tasks_for_day = self._tasks_for_date(date_str)
            counts[status["status"]] = counts.get(status["status"], 0) + 1
            total_done += status["done"]
            total_possible += len(tasks_for_day)
            record = self.data.get("records", {}).get(date_str, {})
            completions = record.get("completions", {}) if isinstance(record, dict) else {}
            daily_tasks = self._daily_tasks_for_record(record)
            one_off_total += len(daily_tasks)
            one_off_done += sum(1 for task in daily_tasks if task.get("done") is True)
            total_possible += len(daily_tasks)
            for task in tasks_for_day:
                task_totals[task.get("id")]["possible"] += 1
                if completions.get(task.get("id")) is True:
                    task_totals[task.get("id")]["done"] += 1
            if status["status"] == "completed":
                running_streak += 1
                longest_streak = max(longest_streak, running_streak)
            else:
                running_streak = 0

        # Current streak: count backwards from today or latest eligible date.
        for date_str in reversed(eligible):
            if self.status_for_date(date_str)["status"] == "completed":
                current_streak += 1
            else:
                break

        rate = total_done / total_possible if total_possible else 0
        return {
            "days_counted": len(eligible),
            "completed_days": counts["completed"],
            "partial_days": counts["partial"],
            "missed_days": counts["missed"],
            "pending_days": counts["pending"],
            "completion_rate": round(rate, 4),
            "total_done": total_done,
            "total_possible": total_possible,
            "one_off_done": one_off_done,
            "one_off_total": one_off_total,
            "current_streak": current_streak,
            "longest_streak": longest_streak,
            "task_totals": list(task_totals.values())
        }

    def month_summary(self, year=None, month=None):
        year, month = self._safe_year_month(year, month)
        return self._summary_for_dates(self._date_range_for_month(year, month))

    def year_summary(self, year=None):
        now = self._now()
        try:
            year = int(year) if year else now.year
        except (TypeError, ValueError):
            year = now.year
        dates = []
        for month in range(1, 13):
            dates.extend(self._date_range_for_month(year, month))
        return self._summary_for_dates(dates)

    def _auto_report_store(self):
        auto_reports = self.data.setdefault("auto_reports", {})
        if not isinstance(auto_reports, dict):
            auto_reports = {"months": {}, "years": {}}
            self.data["auto_reports"] = auto_reports
        if not isinstance(auto_reports.get("months"), dict):
            auto_reports["months"] = {}
        if not isinstance(auto_reports.get("years"), dict):
            auto_reports["years"] = {}
        return auto_reports

    def maybe_auto_export_reports(self):
        now = self._now()
        auto_reports = self._auto_report_store()
        generated = []

        current_month_start = datetime(now.year, now.month, 1)
        previous_month = self._add_months(current_month_start, -1)
        month_label = previous_month.strftime("%Y-%m")

        if month_label not in auto_reports["months"]:
            month_days = self._date_range_for_month(previous_month.year, previous_month.month)
            month_summary = self.month_summary(previous_month.year, previous_month.month)
            if month_summary.get("days_counted", 0) > len(month_days) / 2:
                report = self.export_report(
                    scope="month",
                    year=previous_month.year,
                    month=previous_month.month,
                    generated_by="auto"
                )
                auto_reports["months"][month_label] = {
                    "generated_at": report.get("generated_at"),
                    "json_path": report.get("json_path"),
                    "markdown_path": report.get("markdown_path"),
                    "days_counted": month_summary.get("days_counted", 0)
                }
                generated.append({
                    "scope": "month",
                    "label": month_label,
                    "markdown_path": report.get("markdown_path"),
                    "json_path": report.get("json_path")
                })

        if now.month == 1:
            previous_year = now.year - 1
            year_label = str(previous_year)
            if year_label not in auto_reports["years"]:
                year_summary = self.year_summary(previous_year)
                days_in_year = 366 if (
                    previous_year % 4 == 0 and
                    (previous_year % 100 != 0 or previous_year % 400 == 0)
                ) else 365
                if year_summary.get("days_counted", 0) > days_in_year / 2:
                    report = self.export_report(
                        scope="year",
                        year=previous_year,
                        generated_by="auto"
                    )
                    auto_reports["years"][year_label] = {
                        "generated_at": report.get("generated_at"),
                        "json_path": report.get("json_path"),
                        "markdown_path": report.get("markdown_path"),
                        "days_counted": year_summary.get("days_counted", 0)
                    }
                    generated.append({
                        "scope": "year",
                        "label": year_label,
                        "markdown_path": report.get("markdown_path"),
                        "json_path": report.get("json_path")
                    })

        if generated:
            self.save_data()
            return {"status": "generated", "generated": generated}
        return {"status": "idle", "generated": []}

    def get_state(self, year=None, month=None, selected_date=None):
        year, month = self._safe_year_month(year, month)
        year, month = self._clamp_year_month_to_window(year, month)
        selected_date = self._clamp_date_to_window(selected_date)
        window = self._date_window()
        auto_report = self.maybe_auto_export_reports()
        visible_record_keys = [
            date_str for date_str in sorted(self.data.get("records", {}).keys())
            if window["min_date"] <= date_str <= window["max_date"]
        ]
        return {
            "current_time": self._now().strftime("%Y-%m-%d %H:%M:%S"),
            "today": self._today(),
            "year": year,
            "month": month,
            "selected_date": selected_date,
            "date_window": window,
            "auto_report": auto_report,
            "tasks": self.data.get("tasks", []),
            "active_tasks": self._active_tasks(),
            "selected_tasks": self._tasks_for_date(selected_date),
            "records": {
                date_str: self.decorate_record(date_str)
                for date_str in visible_record_keys
            },
            "selected_record": self.decorate_record(selected_date),
            "month_days": self.month_days(year, month),
            "month_summary": self.month_summary(year, month),
            "year_summary": self.year_summary(year)
        }

    def goal_progress(self, goal_id, start_date=None, end_date=None, limit=36):
        """Return bounded Daily task history for a Life Quest without rewriting old records."""
        goal_id = str(goal_id or "").strip()
        if not goal_id:
            return {"done": 0, "total": 0, "events": [], "truncated": False}

        start_date = self._safe_date(start_date) if start_date else "1970-01-01"
        end_date = self._safe_date(end_date) if end_date else self._today()
        end_date = min(end_date, self._today())
        linked_tasks = [
            task for task in self.data.get("tasks", [])
            if str(task.get("goal_id") or "") == goal_id and task.get("active", True)
        ]
        events = []

        for task in linked_tasks:
            task_start = max(start_date, self._task_created_date(task))
            try:
                cursor = datetime.strptime(task_start, "%Y-%m-%d")
                end_dt = datetime.strptime(end_date, "%Y-%m-%d")
            except ValueError:
                continue
            while cursor <= end_dt:
                date_str = cursor.strftime("%Y-%m-%d")
                if self._is_task_due_on_date(task, date_str):
                    record = self.data.get("records", {}).get(date_str, {})
                    completions = record.get("completions", {}) if isinstance(record, dict) else {}
                    events.append({
                        "date": date_str,
                        "task_id": str(task.get("id") or ""),
                        "title": str(task.get("title") or ""),
                        "done": completions.get(task.get("id")) is True,
                        "kind": "fixed"
                    })
                cursor += timedelta(days=1)

        for date_str, record in self.data.get("records", {}).items():
            if not (start_date <= str(date_str) <= end_date) or not isinstance(record, dict):
                continue
            for task in self._daily_tasks_for_record(record):
                if str(task.get("goal_id") or "") != goal_id:
                    continue
                events.append({
                    "date": str(date_str),
                    "task_id": str(task.get("id") or ""),
                    "title": str(task.get("title") or ""),
                    "done": task.get("done") is True,
                    "kind": "one_off"
                })

        events.sort(key=lambda item: (item.get("date", ""), item.get("title", ""), item.get("task_id", "")))
        total = len(events)
        done = sum(1 for item in events if item.get("done") is True)
        try:
            limit = max(1, min(120, int(limit)))
        except (TypeError, ValueError):
            limit = 36
        visible = events[-limit:]
        return {
            "done": done,
            "total": total,
            "events": visible,
            "truncated": total > len(visible)
        }

    def get_or_create_chat_session(self, date_str, memory_manager):
        date_str = self._safe_date(date_str)
        if not self.is_date_in_edit_window(date_str):
            window = self._date_window()
            raise ValueError(
                f"Check-in date must be between {window['min_date']} and {window['max_date']}."
            )
        record = self._normalize_record(date_str)
        session_id = record.get("chat_session_id")
        if session_id and memory_manager._safe_session_path(session_id) and os.path.exists(memory_manager._safe_session_path(session_id)):
            return session_id, False
        memory_manager.initialize_session()
        session_id = os.path.basename(memory_manager.current_session_file)
        record["chat_session_id"] = session_id
        record["updated_at"] = self._now().strftime("%Y-%m-%d %H:%M:%S")
        self.save_data()
        return session_id, True

    def export_report(self, scope="month", year=None, month=None, generated_by="manual"):
        scope = str(scope or "month").lower()
        now = self._now()
        try:
            year = int(year) if year else now.year
        except (TypeError, ValueError):
            year = now.year
        try:
            month = int(month) if month else now.month
        except (TypeError, ValueError):
            month = now.month
        export_dir = os.path.join(self.base_dir, "exports")
        os.makedirs(export_dir, exist_ok=True)
        if scope == "year":
            summary = self.year_summary(year)
            days = []
            for m in range(1, 13):
                days.extend(self.month_days(year, m))
            label = f"{year}"
            title = f"Daily Check-in Year Report {year}"
        else:
            year, month = self._safe_year_month(year, month)
            summary = self.month_summary(year, month)
            days = self.month_days(year, month)
            label = f"{year}-{month:02d}"
            title = f"Daily Check-in Month Report {label}"

        report_dates = {day.get("date") for day in days if day.get("date")}
        report_records = {
            date_str: record
            for date_str, record in self.data.get("records", {}).items()
            if date_str in report_dates
        }
        payload = {
            "scope": scope,
            "label": label,
            "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "generated_by": generated_by,
            "summary": summary,
            "days": days,
            "tasks": self.data.get("tasks", []),
            "records": report_records
        }
        json_path = os.path.abspath(os.path.join(export_dir, f"checkin_{scope}_{label}.json"))
        md_path = os.path.abspath(os.path.join(export_dir, f"checkin_{scope}_{label}.md"))
        with codecs.open(json_path, "w", "utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=4)

        def pct(value):
            return f"{round((value or 0) * 100)}%"

        lines = [
            f"# {title}",
            "",
            f"Generated at: {payload['generated_at']}",
            "",
            "## Summary",
            f"- Days counted: {summary['days_counted']}",
            f"- Completed days: {summary['completed_days']}",
            f"- Partial days: {summary['partial_days']}",
            f"- Missed days: {summary['missed_days']}",
            f"- Completion rate: {pct(summary['completion_rate'])}",
            f"- Current streak: {summary['current_streak']}",
            f"- Longest streak: {summary['longest_streak']}",
            f"- One-day tasks: {summary.get('one_off_done', 0)}/{summary.get('one_off_total', 0)}",
            "",
            "## Days",
            "| Date | Status | Done | Total | Note |",
            "|---|---:|---:|---:|---|"
        ]
        for day in days:
            record = self.data.get("records", {}).get(day["date"], {})
            note = str(record.get("note", "") if isinstance(record, dict) else "").replace("|", "\\|")
            lines.append(f"| {day['date']} | {day['status']} | {day['done']} | {day['total']} | {note} |")
        with codecs.open(md_path, "w", "utf-8") as f:
            f.write("\n".join(lines))
        return {
            "status": "success",
            "json_path": json_path,
            "markdown_path": md_path,
            "generated_at": payload["generated_at"],
            "generated_by": generated_by,
            "summary": summary
        }

    def build_dialogue_context(self, date_str=None):
        date_str = self._safe_date(date_str)
        if not self.is_date_in_edit_window(date_str):
            date_str = self._clamp_date_to_window(date_str)
        record = self.decorate_record(date_str)
        year, month = self._safe_year_month(date_str[:4], date_str[5:7])
        month_summary = self.month_summary(year, month)
        year_summary = self.year_summary(year)
        tasks = self._tasks_for_date(date_str)
        fixed_task_lines = []
        completions = record.get("completions", {})
        for task in tasks:
            fixed_task_lines.append(
                f"- {task.get('title')}: {'完成' if completions.get(task.get('id')) else '未完成'}"
            )
        if not fixed_task_lines:
            fixed_task_lines.append("- 还没有设置固定打卡任务。")

        daily_task_lines = []
        for task in self._daily_tasks_for_record(record):
            daily_task_lines.append(
                f"- {task.get('title')}: {'完成' if task.get('done') else '未完成'}"
            )
        if not daily_task_lines:
            daily_task_lines.append("- 今天没有额外的一次性任务。")

        def pct(value):
            return f"{round((value or 0) * 100)}%"

        return (
            f"日期时间：{self._now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"今天状态：{record.get('done', 0)}/{record.get('total', 0)}，状态={record.get('status')}\n"
            f"今日自评：mood={record.get('mood') or '未填写'}, energy={record.get('energy') or '未填写'}, difficulty={record.get('difficulty') or '未填写'}\n"
            f"今日固定任务：\n" + "\n".join(fixed_task_lines) + "\n"
            f"今日一次性任务：\n" + "\n".join(daily_task_lines) + "\n"
            f"今日备注：{record.get('note') or '无'}\n"
            f"本月概况：统计 {month_summary['days_counted']} 天，完成日 {month_summary['completed_days']} 天，"
            f"部分完成 {month_summary['partial_days']} 天，未达标 {month_summary['missed_days']} 天，"
            f"完成率 {pct(month_summary['completion_rate'])}，当前连续达标 {month_summary['current_streak']} 天。\n"
            f"今年概况：统计 {year_summary['days_counted']} 天，完成日 {year_summary['completed_days']} 天，"
            f"部分完成 {year_summary['partial_days']} 天，未达标 {year_summary['missed_days']} 天，"
            f"完成率 {pct(year_summary['completion_rate'])}，最长连续达标 {year_summary['longest_streak']} 天。\n"
            "对话风格要求：请 Saya 像自然关心 Doctor 一样回应，先回应今天，再轻轻带到本月和今年；"
            "不要像报表播报，不要责备，用具体观察和一个温柔可执行的小建议结束。"
        )

checkin_manager = CheckInManager()

kb_system = KnowledgeBase()


def refresh_daily_activity_memory(record_entry=False, date_str=None):
    """Refresh the protected memory Saya uses to understand today's state."""
    date_str = checkin_manager._safe_date(date_str)
    activity = (
        checkin_manager.record_app_entry(date_str)
        if record_entry
        else checkin_manager.get_daily_activity(date_str)
    )
    record = checkin_manager.decorate_record(date_str)
    fixed_tasks = checkin_manager._tasks_for_date(date_str)
    completions = record.get("completions", {}) if isinstance(record, dict) else {}
    task_details = [
        {
            "id": task.get("id"),
            "title": task.get("title", ""),
            "done": completions.get(task.get("id")) is True,
            "kind": "fixed",
        }
        for task in fixed_tasks
        if isinstance(task, dict)
    ]
    task_details.extend({
        "id": task.get("id"),
        "title": task.get("title", ""),
        "done": task.get("done") is True,
        "kind": "one_off",
    } for task in record.get("daily_tasks", []) if isinstance(task, dict))
    record["task_details"] = task_details
    chat_stats = memory_manager.get_daily_conversation_stats(date_str)
    memory = kb_system.upsert_daily_activity_memory(
        date_str,
        record,
        activity.get("entry_count", 0),
        chat_stats,
    )
    return {
        "date": date_str,
        "activity": activity,
        "checkin": {
            "status": record.get("status"),
            "done": record.get("done", 0),
            "total": record.get("total", 0),
        },
        "chat": chat_stats,
        "memory": memory,
    }


def sync_anki_checkin_state(anki_state):
    """Apply a fresh Anki snapshot to the Daily preset and managed memory."""
    anki_state = anki_state if isinstance(anki_state, dict) else {}
    summary = anki_state.get("summary")
    if not anki_state.get("connected") or not isinstance(summary, dict):
        return {"status": "unavailable", "task_count": 0, "memory": None}

    completed_decks = [
        str(name) for name in summary.get("completed_decks_today") or [] if str(name).strip()
    ]
    evidence = {
        "rule": str(summary.get("daily_task_rule") or ""),
        "completed_decks": completed_decks,
        "today_reviews": int(summary.get("today_reviews") or 0),
        "today_cards": int(summary.get("today_cards") or 0),
        "source_modified_at": str(anki_state.get("source_modified_at") or ""),
    }
    task_result = checkin_manager.sync_preset_completion(
        "anki",
        bool(summary.get("daily_task_completed")),
        evidence=evidence,
    )
    memory = kb_system.upsert_anki_checkin_memory(anki_state)
    if task_result.get("changed"):
        refresh_daily_activity_memory()
    return {
        "status": "synced",
        **task_result,
        "completed_decks": completed_decks,
        "memory": memory,
    }

class AssistantRoleManager:
    SUMMARY_MEMORY_ID = "assistant-role-learning-summary"
    AREA_MEMORY_PREFIX = "assistant-role-learning-area-"
    NOTE_MEMORY_PREFIX = "assistant-role-learning-note-"
    CURATED_MEMORY_PREFIX = "assistant-role-curated-"
    STATE_VERSION = 4

    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "assistant_role.json")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        self.state = self.load_state()

    def _default_state(self):
        return {
            "schema_version": self.STATE_VERSION,
            "vault_path": "",
            "read_mode": "",
            "last_read_at": None,
            "file_count": 0,
            "memories": [],
            "chapter_summary": [],
            "path_memories": [],
            "session_ids": [],
            "curated_extractions": [],
            "normal_memory_sync_version": 0,
        }

    def load_state(self):
        state = self._default_state()
        if os.path.exists(self.file_path):
            try:
                with codecs.open(self.file_path, "r", "utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    state.update(loaded)
            except Exception as e:
                print(f"Error loading Assistant role state: {e}")
        if not isinstance(state.get("memories"), list):
            state["memories"] = []
        if not isinstance(state.get("chapter_summary"), list):
            state["chapter_summary"] = []
        if not isinstance(state.get("path_memories"), list):
            state["path_memories"] = []
        if not isinstance(state.get("session_ids"), list):
            state["session_ids"] = []
        if not isinstance(state.get("curated_extractions"), list):
            state["curated_extractions"] = []
        return state

    def save_state(self):
        tmp_path = self.file_path + ".tmp"
        with codecs.open(tmp_path, "w", "utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.file_path)

    def default_vault_path(self):
        configured = os.environ.get("OBSIDIAN_VAULT_DIR", "").strip()
        if configured:
            return os.path.abspath(os.path.expanduser(configured))
        workspace_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        return os.path.join(workspace_root, "Obsidian")

    def _resolve_vault_path(self, vault_path=None):
        raw = str(vault_path or "").strip() or self.state.get("vault_path") or self.default_vault_path()
        path = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(path):
            raise ValueError(f"Obsidian vault path does not exist or is not a folder: {path}")
        return path

    def get_state(self, memory_manager=None):
        state = dict(self.state)
        state["memories"] = [
            {
                "id": memory.get("id"),
                "title": memory.get("title"),
                "file": memory.get("file"),
                "folder": memory.get("folder"),
                "top_group": memory.get("top_group"),
                "headings": memory.get("headings", [])[:12],
                "tags": memory.get("tags", [])[:10],
                "updated_at": memory.get("updated_at")
            }
            for memory in self.state.get("memories", [])
            if isinstance(memory, dict)
        ]
        state["default_vault_path"] = self.default_vault_path()
        state["memory_count"] = len(state.get("memories", []))
        state["path_memories"] = [
            {
                "path": item.get("path", ""),
                "type": item.get("type", "file"),
                "description": item.get("description", ""),
                "updated_at": item.get("updated_at"),
            }
            for item in self.state.get("path_memories", [])
            if isinstance(item, dict) and item.get("path")
        ]
        state["path_memory_count"] = len(state["path_memories"])
        state["curated_extractions"] = [
            {
                "memory_id": item.get("memory_id", ""),
                "path": item.get("path", ""),
                "chapter": item.get("chapter", ""),
                "mode": item.get("mode", "simple"),
                "title": item.get("title", ""),
                "content_chars": int(item.get("content_chars") or 0),
                "updated_at": item.get("updated_at"),
            }
            for item in state.get("curated_extractions", [])
            if isinstance(item, dict) and item.get("memory_id")
        ]
        state["curated_extraction_count"] = len(state["curated_extractions"])
        state["sessions"] = self.list_sessions(memory_manager) if memory_manager else []
        return state

    def _resolve_vault_entry(self, vault_path=None, relative_path=""):
        vault = os.path.realpath(self._resolve_vault_path(vault_path))
        relative_path = str(relative_path or "").strip().replace("\\", "/").strip("/")
        candidate = os.path.realpath(os.path.join(vault, relative_path.replace("/", os.sep)))
        try:
            inside_vault = os.path.normcase(os.path.commonpath([vault, candidate])) == os.path.normcase(vault)
        except ValueError:
            inside_vault = False
        if not inside_vault:
            raise ValueError("Selected path must stay inside the configured Obsidian vault.")
        if not os.path.exists(candidate):
            raise ValueError(f"Selected path does not exist: {relative_path or '.'}")
        if os.path.isfile(candidate) and not candidate.lower().endswith(".md"):
            raise ValueError("Only Markdown files or folders can be added as Assistant memory.")
        normalized_relative = os.path.relpath(candidate, vault).replace(os.sep, "/")
        if normalized_relative == ".":
            normalized_relative = ""
        return vault, candidate, normalized_relative

    def browse_vault(self, vault_path=None, relative_path=""):
        vault, current_path, normalized_relative = self._resolve_vault_entry(vault_path, relative_path)
        if not os.path.isdir(current_path):
            raise ValueError("Browse path must be a folder.")
        ignored_dirs = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
        entries = []
        with os.scandir(current_path) as iterator:
            for entry in iterator:
                if entry.name.startswith(".") or entry.name in ignored_dirs:
                    continue
                if entry.is_dir():
                    entry_type = "folder"
                elif entry.is_file() and entry.name.lower().endswith(".md"):
                    entry_type = "file"
                else:
                    continue
                relative_entry = os.path.relpath(entry.path, vault).replace(os.sep, "/")
                entries.append({
                    "name": entry.name,
                    "path": relative_entry,
                    "type": entry_type,
                })
        entries.sort(key=lambda item: (item["type"] != "folder", item["name"].lower()))
        parent = None
        if normalized_relative:
            parent = normalized_relative.rsplit("/", 1)[0] if "/" in normalized_relative else ""
        return {
            "vault_path": vault,
            "current_path": normalized_relative,
            "parent_path": parent,
            "entries": entries,
        }

    def get_note_outline(self, vault_path=None, relative_path=""):
        vault, selected_path, normalized_relative = self._resolve_vault_entry(vault_path, relative_path)
        if not os.path.isfile(selected_path):
            raise ValueError("Select a Markdown note to inspect its chapters.")
        text = self._read_note_text(selected_path, limit=300000)
        lines = text.splitlines()
        chapters = []
        for line_index, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
            if not match:
                continue
            title = self._clean_heading(match.group(2))
            if not title:
                continue
            chapters.append({
                "selector": f"line:{line_index}",
                "line": line_index,
                "level": len(match.group(1)),
                "title": title,
                "label": f"{'  ' * (len(match.group(1)) - 1)}{title}",
            })
        return {
            "vault_path": vault,
            "path": normalized_relative,
            "title": self._note_title(text, normalized_relative, []),
            "char_count": len(text),
            "chapters": chapters[:500],
            "source_truncated": len(text) >= 300000,
        }

    def prepare_note_extraction(self, vault_path=None, relative_path="", chapter_selector=""):
        vault, selected_path, normalized_relative = self._resolve_vault_entry(vault_path, relative_path)
        if not os.path.isfile(selected_path):
            raise ValueError("Only a Markdown note or one of its chapters can be extracted.")
        text = self._read_note_text(selected_path, limit=300000)
        lines = text.splitlines()
        chapter_title = ""
        selected_text = text
        selector = str(chapter_selector or "").strip()
        if selector:
            match = re.fullmatch(r"line:(\d+)", selector)
            if not match:
                raise ValueError("Invalid chapter selector.")
            start = int(match.group(1))
            if start < 0 or start >= len(lines):
                raise ValueError("Selected chapter no longer exists. Refresh the note outline.")
            heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[start].strip())
            if not heading:
                raise ValueError("Selected chapter heading no longer exists. Refresh the note outline.")
            level = len(heading.group(1))
            chapter_title = self._clean_heading(heading.group(2))
            end = len(lines)
            for index in range(start + 1, len(lines)):
                next_heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", lines[index].strip())
                if next_heading and len(next_heading.group(1)) <= level:
                    end = index
                    break
            selected_text = "\n".join(lines[start:end]).strip()
        title = self._note_title(text, normalized_relative, [])
        source_hash = hashlib.sha256(selected_text.encode("utf-8", errors="ignore")).hexdigest()
        selection_key = f"{normalized_relative.casefold()}|{selector}"
        memory_id = self.CURATED_MEMORY_PREFIX + hashlib.sha1(selection_key.encode("utf-8")).hexdigest()[:20]
        return {
            "memory_id": memory_id,
            "vault_path": vault,
            "path": normalized_relative,
            "title": title,
            "chapter": chapter_title,
            "chapter_selector": selector,
            "source_text": selected_text,
            "source_chars": len(selected_text),
            "source_hash": source_hash,
            "source_truncated": len(text) >= 300000,
        }

    def add_path_memory(self, vault_path=None, relative_path="", description=""):
        description = re.sub(r"\s+", " ", str(description or "")).strip()
        if not description:
            raise ValueError("Memory description is required.")
        if len(description) > 4000:
            raise ValueError("Memory description must be 4000 characters or fewer.")
        vault, selected_path, normalized_relative = self._resolve_vault_entry(vault_path, relative_path)
        display_path = normalized_relative or "Vault root"
        item_type = "folder" if os.path.isdir(selected_path) else "file"
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        path_memories = [
            dict(item) for item in self.state.get("path_memories", [])
            if isinstance(item, dict) and item.get("path")
        ]
        lookup_path = display_path.casefold()
        existing = next(
            (item for item in path_memories if str(item.get("path", "")).casefold() == lookup_path),
            None,
        )
        payload = {
            "path": display_path,
            "type": item_type,
            "description": description,
            "updated_at": now,
        }
        if existing:
            existing.update(payload)
        else:
            path_memories.append(payload)
        path_memories.sort(key=lambda item: (item.get("type") != "folder", item.get("path", "").lower()))
        self.state.update({
            "schema_version": self.STATE_VERSION,
            "vault_path": vault,
            "path_memories": path_memories,
        })
        self.save_state()
        return self.get_state(memory_manager)

    def add_session(self, session_id):
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        ids = [str(item) for item in self.state.get("session_ids", []) if item]
        if session_id not in ids:
            ids.insert(0, session_id)
        self.state["session_ids"] = ids[:200]
        self.save_state()

    def remove_session(self, session_id):
        session_id = str(session_id or "").strip()
        ids = [str(item) for item in self.state.get("session_ids", []) if str(item) != session_id]
        if ids != self.state.get("session_ids", []):
            self.state["session_ids"] = ids
            self.save_state()

    def list_sessions(self, memory_manager):
        if not memory_manager:
            return []
        saved_ids = [str(item) for item in self.state.get("session_ids", []) if item]
        sessions_by_id = {}
        for session in memory_manager.get_session_list():
            sid = session.get("id")
            if not sid:
                continue
            if session.get("role_mode") == "assistant" or sid in saved_ids:
                item = dict(session)
                item["role_mode"] = "assistant"
                sessions_by_id[sid] = item
        for sid in saved_ids:
            if sid not in sessions_by_id:
                sessions_by_id[sid] = {
                    "id": sid,
                    "name": sid.replace("session_", "").replace(".jsonl", ""),
                    "preview": "Empty assistant chat",
                    "time": "",
                    "last_qa_time": "",
                    "pinned": False,
                    "role_mode": "assistant"
                }
        sessions = list(sessions_by_id.values())
        sessions.sort(key=lambda item: (item.get("time") or "", item.get("id") or ""), reverse=True)
        return sessions

    def _markdown_files(self, vault_path):
        ignored_dirs = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
        files = []
        for root, dirs, names in os.walk(vault_path):
            dirs[:] = [d for d in dirs if d not in ignored_dirs and not d.startswith(".")]
            for name in names:
                if name.lower().endswith(".md"):
                    files.append(os.path.join(root, name))
        files.sort()
        return files

    def _read_note_text(self, path, limit=160000):
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                with codecs.open(path, "r", encoding) as f:
                    return f.read(limit)
            except UnicodeDecodeError:
                continue
        with codecs.open(path, "r", "utf-8", errors="ignore") as f:
            return f.read(limit)

    def _extract_note_memory(self, path, vault_path, detail):
        text = self._read_note_text(path)
        rel_path = os.path.relpath(path, vault_path).replace(os.sep, "/")
        folder = rel_path.rsplit("/", 1)[0] if "/" in rel_path else "Vault root"
        top_group = rel_path.split("/", 1)[0] if "/" in rel_path else "Vault root"
        headings = []
        for line in text.splitlines():
            match = re.match(r"^(#{1,4})\s+(.+?)\s*$", line.strip())
            if not match:
                continue
            level = len(match.group(1))
            if detail != "detailed" and level > 2:
                continue
            heading = self._clean_heading(match.group(2))
            if heading:
                headings.append(f"{'#' * level} {heading}")
            if len(headings) >= (24 if detail == "detailed" else 10):
                break
        title = self._note_title(text, rel_path, headings)
        tags = []
        brief_lines = []
        if detail == "detailed":
            tags = sorted(set(re.findall(r"(?<!\w)#([A-Za-z0-9_\-/\u4e00-\u9fff]{2,})", text)))[:10]
            for line in text.splitlines():
                clean = self._clean_heading(line)
                if not clean or clean.startswith("#") or clean.startswith("---"):
                    continue
                if len(clean) < 12:
                    continue
                brief_lines.append(clean[:180])
                if len(brief_lines) >= 3:
                    break
        content_lines = [
            f"File: {rel_path}",
            f"Folder: {folder}",
            f"Title: {title}"
        ]
        if headings:
            content_lines.append("Headings: " + " / ".join(headings[:12]))
        if tags:
            content_lines.append("Tags: " + ", ".join(tags))
        if brief_lines:
            content_lines.append("Signals: " + " | ".join(brief_lines))
        return {
            "id": hashlib.sha1(rel_path.encode("utf-8")).hexdigest()[:16],
            "title": title,
            "file": rel_path,
            "folder": folder,
            "top_group": top_group,
            "headings": headings,
            "tags": tags,
            "content": "\n".join(content_lines),
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        }

    def _clean_heading(self, value):
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]+))?\]\]", lambda match: match.group(2) or match.group(1), text)
        text = re.sub(r"^[-*+]\s+", "", text)
        return text[:180]

    def _note_title(self, text, rel_path, headings):
        for heading in headings:
            title = re.sub(r"^#+\s*", "", heading).strip()
            if title:
                return title[:120]
        for line in text.splitlines()[:40]:
            match = re.match(r"^#\s+(.+)$", line.strip())
            if match:
                return self._clean_heading(match.group(1))[:120]
        return os.path.splitext(os.path.basename(rel_path))[0][:120] or "Untitled note"

    def _build_chapter_summary(self, memories):
        groups = {}
        for memory in memories:
            group_name = memory.get("top_group") or memory.get("folder") or "Vault root"
            group = groups.setdefault(group_name, {
                "name": group_name,
                "note_count": 0,
                "titles": [],
                "headings": []
            })
            group["note_count"] += 1
            if memory.get("title") and len(group["titles"]) < 12:
                group["titles"].append(memory["title"])
            for heading in memory.get("headings", [])[:4]:
                if heading not in group["headings"] and len(group["headings"]) < 12:
                    group["headings"].append(heading)
        summary = list(groups.values())
        summary.sort(key=lambda item: (-item.get("note_count", 0), item.get("name", "")))
        return summary

    def read_vault(self, vault_path=None, detail="rough"):
        detail = "detailed" if detail == "detailed" else "rough"
        path = self._resolve_vault_path(vault_path)
        files = self._markdown_files(path)
        if not files:
            raise ValueError(f"No Markdown notes found in vault: {path}")
        max_files = 900 if detail == "detailed" else 600
        memories = []
        for file_path in files[:max_files]:
            try:
                memories.append(self._extract_note_memory(file_path, path, detail))
            except Exception as e:
                print(f"Assistant note read skipped {file_path}: {e}")
        if not memories:
            raise ValueError("No readable Markdown notes found.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.state.update({
            "schema_version": self.STATE_VERSION,
            "vault_path": path,
            "read_mode": detail,
            "last_read_at": now,
            "file_count": len(memories),
            "memories": memories,
            "chapter_summary": self._build_chapter_summary(memories)
        })
        self.save_state()
        return self.get_state(memory_manager)

    def build_prompt_context(self, user_input="", mode="normal"):
        memories = self.state.get("memories", [])
        path_memories = [
            item for item in self.state.get("path_memories", [])
            if isinstance(item, dict) and item.get("path") and item.get("description")
        ]
        if not memories and not path_memories:
            return (
                "[System: Active Role - Assistant]\n"
                "Assistant role is active. Ordinary long-term memory is intentionally disabled. "
                "No Assistant Obsidian memory has been built yet, so rely only on the current conversation and ask Doctor to batch read the vault when note context is needed.\n\n"
            )
        max_chars = 11000 if mode == "deep" else 7500
        parts = [
            "[System: Active Role - Assistant]",
            "Assistant role is active. Ordinary long-term memory is intentionally disabled for this chat.",
            "Use the dedicated Assistant memory from Doctor's Obsidian notes below. Treat it as an outline of learned/studied material, not as exhaustive source text.",
            f"Vault read mode: {self.state.get('read_mode') or 'rough'}; last read: {self.state.get('last_read_at') or 'unknown'}.",
            "",
        ]
        if path_memories:
            parts.append("Doctor's descriptions for selected vault paths (high-priority guidance):")
            for item in path_memories[:40]:
                parts.append(
                    f"- [{item.get('type', 'file')}] {item.get('path')}: {item.get('description')}"
                )
            parts.append("")
        parts.append("Major knowledge areas:")
        for group in self.state.get("chapter_summary", [])[:18]:
            titles = "; ".join(group.get("titles", [])[:6])
            line = f"- {group.get('name')}: {group.get('note_count', 0)} notes"
            if titles:
                line += f" ({titles})"
            parts.append(line)
        parts.append("")
        parts.append("Relevant note outlines:")
        total = len("\n".join(parts))
        for memory in memories[:(45 if mode == "deep" else 28)]:
            block = f"- {memory.get('title')} [{memory.get('file')}]\n  " + " / ".join(memory.get("headings", [])[:10])
            if total + len(block) > max_chars:
                break
            parts.append(block)
            total += len(block)
        return "\n".join(parts).strip() + "\n\n"

    def _normal_mode_summary_content(self):
        groups = self.state.get("chapter_summary", [])
        path_memories = [
            item for item in self.state.get("path_memories", [])
            if isinstance(item, dict) and item.get("path") and item.get("description")
        ]
        if not groups and not path_memories:
            return ""
        lines = [
            "Assistant role learning map from Obsidian notes.",
            f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}.",
            "This managed overview records only broad areas and representative titles. Individual notes enter long-term memory only after Doctor explicitly extracts them in Assistant."
        ]
        for group in groups[:24]:
            line = f"- {group.get('name')}: {group.get('note_count', 0)} notes"
            titles = [self._clean_heading(title) for title in group.get("titles", [])[:5]]
            titles = [title for title in titles if title]
            if titles:
                line += f" ({'; '.join(titles)})"
            lines.append(line)
        if path_memories:
            lines.append("Curated path descriptions:")
            for item in path_memories[:16]:
                lines.append(f"- {item.get('path')}: {item.get('description')}")
        return "\n".join(lines)[:2400]

    def _area_memory_id(self, area_name):
        digest = hashlib.sha1(str(area_name or "Vault root").casefold().encode("utf-8")).hexdigest()[:16]
        return self.AREA_MEMORY_PREFIX + digest

    def _note_memory_id(self, note):
        note_id = str(note.get("id") or "").strip()
        if not note_id:
            note_id = hashlib.sha1(str(note.get("file") or note.get("title") or "note").encode("utf-8")).hexdigest()[:16]
        return self.NOTE_MEMORY_PREFIX + note_id

    def _normal_mode_area_content(self, group):
        area_name = str(group.get("name") or "Vault root")
        lines = [
            f"Obsidian knowledge area: {area_name}",
            f"Notes: {int(group.get('note_count') or 0)}",
        ]
        titles = [self._clean_heading(title) for title in group.get("titles", [])]
        titles = [title for title in titles if title]
        if titles:
            lines.append("Representative notes: " + "; ".join(titles[:20]))
        headings = [self._clean_heading(heading) for heading in group.get("headings", [])]
        headings = [heading for heading in headings if heading]
        if headings:
            lines.append("Topics: " + " / ".join(headings[:20]))
        lines.append("Individual note outlines are stored as child semantic memories and retrieved only when relevant.")
        return "\n".join(lines)[:6000]

    def _upsert_learning_memory(self, kb, memory_id, values, now):
        memory = kb._find_memory(memory_id)
        created = memory is None
        if created:
            memory = {
                "id": memory_id,
                "creation_time": now,
                "date": now[:10],
                "access_count": 0,
                "last_accessed": None,
            }
            kb.memories.append(memory)
        previous = json.dumps(memory, ensure_ascii=False, sort_keys=True, default=str)
        memory.update(values)
        memory["last_updated"] = now
        kb.normalize_memory(memory)
        current = json.dumps(memory, ensure_ascii=False, sort_keys=True, default=str)
        return memory, created or previous != current

    def _update_normal_mode_memory_split_legacy(self, kb):
        content = self._normal_mode_summary_content()
        if not content:
            return {"updated": False, "memory": None, "area_count": 0, "note_count": 0}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changed = False
        memory, item_changed = self._upsert_learning_memory(kb, self.SUMMARY_MEMORY_ID, {
            "title": "Assistant Role · Obsidian Learning Map",
            "content": content,
            "source": "assistant_role",
            "source_detail": {
                "type": "assistant_role_learning_index",
                "updated_at": now
            },
            "category": "learning_map",
            "tags": ["assistant_role", "obsidian", "learning_map", "knowledge_index"],
            "confidence": 0.95,
            "importance_score": 0.78,
            "is_locked": False,
            "is_important": True,
            "is_archived": False,
            "managed_slot": "assistant_role:learning_map:index",
            "memory_layer": "semantic",
            "memory_kind": "knowledge_index",
            "parent_memory_id": None,
            "child_count": len(self.state.get("chapter_summary", [])),
            "note_count": len(self.state.get("memories", [])),
        }, now)
        changed = changed or item_changed

        active_ids = {self.SUMMARY_MEMORY_ID}
        area_ids = {}
        for group in self.state.get("chapter_summary", []):
            if not isinstance(group, dict):
                continue
            area_name = str(group.get("name") or "Vault root")
            area_id = self._area_memory_id(area_name)
            area_ids[area_name.casefold()] = area_id
            active_ids.add(area_id)
            _, item_changed = self._upsert_learning_memory(kb, area_id, {
                "title": f"Obsidian Area · {area_name}",
                "content": self._normal_mode_area_content(group),
                "source": "assistant_role",
                "source_detail": {"type": "assistant_role_learning_area", "updated_at": now},
                "category": "learning_map",
                "tags": ["assistant_role", "obsidian", "learning_area", self._clean_heading(area_name).lower()[:60]],
                "confidence": 0.92,
                "importance_score": 0.58,
                "is_locked": False,
                "is_important": False,
                "is_archived": False,
                "managed_slot": f"assistant_role:learning_area:{area_id}",
                "memory_layer": "semantic",
                "memory_kind": "knowledge_area",
                "parent_memory_id": self.SUMMARY_MEMORY_ID,
                "learning_area": area_name,
                "note_count": int(group.get("note_count") or 0),
            }, now)
            changed = changed or item_changed

        note_count = 0
        for note in self.state.get("memories", []):
            if not isinstance(note, dict):
                continue
            note_id = self._note_memory_id(note)
            active_ids.add(note_id)
            note_count += 1
            area_name = str(note.get("top_group") or note.get("folder") or "Vault root")
            area_id = area_ids.get(area_name.casefold()) or self._area_memory_id(area_name)
            title = self._clean_heading(note.get("title")) or "Untitled note"
            content = str(note.get("content") or "").strip()
            if not content:
                content = f"File: {note.get('file') or ''}\nTitle: {title}"
            note_tags = [self._clean_heading(tag) for tag in list(note.get("tags", []))[:6]]
            note_tags = [tag for tag in note_tags if tag]
            _, item_changed = self._upsert_learning_memory(kb, note_id, {
                "title": f"Obsidian Note · {title}",
                "content": content[:12000],
                "source": "obsidian",
                "source_detail": {
                    "type": "assistant_role_learning_note",
                    "file": note.get("file", ""),
                    "updated_at": note.get("updated_at") or now,
                },
                "category": "learning_note",
                "tags": ["assistant_role", "obsidian", "learning_note", self._clean_heading(area_name).lower()[:60], *note_tags],
                "confidence": 0.9,
                "importance_score": 0.46,
                "is_locked": False,
                "is_important": False,
                "is_archived": False,
                "managed_slot": f"assistant_role:learning_note:{note_id}",
                "memory_layer": "semantic",
                "memory_kind": "knowledge_note",
                "parent_memory_id": area_id,
                "learning_area": area_name,
                "note_ref": note.get("file", ""),
            }, now)
            changed = changed or item_changed

        archived_count = 0
        for existing in kb.memories:
            source_type = ""
            if isinstance(existing.get("source_detail"), dict):
                source_type = str(existing["source_detail"].get("type") or "")
            memory_id = str(existing.get("id") or "")
            is_learning_memory = (
                memory_id.startswith((self.AREA_MEMORY_PREFIX, self.NOTE_MEMORY_PREFIX))
                or source_type in {"assistant_role_learning_area", "assistant_role_learning_note"}
            )
            if is_learning_memory and memory_id not in active_ids and not existing.get("is_archived"):
                existing["is_archived"] = True
                existing["archived_from_layer"] = "semantic"
                existing["memory_layer"] = "archive"
                existing["status"] = "archived"
                existing["last_updated"] = now
                archived_count += 1
                changed = True

        self.state["schema_version"] = self.STATE_VERSION
        self.state["normal_memory_sync_version"] = self.STATE_VERSION
        self.state["normal_memory_synced_at"] = now
        self.save_state()
        if changed:
            kb.save_memories()
        return {
            "updated": changed,
            "memory": memory,
            "area_count": len(area_ids),
            "note_count": note_count,
            "archived_count": archived_count,
        }

    def update_normal_mode_memory(self, kb):
        """Keep one broad map; only explicitly curated extracts become separate memories."""
        content = self._normal_mode_summary_content()
        if not content:
            return {"updated": False, "memory": None, "archived_split_count": 0}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory = kb._find_memory(self.SUMMARY_MEMORY_ID)
        if not memory:
            memory = {
                "id": self.SUMMARY_MEMORY_ID,
                "creation_time": now,
                "date": now[:10],
                "access_count": 0,
                "last_accessed": None,
            }
            kb.memories.append(memory)
        memory.update({
            "title": "Assistant Role · Obsidian Learning Map",
            "content": content,
            "source": "assistant_role",
            "source_detail": {
                "type": "assistant_role_learning_map",
                "captured_at": memory.get("creation_time", now),
                "updated_at": now,
            },
            "category": "learning_map",
            "tags": ["assistant_role", "obsidian", "learning_map"],
            "confidence": 0.92,
            "importance_score": 0.72,
            "is_locked": True,
            "is_important": True,
            "is_archived": False,
            "managed_slot": "assistant_role:learning_map:index",
            "memory_layer": "semantic",
            "memory_kind": "knowledge_index",
            "parent_memory_id": None,
            "note_count": len(self.state.get("memories", [])),
            "last_updated": now,
        })
        kb.normalize_memory(memory)

        legacy_split_count = 0
        for existing in kb.memories:
            memory_id = str(existing.get("id") or "")
            source_type = ""
            if isinstance(existing.get("source_detail"), dict):
                source_type = str(existing["source_detail"].get("type") or "")
            was_automatic_split = (
                memory_id.startswith((self.AREA_MEMORY_PREFIX, self.NOTE_MEMORY_PREFIX))
                or source_type in {"assistant_role_learning_area", "assistant_role_learning_note"}
            )
            if was_automatic_split:
                legacy_split_count += 1

        self.state["schema_version"] = self.STATE_VERSION
        self.state["normal_memory_sync_version"] = self.STATE_VERSION
        self.state["normal_memory_synced_at"] = now
        self.save_state()
        kb.save_memories()
        return {
            "updated": True,
            "memory": memory,
            "archived_split_count": 0,
            "legacy_split_count": legacy_split_count,
            "curated_extraction_count": len(self.state.get("curated_extractions", [])),
        }

    def save_curated_extraction(self, kb, prepared, generated_content, mode="simple", model_name=""):
        mode = "complex" if str(mode).lower() == "complex" else "simple"
        memory_kind = f"knowledge_extract_{mode}"
        bounded, was_truncated, generated_chars = kb.bound_generated_memory_content(
            generated_content,
            memory_kind=memory_kind,
            memory_layer="semantic",
        )
        if not bounded:
            raise ValueError("The extraction model returned an empty memory.")
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory_id = str(prepared.get("memory_id") or "")
        if not kb.revive_deleted_memory(memory_id):
            raise ValueError("Unable to clear the previous deletion marker for this curated memory.")
        memory = kb._find_memory(memory_id)
        if not memory:
            memory = {
                "id": memory_id,
                "creation_time": now,
                "date": now[:10],
                "access_count": 0,
                "last_accessed": None,
            }
            kb.memories.append(memory)
        note_title = str(prepared.get("title") or "Obsidian note")
        chapter = str(prepared.get("chapter") or "")
        display_title = f"Obsidian · {note_title}" + (f" · {chapter}" if chapter else "")
        memory.update({
            "title": display_title[:220],
            "content": bounded,
            "source": "obsidian",
            "source_detail": {
                "type": "assistant_role_curated_extraction",
                "file": prepared.get("path", ""),
                "chapter": chapter,
                "chapter_selector": prepared.get("chapter_selector", ""),
                "source_hash": prepared.get("source_hash", ""),
                "source_chars": int(prepared.get("source_chars") or 0),
                "extraction_mode": mode,
                "model": str(model_name or ""),
                "updated_at": now,
            },
            "category": "learning_note",
            "tags": ["assistant_role", "obsidian", "curated", mode],
            "confidence": 0.9,
            "importance_score": 0.84 if mode == "complex" else 0.8,
            "is_locked": False,
            "is_important": True,
            "is_archived": False,
            "managed_slot": f"assistant_role:curated:{memory_id}",
            "memory_layer": "semantic",
            "memory_kind": memory_kind,
            "parent_memory_id": self.SUMMARY_MEMORY_ID,
            "note_ref": prepared.get("path", ""),
            "chapter_ref": chapter,
            "extraction_mode": mode,
            "content_truncated": was_truncated,
            "original_generated_chars": generated_chars,
            "last_updated": now,
        })
        kb.normalize_memory(memory)
        records = [
            dict(item) for item in self.state.get("curated_extractions", [])
            if isinstance(item, dict) and item.get("memory_id") != memory_id
        ]
        records.insert(0, {
            "memory_id": memory_id,
            "path": prepared.get("path", ""),
            "chapter": chapter,
            "chapter_selector": prepared.get("chapter_selector", ""),
            "mode": mode,
            "title": display_title[:220],
            "content_chars": len(bounded),
            "source_chars": int(prepared.get("source_chars") or 0),
            "source_hash": prepared.get("source_hash", ""),
            "updated_at": now,
        })
        self.state["curated_extractions"] = records[:500]
        self.save_state()
        kb.save_memories()
        return {
            "memory": memory,
            "content_limit": kb.memory_content_limit(memory_kind, "semantic"),
            "content_truncated": was_truncated,
        }

    def remove_curated_extraction(self, kb, memory_id):
        memory_id = str(memory_id or "").strip()
        memory = kb._find_memory(memory_id)
        if not memory or not str(memory.get("managed_slot") or "").startswith("assistant_role:curated:"):
            return {"removed": False}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory["is_archived"] = True
        memory["archived_from_layer"] = "semantic"
        memory["memory_layer"] = "archive"
        memory["status"] = "archived"
        memory["last_updated"] = now
        self.state["curated_extractions"] = [
            item for item in self.state.get("curated_extractions", [])
            if isinstance(item, dict) and str(item.get("memory_id") or "") != memory_id
        ]
        self.save_state()
        kb.save_memories()
        return {"removed": True, "memory_id": memory_id}

assistant_role_manager = AssistantRoleManager()

if (
    assistant_role_manager.state.get("memories")
    and int(assistant_role_manager.state.get("normal_memory_sync_version") or 0) < AssistantRoleManager.STATE_VERSION
):
    try:
        assistant_role_manager.update_normal_mode_memory(kb_system)
    except Exception as exc:
        print(f"Assistant learning-map migration deferred: {exc}")

class VisionService:
    def __init__(self):
        self.model_path = str(LLAVA_MODEL_PATH)
        self.mmproj_path = str(LLAVA_MMPROJ_PATH)
        self.llm = None
        self.chat_handler = None

    def load_model(self):
        if self.llm:
            return
        
        print("Loading Vision Model...")
        try:
            try:
                from llama_cpp import Llama
                from llama_cpp.llama_chat_format import Llava15ChatHandler
            except ImportError:
                print("Local vision requires llama-cpp-python.")
                return

            self.chat_handler = Llava15ChatHandler(clip_model_path=self.mmproj_path)
            
            with safe_c_execution():
                self.llm = Llama(
                    model_path=self.model_path,
                    chat_handler=self.chat_handler,
                    n_ctx=2048,
                    n_gpu_layers=0, # Use CPU to avoid fighting for VRAM with main model
                    verbose=False
                )
            print("Vision Model loaded.")
        except Exception as e:
            print(f"Failed to load Vision Model: {e}")

    def describe_image(self, image_path):
        log_to_file(f"Starting vision analysis for {image_path}")
        self.load_model()
        if not self.llm:
            log_to_file("Vision model unavailable")
            return "Vision capability unavailable."
            
        try:
            # Convert image to base64 data URI
            with open(image_path, "rb") as f:
                base64_image = base64.b64encode(f.read()).decode('utf-8')

            ext = os.path.splitext(image_path)[1].lower()
            mime_type = {
                ".png": "image/png",
                ".webp": "image/webp",
                ".bmp": "image/bmp"
            }.get(ext, "image/jpeg")
            data_uri = f"data:{mime_type};base64,{base64_image}"
            
            messages = [
                {"role": "system", "content": "You are an assistant who perfectly describes images."},
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": "Describe the main objects and actions in this image concisely and accurately. Do not hallucinate details that are not clearly visible. Start directly with the description."}
                    ]
                }
            ]
            
            log_to_file("Sending image to LLaVA...")
            with safe_c_execution():
                response = self.llm.create_chat_completion(messages=messages, max_tokens=300)
            result = response["choices"][0]["message"]["content"]
            log_to_file(f"Vision analysis result: {result[:50]}...")
            return result
            
        except Exception as e:
            print(f"Vision analysis error: {e}")
            log_to_file(f"Vision analysis error: {e}")
            return f"Error analyzing image: {str(e)}"

vision_service = VisionService()

class EmotionEngine:
    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "emotion_state.json")
        self.state = self.load_state()

    def load_state(self):
        if os.path.exists(self.file_path):
            try:
                with codecs.open(self.file_path, "r", "utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading emotion state: {e}")
        
        # Default state
        return {
            "happiness": 50,
            "excitement": 50,
            "stress": 0,
            "last_update": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "is_locked": False
        }

    def save_state(self):
        try:
            self.state["last_update"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with codecs.open(self.file_path, "w", "utf-8") as f:
                json.dump(self.state, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving emotion state: {e}")

    def calculate_offline_changes(self):
        """Calculate changes that happened while offline"""
        # Snapshot before decay
        old_h = self.state.get("happiness", 50)
        old_e = self.state.get("excitement", 50)
        old_s = self.state.get("stress", 0)
        
        # Apply decay
        self.apply_time_decay()
        self.save_state() # Ensure new state is saved
        
        # Snapshot after decay
        new_h = self.state.get("happiness", 50)
        new_e = self.state.get("excitement", 50)
        new_s = self.state.get("stress", 0)
        
        # Calculate diff
        diff_h = int(new_h - old_h)
        diff_e = int(new_e - old_e)
        diff_s = int(new_s - old_s)
        
        changes = []
        if diff_h != 0: changes.append(f"Happiness {'+' if diff_h > 0 else ''}{diff_h}")
        if diff_e != 0: changes.append(f"Excitement {'+' if diff_e > 0 else ''}{diff_e}")
        if diff_s != 0: changes.append(f"Stress {'+' if diff_s > 0 else ''}{diff_s}")
        
        if not changes:
            return {"message": "No significant emotional changes while you were away.", "changes": []}
            
        return {
            "message": f"While you were away: {', '.join(changes)}",
            "changes": [diff_h, diff_e, diff_s]
        }

    def apply_time_decay(self):
        """Apply time-based decay to emotions"""
        if self.state.get("is_locked", False):
            return # Do not decay if locked

        last_str = self.state.get("last_update")
        if not last_str:
            return

        try:
            last_time = datetime.strptime(last_str, "%Y-%m-%d %H:%M:%S")
            current_time = datetime.now()
            hours_passed = (current_time - last_time).total_seconds() / 3600.0
            
            # If time has passed, apply decay
            if hours_passed > 0.1: # Only if at least 6 minutes passed
                # Decay logic:
                # 1. Extreme emotions (near 0 or 100) decay SLOWER (maintain state longer).
                # 2. Moderate emotions decay FASTER towards neutral (50).
                # 3. Base decay rate is around 10% per hour.
                
                for k in ["happiness", "excitement"]:
                    val = self.state[k]
                    dist = val - 50
                    
                    # Calculate decay rate based on intensity
                    # Intensity 0 (at 50) -> Rate 0.15 (Fast decay for small fluctuations)
                    # Intensity 1 (at 0/100) -> Rate 0.05 (Slow decay for deep moods)
                    intensity = abs(dist) / 50.0
                    
                    # MODIFIED: If short interval (Backend likely open/active), reduce decay significantly
                    if hours_passed < 1.0:
                        decay_rate = 0.01 # Very stable during active use
                    else:
                        decay_rate = 0.15 - (0.10 * intensity) 
                    
                    # Apply decay
                    change = dist * decay_rate * hours_passed
                    
                    # Prevent overshooting
                    if abs(change) > abs(dist):
                        self.state[k] = 50
                    else:
                        self.state[k] -= change
                
                # Stress always decays
                s_val = self.state["stress"]
                s_rate = 0.2 # 20% per hour
                self.state["stress"] = max(0, s_val - (s_val * s_rate * hours_passed))
                
        except Exception as e:
            print(f"Time decay error: {e}")

    def analyze_emotion_llm(self, user_input, llama_instance):
        """Use LLM to analyze emotion changes for better logic"""
        prompt = (
            "[System]: You are the Emotion Engine for Saya. Analyze the User's input and output the emotional changes for Saya.\n"
            "Saya loves the user (Doctor) deeply.\n"
            "Rules:\n"
            "1. If User is sad/hurt -> Saya feels empathetic sadness (Happiness -10 to -20, Stress +5 to +10).\n"
            "2. If User is happy/loving -> Saya feels happy (Happiness +10 to +20).\n"
            "3. If User is angry/cold -> Saya feels stressed/scared (Stress +10 to +20, Happiness -10).\n"
            "4. If User is neutral/chatty -> Minor changes.\n"
            "5. Understand negation (e.g., 'not happy' is sad).\n"
            f"Current State: Happiness={int(self.state['happiness'])}, Excitement={int(self.state['excitement'])}, Stress={int(self.state['stress'])}\n"
            f"User Input: \"{user_input}\"\n"
            "Output ONLY a JSON object with integer deltas. Example: {\"happiness\": -10, \"excitement\": 0, \"stress\": 5}\n"
            "Response:"
        )
        
        try:
            output = llama_instance(prompt, max_tokens=100, echo=False)
            text = output["choices"][0]["text"].strip()
            # Find JSON in text
            start = text.find("{")
            end = text.rfind("}") + 1
            if start != -1 and end != -1:
                import json
                return json.loads(text[start:end])
        except Exception as e:
            print(f"LLM Emotion Analysis failed: {e}")
        return None

    def update_emotions(self, user_input, llama_instance=None):
        # 0. Apply time decay first
        self.apply_time_decay()

        if self.state.get("is_locked", False):
            # Even if locked, we save the state to update timestamp so decay doesn't accumulate massively
            # But wait, if locked, we shouldn't change values.
            # We just update timestamp? No, if we update timestamp without changing values, we are effectively "skipping" decay.
            # Which is what we want.
            self.save_state()
            return self.state

        # 1. Try LLM Analysis if available (preferred for logic)
        if llama_instance:
            deltas = self.analyze_emotion_llm(user_input, llama_instance)
            if deltas:
                self.state["happiness"] += deltas.get("happiness", 0)
                self.state["excitement"] += deltas.get("excitement", 0)
                self.state["stress"] += deltas.get("stress", 0)
            else:
                # Fallback to heuristics if LLM fails
                self._apply_heuristics(user_input)
        else:
            self._apply_heuristics(user_input)

        # Clamp values
        for k in ["happiness", "excitement", "stress"]:
            self.state[k] = max(0, min(100, self.state[k]))

        # 2. Determine Dominant Emotion
        h = self.state["happiness"]
        e = self.state["excitement"]
        s = self.state["stress"]

        if s > 60:
            self.state["dominant"] = "stressed" if e > 50 else "depressed"
        elif h > 70:
            self.state["dominant"] = "excited" if e > 60 else "happy"
        elif h < 30:
            self.state["dominant"] = "sad"
        elif e > 80:
            self.state["dominant"] = "hyper"
        elif e < 30:
            self.state["dominant"] = "tired"
        else:
            self.state["dominant"] = "neutral"

        self.save_state()
        return self.state

    def _apply_heuristics(self, user_input):
        pass

    def get_system_prompt_addition(self):
        return ""

emotion_engine = EmotionEngine()

class MotionIntelligence:
    CHAT_MOTION_CATEGORIES = {
        'surprised',
        'happy',
        'angry',
        'coquettish',
        'helpless',
        'refusal',
        'worried',
        'confused_thinking',
        'giving_answer',
    }
    CHAT_MOTION_EXCLUDED_IDS = set()
    CHAT_MOTION_POOLS = {
        'surprised': (
            'surprised_big',
            'surprised_small',
        ),
        'happy': (
            'bold_snicker',
            'cautious_smile',
            'delighted_surprise',
            'hearty_laugh',
            'hold_hands_119',
            'shy_blush',
        ),
        'angry': (
            'coquettish_you_are_bad',
            'jealous_pout',
            'pout_turn_head_hmph',
            'teasing_are_you_picking_a_fight',
        ),
        'coquettish': (
            'Fairy_idle_362',
            'hold_hands_119',
            'looking_96',
            'playful_finger_gun',
        ),
        'helpless': (
            'helpless_listening_look',
            'reassuring_nevermind',
        ),
        'refusal': (
            'refusal_no',
            'refusal_no_alt',
        ),
        'worried': (
            'inviting_left_hand',
            'timid_door_knock',
        ),
        'confused_thinking': (
            'confused_head_scratch_left',
            'confused_head_scratch_right',
            'detective_thinking',
            'thinking',
            'troubled_left_head_scratch',
        ),
        'giving_answer': (
            'got_an_idea',
            'got_an_idea_alt',
            'pointing_to_self_me',
        ),
    }
    KEYWORD_MOTION_POOLS = {
        'farewell_keyword': (
            'loud_greeting',
            'farewell_wave',
        ),
    }
    RUNTIME_MOTION_POOLS = {
        'startup_required': (
            'apologizing',
            'door_knock',
            'looking_96',
        ),
        'touch_face_required': (
            'confused_cheek_scratch',
        ),
        'sleepy_idle': (
            'sleepy_eyes_closed',
        ),
        'reply_fallback': (
            'Idle_motion_confident_148',
            'Idle_searchong_173',
        ),
    }

    def __init__(self):
        self._last_motion_name = None
        self._last_motion_at = 0.0
        self._recent_motion_by_intent = {}
        self.motion_categories = {
            'normal_idle': [
                {'name': 'Idle_normal_187', 'weight': 25},
                {'name': 'Idle4_157', 'weight': 20},
                {'name': 'a_idle_neutral_loop_88', 'weight': 20},
                {'name': 'Idle_341', 'weight': 15},
                {'name': 'lifegurad_idle_272', 'weight': 10},
                {'name': 'wainting_359', 'weight': 10}
            ],
            'listening_emotional': [
                {'name': 'Idle_341', 'weight': 35},
                {'name': 'lifegurad_idle_272', 'weight': 45},
                {'name': 'hold_hands_119', 'weight': 20}
            ],
            'searching_task': [
                {'name': 'Idle_searchong_173', 'weight': 70},
                {'name': 'Idle_normal_187', 'weight': 15},
                {'name': 'Idle4_157', 'weight': 15}
            ],
            'giving_answer': [
                {'name': 'Idle_motion_confident_148', 'weight': 30},
                {'name': 'Idle_normal_187', 'weight': 25},
                {'name': 'Idle4_157', 'weight': 20},
                {'name': 'Idle_341', 'weight': 25}
            ],
            'happy_mood': [
                {'name': 'looking_96', 'weight': 60},
                {'name': 'Fairy_idle_362', 'weight': 25},
                {'name': 'Idle4_157', 'weight': 15}
            ],
            'sad_emotion': [
                {'name': 'wainting_359', 'weight': 60},
                {'name': 'lifegurad_idle_272', 'weight': 25},
                {'name': 'Idle_341', 'weight': 15}
            ],
            'praised': [
                {'name': 'hold_hands_119', 'weight': 70},
                {'name': 'looking_96', 'weight': 15},
                {'name': 'Idle4_157', 'weight': 15}
            ],
            'night_sleepy': [
                {'name': 'Sleepy_yawn_idle_113', 'weight': 50},
                {'name': 'sleepy_eyes_closed', 'weight': 50}
            ],
            'easter_egg': [
                {'name': 'Fairy_idle_362', 'weight': 70},
                {'name': 'looking_96', 'weight': 30}
            ],
            'curious_interest': [
                {'name': 'looking_96', 'weight': 50},
                {'name': 'Idle_341', 'weight': 30},
                {'name': 'Idle_normal_187', 'weight': 20}
            ],
            'gentle_comfort': [
                {'name': 'lifegurad_idle_272', 'weight': 60},
                {'name': 'hold_hands_119', 'weight': 40}
            ],
            'playful_magic': [
                {'name': 'Fairy_idle_362', 'weight': 70},
                {'name': 'looking_96', 'weight': 30}
            ],
            'neutral_waiting': [
                {'name': 'wainting_359', 'weight': 70},
                {'name': 'Idle_341', 'weight': 30}
            ],
            'calm_idle': [
                {'name': 'a_idle_neutral_loop_88', 'weight': 60},
                {'name': 'Idle_normal_187', 'weight': 40}
            ]
        }
        self.fallback_motion_categories = self.motion_categories
        self.manifest_enabled = False
        self.motion_manifest_path = None
        self.motion_manifest_error = ""
        self.motion_categories = self.apply_required_motion_pools(
            self.load_motion_categories()
        )

    def is_night_time(self):
        """Check if it's night time (21:00 - 06:00)."""
        hour = datetime.now().hour
        return hour >= 21 or hour < 6

    def analyze_user_input(self, text):
        """Analyze user input to determine motion category - Improved heuristics"""
        text = text.lower()
        
        if any(k in text for k in ['人机舞', '跳舞', '机器人', '舞蹈', '转圈', '变身', 'robot', 'dance', 'dancing']):
            return 'easter_egg'

        if any(k in text for k in ['谢谢', '好棒', '厉害', '很好', '真棒', 'love you', '喜欢你', '爱你', 'thanks', 'great job']):
            return 'praised'

        if any(k in text for k in ['难过', '伤心', '想哭', '压力', '焦虑', '委屈', '抱抱', 'sad', 'depressed', 'anxious']):
            return 'gentle_comfort'

        if any(k in text for k in ['好奇', '真的吗', '原来如此', 'wow', 'really', 'curious']):
            return 'curious_interest'

        if any(k in text for k in ['如何', '怎么', '什么', '为什么', '解释', '帮我', 'how', 'what', 'why', 'explain', 'help', '?', '？']):
            return 'searching_task'

        return 'normal_idle'

    def select_motion_from_category(self, category):
        """Select motion based on category and weights"""
        motions = self.motion_categories.get(category, [])
        if not motions:
            return None

        total_weight = sum(motion['weight'] for motion in motions)
        random_value = random.random() * total_weight

        for motion in motions:
            random_value -= motion['weight']
            if random_value <= 0:
                selected = motion['name']
                if self._last_motion_name and selected == self._last_motion_name and len(motions) > 1:
                    alternatives = [m['name'] for m in motions if m['name'] != self._last_motion_name]
                    if alternatives:
                        selected = random.choice(alternatives)
                self._last_motion_name = selected
                self._last_motion_at = datetime.now().timestamp()
                return selected

        selected = motions[0]['name']
        if self._last_motion_name and selected == self._last_motion_name and len(motions) > 1:
            selected = motions[1]['name']
        self._last_motion_name = selected
        self._last_motion_at = datetime.now().timestamp()
        return selected

    def get_motion_suggestion(self, context):
        """Get motion suggestion based on context"""
        override = context.get('category_override')
        if override and override in self.motion_categories:
            category = override
        elif context.get('is_listening'):
            question_type = context.get('question_type', 'task')
            category = 'listening_emotional' if question_type == 'emotional' else 'searching_task'
        elif context.get('is_easter_egg'):
            category = 'easter_egg'
        elif context.get('is_praised'):
            category = 'praised'
        elif context.get('is_comforting'):
            category = 'gentle_comfort'
        elif context.get('is_curious'):
            category = 'curious_interest'
        elif context.get('is_playful'):
            category = 'playful_magic'
        elif context.get('emotion') == 'happy':
            category = 'happy_mood'
        elif context.get('emotion') == 'sad':
            category = 'sad_emotion'
        elif context.get('allow_night_idle') is True and self.is_night_time():
            category = 'night_sleepy'
        else:
            category = 'giving_answer' if context.get('is_responding') else 'normal_idle'

        motion_name = self.select_motion_from_category(category)
        return {
            'category': category,
            'motion_name': motion_name,
            'is_night': self.is_night_time()
        }

    # Manifest-aware implementations below override the legacy methods above.
    def get_manifest_candidates(self):
        configured = os.environ.get("SAYA_MOTION_MANIFEST_FILE")
        candidates = []
        if configured:
            candidates.append(Path(configured))
        candidates.append(Path(MMD_DIRECTORY) / "motion" / "motion_manifest.json")
        candidates.append(Path(PROJECT_ROOT) / "static" / "config" / "motion_manifest.default.json")
        return candidates

    def load_motion_categories(self):
        for path in self.get_manifest_candidates():
            try:
                if not path.exists():
                    continue
                with path.open("r", encoding="utf-8-sig") as source:
                    manifest = json.load(source)
                categories = self.build_categories_from_manifest(manifest)
                if categories:
                    self.manifest_enabled = True
                    self.motion_manifest_path = str(path)
                    self.motion_manifest_error = ""
                    return categories
            except Exception as exc:
                self.motion_manifest_error = f"{path}: {exc}"
                log_to_file(f"Motion manifest load failed: {self.motion_manifest_error}")

        self.manifest_enabled = False
        self.motion_manifest_path = None
        return self.build_fallback_categories()

    def apply_required_motion_pools(self, categories):
        categories = categories if isinstance(categories, dict) else {}
        entries_by_id = {}
        for motions in categories.values():
            for motion in motions:
                identifiers = {
                    str(motion.get("id") or "").strip().lower(),
                    str(motion.get("name") or "").strip().lower(),
                    Path(str(motion.get("file") or "")).stem.strip().lower(),
                }
                for identifier in identifiers:
                    if identifier:
                        entries_by_id.setdefault(identifier, motion)

        looping_ids = {
            "looking_96",
            "helpless_listening_look",
            "detective_thinking",
            "thinking",
            "sleepy_eyes_closed",
            "Idle_motion_confident_148",
            "Idle_searchong_173",
        }

        def build_pool(motion_ids):
            pool = []
            for motion_id in motion_ids:
                source = entries_by_id.get(motion_id.lower())
                if source is None:
                    is_loop = motion_id in looping_ids
                    source = self.enrich_motion_entry({
                        "id": motion_id,
                        "name": motion_id,
                        "file": f"{motion_id}.vmd",
                        "kind": "idle" if is_loop else "reaction",
                        "playback": "loop" if is_loop else "once_then_idle",
                        "enabled": True,
                    })
                entry = dict(source)
                entry["weight"] = 1
                pool.append(entry)
            return pool

        for category, motion_ids in self.CHAT_MOTION_POOLS.items():
            categories[category] = build_pool(motion_ids)
        for category, motion_ids in self.KEYWORD_MOTION_POOLS.items():
            categories[category] = build_pool(motion_ids)
        for category, motion_ids in self.RUNTIME_MOTION_POOLS.items():
            categories[category] = build_pool(motion_ids)
        return categories

    def build_fallback_categories(self):
        categories = {}
        for category, motions in self.fallback_motion_categories.items():
            categories[category] = []
            for motion in motions:
                entry = self.enrich_motion_entry(
                    {
                        "id": motion["name"],
                        "name": motion["name"],
                        "file": f"{motion['name']}.vmd",
                        "playback": "loop",
                        "enabled": True,
                    }
                )
                entry["weight"] = motion["weight"]
                categories[category].append(entry)
        return categories

    def build_categories_from_manifest(self, manifest):
        motions = manifest.get("motions") if isinstance(manifest, dict) else manifest
        if not isinstance(motions, list):
            return {}

        categories = {}
        for raw_motion in motions:
            if not isinstance(raw_motion, dict) or raw_motion.get("enabled") is False:
                continue
            entry = self.enrich_motion_entry(raw_motion)
            category_weights = raw_motion.get("categories") or {}

            if isinstance(category_weights, list):
                category_weights = {category: raw_motion.get("weight", 1) for category in category_weights}
            if not isinstance(category_weights, dict):
                continue

            for category, weight in category_weights.items():
                category = str(category or "").strip()
                try:
                    weight = max(0, int(weight))
                except (TypeError, ValueError):
                    weight = 0
                if not category or weight <= 0:
                    continue
                weighted_entry = dict(entry)
                weighted_entry["weight"] = weight
                categories.setdefault(category, []).append(weighted_entry)

        return categories

    def enrich_motion_entry(self, motion):
        file_name = str(motion.get("file") or motion.get("path") or "").replace("\\", "/").strip("/")
        name = str(motion.get("name") or motion.get("id") or Path(file_name).stem).strip()
        motion_id = str(motion.get("id") or name or Path(file_name).stem).strip()
        if not file_name and name:
            file_name = f"{name}.vmd"

        playback = str(motion.get("playback") or "loop").strip().lower()
        if playback not in {"loop", "once", "once_then_idle"}:
            playback = "loop"

        def list_or_empty(value):
            return value if isinstance(value, list) else []

        def int_or_default(value, default=0):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        def float_or_default(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        raw_categories = motion.get("categories") if isinstance(motion.get("categories"), (dict, list)) else {}

        return {
            "id": motion_id,
            "name": name or motion_id,
            "file": file_name,
            "url": self.motion_file_url(file_name),
            "description": str(motion.get("description") or ""),
            "comment": str(motion.get("comment") or ""),
            "source_name": str(motion.get("source_name") or ""),
            "kind": str(motion.get("kind") or "idle").strip().lower(),
            "intent": str(motion.get("intent") or "").strip(),
            "intents": list_or_empty(motion.get("intents")),
            "categories": raw_categories,
            "tags": list_or_empty(motion.get("tags")),
            "trigger_keywords": list_or_empty(motion.get("trigger_keywords")),
            "response_keywords": list_or_empty(motion.get("response_keywords")),
            "phase": str(motion.get("phase") or "").strip().lower(),
            "startup": motion.get("startup") is True,
            "startup_weight": int_or_default(motion.get("startup_weight"), 1),
            "return_category": str(motion.get("return_category") or "normal_idle").strip(),
            "priority": int_or_default(motion.get("priority"), 0),
            "intent_weight": int_or_default(motion.get("intent_weight"), 0),
            "playback": playback,
            "cooldown_ms": int_or_default(motion.get("cooldown_ms"), 0),
            "end_hold_seconds": max(0.0, float_or_default(motion.get("end_hold_seconds"), 0.0)),
            "enabled": motion.get("enabled") is not False,
        }

    def motion_file_url(self, file_name):
        if not file_name:
            return ""
        mmd_root = Path(MMD_DIRECTORY).resolve()
        motion_root = (mmd_root / "motion").resolve()
        path = (motion_root / str(file_name).replace("\\", "/").strip("/")).resolve()
        try:
            rel = path.relative_to(mmd_root).as_posix()
        except ValueError:
            return ""
        return "/MMD/" + quote(rel, safe="/:@!$&'()*+,;=-._~")

    def get_motion_file_entries(self):
        entries = {}
        for motions in self.motion_categories.values():
            for motion in motions:
                url = motion.get("url")
                if not url:
                    continue
                key = motion.get("id") or motion.get("name") or motion.get("file") or url
                if key not in entries:
                    clean = dict(motion)
                    clean.pop("weight", None)
                    entries[key] = clean
        return list(entries.values())

    def has_any_motion_keyword(self, text, keywords):
        return any(str(keyword or "").lower() in text for keyword in keywords if str(keyword or "").strip())

    def is_chat_motion_category(self, category):
        return str(category or "").strip() in self.CHAT_MOTION_CATEGORIES

    def is_chat_motion_entry_allowed(self, motion):
        if not motion:
            return False
        identifiers = {
            str(motion.get('id') or '').strip(),
            str(motion.get('name') or '').strip(),
            Path(str(motion.get('file') or '')).stem,
        }
        return not any(identifier in self.CHAT_MOTION_EXCLUDED_IDS for identifier in identifiers)

    def analyze_motion_context(self, text):
        text = str(text or "").lower()
        explicit_farewells = [
            '再见',
            '过会见',
            '过会儿见',
            '待会见',
            '待会儿见',
            '一会见',
            '一会儿见',
            '回头见',
            '下次见',
            '晚点见',
            '拜拜',
            'bye',
            'goodbye',
            'see you',
        ]
        if self.has_any_motion_keyword(text, explicit_farewells):
            return {
                'category': 'farewell_keyword',
                'reaction_intent': 'farewell_keyword',
            }

        # Other chat actions are chosen only from the model's reply-side
        # [MOTION: category] tag.
        return {
            'category': 'normal_idle',
            'reaction_intent': '',
        }

        text = str(text or "").lower()

        # These are semantic signals from the conversation, not motion-name
        # descriptions from the manifest.
        semantic_checks = [
            (
                ['你拒绝我', '你说不行', '你说不要', '你不许我', '拒绝我', '请拒绝我', '明确拒绝'],
                'neutral_waiting',
                'refusal',
            ),
            (
                [
                    '吃醋', '嫉妒', '别看别人', '只看我', 'jealous',
                    '喜欢别人', '喜欢她', '喜欢他', '陪别人', '找别人',
                    '别的女生', '别的女孩子', '别的ai', '另一个ai', '别的助手',
                    '换个ai', '别人比你', '其他女孩子', '其他ai',
                    '新的助手', '别的bot', '另一个助手', '她比你', '他比你',
                    '不理你了', '不理我', '你不理我', '不陪我', '别陪别人', '只陪别人',
                    '冷落你', '冷落我', '你会不会在意', '你会在意吗',
                    '你是不是在意', '你吃不吃醋', '你会吃醋吗', '你是不是不理我了',
                    '你不理我了吗', '怎么不理我', '怎么不陪我', '去陪别人'
                ],
                'sad_emotion',
                'jealous_pout',
            ),
            (
                ['不行', '不要', '不可以', '别这样', '拒绝', 'stop it', 'no way'],
                'neutral_waiting',
                '',
            ),
            (
                ['拜拜', '再见', '回头见', '下次见', '一会儿见', '待会儿见', '先走了', '先去忙', '下线了', '回头聊', '晚点聊', 'bye', 'goodbye', 'see you'],
                'normal_idle',
                'farewell',
            ),
            (
                ['你好', '你好呀', '早上好', '早安', '早呀', '早啊', '中午好', '晚上好', '在吗', '回来啦', '我回来了', '嗨', '哈喽', 'hello', 'hi '],
                'happy_mood',
                'greeting',
            ),
            (
                ['人机舞', '跳舞', '机器人', '舞蹈', '转圈', '变身', 'robot', 'dance', 'dancing'],
                'easter_egg',
                'easter_egg',
            ),
            (
                ['开枪', 'biu', '耍可爱', '比心开枪', '朝我开一枪'],
                'playful_magic',
                'finger_gun',
            ),
            (
                ['你是在找茬吗', '找茬', '挑衅', '抬杠', '故意气你', '逗你玩'],
                'playful_magic',
                'picking_a_fight',
            ),
            (
                [
                    '你坏坏', '坏坏', '撒娇', '撒个娇', '撒撒娇', '撒一下娇',
                    '撒娇一下', '对我撒娇', '跟我撒娇', '娇一下', '娇一点',
                    '卖萌', '卖个萌', '卖一下萌', '装可爱', '可爱一点',
                    'act cute', 'be cute', 'coquettish',
                ],
                'playful_magic',
                'coquettish_tease',
            ),
            (
                ['向我道歉', '给我道歉', '你道歉', '道个歉', '道歉一下', '说对不起', '应该道歉'],
                'apology',
                'apology_from_saya',
            ),
            (
                ['对不起', '抱歉', '不好意思', '是我的错', '我错了', '原谅我', '别生我的气'],
                'gentle_comfort',
                'forgive_doctor',
            ),
            (
                ['敲门', '看这里', '看我', '注意我', '叫你呢', '喂喂', '回应我', '理理我'],
                'attention_call',
                'attention_call',
            ),
            (
                ['哼', '生气了', '不高兴了', '委屈巴巴', '坏蛋', '讨厌你', '气死我了'],
                'complaint_reaction',
                'complaint',
            ),
            (
                ['笑一个', '笑一下', '微笑一下', '给我笑笑', '温柔地笑'],
                'happy_mood',
                'cautious_smile',
            ),
            (
                ['调皮', '开玩笑', '玩一下', '逗逗', '闹一下', '整活'],
                'playful_magic',
                'playful_tease',
            ),
            (
                ['偷笑', '坏笑', '嘻嘻', '嘿嘿', '偷偷笑'],
                'playful_magic',
                'snicker',
            ),
            (
                ['害羞', '脸红', '喜欢你', '爱你', '亲亲', '抱一下', 'love you', 'i love you'],
                'praised',
                'shy_blush',
            ),
            (
                ['哈哈', '大笑', '太好笑', '笑死', '乐死', '好欢乐'],
                'happy_mood',
                'laugh',
            ),
            (
                ['谢谢', '好棒', '厉害', '很好', '真棒', '可爱', '夸夸你', 'thanks', 'great job', 'good job'],
                'praised',
                'praised',
            ),
            (
                ['难过', '伤心', '想哭', '压力', '焦虑', '委屈', '抱抱', '陪陪我', '安慰我', '不开心', '低落', '好烦', '烦死了', '心累', '崩溃', '破防', '郁闷', '难受', 'emo', 'sad', 'depressed', 'anxious'],
                'gentle_comfort',
                'comfort',
            ),
            (
                ['惊喜', '太好了', '哇', '哇哦', '好耶', 'nice'],
                'happy_mood',
                'delighted_surprise',
            ),
            (
                ['震惊', '很吃惊', '真的假的', '太离谱了', '不可思议'],
                'curious_interest',
                'surprise_big',
            ),
            (
                ['真的吗', '不会吧', '居然', '惊讶', '吃惊', '诶', '咦', 'wow', 'really'],
                'curious_interest',
                'surprise',
            ),
            (
                ['我吗', '是我吗', '指我', '轮到我', 'why me'],
                'curious_interest',
                'self_question',
            ),
            (
                ['小心敲门', '害怕地敲门', '害怕的敲门', '试探敲门'],
                'gentle_comfort',
                'timid_knock',
            ),
            (
                ['害怕', '有点怕', '怕怕', '我怕'],
                'gentle_comfort',
                'comfort',
            ),
            (
                ['困扰', '头疼', '麻烦', '纠结', '为难', '卡住了'],
                'searching_task',
                'troubled',
            ),
            (
                ['什么意思', '啥意思', '什么含义', '没懂', '没看懂', '看不懂', '不懂', '困惑', '奇怪', '不明白', '怎么回事', '为什么会这样'],
                'curious_interest',
                'confused',
            ),
            (
                ['福尔摩斯', '推理', '查一下', '认真分析', '深入分析', '推断'],
                'searching_task',
                'detective_thinking',
            ),
            (
                ['想想', '思考', '分析', '研究一下', '看看怎么', '帮我看看', '怎么办', '怎么做', '想一想', '帮我想'],
                'searching_task',
                'thinking',
            ),
            (
                ['有办法', '想到了', '有主意', '解决方案', '方案来了', 'idea'],
                'giving_answer',
                '',
            ),
            (
                ['牵手', '伸手', '过来', '邀请', '拉我', 'hold hands'],
                'gentle_comfort',
                'invite',
            ),
            (
                ['好奇', '原来如此', 'curious'],
                'curious_interest',
                'curious',
            ),
            (
                ['如何', '怎么', '什么', '为什么', '解释', '帮我', 'how', 'what', 'why', 'explain', 'help', '?', '？'],
                'searching_task',
                '',
            ),
        ]

        for keywords, category, intent in semantic_checks:
            if self.has_any_motion_keyword(text, keywords):
                return {
                    'category': category,
                    'reaction_intent': intent,
                }

        return {
            'category': 'normal_idle',
            'reaction_intent': '',
        }

    def should_play_immediate_reaction(self, analysis):
        return (
            isinstance(analysis, dict)
            and analysis.get('reaction_intent') == 'farewell_keyword'
        )

    def analyze_response_context(self, response_text, user_text="", input_analysis=None):
        # Response text is no longer keyword-scanned for concrete reactions.
        # If the model supplied a valid [MOTION: category] tag, the stream
        # handler has already selected from that category pool. Otherwise the
        # answer falls back to the ordinary explaining pool.
        return {
            'category': 'giving_answer',
            'reaction_intent': '',
        }

        response_text = str(response_text or "").lower()
        user_text = str(user_text or "").lower()
        input_analysis = input_analysis if isinstance(input_analysis, dict) else {}
        input_intent = str(input_analysis.get('reaction_intent') or "")

        if self.has_any_motion_keyword(response_text, ['对不起', '抱歉', '不好意思', '我错了', '我说错', '我搞错', 'sorry']):
            return {
                'category': 'apology',
                'reaction_intent': 'apology_from_saya',
            }

        if input_intent == 'forgive_doctor' or self.has_any_motion_keyword(response_text, ['没关系', '不要紧', '不用道歉', '别自责', '我不怪你']):
            return {
                'category': 'reassurance',
                'reaction_intent': 'forgive_doctor',
            }

        if input_intent == 'apology_from_saya':
            return {
                'category': 'apology',
                'reaction_intent': 'apology_from_saya',
            }

        # Preserve a clear requested reaction before scanning Saya's generic
        # response wording. Otherwise a reply such as "好呀，我很开心" can
        # replace the requested coquettish action with an ordinary happy one.
        input_priority_intents = {
            'apology_from_saya',
            'refusal',
            'jealous_pout',
            'farewell',
            'greeting',
            'easter_egg',
            'finger_gun',
            'picking_a_fight',
            'attention_call',
            'complaint',
            'playful_tease',
            'coquettish_tease',
            'cautious_smile',
            'snicker',
            'shy_blush',
            'laugh',
            'delighted_surprise',
            'surprise_big',
            'surprise',
            'self_question',
            'confused',
            'timid_knock',
            'troubled',
            'invite',
            'curious',
        }
        if input_intent in input_priority_intents:
            return {
                'category': input_analysis.get('category', 'giving_answer'),
                'reaction_intent': input_intent,
            }

        response_checks = [
            (['有办法', '想到了', '可以这样', '方案', '我建议', '解决办法', '办法是', '可以试试'], 'idea', 'idea'),
            (['不行', '不可以', '不能答应', '不要这样', '我拒绝', '这个不行'], 'refusal', 'refusal'),
            (['吃醋', '嫉妒', '只看我', '别看别人', '有点在意', '不许找别人'], 'jealousy', 'jealous_pout'),
            (['撒娇', '坏坏', '娇一点', '卖个萌', '装可爱'], 'playful_magic', 'coquettish_tease'),
            (['调皮', '开玩笑', '逗你', '整活'], 'playful_magic', 'playful_tease'),
            (['小心敲门', '害怕地敲门', '害怕的敲门'], 'timid_knock', 'timid_knock'),
            (['谢谢', '开心', '高兴', '害羞', '喜欢', '被夸'], 'praised', input_intent if input_intent in {'praised', 'shy_blush'} else 'praised'),
            (['哈哈', '好笑', '笑死', '太有趣了'], 'happy_mood', 'laugh'),
            (['惊喜', '太好了', '好耶', '哇'], 'happy_mood', 'delighted_surprise'),
            (['不会吧', '真的吗', '吃惊', '震惊', '诶'], 'curious_interest', 'surprise'),
        ]

        for keywords, category, intent in response_checks:
            if self.has_any_motion_keyword(response_text, keywords):
                return {
                    'category': category,
                    'reaction_intent': intent,
                }

        if self.has_any_motion_keyword(response_text, ['我陪你', '抱抱', '别难过', '没事的', '慢慢来', '我在', '别怕']):
            return {
                'category': 'gentle_comfort',
                'reaction_intent': 'comfort',
            }

        response_safe_intents = {
            'apology_from_saya',
            'forgive_doctor',
            'refusal',
            'jealous_pout',
            'farewell',
            'greeting',
            'easter_egg',
            'finger_gun',
            'picking_a_fight',
            'attention_call',
            'complaint',
            'playful_tease',
            'coquettish_tease',
            'cautious_smile',
            'snicker',
            'shy_blush',
            'laugh',
            'delighted_surprise',
            'surprise_big',
            'surprise',
            'self_question',
            'timid_knock',
            'troubled',
            'invite',
            'curious',
            'confused',
            'comfort',
        }
        if input_intent in response_safe_intents:
            return {
                'category': input_analysis.get('category', 'giving_answer'),
                'reaction_intent': input_intent,
            }

        return {
            'category': 'giving_answer',
            'reaction_intent': '',
        }

    def analyze_user_input(self, text):
        return self.analyze_motion_context(text).get('category', 'normal_idle')

    def select_weighted_motion(self, motions, allow_reaction=True):
        if not motions:
            return None
        if not allow_reaction:
            motions = [
                motion for motion in motions
                if motion.get('kind') != 'reaction'
                and motion.get('playback') not in {'once', 'once_then_idle'}
            ]
            if not motions:
                return None

        total_weight = sum(max(0, int(motion.get('weight', 0))) for motion in motions)
        if total_weight <= 0:
            return None
        random_value = random.random() * total_weight

        for motion in motions:
            random_value -= max(0, int(motion.get('weight', 0)))
            if random_value <= 0:
                selected = dict(motion)
                if self._last_motion_name and selected.get('name') == self._last_motion_name and len(motions) > 1:
                    alternatives = [m for m in motions if m.get('name') != self._last_motion_name]
                    if alternatives:
                        selected = dict(random.choice(alternatives))
                self._last_motion_name = selected.get('name')
                self._last_motion_at = datetime.now().timestamp()
                return selected

        selected = dict(motions[0])
        if self._last_motion_name and selected.get('name') == self._last_motion_name and len(motions) > 1:
            selected = dict(motions[1])
        self._last_motion_name = selected.get('name')
        self._last_motion_at = datetime.now().timestamp()
        return selected

    def select_motion_entry_by_intent(self, intent, preferred_category=None, prefer_reaction=True):
        intent = str(intent or "").strip()
        if not intent:
            return None

        ordered_categories = []
        if preferred_category in self.motion_categories:
            ordered_categories.append(preferred_category)
        ordered_categories.extend(
            category for category in self.motion_categories.keys()
            if category != preferred_category
        )

        candidates = []
        seen = set()
        for category in ordered_categories:
            for motion in self.motion_categories.get(category, []):
                motion_intents = []
                primary_intent = str(motion.get('intent') or '').strip()
                if primary_intent:
                    motion_intents.append(primary_intent)
                motion_intents.extend(str(item or '').strip() for item in motion.get('intents', []))
                if intent not in motion_intents:
                    continue

                key = motion.get('id') or motion.get('file') or motion.get('url')
                if key in seen:
                    continue
                seen.add(key)

                weighted = dict(motion)
                if weighted.get('intent_weight'):
                    weighted['weight'] = weighted['intent_weight']
                elif weighted.get('priority'):
                    weighted['weight'] = weighted['priority']
                else:
                    weighted['weight'] = weighted.get('weight', 1)
                candidates.append(weighted)

        if prefer_reaction:
            reaction_candidates = [
                motion for motion in candidates
                if motion.get('kind') == 'reaction'
                or motion.get('playback') in {'once', 'once_then_idle'}
            ]
            if reaction_candidates:
                candidates = reaction_candidates

        candidates = self.filter_recent_intent_motions(intent, candidates)
        selected = self.select_weighted_motion(candidates)
        self.remember_intent_motion(intent, selected)
        return selected

    def select_motion_entry_from_category(self, category, allow_reaction=True, chat_motion=False):
        motions = self.motion_categories.get(category, [])
        if chat_motion:
            motions = [motion for motion in motions if self.is_chat_motion_entry_allowed(motion)]
        return self.select_weighted_motion(motions, allow_reaction=allow_reaction)

    def select_motion_from_category(self, category):
        selected = self.select_motion_entry_from_category(category)
        return selected.get('name') if selected else None

    def filter_recent_intent_motions(self, intent, candidates):
        intent = str(intent or "").strip()
        if not intent or len(candidates) <= 1:
            return candidates

        recent_keys = self._recent_motion_by_intent.get(intent, [])
        if not recent_keys:
            return candidates

        fresh_candidates = [
            motion for motion in candidates
            if (motion.get('id') or motion.get('file') or motion.get('url')) not in recent_keys
        ]
        return fresh_candidates or candidates

    def remember_intent_motion(self, intent, motion):
        intent = str(intent or "").strip()
        if not intent or not motion:
            return

        key = motion.get('id') or motion.get('file') or motion.get('url')
        if not key:
            return

        recent = [item for item in self._recent_motion_by_intent.get(intent, []) if item != key]
        recent.insert(0, key)
        self._recent_motion_by_intent[intent] = recent[:3]

    def get_motion_suggestion(self, context):
        context = context if isinstance(context, dict) else {}
        is_night = self.is_night_time()
        chat_motion = context.get('chat_motion') is True
        allow_night_idle = context.get('allow_night_idle') is True and is_night
        allow_sleepy_interruption = context.get('allow_sleepy_interruption') is True and is_night
        override = context.get('category_override')
        if chat_motion and not self.is_chat_motion_category(override):
            override = None
        if override in {'night_sleepy', 'sleepy_idle'} and not allow_night_idle:
            override = None
        if override == 'sleepy_interruption' and not allow_sleepy_interruption:
            override = None

        if override and override in self.motion_categories:
            category = override
        elif context.get('is_listening'):
            question_type = context.get('question_type', 'task')
            category = 'listening_emotional' if question_type == 'emotional' else 'searching_task'
        elif context.get('is_easter_egg') and not chat_motion:
            category = 'easter_egg'
        elif context.get('is_praised'):
            category = 'praised'
        elif context.get('is_comforting'):
            category = 'gentle_comfort'
        elif context.get('is_curious'):
            category = 'curious_interest'
        elif context.get('is_playful'):
            category = 'playful_magic'
        elif context.get('emotion') == 'happy':
            category = 'happy_mood'
        elif context.get('emotion') == 'sad':
            category = 'sad_emotion'
        elif allow_night_idle:
            category = 'night_sleepy'
        else:
            category = 'giving_answer' if context.get('is_responding') else 'normal_idle'

        if chat_motion and not self.is_chat_motion_category(category):
            category = 'giving_answer' if context.get('is_responding') else 'normal_idle'

        reaction_intent = str(context.get('reaction_intent') or context.get('intent') or '').strip()
        if chat_motion:
            reaction_intent = ''
        if reaction_intent in {'sleepy', 'sleepy_idle'} and not allow_night_idle:
            reaction_intent = ''
        if reaction_intent == 'sleepy_startled' and not allow_sleepy_interruption:
            reaction_intent = ''
        allow_reaction = context.get('allow_reaction') is not False
        motion_entry = None
        if reaction_intent and allow_reaction:
            motion_entry = self.select_motion_entry_by_intent(
                reaction_intent,
                category,
            prefer_reaction=allow_reaction,
            )
        motion_entry = motion_entry or self.select_motion_entry_from_category(
            category,
            allow_reaction=allow_reaction,
            chat_motion=chat_motion,
        ) or {}
        response_reaction_intent = '' if chat_motion else (reaction_intent or motion_entry.get('intent'))
        return {
            'category': category,
            'reaction_intent': response_reaction_intent,
            'motion_id': motion_entry.get('id'),
            'motion_name': motion_entry.get('name'),
            'motion_file': motion_entry.get('file'),
            'motion_url': motion_entry.get('url'),
            'motion_kind': motion_entry.get('kind'),
            'return_category': motion_entry.get('return_category'),
            'playback': motion_entry.get('playback', 'loop'),
            'cooldown_ms': motion_entry.get('cooldown_ms', 0),
            'is_night': is_night,
            'night_idle_allowed': allow_night_idle,
            'manifest_enabled': self.manifest_enabled,
        }

motion_ai = MotionIntelligence()


def _goal_event_date(goal, status):
    if status == "active":
        return str(goal.get("created_at") or "")
    if status == "completed":
        return str(goal.get("achieved_at") or goal.get("completed_at") or "")
    if status == "failed":
        return str(goal.get("failed_at") or goal.get("completed_at") or "")
    return ""

class GoalManager:
    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "life_quests.json")
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        self.goals = []
        self.load_goals()

    def load_goals(self):
        if os.path.exists(self.file_path):
            try:
                with codecs.open(self.file_path, "r", "utf-8") as f:
                    self.goals = json.load(f)
            except:
                self.goals = []
        else:
            self.goals = []

        if not isinstance(self.goals, list):
            self.goals = []
            return

        migrated = False
        for goal in self.goals:
            if not isinstance(goal, dict):
                continue
            legacy_date = goal.get("completed_at")
            if goal.get("status") == "completed" and not goal.get("achieved_at") and legacy_date:
                goal["achieved_at"] = legacy_date
                migrated = True
            if goal.get("status") == "failed" and not goal.get("failed_at") and legacy_date:
                goal["failed_at"] = legacy_date
                migrated = True
        if migrated:
            self.save_goals()

    def save_goals(self):
        try:
            with codecs.open(self.file_path, "w", "utf-8") as f:
                json.dump(self.goals, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving goals: {e}")

    def add_goal(self, title, description=""):
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        goal = {
            "id": str(datetime.now().timestamp()),
            "title": title,
            "description": description,
            "status": "active",
            "created_at": now,
            "achieved_at": None,
            "failed_at": None,
            "completed_at": None
        }
        self.goals.append(goal)
        self.save_goals()
        return goal

    def update_goal(self, goal_id, updates):
        if not isinstance(updates, dict):
            return None
        allowed = {
            "title", "description", "status", "created_at",
            "achieved_at", "failed_at", "completed_at"
        }
        clean_updates = {key: value for key, value in updates.items() if key in allowed}
        if clean_updates.get("status") not in {None, "active", "completed", "failed"}:
            clean_updates.pop("status", None)
        for g in self.goals:
            if g["id"] == goal_id:
                g.update(clean_updates)
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if g.get("status") == "completed":
                    achieved_at = g.get("achieved_at") or g.get("completed_at") or now
                    g["achieved_at"] = achieved_at
                    g["completed_at"] = achieved_at
                elif g.get("status") == "failed":
                    failed_at = g.get("failed_at") or g.get("completed_at") or now
                    g["failed_at"] = failed_at
                    g["completed_at"] = failed_at
                self.save_goals()
                return g
        return None

    def delete_goal(self, goal_id):
        self.goals = [g for g in self.goals if g["id"] != goal_id]
        self.save_goals()

goal_manager = GoalManager()

def sync_life_memory():
    """Synchronize all goals (Active, Completed, Failed) into a single locked memory"""
    memories = kb_system.get_memories()
    target_mem = None
    
    # Try to find existing "Life Quests Summary" or legacy "Achievements Log"
    for m in memories:
        if m.get("title") in ["Life Quests Summary", "Achievements Log"]:
            target_mem = m
            break
            
    # Group goals
    active = [g for g in goal_manager.goals if g['status'] == 'active']
    completed = [g for g in goal_manager.goals if g['status'] == 'completed']
    failed = [g for g in goal_manager.goals if g['status'] == 'failed']
    
    # Sort them
    active.sort(key=lambda x: _goal_event_date(x, "active"), reverse=True)
    completed.sort(key=lambda x: _goal_event_date(x, "completed"), reverse=True)
    failed.sort(key=lambda x: _goal_event_date(x, "failed"), reverse=True)
    
    # Build content
    content = "=== Life Quests Summary ===\n\n"
    
    content += "[Current Goals]\n"
    if active:
        for g in active:
            content += f"- {g['title']} (Started: {_goal_event_date(g, 'active').split(' ')[0]})\n"
    else:
        content += "(None)\n"
    content += "\n"
        
    content += "[Achievements]\n"
    if completed:
        for g in completed:
            content += f"- {g['title']} (Achieved: {_goal_event_date(g, 'completed').split(' ')[0]})\n"
    else:
        content += "(None)\n"
    content += "\n"
        
    content += "[Reflections & Failures]\n"
    if failed:
        for g in failed:
            reason = g.get('description', 'No reason provided')
            content += f"- {g['title']} (Failed: {_goal_event_date(g, 'failed').split(' ')[0]})\n  Reason: {reason}\n"
    else:
        content += "(None)\n"

    # Update or create the protected system-managed slot.
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if target_mem:
        target_mem.update({
            "title": "Life Quests Summary",
            "content": content,
            "source": "system",
            "source_detail": {
                "type": "life_quests_summary",
                "captured_at": target_mem.get("creation_time", now),
                "updated_at": now,
            },
            "category": "goal",
            "tags": ["life_quests", "goals", "achievements", "reflections"],
            "confidence": 1.0,
            "importance_score": 0.95,
            "is_locked": True,
            "is_important": True,
            "is_archived": False,
            "managed_slot": "life_quests:summary",
            "memory_layer": "core",
            "memory_kind": "goal_summary",
            "date": now[:10],
            "last_updated": now,
        })
        kb_system.normalize_memory(target_mem)
        kb_system.save_memories()
    else:
        kb_system.add_memory(
            content,
            title="Life Quests Summary",
            source="system",
            is_important=True,
            category="goal",
            tags=["life_quests", "goals", "achievements", "reflections"],
            confidence=1.0,
            source_detail={"type": "life_quests_summary", "captured_at": now, "updated_at": now},
            metadata={
                "is_locked": True,
                "managed_slot": "life_quests:summary",
                "memory_layer": "core",
                "memory_kind": "goal_summary",
                "importance_score": 0.95,
            },
        )

