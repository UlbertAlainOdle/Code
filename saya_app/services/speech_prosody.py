"""Opt-in Base direction experiment and explicit timing-only DSP comparison."""
import asyncio
from dataclasses import asdict, dataclass, replace
import io
import shutil
import subprocess
import tempfile
import wave
from typing import Literal

import anyio

SpeechMood = Literal["auto", "neutral", "happy", "excited", "hyper", "sad", "depressed", "stressed"]

# At most 14% faster / 12% slower; never change pitch, reference or sampling.
PROFILES = {
    "neutral": (1.0, 1.0),
    "happy": (1.08, 1.0),
    "excited": (1.14, 1.0),
    "hyper": (1.14, 1.0),
    "sad": (0.88, 0.78),
    "depressed": (0.88, 0.78),
    "stressed": (1.04, 0.88),
}

# These reach the model's instruction prefix through Omni, rather than changing
# the completed waveform. Base does not officially promise instruction control.
MODEL_INSTRUCTIONS = {
    "neutral": "",
    "happy": "用开心、带着笑意和惊喜的语气说。重音鲜明，语调有轻快的起伏，短语间自然停顿，句末略上扬。",
    # Exact directions from the user's accepted x-vector listening comparison.
    "sad": "用明显悲伤、失落和疲惫的语气说。压低声音，语调沉重，重点处有长一点的犹豫停顿，句尾无力地下沉。",
    "stressed": "用明显紧张、不安和谨慎的语气说。声线收紧，压着声音，重音后短暂犹豫，短句结尾收住，表达克制而绷紧。",
}
MODEL_INSTRUCTIONS.update(excited=MODEL_INSTRUCTIONS["happy"], hyper=MODEL_INSTRUCTIONS["happy"], depressed=MODEL_INSTRUCTIONS["sad"])


@dataclass(frozen=True)
class Prosody:
    mood: str = "neutral"
    tempo: float = 1.0
    gain: float = 1.0
    method: str = "none"
    instructions: str = ""

    @property
    def changed(self):
        return self.tempo != 1.0 or self.gain != 1.0

    def metadata(self):
        return asdict(self)

    def for_stream(self, playback_rate):
        # The PCM worklet's resampling speed shifts pitch. Bake user speed here
        # as well, then tell the player to consume PCM at its normal rate.
        return replace(self, tempo=self.tempo * playback_rate)


def map_mood(settings, mood="neutral"):
    if not settings.mood_prosody or settings.mode != "clone" or settings.provider != "qwen_local":
        return Prosody()
    mood = mood if mood in PROFILES else "neutral"
    if settings.mood_control == "model":
        return Prosody(mood=mood, method="omni_base_direction_v3", instructions=MODEL_INSTRUCTIONS[mood])
    tempo, gain = PROFILES[mood]
    return Prosody(mood, tempo, gain, "pitch_preserving_dsp_v1")


def current_mood(session_id=None):
    # Lazy import: the isolated voice worker and tests must not initialize the
    # entire companion engine. Use the same lock/decay/disable semantics as UI.
    from core_services import emotion_engine, kb_system
    from saya_app.services import mood_continuity
    if not kb_system.settings.get("mood_continuity_enabled", True):
        return "neutral"
    if session_id is not None:
        from core_services import memory_manager
        from saya_app.services import conversation_mood
        state = conversation_mood.snapshot(memory_manager, session_id)
        return state["speech_mood"]
    state = mood_continuity.snapshot(emotion_engine, enabled=True)
    return state.get("dominant", "neutral")


def resolve(settings, mood: SpeechMood = "auto", engine=None, session_id=None):
    if mood == "auto" and settings.mood_prosody and settings.mode == "clone" and settings.provider == "qwen_local":
        mood = current_mood(session_id) if session_id is not None else current_mood()
    plan = map_mood(settings, mood)
    if plan.instructions and engine is not None and engine != "vllm_omni":
        raise ValueError("模型表达实验需要流式加速后端。请切换后端，或选择仅语速/音量演示。")
    return plan


def ffmpeg_binary():
    binary = shutil.which("ffmpeg")
    if not binary:
        raise RuntimeError("韵律处理需要 FFmpeg，请将 ffmpeg 加入 PATH，或关闭情绪韵律并使用播放速度 1。")
    return binary


def command(plan, input_format, rate=24000):
    tempo = plan.tempo
    filters = []
    while tempo < 0.5:
        filters.append("atempo=0.5")
        tempo /= 0.5
    while tempo > 2:
        filters.append("atempo=2")
        tempo /= 2
    if tempo != 1:
        filters.append(f"atempo={tempo:.6f}")
    if plan.gain != 1:
        filters.append(f"volume={plan.gain:.6f}")
    args = [ffmpeg_binary(), "-hide_banner", "-loglevel", "error", "-nostdin"]
    if input_format == "s16le":
        args += ["-f", "s16le", "-ar", str(rate), "-ac", "1", "-probesize", "32", "-analyzeduration", "0"]
    args += ["-i", "pipe:0", "-vn", "-af", ",".join(filters) or "anull",
             "-ar", str(rate), "-ac", "1", "-acodec", "pcm_s16le", "-f", "s16le", "pipe:1"]
    return args


def process_wav(content, plan):
    if not plan.changed:
        return content
    with wave.open(io.BytesIO(content)) as source:
        rate = source.getframerate()
    result = subprocess.run(command(plan, "wav", rate), input=content, capture_output=True, timeout=60,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode or not result.stdout or len(result.stdout) % 2:
        raise RuntimeError("语音韵律处理失败：" + result.stderr.decode("utf-8", errors="replace")[:500])
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setparams((1, 2, rate, 0, "NONE", "NONE"))
        wav.writeframes(result.stdout)
    return output.getvalue()


class PCMProsody:
    """One continuous FFmpeg filter per utterance; OS pipes bound buffering.

    Popen + thread I/O also works with Windows' Selector event loop. Kill the
    process before cancelling pipe tasks so blocked reads/writes can finish.
    """
    def __init__(self, plan):
        self.errors = tempfile.TemporaryFile()
        try:
            self.process = subprocess.Popen(command(plan, "s16le"), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                            stderr=self.errors, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except BaseException:
            self.errors.close()
            raise

    def close(self):
        if self.errors.closed:
            return
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait(timeout=5)
        for pipe in (self.process.stdin, self.process.stdout, self.errors):
            try:
                pipe.close()
            except OSError:
                # Buffered stdin may try to flush into the pipe we just killed.
                pass

    async def chunks(self, source):
        async def feed():
            size = 0
            try:
                async for chunk in source:
                    size += len(chunk)
                    await asyncio.to_thread(self.process.stdin.write, chunk)
                    await asyncio.to_thread(self.process.stdin.flush)
                if size == 0 or size % 2:
                    raise RuntimeError("上游流式音频为空或 PCM 不完整。")
                await asyncio.to_thread(self.process.stdin.close)
            except BaseException:
                if self.process.poll() is None:
                    self.process.kill()
                raise
            finally:
                if hasattr(source, "aclose"):
                    with anyio.CancelScope(shield=True):
                        await source.aclose()

        feeder = asyncio.create_task(feed())
        received = 0
        try:
            while True:
                chunk = await asyncio.to_thread(self.process.stdout.read, 4096)
                if not chunk:
                    break
                received += len(chunk)
                yield chunk
            await feeder
            code = await asyncio.to_thread(self.process.wait, 5)
            if code or not received or received % 2:
                self.errors.seek(0)
                raise RuntimeError("流式韵律处理失败：" + self.errors.read(500).decode("utf-8", errors="replace"))
        finally:
            with anyio.CancelScope(shield=True):
                # Unblock all pipe I/O before waiting on the producer task.
                await asyncio.to_thread(self.close)
                feeder.cancel()
                await asyncio.gather(feeder, return_exceptions=True)
