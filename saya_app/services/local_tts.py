"""Persistent voice settings and an isolated, lazily started Qwen GPU worker."""
import atexit
import io
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, ConfigDict

from saya_app.config import PROJECT_ROOT, KNOWLEDGE_BASE_DIR
from saya_app.services.state_storage import atomic_save_json
from saya_app.services.omni_tts import OmniTTS
from saya_app.services.speech_text import prepare_speech
from saya_app.services.speech_prosody import Prosody, process_wav, ffmpeg_binary


class VoiceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    enabled: bool = True
    provider: Literal["qwen_local", "elevenlabs"] = "qwen_local"
    auto_read: bool = False
    engine: Literal["auto", "vllm_omni", "transformers"] = "auto"
    stream_audio: bool = True
    stream_chat: bool = True
    stream_buffer_seconds: float = Field(0.8, ge=0.1, le=5)
    mode: Literal["design", "clone"] = "design"
    language: Literal["Auto", "Chinese", "English", "Japanese", "Korean", "French", "German", "Spanish", "Portuguese", "Russian", "Italian"] = "Chinese"
    description: str = Field("年轻成年女性，清亮柔和的二次元声线，活泼亲切，有自然的情绪起伏，咬字清楚，避免夸张尖叫和机械感。", max_length=1500)
    voice_id: str = Field("", max_length=32, pattern=r"^(|[a-f0-9]{32})$")
    x_vector_only: bool = False
    temperature: float = Field(0.9, ge=0.1, le=2)
    top_p: float = Field(1.0, gt=0, le=1)
    top_k: int = Field(50, ge=1, le=200)
    repetition_penalty: float = Field(1.05, ge=1, le=2)
    max_new_tokens: int = Field(2048, ge=128, le=4096)
    seed: int = Field(42, ge=-1, le=2147483647)
    chunk_chars: int = Field(100, ge=30, le=300)
    volume: float = Field(1, ge=0, le=1)
    playback_rate: float = Field(1, ge=0.5, le=2)
    mood_prosody: bool = False
    mood_control: Literal["model", "timing"] = "model"
    idle_unload_seconds: int = Field(300, ge=0, le=3600)

def settings_dict(settings):
    return settings.model_dump()


class LocalTTS:
    def __init__(self, root=PROJECT_ROOT, data_dir=None):
        self.root = Path(root)
        self.data = Path(data_dir or (KNOWLEDGE_BASE_DIR / "tts"))
        self._settings_lock = threading.RLock()
        self._rpc_lock = threading.Lock()
        self._process_lock = threading.RLock()
        self._process = None
        self._answers = None
        self._log = None
        self._last_used = 0
        self._timer_started = False
        self.omni = OmniTTS(self.root, self.data)
        atexit.register(self.unload)

    def settings(self):
        with self._settings_lock:
            path = self.data / "settings.json"
            if not path.exists():
                return VoiceSettings()
            return VoiceSettings(**json.loads(path.read_text(encoding="utf-8")))

    def save_settings(self, value):
        with self._settings_lock:
            if value.voice_id:
                self.voice(value.voice_id)
            self.data.mkdir(parents=True, exist_ok=True)
            atomic_save_json(self.data / "settings.json", settings_dict(value))
        if not value.enabled or value.provider != "qwen_local":
            self.unload()
        elif self.effective_engine(value) == "transformers":
            self.omni.unload()
        else:
            self._unload_worker()
        return value

    def audio_path(self, identifier, kind="previews"):
        if not re.fullmatch(r"[a-f0-9]{32}", identifier):
            raise ValueError("无效的语音文件标识。")
        return self.data / kind / (identifier + ".wav")

    def voice(self, identifier):
        path = self.audio_path(identifier, "voices").with_suffix(".json")
        if not path.exists():
            raise ValueError("参考声音不存在，请先上传或保存一个声音。")
        return json.loads(path.read_text(encoding="utf-8"))

    def voices(self):
        folder = self.data / "voices"
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]

    def _configured(self):
        python = self.root / ".runtime/qwen-tts/Scripts/python.exe"
        if os.name != "nt":
            python = self.root / ".runtime/qwen-tts/bin/python"
        models = self.root / "models/qwen3-tts"
        installed = python.exists() and all((models / m / "model.safetensors").exists() and (models / m / "speech_tokenizer/model.safetensors").exists() for m in ("Base", "VoiceDesign"))
        return python, installed

    def _start(self):
        with self._process_lock:
            if self._process and self._process.poll() is None:
                return self._process, self._answers
            python, installed = self._configured()
            if not installed:
                raise RuntimeError("本地语音环境或权重尚未安装，请运行 scripts/setup_qwen_tts.ps1。")
            self.data.mkdir(parents=True, exist_ok=True)
            env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1", PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1", PYTHONDONTWRITEBYTECODE="1")
            self._log = (self.data / "worker.log").open("a", encoding="utf-8")
            self._process = subprocess.Popen([str(python), str(self.root / "scripts/qwen_tts_worker.py"), "--root", str(self.root), "--data", str(self.data)], cwd=str(self.root), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self._log, text=True, encoding="utf-8", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            answers = queue.Queue()
            self._answers = answers
            process = self._process

            def read_answers():
                try:
                    for line in process.stdout:
                        answers.put(json.loads(line))
                except Exception as error:
                    answers.put({"error": "语音子进程通信失败：" + str(error)})
                finally:
                    answers.put({"error": "语音进程已退出或已释放显存。详见 knowledge_base/tts/worker.log。"})

            threading.Thread(target=read_answers, daemon=True).start()
            self._last_used = time.monotonic()
            if not self._timer_started:
                self._timer_started = True
                threading.Thread(target=self._idle_loop, daemon=True).start()
            return process, answers

    def _idle_loop(self):
        while True:
            time.sleep(10)
            try:
                seconds = self.settings().idle_unload_seconds
                if seconds and self.omni.running and not self.omni.status()["busy"] and time.monotonic() - self.omni.last_used > seconds:
                    self.omni.unload()
                if seconds and self._last_used and time.monotonic() - self._last_used > seconds:
                    if self._rpc_lock.acquire(blocking=False):
                        try:
                            self._unload_worker()
                        finally:
                            self._rpc_lock.release()
            except Exception:
                pass

    def rpc(self, operation, timeout=600, **payload):
        # Bound the queue too: concurrent requests must not accumulate indefinitely.
        if not self._rpc_lock.acquire(timeout=30):
            raise RuntimeError("语音模型正在处理其他请求，请稍后再试。")
        try:
            process, answers = self._start()
            self._last_used = time.monotonic()
            process.stdin.write(json.dumps(dict(op=operation, **payload), ensure_ascii=False) + "\n")
            process.stdin.flush()
            try:
                answer = answers.get(timeout=timeout)
            except queue.Empty:
                self.unload()
                raise RuntimeError("语音生成超时，已释放模型。请缩短文本或降低生成上限。")
            if "error" in answer:
                if answer.get("error_type") == "validation":
                    raise ValueError(answer["error"])
                raise RuntimeError(answer["error"])
            return answer
        finally:
            self._last_used = time.monotonic()
            self._rpc_lock.release()

    def _unload_worker(self):
        with self._process_lock:
            process, self._process = self._process, None
            if process:
                if process.poll() is None:
                    process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
                for pipe in (process.stdin, process.stdout):
                    if pipe:
                        pipe.close()
            if self._log:
                self._log.close()
                self._log = None
        return {"running": False, "loaded_mode": None}

    def unload(self):
        self.omni.unload()
        return self._unload_worker()

    def effective_engine(self, settings=None):
        settings = settings or self.settings()
        return "vllm_omni" if settings.engine == "vllm_omni" or (settings.engine == "auto" and self.omni.installed) else "transformers"

    def load(self):
        settings = self.settings()
        if self.effective_engine(settings) == "vllm_omni":
            reference = self.reference(settings)
            self._unload_worker()
            self.omni.ensure(settings.mode)
            self.omni.prime(settings, reference)
            self._watch_idle()
            return self.omni.status()
        self.omni.unload()
        return self.rpc("load", mode=settings.mode)

    def _watch_idle(self):
        with self._process_lock:
            if not self._timer_started:
                self._timer_started = True
                threading.Thread(target=self._idle_loop, daemon=True).start()

    def reference(self, settings):
        if settings.mode == "clone":
            ref = self.voice(settings.voice_id)
            if not settings.x_vector_only and not ref.get("text", "").strip():
                raise ValueError("克隆需要参考音频的准确文字；或开启仅提取音色。")
            return ref
        if not settings.description.strip():
            raise ValueError("请填写声音设计描述。")
        return None

    def status(self):
        if self.effective_engine() == "vllm_omni":
            return self.omni.status()
        _, installed = self._configured()
        running = bool(self._process and self._process.poll() is None)
        if running and not self._rpc_lock.locked():
            result = self.rpc("status", timeout=30)
        else:
            result = {"running": running, "busy": self._rpc_lock.locked(), "loaded_mode": None}
        return dict(result, installed=installed, engine="transformers", streaming=False)

    def synthesize(self, text, settings=None, prosody=None):
        start = time.monotonic()
        settings = settings or self.settings()
        text, settings = prepare_speech(text, settings)
        prosody = prosody or Prosody()
        if prosody.instructions and (settings.mode != "clone" or self.effective_engine(settings) != "vllm_omni"):
            raise ValueError("模型表达实验需要克隆模式与流式加速后端。")
        if prosody.changed:
            ffmpeg_binary()  # Fail before starting GPU generation if unavailable.
        reference = self.reference(settings)
        identifier = uuid.uuid4().hex
        if self.effective_engine(settings) == "vllm_omni":
            self._unload_worker()
            self._watch_idle()
            kwargs = {"instructions": prosody.instructions} if prosody.instructions else {}
            audio = process_wav(self.omni.synthesize(text, settings, reference, **kwargs), prosody)
            with wave.open(io.BytesIO(audio)) as wav:
                rate = wav.getframerate()
                duration = wav.getnframes() / rate
            if duration <= 0:
                raise RuntimeError("模型没有生成有效音频。")
            path = self.audio_path(identifier)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(audio)
            result = dict(text=text, duration=round(duration, 2), sample_rate=rate, elapsed_seconds=round(time.monotonic() - start, 2), mode=settings.mode, engine="vllm_omni")
            atomic_save_json(path.with_suffix(".json"), result)
            for old in sorted(path.parent.glob("*.wav"), key=lambda p: p.stat().st_mtime)[:-50]:
                old.unlink(missing_ok=True)
                old.with_suffix(".json").unlink(missing_ok=True)
        else:
            self.omni.unload()
            result = self.rpc("synthesize", text=text, settings=settings_dict(settings), identifier=identifier)
            if prosody.changed:
                path = self.audio_path(identifier)
                path.write_bytes(process_wav(path.read_bytes(), prosody))
                with wave.open(str(path)) as wav:
                    result["duration"] = round(wav.getnframes() / wav.getframerate(), 2)
        result["prosody"] = prosody.metadata()
        result["elapsed_seconds"] = round(time.monotonic() - start, 2)
        # Retain the applied mapping in preview metadata for reproducible audits.
        path = self.audio_path(identifier)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_save_json(path.with_suffix(".json"), result)
        result.update(id=identifier, url="/api/tts/audio/" + identifier)
        return result

    def synthesize_ephemeral(self,text,settings=None,*,session_id=None):
        """Reuse the configured voice, without publishing web preview assets.

        Does not unload either shared engine. An older running worker without
        this byte-only protocol fails closed until its normal idle reload.
        """
        import base64
        from .speech_prosody import resolve
        from .qq_tasks import checkpoint_current
        checkpoint_current()
        settings=settings or self.settings()
        if not settings.enabled or settings.provider!='qwen_local':raise ValueError('本地语音未开启。')
        if self._rpc_lock.locked() or self.omni.active:raise RuntimeError('语音服务正忙。')
        text,settings=prepare_speech(text,settings)
        settings=settings.model_copy(update={'max_new_tokens':min(settings.max_new_tokens,1024)})
        engine=self.effective_engine(settings)
        plan=resolve(settings,engine=engine,session_id=session_id)
        reference=self.reference(settings)
        checkpoint_current()
        if engine=='vllm_omni':
            if self._process and self._process.poll() is None:raise RuntimeError('其他语音后端仍在运行。')
            kwargs={'instructions':plan.instructions} if plan.instructions else {}
            audio=self.omni.synthesize(text,settings,reference,max_bytes=2*1024*1024,**kwargs)
            self._watch_idle()
        else:
            if self.omni.running:raise RuntimeError('其他语音后端仍在运行。')
            supported=self.rpc('status',timeout=15).get('ephemeral_audio')
            checkpoint_current()
            if not supported:raise RuntimeError('语音进程需正常空闲重载后支持临时输出。')
            result=self.rpc('synthesize',text=text,settings=settings_dict(settings),identifier=uuid.uuid4().hex,ephemeral=True)
            encoded=result.get('audio_base64')
            if not isinstance(encoded,str) or len(encoded)>2800000:raise ValueError('语音数据无效。')
            audio=base64.b64decode(encoded,validate=True)
        if not audio or len(audio)>2*1024*1024:raise ValueError('语音数据超过上限。')
        if plan.changed:audio=process_wav(audio,plan)
        if len(audio)>2*1024*1024:raise ValueError('语音数据超过上限。')
        return audio

    def save_preview(self, identifier, name):
        source = self.audio_path(identifier)
        metadata = source.with_suffix(".json")
        if not source.exists() or not metadata.exists():
            raise ValueError("试听文件不存在，请先生成试听。")
        info = json.loads(metadata.read_text(encoding="utf-8"))
        prosody = info.get("prosody") or {}
        if prosody.get("instructions") or prosody.get("tempo", 1) != 1 or prosody.get("gain", 1) != 1:
            raise ValueError("带情绪韵律的试听不能保存为参考声音，请使用平静对照或声音设计试听。")
        voice_id = uuid.uuid4().hex
        target = self.audio_path(voice_id, "voices")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        info.update(id=voice_id, name=name.strip() or "设计声音")
        atomic_save_json(target.with_suffix(".json"), info)
        return info

    def upload_voice(self, content, name, text):
        identifier = uuid.uuid4().hex
        temp = self.data / "uploads" / (identifier + ".audio")
        temp.parent.mkdir(parents=True, exist_ok=True)
        temp.write_bytes(content)
        try:
            return self.rpc("import_voice", identifier=identifier, name=name, text=text)
        finally:
            temp.unlink(missing_ok=True)


local_tts = LocalTTS()
