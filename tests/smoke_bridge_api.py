"""Smoke test: game bridge API + the [MC:] streaming filter extracted from server5."""
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from saya_app.routers import bridge  # noqa: E402

app = FastAPI()
app.include_router(bridge.router)
client = TestClient(app)

# --- 1. state is empty initially ---
response = client.get("/api/bridge/state")
assert response.status_code == 200 and response.json()["sources"] == []
print("bridge: empty state OK")

# --- 2. bot pushes status (registers the source) ---
response = client.post("/api/bridge/minecraft/status", json={
    "text": "Saya bot at spawn, health 20, Doctor nearby",
})
assert response.status_code == 200, response.text
view = response.json()
assert view["online"] is True and view["label"] == "Minecraft"
print("bridge: status push OK, online =", view["online"])

# --- 3. prompt context is injectable ---
from saya_app.services.game_bridge import game_bridge  # noqa: E402
context = game_bridge.prompt_context()
assert "Minecraft bridge is online" in context and "[MC:" in context
print("bridge: prompt context OK")

# --- 4. Saya's [MC:] commands queue and the bot polls them ---
command = game_bridge.queue_command("minecraft", "follow Doctor", origin="saya")
assert command["origin"] == "saya"
pulled = client.get("/api/bridge/minecraft/commands?take=5").json()["commands"]
assert len(pulled) == 1 and pulled[0]["text"] == "follow Doctor"
assert client.get("/api/bridge/minecraft/commands").json()["commands"] == []
print("bridge: saya command queue + poll OK")

# --- 5. doctor manual send + duplicate protection ---
response = client.post("/api/bridge/minecraft/send", json={"text": "go mining"})
assert response.status_code == 200, response.text
response = client.post("/api/bridge/minecraft/send", json={"text": "go mining"})
assert response.status_code == 429, response.text
print("bridge: manual send + 429 dedupe OK")

# --- 6. invalid source ids rejected ---
response = client.post("/api/bridge/Bad!Source/status", json={"text": "x"})
assert response.status_code == 400, response.text
print("bridge: invalid source 400 OK")

# --- 7. the streaming filter from server5: extract and exercise it ---
source = Path("server5-22B----.py").read_text(encoding="utf-8")
start = source.index("MOTION_COMPLETE_TAG_RE = re.compile")
end = source.index("def queue_saya_bridge_commands")
namespace = {"re": re}
exec(source[start:end], namespace)
filt = namespace["filter_motion_stream_buffer"]

chunks = ["[MOT", "ION: hap", "py] Hi Doctor! ", "[MC: fol", "low Doctor]", " Bye!"]
visible_out, held, categories, commands = "", "", [], []
for chunk in chunks:
    held += chunk
    visible, held, cats, cmds = filt(held)
    visible_out += visible
    categories += cats
    commands += cmds
visible, held, cats, cmds = filt(held, final=True)
visible_out += visible
categories += cats
commands += cmds
assert visible_out == " Hi Doctor!  Bye!", repr(visible_out)
assert categories == ["happy"], categories
assert commands == ["follow Doctor"], commands
print("bridge: streaming filter OK ->", repr(visible_out), categories, commands)

# --- 8. a truncated [MC at EOF never leaks ---
visible, held, cats, cmds = filt("end of reply [MC", final=True)
assert visible == "end of reply " and not cmds, (visible, cmds)
print("bridge: truncated tag EOF OK")

print("\nAll bridge smoke tests passed.")
