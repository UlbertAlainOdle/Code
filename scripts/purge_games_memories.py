"""One-off data surgery for the retired entertainment-games module AND the
game_task layer migration.  Idempotent; safe to re-run while the backend is
STOPPED (a live server holds memories in RAM and will re-save its copy).

    python -B scripts/purge_games_memories.py          # dry run
    python -B scripts/purge_games_memories.py --apply

Does three things:
1. knowledge.json: drop memories with source="games" (the chess/gomoku chat
   mini-games; Minecraft's source="game_task" is KEPT);
2. sqlite mirror: delete those rows from memories + memory_fts AND insert
   memory_tombstones - without tombstones the KB's rebuild-from-sqlite path
   resurrects purged rows (that is exactly how the first purge bounced back);
3. layer migration: stored memory_layer for game_task / category=event
   entries becomes "episodic" in both json and the sqlite payload.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

KNOWLEDGE_FILE = ROOT / "knowledge_base" / "knowledge.json"
SQLITE_FILE = ROOT / "knowledge_base" / "memory3.sqlite3"


def _is_games(memory):
    return str(memory.get("source") or "").lower() == "games"


def _needs_episodic(memory):
    if _is_games(memory):
        return False
    if str(memory.get("memory_layer") or "").lower() == "episodic":
        return False
    return (
        str(memory.get("source") or "").lower() == "game_task"
        or str(memory.get("category") or "").lower() == "event"
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument("--kb-dir", default=str(ROOT / "knowledge_base"),
                        help="knowledge base dir (tests point this at a temp copy)")
    args = parser.parse_args()

    kb_dir = Path(args.kb_dir)
    knowledge_file = kb_dir / "knowledge.json"
    sqlite_file = kb_dir / "memory3.sqlite3"
    data = json.loads(knowledge_file.read_text(encoding="utf-8"))
    wrapper = data if isinstance(data, dict) else None
    memories = wrapper.get("memories") if wrapper else data
    doomed = [m for m in memories if _is_games(m)]
    relayer = [m for m in memories if _needs_episodic(m)]
    kept = [m for m in memories if not _is_games(m)]
    print(f"memories: {len(memories)} total | purge games: {len(doomed)} | "
          f"re-layer to episodic: {len(relayer)} | keep: {len(kept)}")
    if not args.apply:
        print("dry run complete (use --apply)")
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if knowledge_file.exists():
        backup = knowledge_file.with_name(f"knowledge.before-games-purge.{stamp}.json")
        shutil.copy2(knowledge_file, backup)
        print(f"backup: {backup.name}")
    if sqlite_file.exists():
        shutil.copy2(sqlite_file, sqlite_file.with_name(f"memory3.before-purge.{stamp}.sqlite3"))

    # sqlite FIRST: delete + tombstone so any rebuild path cannot resurrect.
    doomed_ids = [str(m.get("id")) for m in doomed if m.get("id")]
    relayer_ids = {str(m.get("id")) for m in relayer if m.get("id")}
    if sqlite_file.exists() and doomed_ids:
        conn = sqlite3.connect(sqlite_file)
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            marks = ",".join("?" * len(doomed_ids))
            cur = conn.execute(f"DELETE FROM memories WHERE id IN ({marks})", doomed_ids)
            print(f"sqlite memories: removed {cur.rowcount}")
            cur = conn.execute(f"DELETE FROM memory_fts WHERE memory_id IN ({marks})", doomed_ids)
            print(f"sqlite memory_fts: removed {cur.rowcount}")
            rows = [(mid, now, "games_module_retired") for mid in doomed_ids]
            conn.executemany(
                "INSERT OR REPLACE INTO memory_tombstones (id, deleted_at, reason) "
                "VALUES (?, ?, ?)", rows)
            print(f"sqlite tombstones: {len(rows)} inserted")
            # Layer migration inside the sqlite payload blobs.
            patched = 0
            for row_id, payload in conn.execute("SELECT id, payload FROM memories").fetchall():
                if row_id not in relayer_ids or not payload:
                    continue
                try:
                    entry = json.loads(payload)
                except (TypeError, json.JSONDecodeError):
                    continue
                entry["memory_layer"] = "episodic"
                conn.execute(
                    "UPDATE memories SET payload = ?, memory_layer = ? WHERE id = ?",
                    (json.dumps(entry, ensure_ascii=False), "episodic", row_id))
                patched += 1
            print(f"sqlite payloads re-layered: {patched}")
            conn.commit()
        finally:
            conn.close()

    # json surgery + layer migration
    for memory in kept:
        if _needs_episodic(memory):
            memory["memory_layer"] = "episodic"
    if wrapper is not None:
        wrapper["memories"] = kept
        knowledge_file.write_text(
            json.dumps(wrapper, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        knowledge_file.write_text(
            json.dumps(kept, ensure_ascii=False, indent=2), encoding="utf-8")
    print("applied: json rewritten (games gone, game_task/event episodic)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
