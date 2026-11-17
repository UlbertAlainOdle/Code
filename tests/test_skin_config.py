"""Skin selection plumbing: config template, apply channel, file upload."""

import threading
from pathlib import Path

from fastapi.testclient import TestClient

from saya_app.routers.bridge import router
from saya_app.services.minecraft_intents import DEFAULT_CONFIG


def test_default_skin_config_is_neutral():
    assert DEFAULT_CONFIG.get("skin_value") == ""
    assert "{value}" in DEFAULT_CONFIG.get("skin_command", "")
    assert DEFAULT_CONFIG.get("skin_model") in ("classic", "slim")
    assert DEFAULT_CONFIG.get("skin_url_base", "").startswith("http")


def _service(monkeypatch, skin_value=""):
    from saya_app.services import minecraft_mineflayer as mf

    sent = []
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "_send",
        lambda self, payload: sent.append(payload) or True,
    )
    monkeypatch.setattr(
        mf, "BOT_CONFIG_PATH", mf.BOT_CONFIG_PATH.with_name("minecraft_bot.skin-test.json")
    )
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = threading.RLock()
    service.config = dict(mf.DEFAULT_CONFIG)
    service.config["skin_value"] = skin_value
    service.state = {}
    service._last_broadcast_at = 0.0
    service._listeners = []
    service._pending_player_chats = []
    service._conversation_queue = __import__('collections').deque()
    service._conversation_wake = __import__('threading').Event()
    return service, sent


def test_apply_skin_renders_template_and_sends_doctor_command(monkeypatch):
    service, sent = _service(monkeypatch, skin_value="Notch")
    assert service.apply_skin() is True
    assert len(sent) == 1
    message = sent[0]
    assert message["type"] == "command"
    # A non-file value renders through the configured template verbatim.
    assert message["text"] == "/skin set URL classic Notch"
    assert message["origin"] == "doctor"


def test_apply_skin_local_png_switches_to_upload(monkeypatch, tmp_path):
    """A local PNG value always applies via FabricTailor upload (server
    reads the file directly) - this is what makes a stable local setup
    possible without the server reaching the backend over HTTP."""
    skin_file = tmp_path / "saya-default.png"
    skin_file.write_bytes(b"\x89PNG\r\n\x1a\n" + bytes(8))
    service, sent = _service(monkeypatch, skin_value=str(skin_file))
    service.config["skin_command"] = "/skin set URL {model} {value}"  # even so
    assert service.apply_skin() is True
    assert sent[0]["text"] == f"/skin set upload classic {skin_file}"


def test_apply_skin_without_value_is_a_noop(monkeypatch):
    service, sent = _service(monkeypatch, skin_value="")
    assert service.apply_skin() is False
    assert sent == []


def test_fabrictailor_template_renders_value_and_model(monkeypatch):
    service, sent = _service(monkeypatch, skin_value="http://example.com/skin.png")
    assert service.apply_skin() is True
    assert sent[0]["text"] == "/skin set URL classic http://example.com/skin.png"
    service.config["skin_model"] = "slim"
    service.config["skin_command"] = "/skin set URL {model} {value}"
    assert service.apply_skin() is True
    assert sent[-1]["text"] == "/skin set URL slim http://example.com/skin.png"


def test_upload_template_with_server_dir_mirror(monkeypatch, tmp_path):
    """FabricTailor /skin set upload {model} {value}: the PNG must be
    mirrored into skin_server_dir and the value becomes the file path."""
    server_dir = tmp_path / "server-visible"
    service, sent = _service(monkeypatch, skin_value="")
    service.config["skin_command"] = "/skin set upload {model} {value}"
    service.config["skin_server_dir"] = str(server_dir)
    data = b"\x89PNG\r\n\x1a\n" + bytes(8)
    filename = "skin-test.png"
    server_dir.mkdir(parents=True, exist_ok=True)
    (server_dir / filename).write_bytes(data)
    service.config["skin_value"] = str(server_dir / filename)
    assert service.apply_skin() is True
    assert sent[-1]["text"] == f"/skin set upload classic {server_dir / filename}"


def test_skin_endpoints_roundtrip(monkeypatch, tmp_path):
    from fastapi import FastAPI

    from saya_app.services import minecraft_mineflayer as mf

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    sent = []
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "_send",
        lambda self, payload: sent.append(payload) or True,
    )
    monkeypatch.setattr(mf, "BOT_CONFIG_PATH", tmp_path / "minecraft_bot.json")
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "status",
        lambda self: {"running": True},
    )
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = threading.RLock()
    service.config = dict(mf.DEFAULT_CONFIG)
    service.state = {}
    service._last_broadcast_at = 0.0
    service._listeners = []
    service._pending_player_chats = []
    service._conversation_queue = __import__('collections').deque()
    service._conversation_wake = __import__('threading').Event()

    # JSON endpoint: set value and apply.
    commands = lambda: [m for m in sent if m.get("type") == "command"]  # noqa: E731
    response = client.post("/api/bridge/minecraft/bot/skin", json={"value": "Notch"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["applied"] is True
    assert commands()[-1]["text"] == "/skin set upload classic Notch"
    assert commands()[-1]["origin"] == "doctor"

    # Empty value = revert: STOP applying (no command is sent - the plugin
    # keeps whatever is active until the next join, where apply_skin is a
    # deliberate no-op for an empty value).
    commands_before = len(commands())
    response = client.post("/api/bridge/minecraft/bot/skin", json={"value": ""})
    assert response.status_code == 200
    assert "恢复默认" in response.json().get("message", "")
    assert len(commands()) == commands_before


def test_skin_endpoint_accepts_model(monkeypatch):
    from fastapi import FastAPI

    from saya_app.services import minecraft_mineflayer as mf

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = threading.RLock()
    service.config = dict(mf.DEFAULT_CONFIG)
    service.state = {}
    service._last_broadcast_at = 0.0
    service._listeners = []
    monkeypatch.setattr(mf, "BOT_CONFIG_PATH", tmp_path_dir() / "minecraft_bot.json")
    monkeypatch.setattr(mf.MineflayerMinecraftService, "_send",
                        lambda self, payload: True)
    monkeypatch.setattr(mf.MineflayerMinecraftService, "status",
                        lambda self: {"running": True})
    monkeypatch.setattr(mf, "minecraft_bot_service", service)

    response = client.post(
        "/api/bridge/minecraft/bot/skin",
        json={"value": "http://x/s.png", "model": "slim"},
    )
    assert response.status_code == 200
    assert service.config["skin_model"] == "slim"

    # File endpoint: PNG upload -> static/skins file + skin_value URL.
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "public_config",
        lambda self: {"skin_url_base": "http://127.0.0.1:8000"},
    )
    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x40\x00\x00\x00\x80"
        b"\x08\x06\x00\x00\x00" + b"\x00" * 4
    )
    response = client.post(
        "/api/bridge/minecraft/bot/skin/file",
        files={"file": ("my skin.png", png, "image/png")},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"].startswith("http://127.0.0.1:8000/static/skins/skin-")
    assert body["url"].endswith(".png")

    response = client.post(
        "/api/bridge/minecraft/bot/skin/file",
        files={"file": ("not-a-skin.txt", b"hello", "text/plain")},
    )
    assert response.status_code == 400


def test_skin_endpoint_persists_custom_template(monkeypatch):
    """Any plugin's syntax works: the template is persisted verbatim when it
    contains {value}, and rejected (kept as-is) when it does not."""
    from fastapi import FastAPI

    from saya_app.services import minecraft_mineflayer as mf

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    sent = []
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = threading.RLock()
    service.config = dict(mf.DEFAULT_CONFIG)
    service.state = {}
    service._last_broadcast_at = 0.0
    service._listeners = []
    monkeypatch.setattr(mf, "BOT_CONFIG_PATH", tmp_path_dir() / "minecraft_bot.json")
    monkeypatch.setattr(mf.MineflayerMinecraftService, "_send",
                        lambda self, payload: sent.append(payload) or True)
    monkeypatch.setattr(mf.MineflayerMinecraftService, "status",
                        lambda self: {"running": True})
    # The route resolves the module-level singleton at call time - patch it
    # so assertions can read the config it actually mutated.
    monkeypatch.setattr(mf, "minecraft_bot_service", service)

    response = client.post(
        "/api/bridge/minecraft/bot/skin",
        json={"value": "http://x/skin.png", "command": "/skin url {value} slim"},
    )
    assert response.status_code == 200
    assert service.config["skin_command"] == "/skin url {value} slim"

    # A template without {value} would produce a broken command: ignored.
    response = client.post(
        "/api/bridge/minecraft/bot/skin",
        json={"value": "Notch", "command": "/skin set"},
    )
    assert response.status_code == 200
    assert service.config["skin_command"] == "/skin url {value} slim"


def tmp_path_dir():
    import tempfile

    return Path(tempfile.mkdtemp(prefix="saya-skin-test-"))
