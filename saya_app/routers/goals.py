from fastapi import APIRouter

from core_services import goal_manager, sync_life_memory


router = APIRouter(prefix="/api/goals", tags=["goals"])


def _sync_life_memory_best_effort():
    try:
        sync_life_memory()
    except Exception as exc:
        print(f"Error syncing memory: {exc}")


@router.get("")
def get_goals():
    return goal_manager.goals


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

