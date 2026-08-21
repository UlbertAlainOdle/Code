import json
import os
import re
import sqlite3
import tempfile
import threading
from datetime import date, datetime, time as datetime_time, timedelta
from pathlib import Path

from saya_app.config import KNOWLEDGE_BASE_DIR


class AnkiService:
    """Read Anki collection statistics without mutating the live collection."""

    def __init__(self, base_dir=KNOWLEDGE_BASE_DIR):
        self.base_dir = Path(base_dir)
        self.settings_path = self.base_dir / "anki_settings.json"
        self.snapshot_dir = self.base_dir / ".anki_snapshots"
        self._lock = threading.RLock()
        self._settings = self._load_settings()
        self._state = self._empty_state()

    @staticmethod
    def _expand_path(value):
        text = str(value or "").strip().strip('"').strip("'")
        if not text:
            return None

        # os.path.expandvars() does not expand %NAME% on every platform. Saya
        # may inspect a Windows path while tests run elsewhere, so handle both.
        text = re.sub(
            r"%([^%]+)%",
            lambda match: os.environ.get(match.group(1), match.group(0)),
            text,
        )
        # abspath() is lexical and does not probe the target. Path.resolve()
        # can raise PermissionError on a valid Anki2 folder before the user has
        # chosen to connect it, which must never prevent Saya from starting.
        return Path(os.path.abspath(os.path.expanduser(os.path.expandvars(text))))

    @staticmethod
    def default_data_path():
        appdata = str(os.environ.get("APPDATA") or "").strip()
        if appdata:
            return os.path.abspath(str(Path(appdata) / "Anki2"))
        if os.name == "nt":
            return os.path.abspath(str(Path.home() / "AppData" / "Roaming" / "Anki2"))
        if sys_platform() == "darwin":
            return os.path.abspath(str(Path.home() / "Library" / "Application Support" / "Anki2"))
        return os.path.abspath(str(Path.home() / ".local" / "share" / "Anki2"))

    def _load_settings(self):
        try:
            with self.settings_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
            if isinstance(data, dict):
                return {
                    "data_path": str(data.get("data_path") or "").strip(),
                    "profile": str(data.get("profile") or "").strip(),
                }
        except (OSError, ValueError, TypeError):
            pass
        return {"data_path": "", "profile": ""}

    def _save_settings(self):
        self.base_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.settings_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(self._settings, handle, ensure_ascii=False, indent=2)
        os.replace(str(temporary), str(self.settings_path))

    def _empty_state(self):
        return {
            "status": "not_configured",
            "configured": bool(self._settings.get("data_path")),
            "connected": False,
            "stale": False,
            "error": "",
            "settings": {
                **self._settings,
                "default_data_path": self.default_data_path(),
            },
            "profiles": [],
            "profile": "",
            "source_file": "",
            "source_modified_at": "",
            "last_refreshed_at": "",
            "summary": None,
            "decks": [],
            "daily_activity": [],
        }

    @staticmethod
    def _collection_header_is_valid(path):
        try:
            with Path(path).open("rb") as handle:
                return handle.read(16) == b"SQLite format 3\x00"
        except OSError:
            return False

    def discover_profiles(self, data_path):
        root = self._expand_path(data_path)
        if root is None:
            raise ValueError("Choose an Anki data folder first")
        if not root.exists():
            raise ValueError(f"The Anki data path does not exist: {root}")

        candidates = []
        if root.is_file():
            if root.name.lower() != "collection.anki2":
                raise ValueError("Choose an Anki profile folder or its collection.anki2 file")
            candidates.append((root.parent.name, root.parent, root))
        elif root.is_dir():
            direct = root / "collection.anki2"
            if direct.is_file():
                candidates.append((root.name, root, direct))
            else:
                for child in sorted(root.iterdir(), key=lambda item: item.name.casefold()):
                    collection = child / "collection.anki2"
                    if child.is_dir() and collection.is_file():
                        candidates.append((child.name, child, collection))

        profiles = []
        for name, profile_path, collection_path in candidates:
            profiles.append({
                "name": str(name),
                "path": str(profile_path),
                "collection_path": str(collection_path),
                "valid_sqlite": self._collection_header_is_valid(collection_path),
            })
        if not profiles:
            raise ValueError(
                "No collection.anki2 was found. Choose the Anki2 folder or a profile folder such as User 1"
            )
        return {"data_path": str(root), "profiles": profiles}

    @staticmethod
    def _select_profile(profiles, requested):
        requested = str(requested or "").strip().casefold()
        if requested:
            for profile in profiles:
                if profile["name"].casefold() == requested:
                    return profile
                if profile["path"].casefold() == requested:
                    return profile
        return profiles[0]

    def configure(self, data_path, profile=""):
        with self._lock:
            discovered = self.discover_profiles(data_path)
            selected = self._select_profile(discovered["profiles"], profile)
            if not selected.get("valid_sqlite"):
                raise ValueError("The selected collection.anki2 is not a readable SQLite database")
            self._settings = {
                "data_path": discovered["data_path"],
                "profile": selected["name"],
            }
            self._save_settings()
            self._state = self._empty_state()
            return self.refresh()

    def clear_settings(self):
        with self._lock:
            self._settings = {"data_path": "", "profile": ""}
            try:
                self.settings_path.unlink()
            except FileNotFoundError:
                pass
            self._state = self._empty_state()
            return self.get_state()

    def get_state(self):
        with self._lock:
            state = json.loads(json.dumps(self._state, ensure_ascii=False))
            state["configured"] = bool(self._settings.get("data_path"))
            state["settings"] = {
                **self._settings,
                "default_data_path": self.default_data_path(),
            }
            if state["configured"] and not state.get("profiles"):
                try:
                    discovered = self.discover_profiles(self._settings["data_path"])
                    state["profiles"] = discovered["profiles"]
                except (OSError, ValueError) as exc:
                    state["status"] = "error"
                    state["error"] = str(exc)
            return state

    def _snapshot_collection(self, source):
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        descriptor, snapshot_name = tempfile.mkstemp(
            prefix="anki-", suffix=".anki2", dir=str(self.snapshot_dir),
        )
        os.close(descriptor)
        snapshot = Path(snapshot_name)
        try:
            source_uri = f"{Path(os.path.abspath(str(source))).as_uri()}?mode=ro"
            source_db = sqlite3.connect(source_uri, uri=True, timeout=8)
            destination_db = sqlite3.connect(str(snapshot), timeout=8)
            try:
                source_db.execute("PRAGMA query_only = ON")
                source_db.backup(destination_db)
            finally:
                destination_db.close()
                source_db.close()
            return snapshot
        except Exception:
            try:
                snapshot.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _iso_from_timestamp(timestamp):
        if not timestamp:
            return ""
        return datetime.fromtimestamp(timestamp).astimezone().isoformat(timespec="seconds")

    @staticmethod
    def _table_names(connection):
        return {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    @staticmethod
    def _json_object(value):
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except (ValueError, TypeError):
            return {}

    @staticmethod
    def _table_columns(connection, table_name):
        if table_name not in {"col", "decks"}:
            return set()
        return {
            str(row[1])
            for row in connection.execute(f"PRAGMA table_info({table_name})")
        }

    def _deck_definitions(self, connection, tables, legacy_decks):
        """Load deck names from both current and legacy Anki schemas."""
        definitions = {}

        # Recent Anki versions store deck definitions in a dedicated table.
        # Native deck names use the unit separator; Anki displays it as "::".
        if "decks" in tables:
            columns = self._table_columns(connection, "decks")
            if {"id", "name"}.issubset(columns):
                for row in connection.execute("SELECT id, name FROM decks"):
                    try:
                        deck_id = int(row["id"])
                    except (TypeError, ValueError):
                        continue
                    name = str(row["name"] or f"Deck {deck_id}").replace("\x1f", "::")
                    definitions[deck_id] = {"id": deck_id, "name": name}

        # Older collections serialize deck definitions in col.decks. Keep this
        # fallback so one reader works across old and current Anki profiles.
        for raw_id, raw_deck in self._json_object(legacy_decks).items():
            if not isinstance(raw_deck, dict) or bool(raw_deck.get("dyn")):
                continue
            try:
                deck_id = int(raw_deck.get("id", raw_id))
            except (TypeError, ValueError):
                continue
            if deck_id not in definitions:
                definitions[deck_id] = {
                    "id": deck_id,
                    "name": str(raw_deck.get("name") or f"Deck {deck_id}"),
                }
        return definitions

    @staticmethod
    def _day_streaks(active_days, today):
        day_set = set(active_days)
        cursor = today if today in day_set else today - timedelta(days=1)
        current = 0
        while cursor in day_set:
            current += 1
            cursor -= timedelta(days=1)

        longest = 0
        running = 0
        previous = None
        for day in sorted(day_set):
            running = running + 1 if previous and day == previous + timedelta(days=1) else 1
            longest = max(longest, running)
            previous = day
        return current, longest

    @staticmethod
    def _local_day_bounds(day):
        start = datetime.combine(day, datetime_time.min).astimezone()
        end = datetime.combine(day + timedelta(days=1), datetime_time.min).astimezone()
        return int(start.timestamp() * 1000), int(end.timestamp() * 1000)

    def _read_statistics(self, snapshot, profile_name):
        connection = sqlite3.connect(str(snapshot))
        connection.row_factory = sqlite3.Row
        try:
            tables = self._table_names(connection)
            required = {"col", "cards", "notes", "revlog"}
            missing = sorted(required - tables)
            if missing:
                raise ValueError(f"Unsupported Anki database: missing {', '.join(missing)}")

            try:
                check = connection.execute("PRAGMA quick_check").fetchone()
            except sqlite3.OperationalError as exc:
                # Anki registers its own `unicase` collation for name indexes.
                # A standard read-only SQLite client cannot validate those
                # indexes, but this does not mean that the copied database is
                # corrupt. The statistics queries below do not use them.
                if "no such collation sequence: unicase" not in str(exc).casefold():
                    raise
                check = None
            if check is not None and (not check or str(check[0]).lower() != "ok"):
                raise ValueError("The Anki snapshot did not pass SQLite integrity checking")

            col_columns = self._table_columns(connection, "col")
            if "crt" not in col_columns:
                raise ValueError("Unsupported Anki database: collection creation time is missing")
            metadata_columns = "crt, decks" if "decks" in col_columns else "crt"
            collection = connection.execute(
                f"SELECT {metadata_columns} FROM col LIMIT 1"
            ).fetchone()
            if not collection:
                raise ValueError("The Anki collection metadata is empty")

            legacy_decks = collection["decks"] if "decks" in col_columns else "{}"
            deck_definitions = self._deck_definitions(connection, tables, legacy_decks)

            now = datetime.now().astimezone()
            today = now.date()
            creation_seconds = int(collection["crt"] or 0)
            anki_day = max(0, int((now.timestamp() - creation_seconds) // 86400))
            now_seconds = int(now.timestamp())

            card_rows = connection.execute(
                """
                SELECT CASE WHEN odid != 0 THEN odid ELSE did END AS deck_id,
                       COUNT(*) AS total_cards,
                       COUNT(DISTINCT nid) AS total_notes,
                       SUM(CASE WHEN queue = 0 THEN 1 ELSE 0 END) AS new_cards,
                       SUM(CASE WHEN queue IN (1, 3) THEN 1 ELSE 0 END) AS learning_cards,
                       SUM(CASE WHEN queue = 2 THEN 1 ELSE 0 END) AS review_cards,
                       SUM(CASE WHEN queue = -1 THEN 1 ELSE 0 END) AS suspended_cards,
                       SUM(CASE
                           WHEN queue = 1 AND due <= ? THEN 1
                           WHEN queue IN (2, 3) AND due <= ? THEN 1
                           ELSE 0 END) AS due_cards
                FROM cards
                GROUP BY CASE WHEN odid != 0 THEN odid ELSE did END
                """,
                (now_seconds, anki_day),
            ).fetchall()

            direct_stats = {}
            for row in card_rows:
                deck_id = int(row["deck_id"])
                if deck_id not in deck_definitions:
                    deck_definitions[deck_id] = {"id": deck_id, "name": f"Deck {deck_id}"}
                direct_stats[deck_id] = {
                    key: int(row[key] or 0)
                    for key in (
                        "total_cards", "total_notes", "new_cards", "learning_cards",
                        "review_cards", "suspended_cards", "due_cards",
                    )
                }

            today_start, today_end = self._local_day_bounds(today)
            today_row = connection.execute(
                """
                SELECT COUNT(*) AS reviews,
                       COUNT(DISTINCT cid) AS cards,
                       SUM(CASE WHEN ease > 1 THEN 1 ELSE 0 END) AS correct,
                       SUM(CASE WHEN ease = 1 THEN 1 ELSE 0 END) AS again,
                       SUM(time) AS time_ms
                FROM revlog
                WHERE ease > 0 AND id >= ? AND id < ?
                """,
                (today_start, today_end),
            ).fetchone()

            today_deck_rows = connection.execute(
                """
                SELECT CASE WHEN c.odid != 0 THEN c.odid ELSE c.did END AS deck_id,
                       COUNT(*) AS reviews
                FROM revlog AS r
                JOIN cards AS c ON c.id = r.cid
                WHERE r.ease > 0 AND r.id >= ? AND r.id < ?
                GROUP BY CASE WHEN c.odid != 0 THEN c.odid ELSE c.did END
                """,
                (today_start, today_end),
            ).fetchall()
            direct_today_reviews = {
                int(row["deck_id"]): int(row["reviews"] or 0)
                for row in today_deck_rows
            }

            daily_rows = connection.execute(
                """
                SELECT date(id / 1000, 'unixepoch', 'localtime') AS review_day,
                       COUNT(*) AS reviews,
                       COUNT(DISTINCT cid) AS cards,
                       SUM(CASE WHEN ease > 1 THEN 1 ELSE 0 END) AS correct,
                       SUM(CASE WHEN ease = 1 THEN 1 ELSE 0 END) AS again,
                       SUM(time) AS time_ms
                FROM revlog
                WHERE ease > 0
                GROUP BY review_day
                ORDER BY review_day
                """
            ).fetchall()

            daily_by_date = {}
            active_days = []
            all_time_reviews = 0
            all_time_correct = 0
            all_time_time_ms = 0
            for row in daily_rows:
                try:
                    parsed_day = date.fromisoformat(str(row["review_day"]))
                except (TypeError, ValueError):
                    continue
                item = {
                    "date": parsed_day.isoformat(),
                    "reviews": int(row["reviews"] or 0),
                    "cards": int(row["cards"] or 0),
                    "correct": int(row["correct"] or 0),
                    "again": int(row["again"] or 0),
                    "time_ms": int(row["time_ms"] or 0),
                }
                item["accuracy"] = round(item["correct"] * 100 / item["reviews"], 1) if item["reviews"] else 0.0
                daily_by_date[parsed_day] = item
                active_days.append(parsed_day)
                all_time_reviews += item["reviews"]
                all_time_correct += item["correct"]
                all_time_time_ms += item["time_ms"]

            current_streak, longest_streak = self._day_streaks(active_days, today)
            recent_days = []
            for offset in range(29, -1, -1):
                current_day = today - timedelta(days=offset)
                recent_days.append(daily_by_date.get(current_day, {
                    "date": current_day.isoformat(), "reviews": 0, "cards": 0,
                    "correct": 0, "again": 0, "time_ms": 0, "accuracy": 0.0,
                }))

            deck_items = []
            definitions_by_name = sorted(
                deck_definitions.values(), key=lambda item: (item["name"].casefold(), item["id"]),
            )
            leaf_deck_ids = {
                definition["id"]
                for definition in definitions_by_name
                if not any(
                    other["id"] != definition["id"]
                    and other["name"].startswith(definition["name"] + "::")
                    for other in definitions_by_name
                )
            }
            completed_decks_today = [
                definition["name"]
                for definition in definitions_by_name
                if definition["id"] in leaf_deck_ids
                and direct_today_reviews.get(definition["id"], 0) > 0
                and direct_stats.get(definition["id"], {}).get("due_cards", 0) == 0
            ]
            for definition in definitions_by_name:
                name = definition["name"]
                included_ids = {
                    item["id"] for item in definitions_by_name
                    if item["name"] == name or item["name"].startswith(name + "::")
                }
                aggregate = {
                    key: sum(direct_stats.get(deck_id, {}).get(key, 0) for deck_id in included_ids)
                    for key in (
                        "total_cards", "total_notes", "new_cards", "learning_cards",
                        "review_cards", "suspended_cards", "due_cards",
                    )
                }
                aggregate.update({
                    "id": definition["id"],
                    "name": name,
                    "depth": name.count("::"),
                    "today_reviews": sum(direct_today_reviews.get(deck_id, 0) for deck_id in included_ids),
                    "daily_task_completed": definition["id"] in leaf_deck_ids
                    and definition["name"] in completed_decks_today,
                })
                deck_items.append(aggregate)

            total_cards = int(connection.execute("SELECT COUNT(*) FROM cards").fetchone()[0] or 0)
            total_notes = int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0] or 0)
            total_new = sum(item.get("new_cards", 0) for item in direct_stats.values())
            total_due = sum(item.get("due_cards", 0) for item in direct_stats.values())
            today_reviews = int(today_row["reviews"] or 0)
            today_correct = int(today_row["correct"] or 0)

            return {
                "profile": profile_name,
                "summary": {
                    "checked_in_today": today_reviews > 0,
                    "daily_task_completed": bool(completed_decks_today),
                    "completed_decks_today": completed_decks_today,
                    "daily_task_rule": "studied_today_and_no_due_review_or_learning_cards",
                    "today_reviews": today_reviews,
                    "today_cards": int(today_row["cards"] or 0),
                    "today_correct": today_correct,
                    "today_again": int(today_row["again"] or 0),
                    "today_time_ms": int(today_row["time_ms"] or 0),
                    "today_accuracy": round(today_correct * 100 / today_reviews, 1) if today_reviews else 0.0,
                    "current_streak": current_streak,
                    "longest_streak": longest_streak,
                    "total_cards": total_cards,
                    "total_notes": total_notes,
                    "total_decks": len(deck_items),
                    "new_cards": total_new,
                    "due_cards": total_due,
                    "all_time_reviews": all_time_reviews,
                    "all_time_time_ms": all_time_time_ms,
                    "all_time_accuracy": round(all_time_correct * 100 / all_time_reviews, 1) if all_time_reviews else 0.0,
                },
                "decks": deck_items,
                "daily_activity": recent_days,
            }
        finally:
            connection.close()

    def refresh(self):
        with self._lock:
            if not self._settings.get("data_path"):
                self._state = self._empty_state()
                return self.get_state()

            previous = self._state
            snapshot = None
            try:
                discovered = self.discover_profiles(self._settings["data_path"])
                selected = self._select_profile(
                    discovered["profiles"], self._settings.get("profile"),
                )
                if not selected.get("valid_sqlite"):
                    raise ValueError("The selected collection.anki2 is not a readable SQLite database")
                source = Path(selected["collection_path"])
                snapshot = self._snapshot_collection(source)
                statistics = self._read_statistics(snapshot, selected["name"])

                source_times = [source.stat().st_mtime]
                wal_path = source.with_name(source.name + "-wal")
                if wal_path.exists():
                    source_times.append(wal_path.stat().st_mtime)
                self._settings["profile"] = selected["name"]
                self._state = {
                    "status": "connected",
                    "configured": True,
                    "connected": True,
                    "stale": False,
                    "error": "",
                    "settings": {
                        **self._settings,
                        "default_data_path": self.default_data_path(),
                    },
                    "profiles": discovered["profiles"],
                    "profile": selected["name"],
                    "source_file": str(source),
                    "source_modified_at": self._iso_from_timestamp(max(source_times)),
                    "last_refreshed_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                    **statistics,
                }
                return self.get_state()
            except (OSError, sqlite3.Error, ValueError) as exc:
                if previous.get("summary"):
                    self._state = {
                        **previous,
                        "status": "stale",
                        "connected": False,
                        "stale": True,
                        "error": str(exc),
                    }
                else:
                    self._state = self._empty_state()
                    self._state.update({
                        "status": "error",
                        "configured": True,
                        "error": str(exc),
                    })
                return self.get_state()
            finally:
                if snapshot is not None:
                    try:
                        snapshot.unlink()
                    except OSError:
                        pass


def sys_platform():
    import sys
    return sys.platform


anki_service = AnkiService()
