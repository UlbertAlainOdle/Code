"""Serialize a multi-persona turn in one session, with one user message."""

import asyncio
import json

from fastapi import HTTPException
from fastapi.responses import StreamingResponse

from .chat_agents import agent_store, history_for_agent, entries_for_agent
from .chat_runtime import chat_runtime_manager


def event(name, payload):
    return f"event: {name}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


async def multi_agent_response(req, memory_manager, single_response, normalize_attachments):
    try:
        agents = agent_store.resolve(req.agent_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    context = req.context if isinstance(req.context, dict) else {}
    runtime = chat_runtime_manager.create(
        memory_manager, requested_session_id=req.session_id or context.get("session_id", ""),
        temporary=req.temporary_chat,
        request_id=req.request_id,
    )
    lock = asyncio.Lock() if req.temporary_chat else await chat_runtime_manager.session_lock(runtime.session_id)

    async def generate():
        async with lock:
            items = [] if req.temporary_chat else memory_manager.load_session_full(runtime.session_id, make_current=False)
            count = len(items)
            if not req.temporary_chat:
                metadata = {"agent_ids": [a["id"] for a in agents], "chat_runtime": runtime.public_metadata()}
                attachments = normalize_attachments(req.attachments, include_data=False)
                if attachments:
                    metadata["attachments"] = attachments
                if context.get("project_id"):
                    metadata["project_id"] = context["project_id"]
                memory_manager.save_message("user", req.display_input or req.user_input,
                                            metadata=metadata, session_id=runtime.session_id)
            count += 1
            yield event("chat_runtime", {**runtime.public_metadata(), "history_count": count})
            for index, agent in enumerate(agents):
                public = {"id": agent["id"], "name": agent["name"], "index": index, "total": len(agents), "history_index": count}
                yield event("persona_start", public)
                child = None
                try:
                    child_req = req.model_copy(update={"agent_ids": [], "session_id": runtime.session_id})
                    if agent["id"] != "saya":
                        # Custom personas use their own prompt and conversation history.
                        # Saya's memories, planner, avatar and mood belong to Saya.
                        child_req = child_req.model_copy(update={
                            "context": {"role_mode": "assistant"}, "agent_mode": False,
                            "recall_mode": "off", "mode": "normal", "style_settings": {},
                            "use_strong_logic_model": False,
                        })
                    child = await single_response(child_req, {
                        "agent": agent, "history": history_for_agent(items, agent["id"]),
                        "history_times": entries_for_agent(items, agent["id"]),
                    })
                    async for chunk in child.body_iterator:
                        yield chunk
                except Exception:
                    error = f"{agent['name']} 生成失败，请重试。"
                    if not req.temporary_chat:
                        memory_manager.save_message("ai", error, metadata={
                            "agent_id": agent["id"], "agent_name": agent["name"], "stream_interrupted": True,
                        }, session_id=runtime.session_id)
                    yield event("stream_error", {"message": error})
                finally:
                    if child is not None:
                        await child.body_iterator.aclose()
                if not req.temporary_chat:
                    count = len(memory_manager.load_session_full(runtime.session_id, make_current=False))
                else:
                    count += 1
                yield event("persona_end", {**public, "history_count": count})
            yield event("personas_complete", {"history_count": count, "replies": len(agents)})

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
