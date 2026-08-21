"""Safe, user-visible backup and selective restore for Saya data."""

import hashlib
import json
import os
import secrets
import tempfile
import threading
import zipfile
from datetime import datetime, timedelta
from pathlib import Path


class BackupManager:
    SCOPES = ("memories", "conversations", "checkins", "projects", "preferences")
    MAX_ARCHIVE_BYTES = 512 * 1024 * 1024

    def __init__(self, knowledge_dir, chat_dir, backup_dir=None):
        self.knowledge_dir = Path(knowledge_dir).resolve()
        self.chat_dir = Path(chat_dir).resolve()
        self.backup_dir = Path(backup_dir or (self.knowledge_dir / "user_backups")).resolve()
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self._tokens = {}
        self._lock = threading.RLock()

    @staticmethod
    def _sha256(path):
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _scope_paths(self, scope):
        knowledge_files = {
            "memories": (
                "knowledge.json", "memory_deletions.json", "memory_settings.json",
                "memory_candidates.json", "memory_graph.json", "memory_maintenance.json",
                "memory_cold_archive.jsonl", "long_term_memory.json", "rag_memory.json",
            ),
            "checkins": ("checkins.json", "life_quests.json"),
            "projects": ("projects.json",),
            "preferences": (
                "user_profile.json", "saya_style_settings.json", "emotion_state.json",
                "assistant_role.json", "video_recommendations.json", "learner_tasks.json",
            ),
        }
        entries = []
        if scope == "conversations":
            for path in sorted(self.chat_dir.glob("session_*.jsonl")):
                if path.is_file():
                    entries.append((path, f"conversations/{path.name}"))
            pinned = self.chat_dir / "pinned.json"
            if pinned.is_file():
                entries.append((pinned, "conversations/pinned.json"))
            legacy = self.knowledge_dir / "sessions.json"
            if legacy.is_file():
                entries.append((legacy, "conversations/legacy_sessions.json"))
            return entries
        for name in knowledge_files.get(scope, ()):
            path = self.knowledge_dir / name
            if path.is_file():
                entries.append((path, f"knowledge/{name}"))
        if scope == "preferences":
            profile = self.knowledge_dir / "saya" / "profile.txt"
            if profile.is_file():
                entries.append((profile, "knowledge/saya/profile.txt"))
        return entries

    def _normalize_scopes(self, scopes):
        requested = [str(scope) for scope in (scopes or self.SCOPES)]
        normalized = [scope for scope in self.SCOPES if scope in requested]
        if not normalized:
            raise ValueError("Select at least one backup category")
        return normalized

    def create_backup(self, scopes=None, reason="manual"):
        scopes = self._normalize_scopes(scopes)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        backup_id = f"saya-backup-{stamp}"
        final_path = self.backup_dir / f"{backup_id}.zip"
        temp_path = final_path.with_suffix(".tmp")
        files = []
        seen = set()
        for scope in scopes:
            for source, archive_name in self._scope_paths(scope):
                if archive_name in seen:
                    continue
                seen.add(archive_name)
                files.append({
                    "scope": scope,
                    "path": archive_name,
                    "size": source.stat().st_size,
                    "sha256": self._sha256(source),
                    "source": source,
                })
        if sum(item["size"] for item in files) > self.MAX_ARCHIVE_BYTES:
            raise ValueError("Selected data is too large for one safe backup")
        manifest = {
            "schema_version": 1,
            "backup_id": backup_id,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "reason": str(reason or "manual")[:80],
            "scopes": scopes,
            "files": [{key: value for key, value in item.items() if key != "source"} for item in files],
            "excluded_sensitive_data": [
                "API keys", "browser cookies", "environment variables", "generated SQLite search index",
            ],
        }
        with self._lock:
            try:
                with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
                    for item in files:
                        archive.write(item["source"], item["path"])
                os.replace(temp_path, final_path)
            finally:
                if temp_path.exists():
                    temp_path.unlink()
        result = dict(manifest)
        result["archive_size"] = final_path.stat().st_size
        return result

    def _backup_path(self, backup_id):
        backup_id = str(backup_id or "")
        if not backup_id.startswith("saya-backup-") or Path(backup_id).name != backup_id:
            raise ValueError("Invalid backup id")
        path = (self.backup_dir / f"{backup_id}.zip").resolve()
        if os.path.commonpath([str(path), str(self.backup_dir)]) != str(self.backup_dir):
            raise ValueError("Invalid backup path")
        if not path.is_file():
            raise FileNotFoundError("Backup not found")
        return path

    def _read_manifest(self, path):
        with zipfile.ZipFile(path, "r") as archive:
            names = archive.namelist()
            if "manifest.json" not in names:
                raise ValueError("Backup manifest is missing")
            if any(Path(name).is_absolute() or ".." in Path(name).parts for name in names):
                raise ValueError("Backup contains an unsafe path")
            total = sum(item.file_size for item in archive.infolist())
            if total > self.MAX_ARCHIVE_BYTES:
                raise ValueError("Backup is too large to restore safely")
            manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), list):
            raise ValueError("Unsupported backup format")
        return manifest

    def list_backups(self):
        results = []
        for path in sorted(self.backup_dir.glob("saya-backup-*.zip"), reverse=True):
            try:
                manifest = self._read_manifest(path)
                results.append({
                    "backup_id": manifest.get("backup_id"),
                    "created_at": manifest.get("created_at"),
                    "reason": manifest.get("reason", "manual"),
                    "scopes": manifest.get("scopes", []),
                    "file_count": len(manifest.get("files", [])),
                    "archive_size": path.stat().st_size,
                    "valid": True,
                })
            except Exception as exc:
                results.append({"backup_id": path.stem, "valid": False, "error": str(exc)})
        return results

    def _target_for_entry(self, entry):
        archive_name = str(entry.get("path") or "")
        scope = str(entry.get("scope") or "")
        if scope not in self.SCOPES:
            raise ValueError("Backup contains an unknown category")
        parts = Path(archive_name).parts
        if not parts or ".." in parts:
            raise ValueError("Backup contains an unsafe path")
        if archive_name == "conversations/legacy_sessions.json":
            return self.knowledge_dir / "sessions.json"
        if parts[0] == "conversations" and len(parts) == 2:
            return self.chat_dir / parts[1]
        if parts[0] == "knowledge":
            relative = Path(*parts[1:])
            target = (self.knowledge_dir / relative).resolve()
            if os.path.commonpath([str(target), str(self.knowledge_dir)]) == str(self.knowledge_dir):
                return target
        raise ValueError("Backup target is outside Saya data folders")

    def preview_restore(self, backup_id, scopes=None):
        path = self._backup_path(backup_id)
        manifest = self._read_manifest(path)
        scopes = self._normalize_scopes(scopes or manifest.get("scopes"))
        changes = []
        for entry in manifest["files"]:
            if entry.get("scope") not in scopes:
                continue
            target = self._target_for_entry(entry)
            action = "add"
            if target.is_file():
                action = "unchanged" if self._sha256(target) == entry.get("sha256") else "replace"
            changes.append({
                "scope": entry.get("scope"),
                "path": entry.get("path"),
                "action": action,
                "size": entry.get("size", 0),
            })
        token = secrets.token_urlsafe(24)
        self._tokens[token] = {
            "backup_id": backup_id,
            "scopes": tuple(scopes),
            "expires": datetime.now() + timedelta(minutes=10),
        }
        return {
            "backup": {key: manifest.get(key) for key in ("backup_id", "created_at", "reason", "scopes")},
            "selected_scopes": scopes,
            "changes": changes,
            "summary": {
                "add": sum(item["action"] == "add" for item in changes),
                "replace": sum(item["action"] == "replace" for item in changes),
                "unchanged": sum(item["action"] == "unchanged" for item in changes),
            },
            "confirmation_token": token,
            "expires_in_seconds": 600,
        }

    @staticmethod
    def _validate_content(path, data):
        suffix = path.suffix.lower()
        if suffix == ".json":
            json.loads(data.decode("utf-8"))
        elif suffix == ".jsonl":
            for line in data.decode("utf-8").splitlines():
                if line.strip():
                    json.loads(line)

    def restore(self, backup_id, scopes, confirmation_token):
        scopes = self._normalize_scopes(scopes)
        approval = self._tokens.pop(str(confirmation_token or ""), None)
        if (
            not approval
            or approval["backup_id"] != backup_id
            or approval["scopes"] != tuple(scopes)
            or approval["expires"] < datetime.now()
        ):
            raise ValueError("Restore preview expired or does not match this request")
        path = self._backup_path(backup_id)
        manifest = self._read_manifest(path)
        safety = self.create_backup(scopes=scopes, reason=f"pre_restore:{backup_id}")
        restored = []
        rollback = {}
        created = []
        with self._lock, zipfile.ZipFile(path, "r") as archive:
            try:
                for entry in manifest["files"]:
                    if entry.get("scope") not in scopes:
                        continue
                    data = archive.read(entry["path"])
                    if hashlib.sha256(data).hexdigest() != entry.get("sha256"):
                        raise ValueError(f"Backup checksum failed: {entry['path']}")
                    target = self._target_for_entry(entry)
                    self._validate_content(target, data)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if target.exists():
                        rollback[target] = target.read_bytes()
                    else:
                        created.append(target)
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                        handle.write(data)
                        staged = Path(handle.name)
                    os.replace(staged, target)
                    restored.append(entry["path"])
            except Exception:
                for target, data in rollback.items():
                    with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                        handle.write(data)
                        staged = Path(handle.name)
                    os.replace(staged, target)
                for target in created:
                    if target.exists():
                        target.unlink()
                raise
        return {
            "status": "success",
            "backup_id": backup_id,
            "restored_files": restored,
            "safety_backup_id": safety["backup_id"],
            "restart_recommended": True,
        }
