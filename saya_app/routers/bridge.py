"""REST + WebSocket endpoints for the external game bridge (Minecraft-bot style)."""

import asyncio
import re
import subprocess
import sys
import time
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from typing import Optional

from pydantic import BaseModel

from ..services.game_bridge import game_bridge, validate_source

router = APIRouter(prefix="/api/bridge", tags=["bridge"])

# Desktop companion console launcher (spec section 6). On Windows, prefer the
# GUI interpreter beside the backend interpreter so Qt gets a visible desktop
# window without creating an extra console. desktop/vendor remains a fallback
# when PySide6 is not installed into the environment.
CONSOLE_SCRIPT = (
    Path(__file__).resolve().parents[2] / "desktop" / "saya_companion_console.py"
)
_console_process = None


class StatusRequest(BaseModel):
    text: str
    lane: Optional[str] = "general"


class CommandRequest(BaseModel):
    text: str


def _source_or_400(source):
    try:
        return validate_source(source)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/{source}/status")
def push_status(source: str, req: StatusRequest):
    """External bot heartbeat + context update (airi context:update)."""
    source = _source_or_400(source)
    try:
        return game_bridge.push_status(source, req.text, lane=req.lane)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/{source}/commands")
def pull_commands(source: str, take: int = 5):
    """External bot polls queued high-level commands (spark:command)."""
    source = _source_or_400(source)
    return {"commands": game_bridge.pop_commands(source, take=take)}


@router.post("/{source}/send")
def send_command(source: str, req: CommandRequest):
    """Doctor manually queues a command from the UI."""
    source = _source_or_400(source)
    try:
        command = game_bridge.queue_command(source, req.text, origin="doctor")
        if command is None:
            raise HTTPException(status_code=429, detail="Identical command already queued")
        return command
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/state")
def bridge_state():
    return game_bridge.state()


# --- Minecraft bot control (Python bridge + Mineflayer worker) ------------
class BotConfigRequest(BaseModel):
    host: Optional[str] = None
    port: Optional[int] = None
    username: Optional[str] = None
    owner_username: Optional[str] = None
    version: Optional[str] = None
    account: Optional[str] = None
    status_interval: Optional[int] = None
    auto_discard_unplanned: Optional[bool] = None
    auto_attack_hostiles: Optional[bool] = None
    auto_hunt_animals: Optional[bool] = None
    auto_counterattack: Optional[bool] = None
    counterattack_owner: Optional[bool] = None
    auto_mine_ores: Optional[bool] = None
    auto_mine_range: Optional[int] = None
    auto_mine_ore_modes: Optional[dict] = None
    slu_backend: Optional[str] = None
    slu_local_model: Optional[str] = None
    slu_model_url: Optional[str] = None
    slu_model_name: Optional[str] = None
    combat_detection_range: Optional[int] = None
    follow_search_range: Optional[int] = None


@router.get("/minecraft/bot")
def minecraft_bot_status():
    from ..services.minecraft_mineflayer import minecraft_bot_service

    return minecraft_bot_service.status()


@router.put("/minecraft/bot")
def minecraft_bot_save_config(req: BotConfigRequest):
    from ..services.minecraft_mineflayer import minecraft_bot_service

    updates = {k: v for k, v in req.model_dump().items() if v is not None}
    return {"config": minecraft_bot_service.save_config(updates)}


@router.post("/minecraft/bot/start")
def minecraft_bot_start():
    from ..services.minecraft_mineflayer import minecraft_bot_service

    try:
        minecraft_bot_service.start()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {"started": True}


@router.post("/minecraft/bot/stop")
def minecraft_bot_stop():
    from ..services.minecraft_mineflayer import minecraft_bot_service

    minecraft_bot_service.stop()
    return {"stopped": True}


@router.post("/minecraft/console")
def launch_desktop_console():
    """Launch the PySide6 desktop companion console from the Game Bridge UI.

    The child runs in its own process group, so it keeps its window independent
    of backend restarts, and one click never opens a second instance while the
    previous one is still alive.
    """
    global _console_process

    if not CONSOLE_SCRIPT.exists():
        raise HTTPException(status_code=500, detail="desktop console script is missing")
    if _console_process is not None and _console_process.poll() is None:
        return {"started": False, "already_running": True, "pid": _console_process.pid}
    executable = Path(sys.executable)
    creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    gui_mode = False
    if sys.platform == "win32":
        gui_executable = executable.with_name("pythonw.exe")
        if gui_executable.exists():
            executable = gui_executable
            creationflags = 0
            gui_mode = True
        else:
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        kwargs = {
            "cwd": str(CONSOLE_SCRIPT.parent),
            "creationflags": creationflags,
            "close_fds": not gui_mode,
        }
        if not gui_mode:
            kwargs.update(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        _console_process = subprocess.Popen(
            [str(executable), str(CONSOLE_SCRIPT)], **kwargs
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"could not launch console: {exc}")
    return {"started": True, "already_running": False, "pid": _console_process.pid}


class AutoReplyRequest(BaseModel):
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_model_name: Optional[str] = None
    response_language: Optional[str] = None


class SkinRequest(BaseModel):
    """Set the skin: either a raw value (player name / URL / texture id,
    whatever the configured mod command expects) or a preset name."""
    value: str = ""
    command: Optional[str] = None
    model: Optional[str] = None  # classic | slim


@router.post("/minecraft/bot/skin")
def minecraft_bot_skin(req: SkinRequest):
    """Configure + immediately apply the bot's skin.

    The command template lives in the bot config (``skin_command``, default
    ``/skin set {value}`` for SkinsRestorer-style plugins); the request only
    carries the value so the console cannot inject arbitrary server
    commands.  Empty value = revert to account default.
    """
    from ..services.minecraft_mineflayer import minecraft_bot_service

    updates = {"skin_value": str(req.value or "").strip()}
    if req.command is not None and "{value}" in str(req.command):
        updates["skin_command"] = str(req.command).strip()[:120]
    if req.model in ("classic", "slim"):
        updates["skin_model"] = req.model
    minecraft_bot_service.save_config(updates)
    applied = minecraft_bot_service.apply_skin()
    running = bool(minecraft_bot_service.status().get("running"))
    return {
        "value": updates["skin_value"],
        "applied": bool(applied) if running else False,
        "running": running,
        "message": (
            "皮肤命令已发送" if applied
            else "已保存，机器人下次进服时自动应用" if running
            else "已保存，等机器人进服后自动应用"
        ) if updates["skin_value"] else "已恢复默认皮肤设置（下次进服生效）",
    }


@router.post("/minecraft/bot/skin/file")
async def minecraft_bot_skin_file(file: UploadFile):
    """Upload a local PNG skin, serve it from /static/skins and set it.

    Two application paths, per the configured template:
    - URL templates ("/skin set URL {model} {value}"): the mod downloads
      from the backend URL (server must reach this backend - LAN is fine);
    - upload templates ("/skin set upload {model} {value}"): the PNG is
      ALSO mirrored into ``skin_server_dir`` (the Minecraft server's visible
      filesystem) and the value becomes that absolute path - no HTTP needed.
    """
    from ..services.minecraft_mineflayer import minecraft_bot_service

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="皮肤文件为空")
    if len(data) > 1_500_000:
        raise HTTPException(status_code=413, detail="皮肤文件太大（>1.5MB）")
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise HTTPException(status_code=400, detail="只支持 PNG 皮肤文件")

    skins_dir = Path(__file__).resolve().parents[2] / "static" / "skins"
    skins_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe_suffix = re.sub(r"[^a-zA-Z0-9_.-]", "_", Path(file.filename or "skin.png").stem)[:40]
    filename = f"skin-{stamp}-{safe_suffix}.png"
    target = skins_dir / filename
    target.write_bytes(data)

    template = str(minecraft_bot_service.config.get("skin_command") or "")
    server_dir = str(minecraft_bot_service.config.get("skin_server_dir") or "").strip()
    if "upload" in template and server_dir:
        # FabricTailor upload variant: the file must be readable by the
        # Minecraft server process - mirror it there and pass the path.
        mirror_dir = Path(server_dir)
        try:
            mirror_dir.mkdir(parents=True, exist_ok=True)
            mirror = mirror_dir / filename
            mirror.write_bytes(data)
            skin_value = str(mirror)
        except OSError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"无法写入 skin_server_dir（{server_dir}）：{exc}",
            )
    else:
        base = str(minecraft_bot_service.config.get("skin_url_base") or "").rstrip("/") or "http://127.0.0.1:8000"
        skin_value = f"{base}/static/skins/{filename}"
    minecraft_bot_service.save_config({"skin_value": skin_value})
    applied = minecraft_bot_service.apply_skin()
    running = bool(minecraft_bot_service.status().get("running"))
    return {
        "url": skin_value,
        "applied": bool(applied) if running else False,
        "running": running,
        "message": "皮肤文件已上传并应用" if applied else "已上传，机器人进服后自动应用",
    }


@router.post("/minecraft/bot/auto-reply")
def minecraft_bot_auto_reply(req: AutoReplyRequest):
    """Legacy web polling endpoint. Game chat is now answered directly by the
    unified ConversationService inside the backend; this stays for older
    frontends and always reports the internal router state."""
    from ..services.conversation import conversation_service
    from ..services.minecraft_mineflayer import minecraft_bot_service

    # Keep the legacy payload accepted; request-scoped browser fields no longer
    # replace the shared file. The explicit profile buttons own persistence.
    if req.api_key or req.api_url or req.api_model_name:
        conversation_service.save_credentials(
            from_request=True,
            api_key=req.api_key or "",
            api_url=req.api_url or "",
            api_model_name=req.api_model_name or "",
        )

    status = minecraft_bot_service.status()
    return {
        "result": None,
        "handled_internally": True,
        "conversation": status.get("conversation"),
        "credentials": conversation_service.credentials_status(),
    }


# --- Conversation endpoints (web/desktop and Minecraft share routing, not history) --
class ConversationChatRequest(BaseModel):
    text: str
    source: str = "desktop"          # web | desktop
    force_command: bool = False      # treat the input strictly as a command
    speaker: str = "Doctor"


@router.post("/minecraft/chat")
async def conversation_chat(req: ConversationChatRequest):
    """Send one message through the shared ConversationService.

    Web/desktop and Minecraft use the same persona, memory and command
    routing, while their transcript files remain separate. Command and goal
    execution happens inside the service; this endpoint only relays the
    outcome.
    """
    from ..services.conversation import conversation_service
    from ..services.minecraft_mineflayer import minecraft_bot_service

    if not str(req.text or "").strip():
        raise HTTPException(status_code=400, detail="text is empty")
    # Only an actual Minecraft request may use the per-join game session.
    # Web/desktop requests keep the web session even while the bot is online.
    is_game_request = str(req.source or "").strip().lower() == "minecraft"
    context = {
        "summary": minecraft_bot_service._conversation_context(),
        "game_state": minecraft_bot_service.game_state(),
        "mc_status": minecraft_bot_service.status(),
        "session_id": (
            minecraft_bot_service.current_game_session_id()
            if is_game_request
            else None
        ),
    }
    result = await asyncio.to_thread(
        conversation_service.handle_message,
        req.source,
        req.speaker,
        req.text,
        context,
        req.force_command,
    )
    minecraft_bot_service.record_conversation_result(req.speaker, result)
    return {"result": result}


# --- Command catalog / item catalog / plan state (2026-09 plan) -------------
@router.get("/minecraft/catalog")
def minecraft_command_catalog():
    """Unified capability catalog: UI panels, LLM whitelist, help text."""
    from ..services.minecraft_catalog import catalog_view

    return catalog_view()


@router.get("/minecraft/items")
def minecraft_item_catalog(category: Optional[str] = None, limit: int = 0):
    """Registry item list with Chinese aliases for the crafting search box."""
    from ..services.minecraft_items import item_catalog

    items = item_catalog().item_view()
    if category:
        category = str(category).strip().lower()
        items = [item for item in items if item["category"] == category]
    if limit and limit > 0:
        items = items[:min(limit, 4000)]
    return {"version": item_catalog().version, "items": items}


@router.get("/minecraft/plan")
def minecraft_plan_state():
    from ..services.minecraft_planner import plan_executor

    return plan_executor.state()


@router.post("/minecraft/plan/cancel")
def minecraft_plan_cancel():
    from ..services.minecraft_planner import plan_executor

    plan_executor.cancel(reason="doctor")
    return {"cancelled": True}


@router.post("/minecraft/task/cancel")
def minecraft_task_cancel():
    """Cancel the active plan/worker task without turning persistent follow off."""
    from ..services.minecraft_mineflayer import minecraft_bot_service
    from ..services.minecraft_planner import plan_executor

    plan_executor.cancel(reason="主人取消了当前任务")
    sent = minecraft_bot_service.send_command("cancel task", origin="doctor")
    return {"cancelled": True, "worker_notified": bool(sent)}


class PlanIdRequest(BaseModel):
    plan_id: Optional[str] = None


@router.post("/minecraft/plan/suspend")
def minecraft_plan_suspend():
    """Doctor suspends the currently running plan (stays resumable)."""
    from ..services.minecraft_planner import plan_executor

    suspended = plan_executor.suspend_active()
    if not suspended:
        raise HTTPException(status_code=409, detail="当前没有正在执行的计划")
    return {"suspended": True}


@router.post("/minecraft/plan/resume")
def minecraft_plan_resume(req: PlanIdRequest):
    """Retry a suspended plan now (oldest one when plan_id is omitted)."""
    from ..services.minecraft_planner import plan_executor

    resumed = plan_executor.resume_suspended(req.plan_id)
    if not resumed:
        raise HTTPException(status_code=404, detail="没有可恢复的挂起任务")
    return {"resumed": True}


@router.post("/minecraft/plan/abandon")
def minecraft_plan_abandon(req: PlanIdRequest):
    """Doctor gives up on one suspended plan, or all of them when omitted."""
    from ..services.minecraft_planner import plan_executor

    abandoned = plan_executor.abandon_suspended(req.plan_id)
    if not abandoned:
        raise HTTPException(status_code=404, detail="没有匹配的挂起任务")
    return {"abandoned": True}


@router.get("/minecraft/chat/history")
def conversation_history(limit: int = 80, session: Optional[str] = None):
    from ..services.conversation import SESSION_ID, conversation_service

    # Game conversations live in their own per-join session files; the desktop
    # console can load any of them explicitly. The web/desktop session is the
    # default and is never replaced by the currently connected game session.
    session_id = str(session or "").strip() or SESSION_ID
    return {"items": conversation_service.history(limit=limit, session_id=session_id)}


@router.get("/minecraft/chat/sessions")
def conversation_game_sessions():
    """Game conversation sessions, newest first (one file per world join)."""
    from ..services.conversation import conversation_service

    sessions = conversation_service.list_game_sessions()
    try:
        from ..services.minecraft_mineflayer import minecraft_bot_service

        current = minecraft_bot_service.current_game_session_id()
    except Exception:
        current = None
    for item in sessions:
        item["current"] = item["id"] == current
    return {"sessions": sessions, "current": current}


class ArchiveRequest(BaseModel):
    session: Optional[str] = None


@router.post("/minecraft/chat/archive")
def conversation_archive_game_session(req: ArchiveRequest):
    """Archive a game conversation into the main memory store (episodic).

    Adds a conversation_summary memory (source=session_archive) and moves the
    session file into the archived/ folder - storage separation, no deletion.
    """
    from ..services.conversation import conversation_service

    try:result = conversation_service.archive_game_session(req.session)
    except ValueError as error:raise HTTPException(400,str(error)) from None
    if not result.get("archived"):
        if result.get('operation'):return result
        raise HTTPException(status_code=404, detail=result.get("reason", "无法归档"))
    return result


@router.get('/minecraft/chat/archive-result')
def game_archive_result(session: str, operation_id: str):
    from ..services.conversation import conversation_service
    from ..services.game_archive_writes import path_for,scope_for
    from ..services.chat_memory_writes import journal,public_result
    try:
        path_for(conversation_service,session)
        if not re.fullmatch(r'[a-f0-9]{32}',operation_id):raise ValueError('操作编号无效')
    except ValueError as error:raise HTTPException(400,str(error)) from None
    result=journal(conversation_service._knowledge_base()).lookup(scope_for(session),session,operation_id,kind='game_archive')
    if result is None:raise HTTPException(404,'该游戏会话没有此归档操作')
    return public_result(result)


class DirectCommandRequest(BaseModel):
    text: str


def _normalize_direct_command_text(text: str) -> str:
    """Translate supported natural-language commands before the Worker pipe.

    The Desktop Console's raw command box historically bypassed
    ``ConversationService``.  That made ``brew healing 1`` work while the
    otherwise equivalent ``酿造治疗药水`` fell through to the Worker's chat
    fallback.  Reuse the deterministic parser here so direct, desktop-chat,
    and in-game entry points share the same Chinese aliases.  Unknown input
    is intentionally preserved for advanced Worker commands and ``/raw``.
    """
    from ..services.conversation import parse_intent

    raw = str(text or "").strip()
    return parse_intent(raw) or raw


@router.post("/minecraft/command")
def minecraft_direct_command(req: DirectCommandRequest):
    """Doctor-side direct command injection (trusted origin; allows /raw)."""
    from ..services.minecraft_mineflayer import minecraft_bot_service

    if not minecraft_bot_service.status().get("running"):
        raise HTTPException(status_code=409, detail="Minecraft bot is not running")
    command = _normalize_direct_command_text(req.text)
    if not command:
        raise HTTPException(status_code=400, detail="command is empty")
    sent = minecraft_bot_service.send_command(command, origin="doctor")
    if not sent:
        raise HTTPException(status_code=503, detail="Worker command pipe unavailable")
    return {
        "sent": True,
        "text": req.text,
        "command": command,
        "normalized": command != str(req.text or "").strip(),
    }


@router.get("/minecraft/conversation/status")
def conversation_status():
    from ..services.conversation import SESSION_ID, conversation_service

    return {
        "credentials": conversation_service.credentials_status(),
        "session": SESSION_ID,
    }


@router.websocket("/minecraft/ws")
async def minecraft_status_ws(websocket: WebSocket):
    """Server-push Minecraft status stream for the desktop console."""
    from ..services.minecraft_mineflayer import minecraft_bot_service

    await websocket.accept()
    loop = asyncio.get_event_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=8)

    def on_state(snapshot):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, snapshot)
        except Exception:
            pass

    unsubscribe = minecraft_bot_service.subscribe(on_state)
    try:
        await websocket.send_json({"type": "state", "state": minecraft_bot_service.status()})
        while True:
            snapshot = await queue.get()
            await websocket.send_json({"type": "state", "state": snapshot})
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        unsubscribe()
