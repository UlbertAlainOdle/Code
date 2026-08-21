import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core_services import CheckInManager


class GoalCheckInLinkTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="saya-goal-checkin-"))
        self.manager = CheckInManager(str(self.root))
        self.today = self.manager._today()

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_fixed_and_one_off_tasks_keep_goal_links_and_progress(self):
        fixed = self.manager.add_task("Practice sketching", goal_id="quest-art")
        self.assertEqual(fixed["goal_id"], "quest-art")

        self.manager.save_day(
            date_str=self.today,
            completions={fixed["id"]: True},
            daily_tasks=[{
                "id": "one-off-reference",
                "title": "Collect references",
                "done": False,
                "goal_id": "quest-art",
            }],
        )

        progress = self.manager.goal_progress(
            "quest-art",
            start_date=self.today,
            end_date=self.today,
        )
        self.assertEqual(progress["done"], 1)
        self.assertEqual(progress["total"], 2)
        self.assertEqual({event["kind"] for event in progress["events"]}, {"fixed", "one_off"})

        reloaded = CheckInManager(str(self.root))
        record = reloaded.decorate_record(self.today)
        self.assertEqual(record["daily_tasks"][0]["goal_id"], "quest-art")

    def test_goal_link_can_be_cleared_without_deleting_task(self):
        task = self.manager.add_task("Read a chapter", goal_id="quest-study")
        self.assertTrue(self.manager.update_task(task["id"], {"goal_id": None}))
        stored = next(item for item in self.manager.data["tasks"] if item["id"] == task["id"])
        self.assertIsNone(stored["goal_id"])

    def test_page_entry_counter_updates_and_persists(self):
        first = self.manager.record_app_entry()
        second = self.manager.record_app_entry()
        self.assertEqual(first["entry_count"], 1)
        self.assertEqual(second["entry_count"], 2)
        reloaded = CheckInManager(str(self.root))
        self.assertEqual(reloaded.get_daily_activity()["entry_count"], 2)

    def test_anki_preset_is_opt_in_read_only_and_idempotent(self):
        self.assertFalse(any(task.get("preset") == "anki" for task in self.manager.data["tasks"]))

        task = self.manager.add_task(None, preset="anki")
        duplicate = self.manager.add_task(None, preset="anki")
        self.assertEqual(task["id"], duplicate["id"])
        self.assertEqual(task["completion_mode"], "automatic")

        self.manager.sync_preset_completion(
            "anki",
            False,
            evidence={"today_reviews": 3, "completed_decks": []},
        )
        record = self.manager.save_day(
            self.today,
            completions={task["id"]: True},
        )
        self.assertFalse(record["completions"][task["id"]])

        self.manager.sync_preset_completion(
            "anki",
            True,
            evidence={"today_reviews": 12, "completed_decks": ["Japanese"]},
        )
        completed = self.manager.decorate_record(self.today)
        self.assertTrue(completed["completions"][task["id"]])
        self.assertEqual(
            completed["automatic_completions"][task["id"]]["completed_decks"],
            ["Japanese"],
        )

        reloaded = CheckInManager(str(self.root))
        self.assertTrue(reloaded.decorate_record(self.today)["completions"][task["id"]])


if __name__ == "__main__":
    unittest.main()
