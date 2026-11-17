"""Config plumbing for the auto-mine-ores toggle (UI -> API -> worker)."""

from saya_app.routers.bridge import BotConfigRequest
from saya_app.services.minecraft_intents import (
    DEFAULT_CONFIG,
    normalize_auto_mine_ore_modes,
)


def test_default_config_has_auto_mine_ores_off():
    assert DEFAULT_CONFIG.get("auto_mine_ores") is False


def test_bot_config_request_accepts_auto_mine_ores():
    request = BotConfigRequest(auto_mine_ores=True)
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    assert updates == {"auto_mine_ores": True}


def test_save_config_persists_and_broadcasts(monkeypatch):
    """save_config must accept the key (DEFAULT_CONFIG-driven allowlist) and
    push a config_update to the worker, whose handler loads autoMineOres."""
    from saya_app.services import minecraft_mineflayer as mf

    sent = []
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "_send",
        lambda self, payload: sent.append(payload) or True,
    )
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "public_config",
        lambda self: {"auto_mine_ores": True},
    )
    monkeypatch.setattr(
        mf, "BOT_CONFIG_PATH", mf.BOT_CONFIG_PATH.with_name("minecraft_bot.test.json")
    )
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = __import__("threading").RLock()
    service.config = dict(mf.DEFAULT_CONFIG)

    service.save_config({"auto_mine_ores": True})

    assert service.config["auto_mine_ores"] is True
    assert any(
        message.get("type") == "config_update"
        and (message.get("config") or {}).get("auto_mine_ores") is True
        for message in sent
    ), sent


def test_default_config_has_empty_ore_modes():
    assert DEFAULT_CONFIG.get("auto_mine_ore_modes") == {}


def test_bot_config_request_accepts_ore_modes():
    modes = {"diamond": {"enabled": True, "distance": 16}}
    request = BotConfigRequest(auto_mine_ore_modes=dict(modes))
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    assert updates["auto_mine_ore_modes"] == modes


def test_normalize_ore_modes_keeps_known_groups_and_clamps():
    normalized = normalize_auto_mine_ore_modes({
        "iron": {"enabled": True, "distance": 10},
        "diamond": {"enabled": True},                     # distance default
        "unknown_ore": {"enabled": True, "distance": 8},  # dropped
        "gold": "not-a-dict",                             # dropped
        "coal": {"enabled": False, "distance": 999},      # clamped to 64
        "lapis": {"enabled": True, "distance": "oops"},   # clamped to default
    })
    assert normalized == {
        "iron": {"enabled": True, "distance": 10},
        "diamond": {"enabled": True, "distance": 24},
        "coal": {"enabled": False, "distance": 64},
        "lapis": {"enabled": True, "distance": 24},
    }


def test_normalize_ore_modes_rejects_non_dict_input():
    assert normalize_auto_mine_ore_modes(None) == {}
    assert normalize_auto_mine_ore_modes(["iron"]) == {}
    assert normalize_auto_mine_ore_modes("iron") == {}


def test_save_config_normalizes_ore_modes(monkeypatch):
    """Persisted modes are canonical: unknown groups dropped, ranges clamped."""
    from saya_app.services import minecraft_mineflayer as mf

    sent = []
    monkeypatch.setattr(
        mf.MineflayerMinecraftService, "_send",
        lambda self, payload: sent.append(payload) or True,
    )
    monkeypatch.setattr(
        mf, "BOT_CONFIG_PATH", mf.BOT_CONFIG_PATH.with_name("minecraft_bot.test.json")
    )
    service = mf.MineflayerMinecraftService.__new__(mf.MineflayerMinecraftService)
    service._lock = __import__("threading").RLock()
    service.config = dict(mf.DEFAULT_CONFIG)

    saved = service.save_config({
        "auto_mine_ore_modes": {"iron": {"enabled": True, "distance": 2000}}
    })

    assert saved["auto_mine_ore_modes"] == {"iron": {"enabled": True, "distance": 64}}
    assert any(
        message.get("type") == "config_update"
        and (message.get("config") or {}).get("auto_mine_ore_modes")
        == {"iron": {"enabled": True, "distance": 64}}
        for message in sent
    ), sent


def test_default_auto_mine_range():
    assert DEFAULT_CONFIG.get("auto_mine_range") == 24


def test_bot_config_request_accepts_range():
    request = BotConfigRequest(auto_mine_range=48)
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    assert updates["auto_mine_range"] == 48
