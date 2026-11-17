from fastapi import APIRouter

from core_services import checkin_manager, goal_manager, sync_life_memory


router = APIRouter(prefix="/api/goals", tags=["goals"])


def _sync_life_memory_best_effort():
    try:
        sync_life_memory()
    except Exception as exc:
        print(f"Error syncing memory: {exc}")


@router.get("")
def get_goals():
    enriched = []
    for goal in goal_manager.goals:
        item = dict(goal)
        end_date = None
        if item.get("status") == "completed":
            end_date = item.get("achieved_at") or item.get("completed_at")
        elif item.get("status") == "failed":
            end_date = item.get("failed_at") or item.get("completed_at")
        item["daily_progress"] = checkin_manager.goal_progress(
            item.get("id"),
            start_date=item.get("created_at"),
            end_date=end_date,
        )
        enriched.append(item)
    return enriched


@router.post("/add")
def add_goal(req: dict):
    result = goal_manager.add_goal(req.get("title"), req.get("description", ""))
    _sync_life_memory_best_effort()
    return result


@router.post("/update")
def update_goal(req: dict):
    result = goal_manager.update_goal(req.get("id"), req.get("updates"))
    _sync_life_memory_best_effort()
    return result


@router.post("/delete")
def delete_goal(req: dict):
    goal_manager.delete_goal(req.get("id"))
    _sync_life_memory_best_effort()
    return {"status": "success"}
