### Easy start.
uvicorn server5-22B----:app --host 127.0.0.1 --port 8000

http://127.0.0.1:8000/static/Chat4o5-Modern.html?v=g13-20260811-citation-memory

### Backend layout notes
- `server5-22B----.py` is still the active FastAPI entry point.
- `core_services.py` still contains the current service implementations.
- `saya_app/config.py` now centralizes project paths, model paths, and default API/model settings.
- `saya_app/model_runtime.py` owns local GGUF model loading/unloading state.
- `saya_app/routers/` contains domain-specific FastAPI routers for memories, check-ins, goals, projects, media, motion, emotion, and external integrations.
- `saya_app/services/video_recommendations.py` stores creator subscriptions and ranks Bilibili/YouTube updates for the Tools -> Video Recommendations panel.
- `docs/backend-layout.md` explains where future backend features should be added.
- `docs/cleanup-guide.md` lists runtime-critical folders and safe cleanup candidates.

### Optional service configuration
Set secrets through environment variables instead of writing them into HTML or Python:
- API_KEY / API_URL / API_MODEL
- ELEVEN_API_KEY / ELEVEN_VOICE_ID
- SECRET_ID / SECRET_KEY (Tencent translation)

### Memory System 2.0
Memory entries remain compatible with the old `knowledge.json` format, but now gain extra metadata automatically:
- category, tags, confidence, importance_score
- source_detail / evidence
- access_count / last_accessed
- supersedes / conflicts_with for simple correction tracking

The chat prompt now retrieves relevant memories instead of dumping every memory into context. In the UI:
- View Memories: search, category filter, archived toggle, rebuild metadata
- Modify Memory Settings: enable/disable Memory 2.0 retrieval, set relevant-memory count, choose whether prompt metadata is included
- Conversation "Summarize to Episodic" creates an active Episodic summary. Archive is reserved for retired, compressed, replaced, or manually archived memory records.

Auto-save still only captures explicit requests such as "请记住..." or "remember that..."; it does not guess private facts silently.

### API settings
The top model selector still contains "API", but API URL / API key / model name are configured from:
Settings -> API Settings

The values are saved locally in the browser. Use "Forget Saved API Information" to clear them.

### Daily check-in
The daily check-in entry is hidden behind the movable 3D model area, above the conversation history. Drag the 3D model away to reveal the card.

Data is stored in:
- knowledge_base/checkins.json

Features:
- fixed daily tasks
- one-day tasks for the selected date
- fixed task frequencies: daily, weekdays, weekends, weekly, monthly
- mood / energy / difficulty daily ratings
- daily completion colors: green = completed, yellow = partial, red = missed
- month and year summaries
- month/year report export to Markdown and JSON backup files under knowledge_base/exports
- "Talk with Saya" sends the current day's check-in plus month/year summary as hidden context, so the visible chat stays natural.
- The main page maintains one protected current-day memory with check-in progress, page-entry count, and completed conversation count. It updates in place instead of accumulating one important memory per day.

### Web citation verification
Web-assisted replies are checked after generation without another model call. The local verifier maps each numbered claim to a source, checks lexical and numeric agreement, and stores only a bounded supporting excerpt. Results appear under the answer and are restored with chat history. Because this uses no additional LLM tokens, there is no Mode Setting switch for it.

### Projects and chat search
- Sidebar Search searches JSONL chat history and opens matching sessions.
- Hypnosis and Tool entries are placeholders and currently show "under development".
- Projects are stored in knowledge_base/projects.json.
- New chats created while a project is selected are attached to that project.
- Chats inside a project inject recent excerpts from sibling project chats as Project Memory for stronger continuity.

### DIY ways
#### change the mmd model
If you want to change the model
1.put model file in "Dear\MMD"
2.change Chat :
  const loader = new THREE.MMDLoader();
  loader.load(
    'http://127.0.0.1:8000/MMD/TDA Default Miku (Xoriu Ver.)/TDA Default Miku (Xoriu Ver).pmx',
    mesh => {
      // 根据需要缩放模型
      mesh.scale.set(0.1, 0.1, 0.1);
      mesh.position.y = -1.5;
      scene.add(mesh);
      animateMMD();
    },
    xhr => console.log(`模型加载: ${(xhr.loaded/xhr.total*100).toFixed(1)}%`),
    err => console.error('MMD 模型加载出错：', err)
  );
3.change server-22B :
