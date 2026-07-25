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
from datetime import datetime, timedelta
from contextlib import contextmanager

from saya_app.config import (
    CHAT_MEMORY_DIR,
    KNOWLEDGE_BASE_DIR,
    LLAVA_MMPROJ_PATH,
    LLAVA_MODEL_PATH,
)

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
        self.current_session_file = None
        self.pinned_file = os.path.join(self.base_dir, "pinned.json")
        self.legacy_sessions_file = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "knowledge_base",
            "sessions.json"
        )
        self.initialize_session()

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
        """Start a new session"""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        self.current_session_file = os.path.join(self.base_dir, f"session_{timestamp}.jsonl")

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

    def get_session_list(self):
        """Get list of available sessions with preview"""
        sessions = []
        files = sorted([f for f in os.listdir(self.base_dir) if f.startswith('session_') and f.endswith('.jsonl')], reverse=True)
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
                if entries:
                    first = entries[0]
                    time_str = first.get('time', '')
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
                    time_str = ""
                
                sessions.append({
                    "id": file,
                    "name": file.replace("session_", "").replace(".jsonl", ""),
                    "preview": preview,
                    "time": time_str,
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
                "pinned": False,
                "legacy": True
            })
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
        files = [f for f in os.listdir(self.base_dir) if f.startswith('session_') and f.endswith('.jsonl')]
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
            [f for f in os.listdir(self.base_dir) if f.startswith('session_') and f.endswith('.jsonl')],
            reverse=True
        )
        for file in files:
            path = os.path.join(self.base_dir, file)
            try:
                matches = []
                last_time = ""
                with codecs.open(path, "r", "utf-8") as f:
                    for index, line in enumerate(f):
                        data = json.loads(line)
                        content = str(data.get("content", ""))
                        last_time = data.get("time", last_time)
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
                    matches.sort(key=lambda item: item.get("score", 0), reverse=True)
                    results.append({
                        "id": file,
                        "name": file.replace("session_", "").replace(".jsonl", ""),
                        "time": last_time,
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
                    "legacy": True,
                    "matches": matches[:5],
                    "match_count": len(matches),
                    "score": sum(item.get("score", 0) for item in matches)
                })
        results.sort(key=lambda item: (item.get("score", 0), item.get("time", "")), reverse=True)
        return results[:limit]

    def load_session(self, session_id):
        """Load a specific session completely"""
        legacy = self._find_legacy_session(session_id)
        if legacy:
            # Legacy sessions are read-only. A continued conversation will be saved
            # into a fresh JSONL session instead of overwriting another active file.
            self.current_session_file = None
            return [str(item) for item in legacy.get("history", [])]

        path = self._safe_session_path(session_id)
        if not path:
            return []
        if not os.path.exists(path):
            return []
        
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

    def save_message(self, role, content, translation=None, metadata=None):
        """Save a single message to current session log"""
        if not self.current_session_file:
            self.initialize_session()
            
        entry = {
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "role": role,
            "content": content
        }
        if translation:
            entry["translation"] = translation
        if isinstance(metadata, dict):
            entry.update(metadata)
            
        with codecs.open(self.current_session_file, "a", "utf-8") as f:
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

    def load_session_full(self, session_id):
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
        
        self.current_session_file = path
        items = []
        try:
            with codecs.open(path, "r", "utf-8") as f:
                for line in f:
                    items.append(json.loads(line))
        except Exception as e:
            print(f"Error loading session {session_id}: {e}")
            
        return items

    def truncate_session(self, count):
        """Truncate current session file to keep only first 'count' lines"""
        if not self.current_session_file or not os.path.exists(self.current_session_file):
            return False
            
        try:
            # Read all lines
            with codecs.open(self.current_session_file, "r", "utf-8") as f:
                lines = f.readlines()
            
            # Truncate
            if count < len(lines):
                lines = lines[:count]
                
                # Write back
                with codecs.open(self.current_session_file, "w", "utf-8") as f:
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
            with codecs.open(self.file_path, "w", "utf-8") as f:
                json.dump(self.projects, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"Error saving projects: {e}")

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
    ABOUT_YOU_PERSONALITY_ID = "about-you-personality"
    ABOUT_YOU_POSSESSION_PREFIX = "about-you-possession-"
    ABOUT_YOU_POSSESSION_SUGGESTED_LENGTH = 600

    def __init__(self, base_dir=str(KNOWLEDGE_BASE_DIR)):
        self.base_dir = base_dir
        self.file_path = os.path.join(self.base_dir, "knowledge.json")
        self.settings_path = os.path.join(self.base_dir, "memory_settings.json")
        self.backup_dir = os.path.join(self.base_dir, "backups")
        self._vector_cache = {}
        self._last_memory_backup_at = None
        if not os.path.exists(self.base_dir):
            os.makedirs(self.base_dir)
        
        self.memories = []
        self.load_memories()
        self.settings = self.load_settings()
        self.import_legacy_memories()
        self.normalize_all_memories()

    def load_settings(self):
        defaults = {
            "auto_save_memories": True,
            "auto_merge_enabled": False,
            "auto_merge_days": 7,
            "last_auto_merge": None,
            "memory_2_enabled": True,
            "relevant_memory_count": 8,
            "include_memory_metadata": True,
            "local_vector_retrieval_enabled": True
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

    def update_settings(self, updates):
        allowed = {
            "auto_save_memories",
            "auto_merge_enabled",
            "auto_merge_days",
            "memory_2_enabled",
            "relevant_memory_count",
            "include_memory_metadata",
            "local_vector_retrieval_enabled"
        }
        for key, value in (updates or {}).items():
            if key not in allowed:
                continue
            if key in {"auto_merge_days", "relevant_memory_count"}:
                try:
                    upper = 365 if key == "auto_merge_days" else 30
                    value = max(1, min(upper, int(value)))
                except (TypeError, ValueError):
                    continue
            else:
                value = bool(value)
            self.settings[key] = value
        self.save_settings()
        return self.settings

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

    def save_memories(self):
        try:
            self._backup_existing_file(self.file_path, "knowledge.before-save")
            self._atomic_write_json(self.file_path, self.memories)
            self._vector_cache = {}
        except Exception as e:
            print(f"Error saving KB: {e}")

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

    def _query_relevance_score(self, memory, query):
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
        return self._coerce_float(
            overlap_score * 0.35 + semantic_score * 0.45 + exact_score * 0.20,
            0.0
        )

    def memory_priority_score(self, memory, query=""):
        low, high = self._memory_score_band(memory)
        confidence = self._coerce_float(memory.get("confidence"), 0.75)
        importance = self._coerce_float(memory.get("importance_score"), 0.4)
        recency = self._recency_score(memory)
        access = self._access_score(memory)
        relevance = self._query_relevance_score(memory, query)
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

        if "schema_version" not in memory or memory.get("schema_version") != 2:
            memory["schema_version"] = 2
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

        status = "archived" if memory.get("is_archived") else "active"
        if memory.get("status") != status:
            memory["status"] = status
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
        metadata=None
    ):
        timestamp = datetime.now()
        if not title:
            # Simple fallback title if none provided
            title = f"Memory {timestamp.strftime('%Y-%m-%d %H:%M')}"
            
        memory = {
            "id": str(timestamp.timestamp()),
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

        memory, _ = self.normalize_memory(memory)
        if self._looks_like_correction(memory.get("content")):
            related_ids = self._find_related_memory_ids(memory)
            if related_ids:
                memory["supersedes"] = related_ids
                for existing in self.memories:
                    if existing.get("id") in related_ids:
                        conflicts = existing.setdefault("conflicts_with", [])
                        if memory["id"] not in conflicts:
                            conflicts.append(memory["id"])

        self.memories.append(memory)
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

    def delete_memory(self, memory_id):
        # Prevent deleting locked or managed memories
        for m in self.memories:
            if m["id"] == memory_id and (m.get("is_locked", False) or m.get("managed_slot")):
                print(f"Attempt to delete locked memory {memory_id} blocked.")
                return False

        self.memories = [m for m in self.memories if m["id"] != memory_id]
        self.save_memories()
        return True

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

    def delete_auto_memories_for_session(self, session_id):
        """Delete only unlocked automatic memories explicitly linked to one chat session."""
        related = self._auto_memories_for_session(session_id)
        related_ids = {memory.get("id") for memory in related}
        if not related_ids:
            return {"deleted_count": 0}

        self.memories = [memory for memory in self.memories if memory.get("id") not in related_ids]
        self.save_memories()
        return {"deleted_count": len(related)}

    def update_memory(self, memory_id, updates):
        if not isinstance(updates, dict):
            return False
        for m in self.memories:
            if m["id"] == memory_id:
                allowed = {
                    "title", "content", "source", "is_locked", "is_important", "is_archived",
                    "category", "tags", "confidence", "importance_score", "expires_at",
                    "status", "supersedes", "conflicts_with"
                }
                clean_updates = {k: v for k, v in updates.items() if k in allowed}
                if m.get("managed_slot"):
                    blocked = {"title", "content", "is_locked", "is_archived", "source", "category", "tags", "supersedes", "conflicts_with"}
                    clean_updates = {k: v for k, v in clean_updates.items() if k not in blocked}
                    clean_updates["is_locked"] = True
                    clean_updates["source"] = "about_you"
                m.update(clean_updates)
                if "content" in clean_updates and "tags" not in clean_updates:
                    m["category"] = self.infer_category(m.get("content", ""), m.get("title", ""), m.get("source", ""))
                    m["tags"] = self.infer_tags(m.get("content", ""), m.get("category"))
                self.normalize_memory(m)
                self.save_memories()
                return True
        return False

    def get_memories(self, include_archived=False):
        memories = self.memories
        if not include_archived:
            memories = [m for m in memories if not m.get("is_archived", False)]
        return sorted(memories, key=lambda x: x.get("date", ""), reverse=True)

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

    def score_memory(self, memory, query, project_id=None):
        memory_project_id = str(memory.get("project_id") or "")
        active_project_id = str(project_id or "")
        base_score = self.memory_priority_score(memory, query)
        low, high = self._memory_score_band(memory)
        adjustment = 0.0
        if active_project_id and memory_project_id == active_project_id:
            adjustment += (high - low) * 0.18
        elif memory_project_id and memory_project_id != active_project_id:
            adjustment -= (high - low) * 0.18
        return round(max(low, min(high, base_score + adjustment)), 4)

    def _memory_allowed_for_project(self, memory, project_id=None):
        memory_project_id = str(memory.get("project_id") or "")
        active_project_id = str(project_id or "")
        if not memory_project_id:
            return True
        return bool(active_project_id and memory_project_id == active_project_id)

    def search_memories(self, query="", category=None, include_archived=False, limit=50):
        query = str(query or "").strip()
        results = []
        for memory in self.get_memories(include_archived=include_archived):
            if category and memory.get("category") != category:
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

        try:
            limit = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            limit = 50
        return results[:limit]

    def get_relevant_memories(self, user_input, mode="normal", limit=None, mark_accessed=True, project_id=None):
        if limit is None:
            limit = self.settings.get("relevant_memory_count", 8)
        try:
            limit = max(1, min(30, int(limit)))
        except (TypeError, ValueError):
            limit = 8

        candidates = [
            m for m in self.memories
            if not m.get("is_archived")
            and not self._is_expired(m)
            and self._memory_allowed_for_project(m, project_id=project_id)
        ]

        if mode == "deep":
            scored = []
            for memory in candidates:
                item = dict(memory)
                item["_score"] = self.score_memory(memory, user_input, project_id=project_id)
                item["_tier"] = self._memory_tier(memory)
                item["_priority_score"] = self.memory_priority_score(memory, user_input)
                scored.append(item)
            scored.sort(
                key=lambda m: (
                    m.get("_priority_score", 0),
                    m.get("_score", 0),
                    m.get("date", "")
                ),
                reverse=True
            )
            selected = scored[: max(limit, 20)]
        else:
            core = [
                dict(m) for m in candidates
                if m.get("is_locked") or m.get("is_important")
            ]
            relevant = []
            core_ids = {m.get("id") for m in core}
            for memory in candidates:
                if memory.get("id") in core_ids:
                    continue
                semantic_score = self.semantic_memory_score(memory, user_input)
                query_tokens = self._tokenize(user_input)
                memory_text = self._memory_text(memory).lower()
                memory_tokens = self._tokenize(memory_text)
                has_query_signal = (
                    str(user_input or "").lower() in memory_text
                    or bool(query_tokens & memory_tokens)
                    or semantic_score >= self.VECTOR_MATCH_THRESHOLD
                )
                if not has_query_signal:
                    continue
                score = self.score_memory(memory, user_input, project_id=project_id)
                if score > 0.12:
                    item = dict(memory)
                    item["_score"] = score
                    item["_tier"] = self._memory_tier(memory)
                    item["_priority_score"] = self.memory_priority_score(memory, user_input)
                    item["_semantic_score"] = round(semantic_score, 4)
                    relevant.append(item)
            for item in core:
                item["_tier"] = self._memory_tier(item)
                item["_priority_score"] = self.memory_priority_score(item, user_input)
                item["_score"] = self.score_memory(item, user_input, project_id=project_id)
            core.sort(key=lambda m: (m.get("_priority_score", 0), m.get("_score", 0), m.get("date", "")), reverse=True)
            relevant.sort(key=lambda m: (m.get("_priority_score", 0), m.get("_score", 0), m.get("date", "")), reverse=True)
            selected = (core[:limit] + relevant[:limit])[: limit * 2]

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
                self.save_memories()

        return selected

    def format_memory_for_prompt(self, memory, include_metadata=True):
        content = str(memory.get("content", "")).strip()
        if len(content) > 1200:
            content = content[:1200] + "…"

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
        flag_text = f"; flags={', '.join(flags)}" if flags else ""
        project_text = ""
        if memory.get("project_name"):
            project_text = f"; project={memory.get('project_name')}"
        return (
            f"- {memory.get('title', 'Untitled')} "
            f"[category={memory.get('category', 'general')}; date={memory.get('date', '')}; "
            f"confidence={self._coerce_float(memory.get('confidence'), 0.75):.2f}; "
            f"source={memory.get('source', 'manual')}; tags={tag_text}{project_text}{flag_text}]\n"
            f"  {content}"
        )

    def build_prompt_context(self, user_input, mode="normal", project_id=None):
        if not self.settings.get("memory_2_enabled", True):
            memories = [
                m for m in self.get_memories()
                if self._memory_allowed_for_project(m, project_id=project_id)
            ]
            if not memories:
                return ""
            target = [m for m in memories if m.get("is_locked") or m.get("is_important")]
            if mode == "deep":
                target = memories[:25]
            if not target:
                return ""
            lines = [self.format_memory_for_prompt(m, include_metadata=False) for m in target]
            return (
                "[System: Long-term Memory]\n"
                "Use these notes to help answer Doctor. Do not invent details that are not present.\n"
                + "\n".join(lines) + "\n\n"
            )

        memories = self.get_relevant_memories(user_input, mode=mode, project_id=project_id)
        if not memories:
            return ""

        include_metadata = self.settings.get("include_memory_metadata", True)
        lines = [self.format_memory_for_prompt(m, include_metadata=include_metadata) for m in memories]
        mode_note = (
            "Deep recall mode is active: search all supplied memories carefully, but say when a detail is missing."
            if mode == "deep"
            else "Normal mode is active: prioritize locked/starred memories and relevant notes."
        )
        return (
            "[System: Memory 2.0]\n"
            "The following are long-term memories about Doctor, Saya, projects, preferences, and prior events.\n"
            f"{mode_note}\n"
            "Memory rules: prefer newer locked/starred memories when notes conflict; treat low-confidence notes as hints; "
            "do not fabricate missing details; do not expose internal IDs unless Doctor asks to manage memory.\n"
            + "\n".join(lines) + "\n\n"
        )

    def merge_memories(self, days=7):
        """Non-destructively compact old memories while preserving originals as archived."""
        try:
            days = max(1, min(365, int(days)))
        except (TypeError, ValueError):
            days = 7
        cutoff = (datetime.now() - timedelta(days=days)).date()
        eligible = []
        for memory in self.memories:
            if memory.get("is_archived") or memory.get("is_locked") or memory.get("is_important"):
                continue
            if memory.get("source") == "system":
                continue
            try:
                memory_date = datetime.strptime(memory.get("date", ""), "%Y-%m-%d").date()
            except (TypeError, ValueError):
                continue
            if memory_date <= cutoff:
                eligible.append(memory)

        if len(eligible) < 2:
            return {"status": "skipped", "merged_count": 0}

        eligible.sort(key=lambda x: x.get("date", ""))
        content_parts = []
        for memory in eligible:
            content = str(memory.get("content", "")).strip()
            if len(content) > 800:
                content = content[:800] + "…"
            content_parts.append(
                f"- [{memory.get('date', '')}] {memory.get('title', 'Untitled')}\n  {content}"
            )

        merged = self.add_memory(
            "\n\n".join(content_parts),
            title=f"Merged Memories · {eligible[0].get('date')} to {eligible[-1].get('date')}",
            source="auto_merge",
            is_important=False
        )
        for memory in eligible:
            memory["is_archived"] = True
            memory["merged_into"] = merged["id"]
        self.save_memories()
        return {
            "status": "success",
            "merged_count": len(eligible),
            "memory": merged
        }

    def maybe_auto_merge(self):
        if not self.settings.get("auto_merge_enabled", False):
            return {"status": "disabled", "merged_count": 0}
        today = datetime.now().strftime("%Y-%m-%d")
        if self.settings.get("last_auto_merge") == today:
            return {"status": "already_checked", "merged_count": 0}
        result = self.merge_memories(self.settings.get("auto_merge_days", 7))
        self.settings["last_auto_merge"] = today
        self.save_settings()
        return result

    def capture_explicit_memory(self, user_input, project_id=None, project_name=None, session_id=None):
        """Save only explicit 'remember this' requests, avoiding speculative extraction."""
        if not self.settings.get("auto_save_memories", True):
            return None
        text = str(user_input or "").strip()
        patterns = [
            r"(?:请|請|帮我|幫我|你要|以后|以後)?\s*(?:记住|記住|记一下|記一下|记下来|記下來)(?:这件事|這件事|这一点|這一點|一下|[:：])?\s*(.+)",
            r"(?:please\s+)?remember(?:\s+that| this|:)?\s+(.+)",
            r"(?:save|keep)\s+(?:this\s+)?(?:memory|note)(?:\s*[:：])?\s+(.+)"
        ]
        captured = None
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
            if match:
                captured = match.group(1).strip()
                break
        if not captured or len(captured) < 2:
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

        return self.add_memory(
            captured,
            title=f"Explicit Memory · {datetime.now().strftime('%Y-%m-%d')}",
            source="auto_explicit",
            is_important=True,
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
            "schema_version": 1,
            "tasks": [],
            "records": {},
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
                "created_at": str(task.get("created_at") or self._now().strftime("%Y-%m-%d %H:%M:%S"))
            })
        return clean

    def add_task(self, title):
        title = re.sub(r"\s+", " ", str(title or "")).strip()
        if not title:
            return None
        now = self._now().strftime("%Y-%m-%d %H:%M:%S")
        task = {
            "id": str(uuid.uuid4()),
            "title": title[:80],
            "frequency": "daily",
            "order": len(self.data.get("tasks", [])),
            "active": True,
            "created_at": now
        }
        self.data["tasks"].append(task)
        self.save_data()
        return task

    def update_task(self, task_id, updates):
        if not isinstance(updates, dict):
            return False
        for task in self.data.get("tasks", []):
            if task.get("id") == task_id:
                if "title" in updates:
                    title = re.sub(r"\s+", " ", str(updates.get("title") or "")).strip()
                    if title:
                        task["title"] = title[:80]
                if "active" in updates:
                    task["active"] = bool(updates.get("active"))
                if "frequency" in updates:
                    frequency = str(updates.get("frequency") or "daily").lower()
                    if frequency in {"daily", "weekdays", "weekends", "weekly", "monthly"}:
                        task["frequency"] = frequency
                if "order" in updates:
                    try:
                        task["order"] = int(updates.get("order"))
                    except (TypeError, ValueError):
                        pass
                self.save_data()
                return True
        return False

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
        for task_id in active_ids:
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

class AssistantRoleManager:
    SUMMARY_MEMORY_ID = "assistant-role-learning-summary"
    STATE_VERSION = 2

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
            "session_ids": []
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
            "This ordinary-mode locked memory records only broad chapter/area names so normal chats know what Doctor has been studying."
        ]
        for group in groups[:24]:
            titles = "; ".join(group.get("titles", [])[:5])
            line = f"- {group.get('name')}: {group.get('note_count', 0)} notes"
            if titles:
                line += f" ({titles})"
            lines.append(line)
        if path_memories:
            lines.append("Curated path descriptions:")
            for item in path_memories[:24]:
                lines.append(f"- {item.get('path')}: {item.get('description')}")
        content = "\n".join(lines)
        if len(content) <= 1800:
            return content
        compact = lines[:3]
        if path_memories:
            compact.append("Curated path descriptions:")
            for item in path_memories[:12]:
                compact.append(f"- {item.get('path')}: {item.get('description')}")
        for group in groups[:40]:
            compact.append(f"- {group.get('name')}: {group.get('note_count', 0)} notes")
        return "\n".join(compact)[:2200]

    def update_normal_mode_memory(self, kb):
        content = self._normal_mode_summary_content()
        if not content:
            return {"updated": False, "memory": None}
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        memory = kb._find_memory(self.SUMMARY_MEMORY_ID)
        if not memory:
            memory = {
                "id": self.SUMMARY_MEMORY_ID,
                "creation_time": now,
                "date": now[:10],
            }
            kb.memories.append(memory)
        memory.update({
            "title": "Assistant Role · Obsidian Learning Map",
            "content": content,
            "source": "session_archive",
            "source_detail": {
                "type": "assistant_role_learning_map",
                "captured_at": memory.get("creation_time", now),
                "updated_at": now
            },
            "category": "conversation_summary",
            "tags": ["assistant_role", "obsidian", "learning_map", "archive"],
            "confidence": 0.9,
            "importance_score": 0.86,
            "is_locked": True,
            "is_important": True,
            "is_archived": False,
            "last_updated": now
        })
        kb.normalize_memory(memory)
        kb.save_memories()
        return {"updated": True, "memory": memory}

assistant_role_manager = AssistantRoleManager()

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
    def __init__(self):
        self._last_motion_name = None
        self._last_motion_at = 0.0
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
                {'name': 'Sleepy_idle_158', 'weight': 50}
            ],
            'easter_egg': [
                {'name': 'Robot_idle_418', 'weight': 70},
                {'name': 'Fairy_idle_362', 'weight': 30}
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

    def is_night_time(self):
        """Check if it's night time (18:00 - 06:00)"""
        hour = datetime.now().hour
        return hour >= 18 or hour <= 6

    def analyze_user_input(self, text):
        """Analyze user input to determine motion category - Improved heuristics"""
        text = text.lower()
        
        if any(k in text for k in ['人机舞', '跳舞', '机器人', '舞蹈', '转圈', '变身', 'robot', 'dance', 'dancing']):
            return 'easter_egg'

        if any(k in text for k in ['困', '想睡', '睡了', '晚安', 'good night', 'sleepy']):
            return 'night_sleepy'

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
        elif self.is_night_time() and random.random() < 0.3:
            category = 'night_sleepy'
        else:
            category = 'giving_answer' if context.get('is_responding') else 'normal_idle'

        motion_name = self.select_motion_from_category(category)
        return {
            'category': category,
            'motion_name': motion_name,
            'is_night': self.is_night_time()
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

    # Update or Create
    if target_mem:
        # If it was "Achievements Log", rename it too
        kb_system.update_memory(target_mem["id"], {
            "title": "Life Quests Summary",
            "content": content, 
            "is_locked": True
        })
        target_mem["date"] = datetime.now().strftime("%Y-%m-%d")
        target_mem["last_updated"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        kb_system.save_memories()
    else:
        mem = kb_system.add_memory(content, title="Life Quests Summary", source="system", is_important=True)
        kb_system.update_memory(mem["id"], {"is_locked": True})

