"""Smoke test: Minecraft bot service + API (no real server needed)."""
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Isolated config file before the singleton imports.
_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
_tmp.close()
os.environ["SAYA_MC_BOT_CONFIG"] = _tmp.name

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from saya_app.routers import bridge  # noqa: E402
from saya_app.services.minecraft_intents import detect_player_action  # noqa: E402

app = FastAPI()
app.include_router(bridge.router)
client = TestClient(app)

# --- 1. deterministic owner-action parsing (in-game direct commands) ---
assert detect_player_action("<Kurosawa_Jin> 跟着我") == "follow Doctor"
assert detect_player_action("<Kurosawa_Jin> 取消跟随") == "unfollow"
assert detect_player_action("<Kurosawa_Jin> 收集3个铁") == "collect iron_ore 3"
assert detect_player_action("<Kurosawa_Jin> 挖铁") == "collect iron_ore 1"
assert detect_player_action("<Kurosawa_Jin> mine iron ore 2") == "collect iron_ore 2"
assert detect_player_action("<Kurosawa_Jin> 扔一个") == "drop one"
assert detect_player_action("<Kurosawa_Jin> 捡起来") == "pickup nearest"
assert detect_player_action("<Kurosawa_Jin> 攻击那个怪物") == "attack nearest"
assert detect_player_action("<Kurosawa_Jin> 制作4个木板") == "craft planks 4"
assert detect_player_action("<Kurosawa_Jin> 合成工作台") == "craft crafting_table 1"
# 2026-09 plan: 石头 defaults to cobblestone; 挖点X without count -> 8.
assert detect_player_action("<Kurosawa_Jin> 挖点石头") == "collect cobblestone 8"
assert detect_player_action("<Kurosawa_Jin> 弄点木头") == "chop"
assert detect_player_action("<Kurosawa_Jin> 刚才砍树挺有趣的") is None
print("deterministic intents: OK")

# --- 2. config save/load round trip through the API ---
response = client.put("/api/bridge/minecraft/bot", json={
    "host": "127.0.0.1", "port": 1, "username": "TestSaya", "version": "1.21.5", "account": "",
})
assert response.status_code == 200, response.text
assert response.json()["config"]["port"] == 1
import json  # noqa: E402
stored = json.loads(Path(_tmp.name).read_text(encoding="utf-8"))
assert stored["username"] == "TestSaya"
print("config persistence: OK")

# --- 3. Mineflayer lifecycle against an unreachable port ---
response = client.post("/api/bridge/minecraft/bot/start")
assert response.status_code == 200
# The worker stays alive and reconnects; the Minecraft connection stays false.
deadline = time.time() + 20
while time.time() < deadline:
    status = client.get("/api/bridge/minecraft/bot").json()
    if status["last_error"] and status["running"]:
        break
    time.sleep(0.5)
assert status["last_error"], f"expected a connection error, got {status}"
assert status["running"] is True and status["connected"] is False
assert status["engine"] == "mineflayer"
print("reconnecting lifecycle path: OK ->", status["last_error"][:90])
client.post("/api/bridge/minecraft/bot/stop")

# --- 4. restart after a clean stop works ---
response = client.post("/api/bridge/minecraft/bot/start")
assert response.status_code == 200
client.post("/api/bridge/minecraft/bot/stop")
deadline = time.time() + 10
status = client.get("/api/bridge/minecraft/bot").json()
while time.time() < deadline and status["running"]:
    time.sleep(0.5)
    status = client.get("/api/bridge/minecraft/bot").json()
print("after stop: running =", status["running"], "log tail:", status["log"][-1]["text"] if status["log"] else "-")

# --- 5. bridge state still intact for the generic endpoints ---
response = client.get("/api/bridge/state")
assert response.status_code == 200 and "sources" in response.json()
print("bridge state: OK")

os.unlink(_tmp.name)
print("\nAll Minecraft bot smoke tests passed.")
