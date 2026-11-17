"""Smoke test: journal + weather API endpoints through the FastAPI TestClient."""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["SAYA_JOURNAL_FILE"] = tempfile.mktemp(suffix=".json")

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from saya_app.routers import integrations, journal  # noqa: E402

app = FastAPI()
app.include_router(journal.router)
app.include_router(integrations.router)
client = TestClient(app, raise_server_exceptions=False)

# --- journal -----------------------------------------------------------------
# 1. empty list
response = client.get("/api/journal/entries")
assert response.status_code == 200, response.text
assert response.json()["total"] == 0, response.text
print("journal: empty list OK")

# 2. manual add
response = client.post("/api/journal/add", json={
    "content": "Doctor tested the diary feature today.", "mood": "curious",
})
assert response.status_code == 200, response.text
entry = response.json()
assert entry["source"] == "doctor" and entry["mood"] == "curious"
print("journal: manual add OK ->", entry["id"][:8], entry["date"])

# 3. list reflects entry
response = client.get("/api/journal/entries")
assert response.json()["total"] == 1
print("journal: list OK")

# 4. write without API key -> 503 (frontend shows guidance)
response = client.post("/api/journal/write", json={})
assert response.status_code == 503, response.text
print("journal: write without key -> 503 OK")

# 5. delete + 404 path
response = client.post(f"/api/journal/{entry['id']}/delete")
assert response.status_code == 200, response.text
response = client.post(f"/api/journal/{entry['id']}/delete")
assert response.status_code == 404, response.text
print("journal: delete + 404 OK")

# --- weather -----------------------------------------------------------------
# 6. missing params -> 400
response = client.post("/api/weather", json={})
assert response.status_code == 400, response.text
print("weather: missing location -> 400 OK")

# 7. live call (needs internet); skip gracefully when offline
response = client.post("/api/weather", json={"location": "Shanghai"})
if response.status_code == 200:
    payload = response.json()
    assert payload["current"]["temperature"] is not None
    assert len(payload["daily"]) == 3 and payload["summary_cn"]
    print(f"weather: live OK -> {payload['location']} "
          f"{payload['current']['temperature']}C {payload['current']['text_cn']}")
elif response.status_code == 502:
    print("weather: offline (502), network path skipped")
else:
    raise AssertionError(f"unexpected weather status {response.status_code}: {response.text}")

print("\nAll smoke tests passed.")
