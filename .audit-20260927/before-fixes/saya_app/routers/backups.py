from fastapi import APIRouter, HTTPException

from saya_app.config import CHAT_MEMORY_DIR, KNOWLEDGE_BASE_DIR
from saya_app.services.backup_manager import BackupManager


router = APIRouter(prefix="/api/backups", tags=["backups"])
backup_manager = BackupManager(KNOWLEDGE_BASE_DIR, CHAT_MEMORY_DIR)


@router.get("")
def list_backups():
    return {"backups": backup_manager.list_backups(), "scopes": list(backup_manager.SCOPES)}


@router.post("")
def create_backup(req: dict = None):
    try:
        return backup_manager.create_backup((req or {}).get("scopes"), reason="manual")
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to create backup: {exc}")


@router.post("/{backup_id}/preview")
def preview_restore(backup_id: str, req: dict = None):
    try:
        return backup_manager.preview_restore(backup_id, (req or {}).get("scopes"))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to preview restore: {exc}")


@router.post("/{backup_id}/restore")
def restore_backup(backup_id: str, req: dict):
    try:
        scopes = req.get("scopes")
        result = backup_manager.restore(
            backup_id,
            scopes,
            req.get("confirmation_token"),
        )
        # Reload the data stores that can safely be refreshed in-process. This
        # prevents a later save from writing the pre-restore in-memory state
        # back over the restored files. A restart is still recommended for
        # persona/profile presentation settings.
        from core_services import (
            assistant_role_manager,
            checkin_manager,
            goal_manager,
            kb_system,
            project_manager,
        )
        selected = set(scopes or [])
        reloaded = []
        reload_errors = []
        try:
            if "memories" in selected:
                kb_system.load_memory_deletions()
                kb_system.load_memories()
                kb_system.load_memory_candidates()
                kb_system.settings = kb_system.load_settings()
                kb_system.memory_maintenance = kb_system.load_memory_maintenance_state()
                kb_system.normalize_all_memories()
                kb_system.memory_graph.load()
                kb_system.ensure_memory_graph(force=False)
                reloaded.append("memories")
            if "checkins" in selected:
                checkin_manager.data = checkin_manager.load_data()
                goal_manager.goals = goal_manager.load_goals()
                reloaded.append("checkins")
            if "projects" in selected:
                project_manager.projects = project_manager.load_projects()
                reloaded.append("projects")
            if "preferences" in selected:
                assistant_role_manager.state = assistant_role_manager.load_state()
                reloaded.append("preferences")
        except Exception as exc:
            # The disk restore has already succeeded and has its safety backup.
            # Report reload trouble honestly instead of mislabeling restoration
            # as failed; restarting Saya completes the refresh.
            reload_errors.append(str(exc))
        result["runtime_reloaded"] = reloaded
        if reload_errors:
            result["runtime_reload_warning"] = "; ".join(reload_errors)[:500]
        return result
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Unable to restore backup: {exc}")
