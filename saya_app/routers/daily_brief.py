from datetime import datetime

from fastapi import APIRouter

from core_services import checkin_manager, goal_manager, kb_system, memory_manager, project_manager
from saya_app.services.video_recommendations import video_recommendation_service


router = APIRouter(prefix="/api/daily_brief", tags=["daily_brief"])


def _pct(value):
    return f"{round(float(value or 0) * 100)}%"


def _clean_text(value, limit=120):
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _time_greeting(now):
    hour = now.hour
    if hour < 6:
        return "It is late, Doctor."
    if hour < 12:
        return "Good morning, Doctor."
    if hour < 18:
        return "Good afternoon, Doctor."
    return "Good evening, Doctor."


def _goal_date(goal):
    if goal.get("status") == "completed":
        return str(goal.get("achieved_at") or goal.get("completed_at") or "")
    if goal.get("status") == "failed":
        return str(goal.get("failed_at") or goal.get("completed_at") or "")
    return str(goal.get("created_at") or "")


def _brief_video_state(refresh_videos):
    try:
        if refresh_videos:
            return video_recommendation_service.get_recommendations(refresh=True), None
        return video_recommendation_service.get_state(), None
    except Exception as exc:
        try:
            return video_recommendation_service.get_state(), str(exc)
        except Exception as fallback_exc:
            return {}, str(fallback_exc)


def _task_items(checkin_state):
    record = checkin_state.get("selected_record") or {}
    completions = record.get("completions") if isinstance(record.get("completions"), dict) else {}
    fixed = []
    for task in checkin_state.get("selected_tasks") or []:
        fixed.append({
            "id": str(task.get("id") or ""),
            "title": _clean_text(task.get("title"), 90),
            "done": bool(completions.get(task.get("id"))),
            "type": "fixed",
        })
    daily = []
    for task in record.get("daily_tasks") or []:
        daily.append({
            "id": str(task.get("id") or ""),
            "title": _clean_text(task.get("title"), 90),
            "done": bool(task.get("done")),
            "type": "today",
        })
    return fixed, daily


def _build_opening_message(now, record, active_goals, projects, candidates, videos):
    greeting = _time_greeting(now)
    done = int(record.get("done") or 0)
    total = int(record.get("total") or 0)
    pending = max(0, total - done)
    parts = [greeting]

    if total:
        if pending:
            parts.append(f"Today's Daily is {done}/{total}, with {pending} item left.")
        else:
            parts.append(f"Today's Daily is already {done}/{total}. Nice and clean.")
    else:
        parts.append("There are no Daily tasks set for today yet.")

    if active_goals:
        parts.append(f"{len(active_goals)} Life Quest{'s' if len(active_goals) != 1 else ''} are still active.")
    if projects:
        parts.append(f"The latest project on deck is {projects[0].get('name') or 'Untitled project'}.")
    if candidates:
        parts.append(f"{len(candidates)} memory note{'s' if len(candidates) != 1 else ''} need your confirmation.")
    if videos:
        parts.append(f"{len(videos)} unwatched video update{'s' if len(videos) != 1 else ''} are ready.")

    parts.append("I can help you choose one small next step instead of letting the day stay blurry.")
    return " ".join(parts)


def _build_followups(record, active_goals, projects, candidates, videos):
    followups = []
    total = int(record.get("total") or 0)
    done = int(record.get("done") or 0)
    if total and done < total:
        followups.append("Pick one remaining Daily task and make it the next small move.")
    elif not total:
        followups.append("Add one tiny Daily task so today has a visible anchor.")

    if active_goals:
        followups.append(f"Choose a next action for Life Quest: {active_goals[0].get('title') or 'Untitled quest'}.")
    if candidates:
        followups.append("Review pending memory notes before they get stale.")
    if videos:
        followups.append("Open one recommended video only if it helps today's direction.")
    if projects:
        followups.append(f"Continue the latest project thread: {projects[0].get('name') or 'Untitled project'}.")
    return followups[:4]


def _build_chat_context(brief):
    lines = [
        "Daily Brief Context",
        f"Generated at: {brief.get('generated_at')}",
        "",
        f"Saya opening: {brief.get('opening_message')}",
        "",
        "Cards:",
    ]
    for card in brief.get("cards") or []:
        lines.append(f"- {card.get('label')}: {card.get('value')} ({card.get('detail')})")

    lines.append("")
    lines.append("Today tasks:")
    today = brief.get("today") or {}
    for item in (today.get("fixed_tasks") or []) + (today.get("daily_tasks") or []):
        lines.append(f"- [{'done' if item.get('done') else 'todo'}] {item.get('title')}")
    if not (today.get("fixed_tasks") or today.get("daily_tasks")):
        lines.append("- No Daily tasks are configured for today.")

    lines.append("")
    lines.append("Active Life Quests:")
    for goal in brief.get("active_goals") or []:
        lines.append(f"- {goal.get('title')} ({goal.get('date') or 'no date'})")
    if not brief.get("active_goals"):
        lines.append("- No active Life Quests.")

    lines.append("")
    lines.append("Recent Projects:")
    for project in brief.get("projects") or []:
        lines.append(f"- {project.get('name')} ({project.get('session_count')} chats)")
    if not brief.get("projects"):
        lines.append("- No projects yet.")

    lines.append("")
    lines.append("Follow-up ideas:")
    for item in brief.get("followups") or []:
        lines.append(f"- {item}")

    lines.append("")
    lines.append(
        "When replying, speak naturally as Saya. Start with today's state, then suggest one gentle next step. "
        "Do not sound like a dashboard report."
    )
    return "\n".join(lines)


@router.get("")
def get_daily_brief(refresh_videos: bool = False):
    now = datetime.now()
    checkin_state = checkin_manager.get_state(
        year=now.year,
        month=now.month,
        selected_date=now.strftime("%Y-%m-%d"),
    )
    record = checkin_state.get("selected_record") or {}
    fixed_tasks, daily_tasks = _task_items(checkin_state)

    goals = [goal for goal in goal_manager.goals if isinstance(goal, dict)]
    active_goals = sorted(
        [goal for goal in goals if goal.get("status") == "active"],
        key=_goal_date,
        reverse=True,
    )
    completed_goals = [goal for goal in goals if goal.get("status") == "completed"]
    failed_goals = [goal for goal in goals if goal.get("status") == "failed"]

    projects = []
    for project in project_manager.list_projects()[:5]:
        projects.append({
            "id": project.get("id"),
            "name": _clean_text(project.get("name"), 80),
            "updated_at": project.get("updated_at") or project.get("created_at"),
            "session_count": len(project.get("session_ids") or []),
        })

    candidates = kb_system.get_memory_candidates(status="pending", limit=5)
    candidate_count = len(kb_system.get_memory_candidates(status="pending", limit=200))
    all_memories = [memory for memory in kb_system.get_memories(include_archived=True) if isinstance(memory, dict)]
    active_memories = [memory for memory in all_memories if not memory.get("is_archived")]
    locked_memories = [memory for memory in active_memories if memory.get("is_locked")]
    important_memories = [memory for memory in active_memories if memory.get("is_important")]
    video_state, video_error = _brief_video_state(refresh_videos)
    videos = (video_state.get("recommendations") or [])[:8]
    creators = video_state.get("creators") or []
    active_creators = [creator for creator in creators if creator.get("enabled", True)]
    stats = memory_manager.get_chat_activity_stats(year=now.year, month=now.month)
    month_summary = checkin_state.get("month_summary") or {}
    year_summary = checkin_state.get("year_summary") or {}
    project_count = len(project_manager.list_projects())

    cards = [
        {
            "id": "daily",
            "label": "Daily",
            "value": f"{int(record.get('done') or 0)}/{int(record.get('total') or 0)}",
            "detail": "Today tasks completed" if int(record.get("total") or 0) else "No tasks set today",
            "tone": record.get("status") or "pending",
        },
        {
            "id": "month",
            "label": "Month",
            "value": _pct(month_summary.get("completion_rate")),
            "detail": f"Streak {month_summary.get('current_streak') or 0}, counted {month_summary.get('days_counted') or 0} days",
            "tone": "completed" if float(month_summary.get("completion_rate") or 0) >= 0.8 else "partial",
        },
        {
            "id": "life_quests",
            "label": "Life Quests",
            "value": str(len(active_goals)),
            "detail": f"{len(completed_goals)} completed, {len(failed_goals)} failed",
            "tone": "active" if active_goals else "no_tasks",
        },
        {
            "id": "memory",
            "label": "Memory Review",
            "value": str(candidate_count),
            "detail": "Pending confirmations",
            "tone": "partial" if candidate_count else "completed",
        },
        {
            "id": "videos",
            "label": "Video Updates",
            "value": str(len(videos)),
            "detail": f"{len(active_creators)} enabled creators",
            "tone": "active" if videos else "no_tasks",
        },
        {
            "id": "chats",
            "label": "Chat Days",
            "value": str(stats.get("active_days_month") or 0),
            "detail": f"{stats.get('total_sessions') or 0} total sessions",
            "tone": "active" if stats.get("active_days_month") else "no_tasks",
        },
    ]
    available_cards = cards + [
        {
            "id": "total_memory",
            "label": "Total Memory",
            "value": str(len(active_memories)),
            "detail": f"{len(locked_memories)} locked, {len(important_memories)} starred",
            "tone": "active" if active_memories else "no_tasks",
            "hidden": True,
        },
        {
            "id": "important_memory",
            "label": "Starred Memory",
            "value": str(len(important_memories)),
            "detail": f"{len(locked_memories)} locked notes",
            "tone": "completed" if important_memories else "no_tasks",
            "hidden": True,
        },
        {
            "id": "projects",
            "label": "Projects",
            "value": str(project_count),
            "detail": f"Latest: {_clean_text(projects[0].get('name'), 60) if projects else 'No project yet'}",
            "tone": "active" if projects else "no_tasks",
            "hidden": True,
        },
        {
            "id": "year",
            "label": "Year",
            "value": _pct(year_summary.get("completion_rate")),
            "detail": f"Counted {year_summary.get('days_counted') or 0} days",
            "tone": "completed" if float(year_summary.get("completion_rate") or 0) >= 0.8 else "partial",
            "hidden": True,
        },
        {
            "id": "today_tasks",
            "label": "Today Tasks",
            "value": str(len(fixed_tasks) + len(daily_tasks)),
            "detail": f"{len(fixed_tasks)} fixed, {len(daily_tasks)} added today",
            "tone": "active" if fixed_tasks or daily_tasks else "no_tasks",
            "hidden": True,
        },
        {
            "id": "video_sources",
            "label": "Video Sources",
            "value": str(len(active_creators)),
            "detail": f"{len(active_creators)} enabled, {len(creators)} configured",
            "tone": "active" if active_creators else "no_tasks",
            "hidden": True,
        },
    ]

    active_goal_items = [
        {
            "id": goal.get("id"),
            "title": _clean_text(goal.get("title"), 100),
            "description": _clean_text(goal.get("description"), 160),
            "date": _goal_date(goal),
        }
        for goal in active_goals[:5]
    ]
    memory_items = [
        {
            "id": item.get("id"),
            "title": _clean_text(item.get("title") or item.get("content"), 100),
            "content": _clean_text(item.get("content"), 180),
            "category": item.get("category"),
        }
        for item in candidates
    ]

    brief = {
        "status": "success",
        "generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "today": {
            "date": checkin_state.get("today"),
            "record": record,
            "fixed_tasks": fixed_tasks,
            "daily_tasks": daily_tasks,
            "month_summary": month_summary,
            "year_summary": year_summary,
        },
        "cards": cards,
        "available_cards": available_cards,
        "active_goals": active_goal_items,
        "projects": projects,
        "memory_candidates": memory_items,
        "memory_candidate_count": candidate_count,
        "video_state": video_state,
        "videos": videos,
        "video_error": video_error,
        "chat_activity": stats,
        "settings": {
            "auto_open": bool((video_state.get("settings") or {}).get("auto_open")),
        },
    }
    brief["opening_message"] = _build_opening_message(
        now,
        record,
        active_goal_items,
        projects,
        memory_items,
        videos,
    )
    brief["followups"] = _build_followups(record, active_goal_items, projects, memory_items, videos)
    brief["talk_prompt"] = "Saya, give me today's brief and help me choose the next small step."
    brief["chat_context"] = _build_chat_context(brief)
    return brief
