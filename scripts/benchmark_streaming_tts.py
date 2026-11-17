"""Measure actual PCM arrival and simulated continuous playout through Saya.

The browser's real AudioWorklet underrun counter is checked separately.
"""
import argparse
import json
import re
import time
import wave
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / ".runtime/tts-optimized"
PARAGRAPH = "博士，先坐下来休息一会儿吧。今天的任务可以分成几个小步骤，不需要一次全部完成。我们先整理最重要的事情，再确认每一步需要哪些材料和工具。遇到不确定的地方，就停下来检查，慢慢把问题弄清楚。你已经付出了很多努力，剩下的事情我们一起安排。等忙完以后，可以喝一杯温水，看看窗外，给自己留一点安静的时间。明天还有新的机会，今天先把这一件事做好就足够了。"


def split_text(text, limit):
    chunks, buffer = [], ""
    for sentence in re.findall(r"[^。！？.!?\n]+[。！？.!?]*\s*", text):
        if buffer and len(buffer + sentence) > limit:
            chunks.append(buffer.strip())
            buffer = ""
        while len(sentence) > limit:
            chunks.append(sentence[:limit].strip())
            sentence = sentence[limit:]
        buffer += sentence
    if buffer.strip():
        chunks.append(buffer.strip())
    return chunks


def playout(arrivals, buffer_seconds):
    start = None
    delivered = 0.0
    available_until = 0.0
    gaps = []
    for packet in arrivals:
        delivered += packet["audio_seconds"]
        if start is None:
            if delivered < buffer_seconds:
                continue
            start = packet["seconds"]
            available_until = start + delivered
        else:
            if packet["seconds"] > available_until + 0.01:
                gaps.append(packet["seconds"] - available_until)
                available_until = packet["seconds"]
            available_until += packet["audio_seconds"]
    if start is None and arrivals:
        start = arrivals[-1]["seconds"]
    return dict(simulated_first_playback_seconds=round(start, 3) if start is not None else None, simulated_gap_count=len(gaps), simulated_gap_seconds=round(sum(gaps), 3))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    results = []
    with httpx.Client(trust_env=False, timeout=600) as client:
        status = client.get(args.url + "/api/tts/status").json()
        if status.get("engine") != "vllm_omni":
            raise RuntimeError("Streaming accelerator is not active")
        settings = client.get(args.url + "/api/tts/settings").json()
        start = time.perf_counter()
        response = client.post(args.url + "/api/tts/load", json={})
        response.raise_for_status()
        loading = time.perf_counter() - start
        cases = [("warmup", "博士，你回来啦。今天辛苦了，先休息一下吧。"), ("medium", PARAGRAPH), ("long", PARAGRAPH * args.repeat)]
        for label, text in cases:
            packets, content = [], bytearray()
            started = time.perf_counter()
            segments = split_text(text, settings["chunk_chars"])
            for index, segment in enumerate(segments):
                with client.stream("POST", args.url + "/api/tts/stream", json={"text": segment, "settings": settings}) as response:
                    if response.status_code != 200:
                        raise RuntimeError(response.read().decode("utf-8", errors="replace"))
                    for piece in response.iter_bytes():
                        if piece:
                            packets.append(dict(seconds=time.perf_counter() - started, audio_seconds=len(piece) / 48000, segment=index))
                            content.extend(piece)
            elapsed = time.perf_counter() - started
            duration = len(content) / 48000
            if not content or len(content) % 2:
                raise RuntimeError("Invalid PCM output")
            with wave.open(str(OUT / (label + ".wav")), "wb") as wav:
                wav.setparams((1, 2, 24000, 0, "NONE", "NONE"))
                wav.writeframes(content)
            result = dict(label=label, chars=len(text), segments=len(segments), generated_seconds=round(elapsed, 3), audio_seconds=round(duration, 3), rtf=round(elapsed / duration, 3), first_packet_seconds=round(packets[0]["seconds"], 3), packets=len(packets), **playout(packets, settings["stream_buffer_seconds"]))
            results.append(result)
            print(json.dumps(result, ensure_ascii=False), flush=True)
            (OUT / "benchmark.json").write_text(json.dumps(dict(load_seconds=loading, settings=settings, results=results), ensure_ascii=False, indent=2), encoding="utf-8")
            (OUT / (label + "-arrivals.json")).write_text(json.dumps(packets), encoding="utf-8")


if __name__ == "__main__":
    main()
