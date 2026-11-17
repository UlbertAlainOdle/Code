from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from saya_app.services.chat_agents import agent_store

router = APIRouter(prefix="/api/chat-agents", tags=["chat-agents"])


class AgentSettings(BaseModel):
    name: str = Field(min_length=1, max_length=32)
    prompt: str = Field(min_length=1, max_length=20000)


def mutate(operation):
    try:
        return operation()
    except KeyError as exc:
        raise HTTPException(404, "智能体不存在") from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("")
def list_agents():
    return {"agents": agent_store.list()}


@router.post("")
def create_agent(req: AgentSettings):
    return mutate(lambda: agent_store.save(req.name, req.prompt))


@router.put("/{agent_id}")
def update_agent(agent_id: str, req: AgentSettings):
    return mutate(lambda: agent_store.save(req.name, req.prompt, agent_id))


@router.delete("/{agent_id}")
def delete_agent(agent_id: str):
    mutate(lambda: agent_store.delete(agent_id))
    return {"deleted": True}
