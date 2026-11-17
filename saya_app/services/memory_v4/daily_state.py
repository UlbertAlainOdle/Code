"""D0-D3 DayState ring with separate page-load/backend-start accounting."""

from __future__ import annotations

from datetime import date as date_type, datetime, timedelta
from typing import Any, Iterable, Mapping

from .models import DayState, ValidationError
from .store import MemoryV4Store


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _valid_date(value: Any) -> str:
    text = str(value or _today())[:10]
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationError("date must be YYYY-MM-DD") from exc
    return text


class DayStateManager:
    """Keep operational daily state separate from long-lived memory facts."""

    RING_WEIGHTS = (1.0, 0.70, 0.35, 0.15)

    def __init__(self, store: MemoryV4Store, *, timezone: str = "Asia/Shanghai"):
        self.store = store
        self.timezone = str(timezone or "UTC")

    def ensure(self, day: str | None = None) -> DayState:
        day = _valid_date(day)
        state = self.store.get_day_state(day)
        if state is not None:
            return state
        now = datetime.now().isoformat(timespec="seconds")
        state = DayState(
            date=day, timezone=self.timezone, first_contact_at=None, last_contact_at=now,
            purge_after=(datetime.strptime(day, "%Y-%m-%d") + timedelta(days=4)).strftime("%Y-%m-%d"),
        )
        self.store.upsert_day_state(state)
        return state

    def record_page_load(self, day: str | None = None) -> DayState:
        state = self.ensure(day)
        state.page_load_count += 1
        state.last_contact_at = datetime.now().isoformat(timespec="seconds")
        self.store.upsert_day_state(state)
        return state

    def record_backend_start(self, day: str | None = None) -> DayState:
        state = self.ensure(day)
        state.backend_start_count += 1
        state.last_contact_at = datetime.now().isoformat(timespec="seconds")
        self.store.upsert_day_state(state)
        return state

    def record_conversation(self, day: str | None = None) -> DayState:
        state = self.ensure(day)
        state.conversation_count += 1
        state.last_contact_at = datetime.now().isoformat(timespec="seconds")
        self.store.upsert_day_state(state)
        return state

    def first_contact_today(self, day: str | None = None) -> dict[str, Any]:
        state = self.ensure(day)
        first = state.first_contact_at is None
        if first:
            state.first_contact_at = datetime.now().isoformat(timespec="seconds")
            state.last_contact_at = state.first_contact_at
            self.store.upsert_day_state(state)
        return {"date": state.date, "first_contact_today": first, "state": state.to_dict()}

    def set_weather_snapshot(self, snapshot: Mapping[str, Any] | None, day: str | None = None) -> DayState:
        state = self.ensure(day)
        state.weather_snapshot = dict(snapshot) if isinstance(snapshot, Mapping) else None
        self.store.upsert_day_state(state)
        return state

    def ring(self, day: str | None = None) -> list[dict[str, Any]]:
        current = datetime.strptime(_valid_date(day), "%Y-%m-%d").date()
        result = []
        for offset, weight in enumerate(self.RING_WEIGHTS):
            target = (current - timedelta(days=offset)).isoformat()
            state = self.store.get_day_state(target)
            if state is not None:
                result.append({"date": target, "offset": offset, "weight": weight, "state": state.to_dict()})
        return result

    def rollover(self, day: str | None = None, tasks: Iterable[Mapping[str, Any]] | None = None) -> dict[str, Any]:
        day = _valid_date(day)
        state = self.ensure(day)
        task_list = [dict(task) for task in (tasks or []) if isinstance(task, Mapping)]
        incomplete = [
            task for task in task_list
            if str(task.get("status") or "pending").lower() not in {"done", "completed", "cancelled", "canceled"}
        ]
        state.task_snapshot = task_list
        state.carryover_task_ids = [
            str(task.get("id")) for task in incomplete if str(task.get("id") or "").strip()
        ]
        state.frozen_at = datetime.now().isoformat(timespec="seconds")
        self.store.upsert_day_state(state)
        cutoff = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=3)).strftime("%Y-%m-%d")
        purged = self.store.purge_day_states_before(cutoff)
        return {
            "status": "success", "date": day, "frozen": state.to_dict(),
            "carryover_task_ids": state.carryover_task_ids, "purged_count": purged,
            "retained_days": [entry["date"] for entry in self.ring(day)],
        }

