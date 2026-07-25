# Saya Project Cleanup Guide

This file documents which folders are active runtime assets and which ones are safe candidates for cleanup.

## Do Not Delete

- `models/`: local GGUF and vision models used by the backend.
- `knowledge_base/`: persistent memory, check-ins, projects, goals, emotion state, legacy sessions, and exports.
- `knowledge_base/video_recommendations.json`: saved Bilibili/YouTube creators, auto-open setting, and cached recommendations.
- `static/`: the web UI, JavaScript, CSS, Three.js/MMD libraries, generated TTS audio, and served upload images.
- `saya_app/`: shared backend package for configuration and model runtime.
- `server5-22B----.py`: current FastAPI service entry used by `uvicorn`.
- `core_services.py`: current service implementations for memory, projects, knowledge base, check-ins, vision, emotion, motion, and goals.
- `start_saya.bat`: local launcher.
- `DEAR_ROOT\MMD`: MMD model and motion files mounted at `/MMD`.
- `DEAR_ROOT\Talk\memory\chats`: JSONL chat history used by `MemoryManager`.
- `DEAR_ROOT\Saya\saya.q5_k_m.gguf`: optional Saya R1 local model configured by default.

## Safe Cleanup Candidates

- `__pycache__/`: generated Python bytecode cache.
- `logs/`: old logs, if you do not need historical debugging records.
- `vision_engine.log` and `server_vision.log`: old runtime logs, if not needed.
- `static/uploads/`: user-uploaded files and images. Keep anything you still reference in conversations.
- `knowledge_base/exports/`: generated check-in report exports. Back up first if you care about old reports.
- Screenshot/image files in the project root, if they are not referenced by the UI or documentation.

## Be Careful

- `knowledge_base/*.json`: deleting these resets memories, goals, check-ins, projects, or legacy sessions.
- `static/libs/`: looks like vendored frontend dependencies; do not delete unless the HTML switches to CDN-only.
- `static/tts_output.mp3`: generated output; safe only if you do not need the current cached audio.
- `0.12.0`: unclear artifact. Inspect before deleting.
- `main.py` and `index.py`: older helper entry/index scripts. They are not the current launcher target, but keep them until you finish cleanup planning.
