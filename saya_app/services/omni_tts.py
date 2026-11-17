"""Managed single-GPU vLLM-Omni server, isolated in the project's WSL venv."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx


def linux_path(path):
    value = str(Path(path).resolve()).replace("\\", "/")
    if len(value) > 2 and value[1:3] == ":/":
        return "/mnt/" + value[0].lower() + value[2:]
    return value


class OmniTTS:
    def __init__(self, root, data):
        self.root, self.data = Path(root), Path(data)
        self.url = "http://127.0.0.1:8091"
        self._lock = threading.RLock()
        self._process = None
        self._guardian = None
        self._log = None
        self._mode = None
        self._primed_key = None
        self._stop_loading = threading.Event()
        self._token = uuid.uuid4().hex
        self.active = 0
        self.last_used = time.monotonic()

    @property
    def installed(self):
        return (self.root / ".runtime/qwen-omni-installed.json").exists()

    @property
    def running(self):
        return bool(self._process and self._process.poll() is None)

    def python(self):
        marker = self.root / ".runtime/qwen-omni-installed.json"
        metadata = json.loads(marker.read_text()) if marker.exists() else {}
        return metadata.get("python") or linux_path(self.root / ".runtime/qwen-omni-linux/bin/python")

    def ensure(self, mode):
        with self._lock:
            if self.running and self._mode == mode:
                return
            if self.active:
                raise RuntimeError("语音正在生成，请停止朗读后再切换模型。")
            if not self.installed:
                raise RuntimeError("流式加速后端未安装，请运行 scripts/setup_qwen_omni.sh。")
            self.unload()
            self._stop_loading.clear()
            self.data.mkdir(parents=True, exist_ok=True)
            self._log = (self.data / "omni-worker.log").open("a", encoding="utf-8")
            binary = self.python()
            command = [binary, linux_path(self.root / "scripts/run_qwen_omni.py"), "--root", linux_path(self.root), "--mode", mode, "--token", self._token]
            if os.name == "nt":
                command.append('--guarded')
                command = ["wsl.exe", "-d", "Ubuntu", "--", *command]
            self._process = subprocess.Popen(command, cwd=self.root, stdin=subprocess.DEVNULL, stdout=self._log, stderr=self._log, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if os.name == 'nt':
                try:
                    self._start_guardian()
                except Exception:
                    self.unload()
                    raise
            start = time.monotonic()
            with httpx.Client(trust_env=False, timeout=2) as client:
                while time.monotonic() - start < 600:
                    if self._guardian and self._guardian.poll() is not None:
                        self.unload()
                        raise RuntimeError('语音退出守护进程已退出，已停止加载。')
                    if self._stop_loading.is_set():
                        raise RuntimeError("语音模型加载已取消。")
                    if self._process.poll() is not None:
                        raise RuntimeError("语音加速后端启动失败，详见 knowledge_base/tts/omni-worker.log。")
                    try:
                        response = client.get(self.url + "/v1/models")
                        if response.is_success and any(item["id"] == "saya-qwen-tts" for item in response.json().get("data", [])):
                            self._mode = mode
                            self.last_used = time.monotonic()
                            return
                    except (httpx.HTTPError, ValueError, KeyError):
                        pass
                    time.sleep(1)
            self.unload()
            raise RuntimeError("流式语音后端加载超时。详见 knowledge_base/tts/omni-worker.log。")

    def _start_guardian(self):
        from .process_watch import process_birth
        marker = self.root / ('.runtime/qwen-omni-' + self._token + '.guardian')
        marker.unlink(missing_ok=True)
        command = [sys.executable, str(self.root / 'scripts/watch_qwen_omni.py'),
                   '--root', str(self.root), '--token', self._token,
                   '--owner-pid', str(os.getpid()), '--owner-birth', str(process_birth(os.getpid())),
                   '--worker-pid', str(self._process.pid), '--worker-birth', str(process_birth(self._process.pid))]
        self._guardian = subprocess.Popen(command, cwd=self.root, stdin=subprocess.DEVNULL,
                                         stdout=self._log, stderr=self._log, creationflags=subprocess.CREATE_NO_WINDOW)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if self._guardian.poll() is not None:
                break
            if marker.exists():
                return
            time.sleep(0.1)
        raise RuntimeError('语音退出守护进程启动失败，已停止加载。请查看 omni-worker.log。')

    def unload(self):
        self._stop_loading.set()
        with self._lock:
            if self._process:
                # The stop helper needs only the standard library, even if the venv is broken.
                command = ["python3", linux_path(self.root / "scripts/stop_qwen_omni.py"), "--root", linux_path(self.root), "--token", self._token]
                if os.name == "nt":
                    command = ["wsl.exe", "-d", "Ubuntu", "--", *command]
                try:
                    subprocess.run(command, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    self._process.wait(timeout=10)
                except (OSError, subprocess.TimeoutExpired):
                    self._process.terminate()
                self._process = None
            if self._guardian:
                try:
                    self._guardian.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._guardian.terminate()
                self._guardian = None
            if self._log:
                self._log.close()
                self._log = None
            self._mode = None
            self._primed_key = None
        return dict(running=False, loaded_mode=None)

    def status(self):
        return dict(installed=self.installed, running=self.running, busy=bool(self.active or (self.running and self._mode is None)), loaded_mode=self._mode if self.running else None, engine="vllm_omni", streaming=self.installed)

    def payload(self, text, settings, reference=None, streaming=False, instructions=None):
        body = dict(model="saya-qwen-tts", input=text, task_type="Base" if settings.mode == "clone" else "VoiceDesign", language=settings.language, response_format="pcm" if streaming else "wav", sample_rate=24000, max_new_tokens=settings.max_new_tokens, extra_params=dict(temperature=settings.temperature, top_p=settings.top_p, top_k=settings.top_k, repetition_penalty=settings.repetition_penalty))
        if settings.seed >= 0:
            body["seed"] = settings.seed
        if streaming:
            body.update(stream_format="audio", initial_codec_chunk_frames=4)
        if settings.mode == "clone":
            body.update(ref_audio="file://" + linux_path(self.data / "voices" / (settings.voice_id + ".wav")), ref_text=reference["text"] or None, x_vector_only_mode=settings.x_vector_only)
            if instructions:
                body["instructions"] = instructions
        else:
            body["instructions"] = settings.description
        return body

    def prime(self, settings, reference=None):
        key = (settings.mode, settings.voice_id, settings.x_vector_only, settings.description, (reference or {}).get("text"))
        with self._lock:
            if self._primed_key == key or self.active:
                return
            # Populate the speaker cache and request-time kernels before the first read.
            self.synthesize("你好。", settings.model_copy(update={"max_new_tokens": 128}), reference)
            self._primed_key = key

    def begin(self, mode):
        with self._lock:
            self.ensure(mode)
            self.active += 1

    async def open_stream(self, text, settings, reference=None, is_disconnected=None, instructions=None):
        await asyncio.to_thread(self.begin, settings.mode)
        client = None
        response = None
        try:
            if is_disconnected and await is_disconnected():
                raise RuntimeError("朗读请求已取消。")
            client = httpx.AsyncClient(trust_env=False, timeout=httpx.Timeout(120, connect=5))
            request = client.build_request("POST", self.url + "/v1/audio/speech", json=self.payload(text, settings, reference, streaming=True, instructions=instructions))
            response = await client.send(request, stream=True)
            if not response.is_success:
                message = (await response.aread()).decode("utf-8", errors="replace")[:1500]
                raise RuntimeError("流式语音生成失败：" + message)
            return client, response
        except BaseException:
            if response is not None:
                await response.aclose()
            if client is not None:
                await client.aclose()
            self.finished()
            raise

    def finished(self):
        with self._lock:
            self.active = max(0, self.active - 1)
            self.last_used = time.monotonic()

    def synthesize(self, text, settings, reference=None, instructions=None, max_bytes=None):
        self.begin(settings.mode)
        try:
            with httpx.Client(trust_env=False, timeout=600) as client:
                if max_bytes is not None:
                    audio=bytearray()
                    with client.stream('POST',self.url+'/v1/audio/speech',json=self.payload(text,settings,reference,instructions=instructions)) as response:
                        if not response.is_success:raise RuntimeError('语音生成失败。')
                        for part in response.iter_bytes(65536):
                            if len(audio)+len(part)>max_bytes:raise ValueError('语音数据超过上限。')
                            audio.extend(part)
                    return bytes(audio)
                response = client.post(self.url + "/v1/audio/speech", json=self.payload(text, settings, reference, instructions=instructions))
                if not response.is_success:
                    raise RuntimeError("语音加速生成失败：" + response.text[:1500])
                return response.content
        finally:
            self.finished()
