# Backend Layout

`server5-22B----.py` remains the active uvicorn entry point, but it should now be treated as an app assembly file plus the still-coupled chat/session core.

## Where New Code Goes

- New HTTP endpoints should go in `saya_app/routers/<domain>.py`.
- Shared paths, model paths, default API settings, and environment-variable defaults should go in `saya_app/config.py`.
- Local GGUF model lifecycle changes should go in `saya_app/model_runtime.py`.
- Existing domain logic still lives in `core_services.py` until each service is split into `saya_app/services/`.
- Do not add new feature routes directly to `server5-22B----.py` unless they must share `history`, `llama`, `llama_lock`, or streaming chat internals.

## Current Router Ownership

- `routers/memories.py`: `/api/kb/*`
- `routers/checkins.py`: `/api/checkins/*`
- `routers/goals.py`: `/api/goals*`
- `routers/projects.py`: `/api/projects*`
- `routers/emotion.py`: `/api/status`, `/api/emotion/*`
- `routers/media.py`: uploads and VMD file listing
- `routers/motion.py`: motion suggestion/config endpoints
- `routers/integrations.py`: Tencent translation and ElevenLabs TTS
- `routers/video_recommendations.py`: Bilibili/YouTube creator lists and recommendations

## Still In The Entry Point

These remain in `server5-22B----.py` because they share process-level chat/model state:

- model switch/current model endpoints
- system prompt endpoints
- session/history endpoints
- `/chat_stream`
- `/reset`
- `/set_emotion`
- static app mounting

The next safe refactor is to introduce a `ChatRuntime` state object for `history`, `llama`, and `llama_lock`, then move sessions and chat streaming into routers.
