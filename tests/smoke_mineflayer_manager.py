"""Offline process/pipe smoke test for the Mineflayer service manager."""

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
tmp.close()
os.environ["SAYA_MC_BOT_CONFIG"] = tmp.name

from saya_app.services.minecraft_mineflayer import MineflayerMinecraftService  # noqa: E402

service = MineflayerMinecraftService()
service.save_config({
    "host": "127.0.0.1",
    "port": 1,
    "username": "SayaPipeTest",
    "owner_username": "Doctor",
    "version": "1.21.5",
    "account": "",
})
service.start()

deadline = time.time() + 12
status = service.status()
while time.time() < deadline:
    status = service.status()
    if any("Connecting Mineflayer" in item["text"] for item in status["log"]):
        break
    time.sleep(0.1)

assert status["engine"] == "mineflayer"
assert status["running"] is True
assert status["username"] == "SayaPipeTest"
assert any("Connecting Mineflayer" in item["text"] for item in status["log"]), status
print("worker process + NDJSON state pipe: OK")

service.stop()
deadline = time.time() + 5
while time.time() < deadline and service.status()["running"]:
    time.sleep(0.1)
assert service.status()["running"] is False
print("graceful worker shutdown: OK")

# Owner chat, including deterministic commands, now enters the unified
# ConversationService queue so game/desktop/web share one transcript.
captured = []
service.config["owner_username"] = "Kurosawa_Jin"
service._send = lambda payload, process=None: captured.append(payload) or True
service._handle_player_chat("Kurosawa_Jin", "制作4个木板")
assert captured == [], captured
assert service.status()["conversation"]["queued"] == 1
service._handle_player_chat("Visitor", "跟着我")
assert captured == [], captured
assert service.status()["pending_chats"] == 1
print("owner identity + unified direct command routing: OK")

# Owner chat without a deterministic command is queued for the unified
# ConversationService instead of waiting for web polling.
service._handle_player_chat("Kurosawa_Jin", "今天过得怎么样呀")
assert service.status()["conversation"]["queued"] == 2
print("owner chat routed to unified conversation queue: OK")

Path(tmp.name).unlink(missing_ok=True)
print("Mineflayer manager smoke test passed.")
