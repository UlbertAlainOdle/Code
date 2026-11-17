"""Request-scoped chat state and per-session serialization.

The model object may still require its own process-wide lock, but conversation
history and persistence must never be selected through a global "current file".
"""

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List
from uuid import uuid4


@dataclass
class ChatRuntime:
    request_id: str
    session_id: str
    history: List[str]
    project_id: str = ""
    project_name: str = ""
    role_mode: str = ""
    temporary: bool = False
    memory_recall: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def public_metadata(self):
        return {
            "request_id": self.request_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "role_mode": self.role_mode,
            "temporary": self.temporary,
            "created_at": self.created_at,
        }


class ChatRuntimeManager:
    def __init__(self):
        self._locks: Dict[str, asyncio.Lock] = {}
        self._guard = asyncio.Lock()

    async def session_lock(self, session_id):
        async with self._guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[session_id] = lock
            return lock

    def create(self, memory_manager, requested_session_id="", project_id="", project_name="", role_mode="", temporary=False, request_id=""):
        if request_id and not re.fullmatch(r'[a-f0-9]{32}',request_id):
            raise ValueError('invalid_chat_request_id')
        if temporary:
            # No session lookup, history read, or empty file creation.
            return ChatRuntime(uuid4().hex, "temporary_" + uuid4().hex, [], temporary=True)
        requested_session_id = str(requested_session_id or "").strip()
        path = memory_manager._safe_session_path(requested_session_id)
        if not path:
            requested_session_id = ('session_request_'+request_id+'.jsonl' if request_id
                                    else memory_manager.create_session(make_current=False))
        history = memory_manager.load_session(requested_session_id, make_current=False)
        return ChatRuntime(
            request_id=request_id or uuid4().hex,
            session_id=requested_session_id,
            history=list(history or []),
            project_id=str(project_id or ""),
            project_name=str(project_name or ""),
            role_mode=str(role_mode or ""),
        )


chat_runtime_manager = ChatRuntimeManager()
