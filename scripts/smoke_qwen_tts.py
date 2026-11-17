"""Generate an actual design + clone sample and report CUDA memory/latency."""
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from saya_app.services.local_tts import local_tts, VoiceSettings, settings_dict


if __name__ == "__main__":
    output = ROOT / "knowledge_base/tts/samples"
    output.mkdir(parents=True, exist_ok=True)
    try:
        settings = VoiceSettings()
        result = local_tts.synthesize("博士，你回来啦。今天也辛苦了，先休息一下，再和我聊聊天吧。", settings)
        shutil.copyfile(local_tts.audio_path(result["id"]), output / "saya-design.wav")
        print("DESIGN " + json.dumps(result, ensure_ascii=False), flush=True)
        voice = local_tts.save_preview(result["id"], "Saya · 二次元柔和声线")
        settings.mode = "clone"
        settings.voice_id = voice["id"]
        result = local_tts.synthesize("嗯，我会一直陪着你。慢慢来，我们一起把今天的事情做好。", settings)
        shutil.copyfile(local_tts.audio_path(result["id"]), output / "saya-clone.wav")
        print("CLONE " + json.dumps(result, ensure_ascii=False), flush=True)
        local_tts.save_settings(settings)
        (output / "verification.json").write_text(json.dumps(dict(design_voice=voice, clone=result, settings=settings_dict(settings)), ensure_ascii=False, indent=2), encoding="utf-8")
    finally:
        local_tts.unload()
