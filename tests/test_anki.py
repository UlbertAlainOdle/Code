import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, time, timedelta
from pathlib import Path

from saya_app.services.anki import AnkiService


def create_collection(path, modern_decks=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path))
    connection.create_collation(
        "unicase",
        lambda left, right: (left.casefold() > right.casefold())
        - (left.casefold() < right.casefold()),
    )
    now = datetime.now().astimezone()
    created = int((now - timedelta(days=100)).timestamp())
    decks = {
        "1": {"id": 1, "name": "Languages", "dyn": 0},
        "2": {"id": 2, "name": "Languages::Japanese", "dyn": 0},
        "3": {"id": 3, "name": "Medicine", "dyn": 0},
    }
    connection.executescript(
        """
        CREATE TABLE col (crt integer, conf text, decks text);
        CREATE TABLE notes (id integer PRIMARY KEY);
        CREATE TABLE cards (
            id integer PRIMARY KEY, nid integer, did integer, odid integer,
            queue integer, due integer
        );
        CREATE TABLE revlog (
            id integer PRIMARY KEY, cid integer, ease integer, time integer
        );
        """
    )
    connection.execute(
        "INSERT INTO col (crt, conf, decks) VALUES (?, ?, ?)",
        (created, "{}", "{}" if modern_decks else json.dumps(decks)),
    )
    if modern_decks:
        connection.executescript(
            """
            CREATE TABLE decks (
                id integer PRIMARY KEY,
                name text NOT NULL COLLATE unicase
            );
            CREATE UNIQUE INDEX idx_decks_name ON decks(name COLLATE unicase);
            """
        )
        connection.executemany(
            "INSERT INTO decks (id, name) VALUES (?, ?)",
            [(1, "Languages"), (2, "Languages\x1fJapanese"), (3, "Medicine")],
        )
    connection.executemany("INSERT INTO notes (id) VALUES (?)", [(11,), (12,), (13,)])
    connection.executemany(
        "INSERT INTO cards (id, nid, did, odid, queue, due) VALUES (?, ?, ?, 0, ?, ?)",
        [
            (101, 11, 2, 2, 1),
            (102, 12, 2, 0, 2),
            (103, 13, 3, -1, 999),
        ],
    )

    review_rows = []
    for sequence, (offset, ease, cid, spent) in enumerate([
        (2, 4, 101, 12000),
        (1, 4, 101, 9000),
        (0, 1, 101, 8000),
        (0, 3, 102, 7000),
    ]):
        day = now.date() - timedelta(days=offset)
        local_noon = datetime.combine(day, time(hour=12)).astimezone()
        review_rows.append((int(local_noon.timestamp() * 1000) + sequence, cid, ease, spent))
    connection.executemany(
        "INSERT INTO revlog (id, cid, ease, time) VALUES (?, ?, ?, ?)",
        review_rows,
    )
    connection.commit()
    connection.close()


class AnkiServiceTests(unittest.TestCase):
    def test_daily_task_requires_a_studied_leaf_deck_with_no_due_cards(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "User 1" / "collection.anki2"
            create_collection(collection)
            service = AnkiService(base_dir=root / "knowledge_base")

            pending = service.configure(collection)
            self.assertFalse(pending["summary"]["daily_task_completed"])

            connection = sqlite3.connect(str(collection))
            connection.execute("UPDATE cards SET due = 999999 WHERE did = 2 AND queue = 2")
            connection.commit()
            connection.close()

            completed = service.refresh()
            self.assertTrue(completed["summary"]["daily_task_completed"])
            self.assertEqual(
                completed["summary"]["completed_decks_today"],
                ["Languages::Japanese"],
            )

    def test_custom_unicase_index_and_modern_decks_table_are_supported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "账户 1" / "collection.anki2"
            create_collection(collection, modern_decks=True)

            # Reproduce the behavior of a normal SQLite client before Saya's
            # compatibility handling: Anki's custom index cannot be checked.
            plain_connection = sqlite3.connect(str(collection))
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError, "unicase"):
                    plain_connection.execute("PRAGMA quick_check").fetchone()
            finally:
                plain_connection.close()

            service = AnkiService(base_dir=root / "knowledge_base")
            state = service.configure(collection)

            self.assertEqual(state["status"], "connected")
            self.assertEqual(state["profile"], "账户 1")
            self.assertEqual(state["summary"]["total_cards"], 3)
            self.assertTrue(any(
                deck["name"] == "Languages::Japanese" for deck in state["decks"]
            ))
            self.assertFalse(any(service.snapshot_dir.glob("*.anki2")))

    def test_configure_uses_read_only_snapshot_and_builds_statistics(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "Anki2" / "User 1" / "collection.anki2"
            create_collection(collection)
            modified_before = collection.stat().st_mtime_ns

            service = AnkiService(base_dir=root / "knowledge_base")
            state = service.configure(root / "Anki2", "User 1")

            self.assertEqual(state["status"], "connected")
            self.assertEqual(state["profile"], "User 1")
            self.assertTrue(state["summary"]["checked_in_today"])
            self.assertEqual(state["summary"]["today_reviews"], 2)
            self.assertEqual(state["summary"]["today_cards"], 2)
            self.assertEqual(state["summary"]["today_accuracy"], 50.0)
            self.assertEqual(state["summary"]["current_streak"], 3)
            self.assertEqual(state["summary"]["longest_streak"], 3)
            self.assertEqual(state["summary"]["total_cards"], 3)
            self.assertEqual(state["summary"]["new_cards"], 1)

            parent = next(deck for deck in state["decks"] if deck["name"] == "Languages")
            child = next(deck for deck in state["decks"] if deck["name"] == "Languages::Japanese")
            self.assertEqual(parent["total_cards"], 2)
            self.assertEqual(child["today_reviews"], 2)
            self.assertEqual(len(state["daily_activity"]), 30)
            self.assertEqual(collection.stat().st_mtime_ns, modified_before)
            self.assertFalse(any(service.snapshot_dir.glob("*.anki2")))

    def test_discovery_accepts_collection_file_and_rejects_program_folder(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "User 1" / "collection.anki2"
            create_collection(collection)
            service = AnkiService(base_dir=root / "knowledge_base")

            result = service.discover_profiles(collection)
            self.assertEqual(result["profiles"][0]["name"], "User 1")

            program_folder = root / "Anki"
            program_folder.mkdir()
            (program_folder / "anki.exe").write_bytes(b"program")
            with self.assertRaisesRegex(ValueError, "No collection.anki2"):
                service.discover_profiles(program_folder)

    def test_clear_settings_disconnects_without_touching_collection(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            collection = root / "User 1" / "collection.anki2"
            create_collection(collection)
            service = AnkiService(base_dir=root / "knowledge_base")
            service.configure(collection)
            service.clear_settings()

            self.assertTrue(collection.exists())
            self.assertFalse(service.get_state()["configured"])
            self.assertEqual(service.get_state()["status"], "not_configured")


if __name__ == "__main__":
    unittest.main()
