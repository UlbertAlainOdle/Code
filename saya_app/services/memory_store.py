import hashlib
import json
import math
import os
import re
import sqlite3
import struct
import threading
from contextlib import contextmanager
from datetime import datetime


class SQLiteMemoryStore:
    """A recoverable SQLite index/store that preserves JSON memory payloads verbatim."""

    SCHEMA_VERSION = 2

    def __init__(self, path):
        self.path = os.path.abspath(str(path))
        self._lock = threading.RLock()
        self.available = False
        self.last_error = ""
        self.fts_tokenizer = "trigram"
        self._lsh_projection_cache = {}
        self._initialize()

    def _connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    @contextmanager
    def _connection(self):
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with self._lock, self._connection() as connection:
                connection.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS memory_meta (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        payload TEXT NOT NULL,
                        title TEXT NOT NULL DEFAULT '',
                        content TEXT NOT NULL DEFAULT '',
                        tags TEXT NOT NULL DEFAULT '',
                        category TEXT NOT NULL DEFAULT 'general',
                        memory_layer TEXT NOT NULL DEFAULT 'semantic',
                        project_id TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT 'manual',
                        memory_date TEXT NOT NULL DEFAULT '',
                        importance REAL NOT NULL DEFAULT 0.4,
                        confidence REAL NOT NULL DEFAULT 0.75,
                        is_locked INTEGER NOT NULL DEFAULT 0,
                        is_important INTEGER NOT NULL DEFAULT 0,
                        is_archived INTEGER NOT NULL DEFAULT 0,
                        is_deleted INTEGER NOT NULL DEFAULT 0,
                        content_hash TEXT NOT NULL DEFAULT '',
                        updated_at TEXT NOT NULL DEFAULT ''
                    );

                    CREATE TABLE IF NOT EXISTS memory_tombstones (
                        id TEXT PRIMARY KEY,
                        deleted_at TEXT NOT NULL DEFAULT '',
                        reason TEXT NOT NULL DEFAULT 'user_delete'
                    );

                    CREATE INDEX IF NOT EXISTS idx_memories_scope
                        ON memories(is_deleted, is_archived, project_id, memory_layer, category);
                    CREATE INDEX IF NOT EXISTS idx_memories_date
                        ON memories(memory_date DESC, updated_at DESC);

                    CREATE TABLE IF NOT EXISTS memory_embeddings (
                        memory_id TEXT NOT NULL,
                        provider_key TEXT NOT NULL,
                        dimension INTEGER NOT NULL,
                        vector BLOB NOT NULL,
                        content_hash TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY(memory_id, provider_key),
                        FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
                    );

                    CREATE TABLE IF NOT EXISTS embedding_lsh (
                        memory_id TEXT NOT NULL,
                        provider_key TEXT NOT NULL,
                        band INTEGER NOT NULL,
                        bucket INTEGER NOT NULL,
                        PRIMARY KEY(memory_id, provider_key, band),
                        FOREIGN KEY(memory_id, provider_key)
                            REFERENCES memory_embeddings(memory_id, provider_key)
                            ON DELETE CASCADE
                    );

                    CREATE INDEX IF NOT EXISTS idx_embedding_lsh_lookup
                        ON embedding_lsh(provider_key, band, bucket);
                    """
                )
                try:
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                        "USING fts5(memory_id UNINDEXED, title, content, tags, category, tokenize='trigram')"
                    )
                except sqlite3.OperationalError:
                    self.fts_tokenizer = "unicode61"
                    connection.execute(
                        "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts "
                        "USING fts5(memory_id UNINDEXED, title, content, tags, category, tokenize='unicode61')"
                    )
                fts_schema = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='memory_fts'"
                ).fetchone()
                if fts_schema and "unicode61" in str(fts_schema[0] or "").lower():
                    self.fts_tokenizer = "unicode61"
                # Keep legacy soft-deleted payloads untouched. New confirmed
                # permanent deletions use purge_memory() below.
                connection.execute(
                    "INSERT OR IGNORE INTO memory_tombstones(id, deleted_at, reason) "
                    "SELECT id, updated_at, 'legacy_soft_delete' FROM memories WHERE is_deleted=1"
                )
                self._set_meta(connection, "schema_version", str(self.SCHEMA_VERSION))
                self._set_meta(connection, "fts_tokenizer", self.fts_tokenizer)
            self.available = True
            self.last_error = ""
        except Exception as exc:
            self.available = False
            self.last_error = str(exc)

    def _set_meta(self, connection, key, value):
        connection.execute(
            "INSERT INTO memory_meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(key), str(value)),
        )

    def _memory_values(self, memory):
        memory_id = str(memory.get("id") or "").strip()
        payload = json.dumps(memory, ensure_ascii=False, separators=(",", ":"))
        tags = memory.get("tags") if isinstance(memory.get("tags"), list) else []
        tags_text = " ".join(str(tag) for tag in tags if tag)
        updated_at = str(
            memory.get("last_updated")
            or memory.get("updated_at")
            or memory.get("creation_time")
            or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )
        content_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return {
            "id": memory_id,
            "payload": payload,
            "title": str(memory.get("title") or ""),
            "content": str(memory.get("content") or ""),
            "tags": tags_text,
            "category": str(memory.get("category") or "general"),
            "memory_layer": str(memory.get("memory_layer") or "semantic"),
            "project_id": str(memory.get("project_id") or ""),
            "source": str(memory.get("source") or "manual"),
            "memory_date": str(memory.get("date") or "")[:10],
            "importance": float(memory.get("importance_score", 0.4) or 0.4),
            "confidence": float(memory.get("confidence", 0.75) or 0.75),
            "is_locked": 1 if memory.get("is_locked") else 0,
            "is_important": 1 if memory.get("is_important") else 0,
            "is_archived": 1 if memory.get("is_archived") else 0,
            "content_hash": content_hash,
            "updated_at": updated_at,
        }

    def upsert_memories(self, memories, migration_source=""):
        if not self.available:
            return {"status": "unavailable", "count": 0, "error": self.last_error}
        rows = [self._memory_values(memory) for memory in memories if isinstance(memory, dict) and memory.get("id")]
        try:
            with self._lock, self._connection() as connection:
                for row in rows:
                    connection.execute("DELETE FROM memory_tombstones WHERE id=?", (row["id"],))
                    connection.execute(
                        """
                        INSERT INTO memories(
                            id, payload, title, content, tags, category, memory_layer,
                            project_id, source, memory_date, importance, confidence,
                            is_locked, is_important, is_archived, is_deleted,
                            content_hash, updated_at
                        ) VALUES(
                            :id, :payload, :title, :content, :tags, :category, :memory_layer,
                            :project_id, :source, :memory_date, :importance, :confidence,
                            :is_locked, :is_important, :is_archived, 0,
                            :content_hash, :updated_at
                        )
                        ON CONFLICT(id) DO UPDATE SET
                            payload=excluded.payload,
                            title=excluded.title,
                            content=excluded.content,
                            tags=excluded.tags,
                            category=excluded.category,
                            memory_layer=excluded.memory_layer,
                            project_id=excluded.project_id,
                            source=excluded.source,
                            memory_date=excluded.memory_date,
                            importance=excluded.importance,
                            confidence=excluded.confidence,
                            is_locked=excluded.is_locked,
                            is_important=excluded.is_important,
                            is_archived=excluded.is_archived,
                            is_deleted=0,
                            content_hash=excluded.content_hash,
                            updated_at=excluded.updated_at
                        """,
                        row,
                    )
                    connection.execute("DELETE FROM memory_fts WHERE memory_id = ?", (row["id"],))
                    if not row["is_archived"]:
                        connection.execute(
                            "INSERT INTO memory_fts(memory_id, title, content, tags, category) VALUES(?, ?, ?, ?, ?)",
                            (row["id"], row["title"], row["content"], row["tags"], row["category"]),
                        )
                now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._set_meta(connection, "last_sync_at", now)
                self._set_meta(connection, "last_sync_count", str(len(rows)))
                if migration_source:
                    self._set_meta(connection, "migration_complete", "1")
                    self._set_meta(connection, "migration_source", str(migration_source))
                    self._set_meta(connection, "migrated_at", now)
            self.last_error = ""
            return {"status": "success", "count": len(rows)}
        except Exception as exc:
            self.last_error = str(exc)
            return {"status": "error", "count": 0, "error": self.last_error}

    def mark_deleted(self, memory_id):
        if not self.available or not memory_id:
            return False
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    "UPDATE memories SET is_deleted=1, updated_at=? WHERE id=?",
                    (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), str(memory_id)),
                )
                connection.execute("DELETE FROM memory_fts WHERE memory_id=?", (str(memory_id),))
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def purge_memory(self, memory_id, reason="confirmed_permanent_delete"):
        """Remove payload/index data while retaining a content-free anti-resurrection marker."""
        if not self.available or not memory_id:
            return False
        try:
            with self._lock, self._connection() as connection:
                connection.execute(
                    "INSERT INTO memory_tombstones(id, deleted_at, reason) VALUES(?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET deleted_at=excluded.deleted_at, reason=excluded.reason",
                    (
                        str(memory_id),
                        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        str(reason or "confirmed_permanent_delete"),
                    ),
                )
                connection.execute("DELETE FROM memory_fts WHERE memory_id=?", (str(memory_id),))
                connection.execute("DELETE FROM memories WHERE id=?", (str(memory_id),))
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def revive_memory(self, memory_id):
        if not self.available or not memory_id:
            return False
        try:
            with self._lock, self._connection() as connection:
                connection.execute("DELETE FROM memory_tombstones WHERE id=?", (str(memory_id),))
            return True
        except Exception as exc:
            self.last_error = str(exc)
            return False

    def load_deleted_ids(self):
        """Return durable SQLite tombstones without exposing deleted payloads."""
        if not self.available:
            return set()
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(
                    "SELECT id FROM memory_tombstones UNION SELECT id FROM memories WHERE is_deleted=1"
                ).fetchall()
            return {str(row["id"]) for row in rows if row["id"]}
        except Exception as exc:
            self.last_error = str(exc)
            return set()

    def load_active_memories(self):
        if not self.available:
            return []
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(
                    "SELECT payload FROM memories WHERE is_deleted=0 ORDER BY memory_date, updated_at"
                ).fetchall()
            loaded = []
            for row in rows:
                try:
                    value = json.loads(row["payload"])
                    if isinstance(value, dict):
                        loaded.append(value)
                except (TypeError, json.JSONDecodeError):
                    continue
            return loaded
        except Exception as exc:
            self.last_error = str(exc)
            return []

    def _fts_expression(self, query):
        normalized = re.sub(r"\s+", " ", str(query or "").strip().lower())
        if not normalized:
            return ""
        candidates = []
        english = re.findall(r"[a-z0-9_]{3,}", normalized)
        candidates.extend(english[:12])
        cjk = "".join(re.findall(r"[\u3400-\u9fff]", normalized))
        if self.fts_tokenizer == "trigram":
            candidates.extend(cjk[index:index + 3] for index in range(max(0, len(cjk) - 2)))
        elif len(cjk) >= 2:
            candidates.append(cjk)
        if len(normalized) >= 3:
            candidates.append(normalized[:120])
        unique = []
        for candidate in candidates:
            candidate = candidate.replace('"', '""').strip()
            if len(candidate) >= 3 and candidate not in unique:
                unique.append(candidate)
            if len(unique) >= 24:
                break
        return " OR ".join(f'"{candidate}"' for candidate in unique)

    def search_candidate_ids(self, query, limit=80, project_id=None):
        if not self.available:
            return []
        expression = self._fts_expression(query)
        if not expression:
            return []
        try:
            limit = max(1, min(2000, int(limit)))
        except (TypeError, ValueError):
            limit = 80
        active_project_id = str(project_id or "")
        if active_project_id:
            scope_sql = "AND (m.project_id='' OR m.project_id=?)"
            params = [expression, active_project_id, limit]
        else:
            scope_sql = "AND m.project_id=''"
            params = [expression, limit]
        sql = f"""
            SELECT m.id, bm25(memory_fts) AS rank
            FROM memory_fts
            JOIN memories m ON m.id=memory_fts.memory_id
            WHERE memory_fts MATCH ?
              AND m.is_deleted=0
              AND m.is_archived=0
              {scope_sql}
            ORDER BY rank ASC, m.memory_date DESC
            LIMIT ?
        """
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(sql, params).fetchall()
            return [str(row["id"]) for row in rows]
        except Exception as exc:
            self.last_error = str(exc)
            return []

    def _normalize_vector(self, vector):
        values = [float(value) for value in vector]
        norm = math.sqrt(sum(value * value for value in values))
        if not values or not norm:
            raise ValueError("Embedding vector is empty or has zero norm.")
        return [value / norm for value in values]

    def _pack_vector(self, vector):
        return struct.pack(f"<{len(vector)}f", *vector)

    def _unpack_vector(self, blob, dimension):
        return struct.unpack(f"<{int(dimension)}f", blob)

    def _lsh_projection(self, provider_key, dimension):
        cache_key = (str(provider_key), int(dimension))
        cached = self._lsh_projection_cache.get(cache_key)
        if cached is not None:
            return cached
        projection = []
        for bit in range(64):
            samples = []
            for sample in range(16):
                digest = hashlib.sha256(
                    f"{provider_key}:{dimension}:{bit}:{sample}".encode("utf-8")
                ).digest()
                index = int.from_bytes(digest[:4], "little") % dimension
                sign = 1.0 if digest[4] & 1 else -1.0
                samples.append((index, sign))
            projection.append(samples)
        self._lsh_projection_cache[cache_key] = projection
        return projection

    def _lsh_buckets(self, vector, provider_key):
        projection = self._lsh_projection(provider_key, len(vector))
        signature = 0
        for bit, samples in enumerate(projection):
            value = sum(vector[index] * sign for index, sign in samples)
            if value >= 0:
                signature |= 1 << bit
        return [(band, int((signature >> (band * 8)) & 0xFF)) for band in range(8)]

    def upsert_embeddings(self, provider_key, entries):
        if not self.available:
            return {"status": "unavailable", "count": 0, "error": self.last_error}
        prepared = []
        try:
            for entry in entries:
                memory_id = str(entry.get("memory_id") or "")
                vector = self._normalize_vector(entry.get("vector") or [])
                if not memory_id:
                    continue
                prepared.append({
                    "memory_id": memory_id,
                    "vector": vector,
                    "dimension": len(vector),
                    "content_hash": str(entry.get("content_hash") or ""),
                })
            with self._lock, self._connection() as connection:
                for entry in prepared:
                    connection.execute(
                        """
                        INSERT INTO memory_embeddings(
                            memory_id, provider_key, dimension, vector, content_hash, updated_at
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        ON CONFLICT(memory_id, provider_key) DO UPDATE SET
                            dimension=excluded.dimension,
                            vector=excluded.vector,
                            content_hash=excluded.content_hash,
                            updated_at=excluded.updated_at
                        """,
                        (
                            entry["memory_id"],
                            str(provider_key),
                            entry["dimension"],
                            self._pack_vector(entry["vector"]),
                            entry["content_hash"],
                            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        ),
                    )
                    connection.execute(
                        "DELETE FROM embedding_lsh WHERE memory_id=? AND provider_key=?",
                        (entry["memory_id"], str(provider_key)),
                    )
                    connection.executemany(
                        "INSERT INTO embedding_lsh(memory_id, provider_key, band, bucket) VALUES(?, ?, ?, ?)",
                        [
                            (entry["memory_id"], str(provider_key), band, bucket)
                            for band, bucket in self._lsh_buckets(entry["vector"], provider_key)
                        ],
                    )
                self._set_meta(connection, "embedding_last_indexed_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
                self._set_meta(connection, "embedding_provider_key", str(provider_key))
            return {"status": "success", "count": len(prepared)}
        except Exception as exc:
            self.last_error = str(exc)
            return {"status": "error", "count": 0, "error": self.last_error}

    def embedding_hashes(self, provider_key):
        if not self.available:
            return {}
        try:
            with self._lock, self._connection() as connection:
                rows = connection.execute(
                    "SELECT memory_id, content_hash FROM memory_embeddings WHERE provider_key=?",
                    (str(provider_key),),
                ).fetchall()
            return {str(row["memory_id"]): str(row["content_hash"]) for row in rows}
        except Exception as exc:
            self.last_error = str(exc)
            return {}

    def embedding_status(self, provider_key=""):
        if not self.available:
            return {"available": False, "count": 0, "last_error": self.last_error}
        try:
            with self._lock, self._connection() as connection:
                if provider_key:
                    row = connection.execute(
                        "SELECT COUNT(*) AS count, MIN(dimension) AS min_dim, MAX(dimension) AS max_dim "
                        "FROM memory_embeddings WHERE provider_key=?",
                        (str(provider_key),),
                    ).fetchone()
                else:
                    row = connection.execute(
                        "SELECT COUNT(*) AS count, MIN(dimension) AS min_dim, MAX(dimension) AS max_dim "
                        "FROM memory_embeddings"
                    ).fetchone()
            return {
                "available": True,
                "count": int(row["count"] or 0),
                "min_dimension": int(row["min_dim"] or 0),
                "max_dimension": int(row["max_dim"] or 0),
                "provider_key": str(provider_key or ""),
                "last_error": self.last_error,
            }
        except Exception as exc:
            self.last_error = str(exc)
            return {"available": False, "count": 0, "last_error": self.last_error}

    def search_embedding(self, query_vector, provider_key, limit=80, min_similarity=0.25, project_id=None):
        if not self.available:
            return []
        try:
            query_vector = self._normalize_vector(query_vector)
            limit = max(1, min(1000, int(limit)))
            min_similarity = max(-1.0, min(1.0, float(min_similarity)))
            exact_probes = self._lsh_buckets(query_vector, provider_key)
            expanded_probes = list(exact_probes)
            for band, bucket in exact_probes:
                for bit in range(8):
                    expanded_probes.append((band, bucket ^ (1 << bit)))
            active_project_id = str(project_id or "")
            if active_project_id:
                scope_sql = "AND (m.project_id='' OR m.project_id=?)"
            else:
                scope_sql = "AND m.project_id=''"
            candidate_limit = max(400, min(10000, limit * 20))

            def fetch_candidate_ids(connection, probes):
                values_sql = ",".join(["(?, ?)"] * len(probes))
                params = []
                for band, bucket in probes:
                    params.extend([band, bucket])
                params.append(str(provider_key))
                if active_project_id:
                    params.append(active_project_id)
                params.append(candidate_limit)
                sql = f"""
                    WITH probes(band, bucket) AS (VALUES {values_sql})
                    SELECT l.memory_id, COUNT(*) AS collisions
                    FROM embedding_lsh l
                    JOIN probes p ON p.band=l.band AND p.bucket=l.bucket
                    JOIN memories m ON m.id=l.memory_id
                    WHERE l.provider_key=?
                      AND m.is_deleted=0
                      AND m.is_archived=0
                      {scope_sql}
                    GROUP BY l.memory_id
                    ORDER BY collisions DESC
                    LIMIT ?
                """
                return [str(row["memory_id"]) for row in connection.execute(sql, params).fetchall()]

            with self._lock, self._connection() as connection:
                candidate_ids = fetch_candidate_ids(connection, exact_probes)
                if len(candidate_ids) < max(100, limit * 2):
                    candidate_ids = fetch_candidate_ids(connection, expanded_probes)
                if not candidate_ids:
                    return []
                vector_rows = []
                for start in range(0, len(candidate_ids), 800):
                    chunk = candidate_ids[start:start + 800]
                    placeholders = ",".join(["?"] * len(chunk))
                    vector_rows.extend(connection.execute(
                        f"SELECT memory_id, dimension, vector FROM memory_embeddings "
                        f"WHERE provider_key=? AND memory_id IN ({placeholders})",
                        [str(provider_key), *chunk],
                    ).fetchall())
            scored = []
            for row in vector_rows:
                if int(row["dimension"]) != len(query_vector):
                    continue
                vector = self._unpack_vector(row["vector"], row["dimension"])
                score = sum(left * right for left, right in zip(query_vector, vector))
                if score >= min_similarity:
                    scored.append({"id": str(row["memory_id"]), "score": round(float(score), 6)})
            scored.sort(key=lambda item: item["score"], reverse=True)
            return scored[:limit]
        except Exception as exc:
            self.last_error = str(exc)
            return []

    def rebuild(self, memories):
        if not self.available:
            return {"status": "unavailable", "count": 0, "error": self.last_error}
        try:
            with self._lock, self._connection() as connection:
                connection.execute("DELETE FROM memory_fts")
            result = self.upsert_memories(memories, migration_source="rebuild")
            return result
        except Exception as exc:
            self.last_error = str(exc)
            return {"status": "error", "count": 0, "error": self.last_error}

    def integrity_check(self):
        if not self.available:
            return {"ok": False, "message": self.last_error or "SQLite unavailable"}
        try:
            with self._lock, self._connection() as connection:
                result = connection.execute("PRAGMA integrity_check").fetchone()[0]
                memory_count = connection.execute(
                    "SELECT COUNT(*) FROM memories WHERE is_deleted=0"
                ).fetchone()[0]
                indexed_count = connection.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
                meta_rows = connection.execute("SELECT key, value FROM memory_meta").fetchall()
            return {
                "ok": result == "ok",
                "message": result,
                "memory_count": int(memory_count),
                "indexed_count": int(indexed_count),
                "meta": {row["key"]: row["value"] for row in meta_rows},
                "path": self.path,
                "fts_tokenizer": self.fts_tokenizer,
                "last_error": self.last_error,
            }
        except Exception as exc:
            self.last_error = str(exc)
            return {"ok": False, "message": self.last_error, "path": self.path}
