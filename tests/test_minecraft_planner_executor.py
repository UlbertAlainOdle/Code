"""Focused regressions for the Minecraft single-step plan executor."""

import time

from saya_app.services import conversation
from saya_app.services import minecraft_mineflayer
from saya_app.services import minecraft_planner


class _CompletedBeforeInventoryService:
    """Report completion immediately, but expose the item only on retry."""

    def __init__(self):
        self.calls = 0
        self.spoken = []
        self.state = {
            "activity": {"id": "before", "state": "completed", "message": "idle"},
            "runtime": {"inventory": []},
        }

    def status(self):
        return self.state

    def send_command(self, text, origin="doctor", quiet=False, task_id=None):
        assert text == "craft wooden_sword 1"
        assert origin == "plan"
        assert quiet is True
        assert task_id, "plan steps must carry a task envelope id"
        self.calls += 1
        if self.calls == 2:
            self.state["runtime"]["inventory"] = [
                {"name": "wooden_sword", "count": 1}
            ]
        self.state["activity"] = {
            "id": f"attempt-{self.calls}",
            "state": "completed",
            "message": "worker reported completed",
        }
        return True

    def _speak(self, text):
        self.spoken.append(text)
        return True


def test_completed_activity_must_also_satisfy_inventory_postcondition(monkeypatch):
    service = _CompletedBeforeInventoryService()
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(conversation.conversation_service, "_record", lambda *a, **k: None)
    monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.001)
    monkeypatch.setattr(minecraft_planner, "VERIFY_GRACE_S", 0.01)
    monkeypatch.setattr(minecraft_planner, "PLAN_STEP_TIMEOUT_S", 1.0)

    executor = minecraft_planner.PlanExecutor()
    plan = minecraft_planner.new_plan(
        {"type": "craft", "target": "wooden_sword", "count": 1},
        [
            minecraft_planner.step(
                "craft",
                "craft wooden_sword 1",
                postcondition="inventory:wooden_sword>=1",
                description="制作木剑",
            )
        ],
    )

    executor._run_plan(plan)

    assert service.calls == 2
    assert plan["steps"][0]["attempts"] == 1
    assert plan["current_step"] == 1
    assert plan["status"] == "completed"


class _StaleSnapshotService:
    """The completed-activity state message arrives BEFORE the inventory
    snapshot catches up (the worker's ~1s cadence) - the item shows up a
    moment later without any retry command being sent."""

    def __init__(self):
        self.calls = 0
        self.spoken = []
        self._checks = 0
        self.state = {
            "activity": {"id": "before", "state": "completed", "message": "idle"},
            "runtime": {"inventory": []},
        }

    def status(self):
        return self.state

    def _refresh(self):
        self._checks += 1
        if self._checks >= 3:  # snapshot lands after a couple of polls
            self.state["runtime"]["inventory"] = [
                {"name": "cooked_porkchop", "count": 1}
            ]

    def send_command(self, text, origin="doctor", quiet=False, task_id=None):
        self.calls += 1
        self.state["activity"] = {
            "id": task_id or f"task-{self.calls}",
            "state": "completed",
            "message": "已烧炼并取出 1/1 个 cooked_porkchop",
        }
        return True

    def _speak(self, text):
        self.spoken.append(text)
        return True


def test_stale_snapshot_does_not_trigger_a_retry(monkeypatch):
    service = _StaleSnapshotService()
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(conversation.conversation_service, "_record", lambda *a, **k: None)
    monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.001)
    monkeypatch.setattr(minecraft_planner, "VERIFY_GRACE_S", 2.0)
    monkeypatch.setattr(minecraft_planner, "PLAN_STEP_TIMEOUT_S", 1.0)

    # Patch _verify to advance the fake snapshot clock each poll.
    original_verify = minecraft_planner.PlanExecutor._verify

    def verify_with_snapshot_advance(self, step_entry, svc):
        service._refresh()
        return original_verify(self, step_entry, svc)

    monkeypatch.setattr(
        minecraft_planner.PlanExecutor, "_verify", verify_with_snapshot_advance
    )

    executor = minecraft_planner.PlanExecutor()
    plan = minecraft_planner.new_plan(
        {"type": "obtain_and_deliver", "target": "cooked_porkchop", "count": 1},
        [
            minecraft_planner.step(
                "smelt", "smelt porkchop cooked_porkchop 1 coal 1",
                postcondition="inventory:cooked_porkchop>=1",
                description="烧炼熟猪排",
            ),
            minecraft_planner.step(
                "give", "give cooked_porkchop 1 to Doctor",
                postcondition="delivered:cooked_porkchop",
                description="交付熟猪排",
            ),
        ],
    )
    plan["status"] = "running"
    executor._run_plan(plan)

    # The stale first verify must NOT re-send the smelt command.
    assert service.calls == 2, f"应恰好两条指令（烧炼+交付），实际 {service.calls}"
    assert plan["status"] == "completed"


def test_plan_state_envelope_sent_at_start_and_finish(monkeypatch):
    """The worker must know a plan is in flight so follow/hunt stay deferred
    between steps, and must be told when it ends (any terminal state)."""
    service = _CompletedBeforeInventoryService()
    plan_state_events = []

    def fake_send_plan_state(active):
        plan_state_events.append(bool(active))
        return True

    service.send_plan_state = fake_send_plan_state
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(conversation.conversation_service, "_record", lambda *a, **k: None)
    monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.001)
    monkeypatch.setattr(minecraft_planner, "PLAN_STEP_TIMEOUT_S", 1.0)

    executor = minecraft_planner.PlanExecutor()
    plan = minecraft_planner.new_plan(
        {"type": "craft", "target": "wooden_sword", "count": 1},
        [
            minecraft_planner.step(
                "craft",
                "craft wooden_sword 1",
                postcondition="inventory:wooden_sword>=1",
                description="制作木剑",
            )
        ],
    )

    executor._run_plan(plan)

    assert plan_state_events[0] is True, plan_state_events
    assert plan_state_events[-1] is False, plan_state_events
    assert all(event is True for event in plan_state_events[:-1])


def test_suspended_obtain_goal_completes_when_already_satisfied(monkeypatch):
    """A suspended pure-obtain plan whose goal became satisfied must
    COMPLETE on resume without re-running any worker command, and leave the
    suspended list empty - never linger as a zombie entry."""
    service = _StaleSnapshotService()
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(conversation.conversation_service, "_record", lambda *a, **k: None)

    executor = minecraft_planner.PlanExecutor()
    plan = minecraft_planner.new_plan(
        {"type": "obtain", "target": "cooked_porkchop", "count": 1},
        [
            minecraft_planner.step(
                "smelt", "smelt porkchop cooked_porkchop 1 coal 1",
                postcondition="inventory:cooked_porkchop>=1",
                description="烧炼熟猪排",
            )
        ],
    )
    # Park it as suspended, then satisfy the goal in live state.
    executor._suspend_plan(plan, "测试挂起")
    service.state["runtime"]["inventory"] = [{"name": "cooked_porkchop", "count": 2}]

    resumed = executor.resume_suspended(plan["plan_id"])

    assert resumed is True
    assert plan["status"] == "completed"
    assert service.calls == 0, "目标已满足时不应再发任何指令"
    view = executor.state()
    assert view.get("suspended") == []
    assert view["history"][-1]["status"] == "completed"


def test_suspended_deliver_goal_resumes_and_delivers_then_clears(monkeypatch):
    """Deliver goals with stock resume into a real give step; after it
    completes the suspended list must be empty (the user-reported zombie)."""
    service = _StaleSnapshotService()
    monkeypatch.setattr(minecraft_mineflayer, "minecraft_bot_service", service)
    monkeypatch.setattr(conversation.conversation_service, "_record", lambda *a, **k: None)
    monkeypatch.setattr(minecraft_planner, "STEP_POLL_S", 0.01)

    executor = minecraft_planner.PlanExecutor()
    plan = minecraft_planner.new_plan(
        {"type": "obtain_and_deliver", "target": "cooked_porkchop", "count": 1},
        [
            minecraft_planner.step(
                "give", "give cooked_porkchop 1 to Doctor",
                postcondition="delivered:cooked_porkchop",
                description="交付熟猪排",
            )
        ],
    )
    executor._suspend_plan(plan, "测试挂起")
    service.state["runtime"]["inventory"] = [{"name": "cooked_porkchop", "count": 2}]

    assert executor.resume_suspended(plan["plan_id"]) is True

    deadline = time.time() + 10
    while time.time() < deadline:
        view = executor.state()
        if not view.get("active") and view.get("history"):
            break
        time.sleep(0.02)
    view = executor.state()
    assert service.calls >= 1, "恢复后应实际执行交付"
    assert plan["status"] == "completed"
    assert view.get("suspended") == []
    assert view["history"][-1]["status"] == "completed"
