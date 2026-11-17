"""Smoke: Listener endpoints through the FastAPI TestClient (fake service)."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("SAYA_GAME_MEMORY", "0")
os.environ.setdefault("SAYA_SLU_CORPUS", "0")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from saya_app.routers import listener  # noqa: E402


class _Fake:
    def open_session(self):
        return "session_listener_smoke.jsonl"

    def handle_message(self, sid, text):
        if not str(text or "").strip():
            raise listener.ListenerError("请输入要记录的内容")
        return {"type": "proposal", "proposal": {
            "title": "t", "content": "c", "category": "preference", "layer": "semantic"}}

    def confirm(self, sid, content, layer, category=None, title=None):
        return {"type": "confirmed", "memory_id": "m-1", "action": "created",
                "locked": True, "proposal": {"content": content, "layer": layer}}

    def update_saved(self, sid, content, layer, category=None, title=None):
        return {"type": "updated", "memory_id": "m-1"}

    def derive_state(self, sid):
        return {"locked": True, "memory_id": "m-1", "question_used": False,
                "proposal": None, "linked": True}

    def listener_sessions(self):
        return ["session_listener_smoke.jsonl"]


listener.listener_service = _Fake()
app = FastAPI()
app.include_router(listener.router)
client = TestClient(app)

r = client.post("/api/listener/open")
assert r.status_code == 200 and r.json()["session_id"], r.text

r = client.post("/api/listener/send", json={
    "session_id": "s", "text": "我喜欢安静的环境"})
assert r.status_code == 200 and r.json()["type"] == "proposal", r.text

r = client.post("/api/listener/confirm", json={
    "session_id": "s", "content": "Doctor 喜欢安静", "layer": "semantic",
    "category": "preference", "title": "安静"})
assert r.status_code == 200 and r.json()["locked"], r.text

r = client.post("/api/listener/update", json={
    "session_id": "s", "content": "Doctor 喜欢安静（更新）", "layer": "core"})
assert r.status_code == 200 and r.json()["type"] == "updated", r.text

r = client.get("/api/listener/state?session_id=s")
assert r.status_code == 200 and r.json()["locked"], r.text

r = client.get("/api/listener/sessions")
assert r.status_code == 200 and r.json()["session_ids"], r.text

r = client.post("/api/listener/send", json={"session_id": "s", "text": ""})
assert r.status_code == 400, r.text

print("Listener endpoint smoke passed.")
