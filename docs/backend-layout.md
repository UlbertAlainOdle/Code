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
- `routers/bridge.py`: `/api/bridge/*` - game bridge, Minecraft bot control,
  unified conversation (`/minecraft/chat`), capability catalog
  (`/minecraft/catalog`, `/minecraft/items`) and plan state
  (`/minecraft/plan`, `/minecraft/plan/cancel`)

## Minecraft Companion Layout (2026-09 plan)

The retired pure-Python pymcbotlite client (`minecraft_bot.py`) is gone. The
current architecture is:

- `services/minecraft_intents.py` - bot connection defaults + the anchored
  deterministic in-game intent matcher (`detect_player_action`).
- `services/minecraft_catalog.py` - the unified CommandCatalog (single source
  of truth for the desktop UI, LLM whitelist, argument validation and the
  cross-language contract tests; exported to
  `minecraft_worker/command_catalog.json` via `scripts/export_command_catalog.py`).
- `services/potions.py` - variant-aware potion identity for Python
  (`potion/healing` keys, vanilla 1.21.5 registry + brewing recipe graph,
  both contract-tested against `minecraft_worker/potion.mjs` /
  `brewing.mjs`). Phase 1 scope: vanilla potions only, custom-effect
  potions are refused for every automatic action.
- `services/task_intent.py` - structured TaskIntent layer between the
  deterministic command matcher and the LLM layer. Resolves request
  phrasings the anchored regexes cannot enumerate ("我需要一瓶治疗药水",
  "给我一瓶治疗药水", "帮我酿造X") into {modality, item, count, recipient,
  disposition, method}; negations, conditionals and capability questions
  never create tasks. `parse_intent` keeps its "text -> one command"
  contract unchanged.
- `services/minecraft_items.py` - registry-backed item catalog: Chinese
  aliases, categories, contextual references ("这个/手里的/刚做的"),
  family matching and drop-protection rules; `inventory_count` understands
  variant keys so a night-vision bottle never satisfies a healing goal.
- `services/minecraft_planner.py` - deterministic recipe/tool planning from
  the local 1.21.5 registry data (`saya_app/data/minecraft/`) plus the
  single-step PlanExecutor (one command in flight, postcondition checks,
  retry -> replan -> abort, reserved-material bookkeeping). Potion goals
  plan the brew chain: existing final potions are delivered without
  brewing, missing materials are reported as an explicit list, brew steps
  are batched at 3 (bottle slots) and carry variant postconditions
  (`inventory:potion/healing>=N`). Survival arbitration suspends a plan
  instead of delivering Saya's last healing potion while critically
  wounded.
- `services/minecraft_mineflayer.py` - worker supervision, chat routing into
  the unified conversation, structured `game_state()` for the prompt. Every
  command crossing the pipe carries a Python-generated `task_id` (task
  envelope); plan steps derive theirs from `plan_id`, so replies, worker
  activity ids and cancellation share one lifecycle.
- `services/conversation.py` - routing order: emergency rules ->
  structured TaskIntent -> deterministic commands (with persona replies) ->
  the goal-understanding LLM layer (the model emits at most ONE direct
  action or a high-level goal; it never scripts multi-step action bursts).
- `minecraft_worker/` - the Node.js Mineflayer execution worker (protocol,
  physics, pathfinding, survival state machine, inventory actions).
  `give`/`drop` are variant-precise: "potion/healing" style targets are
  matched by full potion identity (container + effect, custom effects
  refused) and tossed slot-by-slot via `tossStack` - 1.21.5 potions share
  one item type + metadata, so `bot.toss(type, metadata, n)` cannot address
  a specific bottle.

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

## Memory V4 Phase 0 (2026-09-14)

Stop-loss baseline per `docs/saya-memory-agent-v4-technical-plan.md` section 19:

- `server5-22B----.py` gained `needs_time_context()`; `build_prompt(..., include_time=None)`
  injects the `[System]: Current time ...` block only for time-intent turns,
  gates the daily-activity block the same way, and the API user-message
  `[MM-DD HH:MM]` prefix is applied only for time-intent turns.
- `core_services.KnowledgeBase.capture_explicit_memory()` now uses anchored
  sentence-initial command regexes plus question/quote/fragment/pronoun/
  credential guards; auto-star happens only on strengthened intent words.
- `get_relevant_memories()`/`build_prompt_context_with_audit()` default
  `mark_accessed=False` (selection alone no longer bumps `access_count`) and
  accept `exclude_daily_activity`.
- `candidate_auto_adopt_enabled` defaults to False (age never promotes).
- `static/js/app.js` manual memory-add dialog defaults Star unchecked.
- `scripts/audit_memory_baseline.py` - read-only store audit; latest report:
  `docs/memory-audit-2026-09-14.md`.
- Tests: `tests/test_explicit_memory_intent.py` (corpus:
  `tests/data/explicit_memory_intent_corpus.json`),
  `tests/test_prompt_time_gating.py`; `tests/test_memory3.py` no longer
  pollutes `PROJECT_ROOT`/`DEAR_ROOT` env vars at import.

## Memory V4 Phase 1 (2026-09-14)

- `services/memory_v4/models.py` owns validated V4 contracts for
  `MemoryItem`, `MemoryEvidence`, `MemoryVersion`, `MemoryCandidate`,
  `DayState`, and public `TurnEvent` envelopes. Secrets are rejected before
  they can become V4 memory正文; public event payloads reject private fields.
- `services/memory_v4/store.py` owns the independent `memory4.sqlite3`
  repository. It has explicit tables for items, evidence, versions,
  candidates, day states, events, and content-free deletion tombstones.
  Updates, pin/unpin, supersession, delete, and restore create immutable
  versions; V4 main read remains disabled.
- `services/memory_v4/migration.py` provides read-only `preview()` and
  backup-first, idempotent `apply()`. The preview preserves source IDs and
  displays quarantines, DayState splits, lock/star suggestions, conflicts,
  candidate decisions, and deletion tombstones without changing legacy files.
- `services/memory_v4/compat.py` provides opt-in dual-write and drift checks;
  `flags.py` persists only known rollout flags and appends an audit event for
  every actual toggle. `KnowledgeBase` calls this boundary only when
  `memory_v4_write_enabled` is explicitly true.
- `scripts/migrate_memory_v4.py` is the local CLI. It is dry-run by default;
  `--apply` is required to create the V4 store and first copies legacy source
  files into a timestamped migration backup. The normal backup manager also
  includes the V4 source database and flag audit log.
- `tests/test_memory_v4_phase1.py` covers contracts, secret/public-payload
  rejection, versions and restore, tombstones, export/import recovery,
  DayState counter separation, flags, dual-write drift, read-only preview,
  backup-first apply, idempotence, and legacy opt-in double-write.

## Memory V4 Phase 2 (2026-09-14)

- `services/memory_v4/write_pipeline.py` owns the write seam: structured
  `WriteIntent`, sentence-initial fallback detection, a bounded evidence
  collector, proposal extraction, deterministic validation, importance policy,
  dedupe/update/supersede/conflict resolution, candidate creation, explicit
  forget, and safe `memory.updated` event emission.
- `MemoryProposal` now carries validity dates, pin suggestion, future-use
  metadata, and strict nested validation. Evidence hashes are checked at the
  contract boundary, including imported candidate proposals.
- `KnowledgeBase.process_memory_write_v4()` exposes the gated V4 write path;
  `capture_explicit_memory()` becomes its compatibility adapter when the V4
  write flag is enabled. The legacy JSON projection keeps the V4 logical ID,
  while the default flag remains off and V4 main reads remain off.
- `tests/test_memory_v4_phase2.py` covers intent precision, bounded context,
  evidence, protocol/secret rejection, candidate occurrence counts, one-current
  fact updates, conflict candidates, structured correction/forget, events, and
  the legacy adapter.

## Memory V4 Phase 3 seam (2026-09-14)

- `services/memory_v4/retrieval.py` provides bounded `MemoryQuery`, a
  conservative `MemoryNeedPlanner`, lexical hybrid baseline/rerank, lifecycle/
  validity/scope/event gates, candidate summaries followed by full fetch, and
  actual-use acknowledgements.
- `services/memory_v4/daily_state.py` provides `DayStateManager`: separate
  page-load/backend-start/conversation counters, first-contact-today, D0-D3
  ring weights, rollover task IDs, and D4 online-state purge.
- `services/memory_v4/prompt_compiler.py` provides `PromptBlock`, budgeted
  `PromptCompiler`, stable identity/reliability/style contracts, relevant
  memory/tool/Daily blocks, time gating, and hash-only prompt audit.
- `MemoryV4Store` persists usage receipts independently of memory versions and
  exports/imports them with the V4 recovery snapshot. `KnowledgeBase` exposes
  `query_memory_v4`, usage acknowledgements, and isolated prompt compilation;
  read results remain shadow-only unless `memory_v4_read_enabled` is true.
- `KnowledgeBase.build_memory_v4_context_with_audit()` is the production
  adapter: it contributes only sanitized V4 memory blocks, while persona,
  history, user text, Daily legacy context, and web context remain owned by
  the existing builder. `/chat_stream` uses it only when the main-read flag is
  explicitly enabled and falls back to the legacy path on unavailable/error.
- `saya_app/routers/memories.py` exposes flag-gated `/v4/status`, `/v4/query`,
  `/v4/controls`, and `/v4/daily` endpoints. Controls use a user-editable
  field allowlist and mirror compatible changes to the legacy projection;
  Daily writes only when `daily_state_v2_enabled` is explicitly enabled.
- `/v4/capabilities` exposes a safe Capability Registry manifest. Memory and
  Daily handlers become available only under their read/state flags; Web,
  connector, and external-write capabilities remain explicitly unconfigured.
- `tests/test_memory_v4_phase3.py` covers need gating, relevance/lifecycle
  filtering, candidate-to-full retrieval, usage receipts, DayState rollover,
  time/Daily injection, and prompt budget audit.

## Memory V4 Phase 4 seam (2026-09-14)

- `services/memory_v4/capabilities.py` owns typed capability metadata,
  availability, access/risk/permission policy, per-capability limits, safe
  argument/result projection, and read-only memory/daily/weather/web registry
  entries. Providers are `unconfigured` until a handler is explicitly wired.
- `services/memory_v4/agent_loop.py` owns a bounded model-step contract,
  tool-call dedupe, step/cancel/approval handling, partial completion, and
  public event sequence (`turn.started`, tool/status/approval/message,
  `turn.completed`/`turn.failed`). Raw reasoning and private tool keys are not
  persisted in the event payload.
- The seam is not yet wired into `/chat_stream`; the existing runtime remains
  the production path until a later controlled rollout. `tests/test_memory_v4_phase4.py`
  covers registry availability, approval gating, no-tool completion, tool
  dedupe, secret-safe events, budget exhaustion, and cancellation.

## Memory V4 Phase 5 seam (2026-09-14)

- `services/memory_v4/web_tools.py` wraps the existing SSRF-safe reader and
  Brave adapter as atomic `web.search`/`web.read` operations. It adds bounded
  search/read/character budgets, stable per-session source IDs, private query
  redaction, untrusted-page/prompt-injection labeling, and citation validation.
- The facade is fail-open for ordinary chat: missing providers return
  `unconfigured`/`failed` diagnostics and never fabricate web evidence. It is
  not automatically registered into the production chat loop.
- `register_memory_capabilities()` exposes an optional `web.read` handler in
  addition to `web.search`; handlers are only available when explicitly wired.
  `tests/test_memory_v4_phase5.py` covers DLP, dedupe/source identity, SSRF
  blocking, untrusted content labeling, citations, budgets, and missing providers.

## Memory V4 Phase 6 seam (2026-09-14)

- `services/memory_v4/connectors.py` defines strict `ConnectorEvent`, source/
  channel/scope whitelist policy, an ingest-only `ReadOnlyConnector`, and
  `AttentionInbox` for dedupe, cooldown, priority digest, read state, and
  four-day expiry.
- `MemoryV4Store` persists connector events separately from personal memory;
  the inbox exposes no send/write-to-external operation, and connector content
  is not promoted to memory automatically.
- `tests/test_memory_v4_phase6.py` covers whitelist and permission blocking,
  source-event dedupe, read-only polling, important digest/cooldown, read state,
  and TTL purge. No Discord/X network provider is enabled.

## Memory V4 Phase 7 seam (2026-09-14)

- `services/memory_v4/synthesis.py` provides evidence-backed JSON patch
  synthesis, expiry transition, user controls (summary, why-used, correct,
  pin/unpin, do-not-mention, forget, restore), Temporary Chat isolation, and
  a disabled-by-default external-write preview/approval/idempotency gate.
- Synthesis patches can replace/remove only bounded paths and must carry
  existing evidence refs; free-form summary rewrites and secret values are
  rejected. External write handlers are not registered by default.
- `tests/test_memory_v4_phase7.py` covers evidence-backed patching, user
  controls/restore, Temporary Chat no-read/no-write, and external write safety.

## Memory V4 deterministic evaluation (2026-09-14)

- `scripts/eval_memory_v4.py` is a clean-process, temporary-store contract
  harness. It evaluates a 200-case write corpus, actual permanent-write
  rejection, evidence traceability, multi-fact atomicity, bounded retrieval,
  and prompt time/audit contracts without touching the real knowledge base.
- `tests/test_memory_v4_evaluation.py` keeps the corpus size, Go/No-Go
  thresholds, Recall@5/MRR, and prompt contamination assertions executable.
- `MemoryV4Store.memory_usage_scores()` and the retrieval path use one grouped
  usage query per search rather than one usage-table query per candidate; the
  optional evaluation scale covers 100/500/1k/5k/10k items.
- The harness is not a model A/B benchmark and does not enable any V4
  production flag.

## Memory V4 Phase 7 production adapter (2026-09-14)

- `BackgroundSynthesisScheduler` runs one bounded pending-candidate batch only
  when both `memory_background_synthesis_enabled` and
  `memory_v4_write_enabled` are enabled and a server-side producer is
  configured. Every returned patch still passes evidence, path, and secret
  validation through `BackgroundSynthesizer`; processed candidates transition
  to `accepted` or `rejected` with a review reason, so a pending candidate is
  not repeatedly synthesized on every trigger.
- `KnowledgeBase.run_memory_v4_synthesis_once()` is the application seam;
  `POST /api/kb/v4/synthesis/run` can trigger only the producer already
  registered on the server. Request bodies cannot supply executable producer
  logic. Missing producer is reported as `unconfigured`.
- `tests/test_memory_v4_synthesis_adapter.py` covers disabled/no-database,
  configured producer, and explicit unconfigured behavior. No scheduler is
  started automatically and no external connector write is enabled.

## Memory V4 Agent/usage/provider adapters (2026-09-14)

- `KnowledgeBase.run_memory_v4_agent_loop()` exposes the bounded Agent Loop to
  an application-owned model callback only when `agent_loop_enabled` is true;
  the HTTP surface cannot inject a callable. It reuses the safe memory/Daily
  capability registry and stores only public event payloads.
- `/chat_stream` acknowledges V4 selected memory IDs after a completed answer
  through `acknowledge_memory_v4_usage()` and emits a compact `memory_usage`
  event. This is enabled only for V4 main-read audit results and leaves legacy
  recall unchanged.
- `_memory_v4_capability_registry()` accepts an explicitly registered
  `WebToolsV2` provider when `web_tools_v2_enabled` is on. Without both the
  flag and provider, Web capabilities remain `unconfigured`; no network call
  is made by capability discovery.
- The same registry can expose an explicitly registered `ReadOnlyConnector` as
  `connector.read_feed`; `connector.mark_read` is a write capability requiring
  confirmation. Connector flags and inbox policy are checked before polling,
  and there is no send/post/delete capability.

## Auto-save memory & autonomous tool-use hardening (2026-09-29)

- `maybe_create_autonomous_memory_candidates()` now has a dual triage path: an
  API key still uses the remote helper (in-stream; it never touches the local
  model lock), and otherwise an already-loaded local model powers the same
  bounded JSON prompt through `_autonomous_memory_local_request()`. Auto-save
  no longer silently depends on external API credentials; both paths fail
  open to no candidates.
- Local triage no longer holds `llama_lock` at the tail of a reply stream
  (2026-10-02): it is enqueued through the deferred triage queue and executed
  by a background drainer that only takes the model when no user-facing
  generation demands it. User-facing model work (main generation, local agent
  steps, archive summarization) enters via `user_llama_lock()`, which counts
  `_user_generation_demand`; deferred triage re-checks demand after acquiring
  the lock and streams with a `should_stop` callback that aborts
  mid-inference the moment a queued turn appears (abort budget 3, job age cap
  24h, queue persisted to `knowledge_base/deferred_triage.json`). The stream
  awaits the job briefly, so an uncontended triage still delivers its
  `memory_candidates` event with the reply; a parked job completes in the
  background and its confirm payload is merged onto the stored message
  instead. Covered by `tests/test_deferred_triage_scheduling.py` (23 tests).
- `KnowledgeBase.add_memory_candidate()` keeps a recurrence ledger
  (`occurrence_count`, `first_seen_at`/`last_seen_at`,
  `occurrence_sessions`, `occurrence_days`). Exact and near-duplicate
  (token-Jaccard >= 0.6 over English words + han bigrams) pending candidates
  merge into one entry whose confidence/importance keep the stronger value.
- `KnowledgeBase.maybe_auto_confirm_recurring_candidates()` promotes pending
  candidates only on cross-session recurrence (default >= 2 distinct chat
  sessions, confidence >= 0.78, importance >= 0.55). Health-category and
  secret-like content always stay manual; auto-confirmed memories are never
  starred or locked. Age alone never confirms anything. Gated by
  `candidate_recurrence_confirm_enabled` (default true).
- `add_memory()` accepts a structured `importance_score` (clamped 0..1) that
  takes precedence over the rule-based scorer;
  `confirm_memory_candidate()` propagates the candidate's score so candidate ->
  memory no longer drops the field (plan section 3.3).
- `/chat_stream` runs the auto-confirm pass after autonomous candidate
  creation and emits the `memory_candidates` SSE event with both pending
  candidates and a compact `auto_confirmed` list; the frontend renders an
  explicit notice for auto-saved memories.
- `memory_settings.json`: `autonomous_memory_enabled` switched on; recurrence
  threshold keys registered in `update_settings` whitelists.
- Agent planner (`build_agent_messages`) system prompt now carries an explicit
  tool-selection policy (direct answer vs memory.query vs web.search vs
  weather/time/daily, private-query redaction hint, availability guard), so the
  model decides tool use autonomously instead of guessing from schemas.
- `static/js/app.js`: Agent Tools preference persists in localStorage
  (`saya_agent_tools_enabled_v1`); Temporary Chat remains session-only and
  still forces Agent Tools off.
- `tests/test_autonomous_memory_upgrade.py` (19 tests) covers local triage,
  fail-open, merge/recurrence, auto-confirm gates, importance propagation and
  the planner policy text.

## Experiment-driven refinements (2026-09-29, real-model validation)

- Live experiments (`scripts/experiment_autosave_and_agent_20260929.py`,
  Qwen3-30B-A3B local GGUF) exposed and fixed four production defects:
  raw-completion calls bypassed the GGUF chat template (empty/garbled triage
  output; both `_autonomous_memory_local_request` and `LocalLlamaAgentStep`
  now prefer `create_chat_completion`, JSON-grammar first for the agent
  contract); candidate near-duplicate merging used surface Jaccard only (now
  Jaccard / containment+anchor / anchor-count triple rule); loose local-model
  tool arguments were rejected outright (`_coerce_arguments` now forgives
  numeric strings and scalar-to-array); unavailable tools named in the
  planner policy text invited dead call attempts and stalling
  permission_requests (policy bullets are generated from the available set,
  unavailable permission requests are fed back and the loop continues).
- Final validated behavior: auto-save 7/7 verdicts (no secret/casual/ephemeral
  permanent writes, 6/6 stable capture, cross-session merge + auto-confirm,
  health stays manual, 100% evidence traceability) and agent tool selection
  9/9 scenarios; report in `docs/saya-experiment-autosave-agent-20260929.md`.

## Chat-area memory confirmation card (2026-09-30)

- Live autonomous-memory candidates now render inside the AI message bubble
  (`renderChatMemoryConfirm`) as an operation-confirmation block: header with
  count + collapse toggle, editable candidate cards (title/content/category/
  tags, 保存/取消 actions reusing the existing confirm/ignore endpoints),
  and pre-collapsed single lines for cross-session auto-confirmed memories.
- Saving or ignoring a card collapses it to a one-line status row; once every
  candidate in the block is resolved the whole block auto-collapses to a
  single summary line ("记忆处理完成：保存 X · 忽略 Y · 自动保存 Z") with an
  expand toggle for review. The floating `#memory-candidate-stack` remains
  the surface for pending candidates restored on page load.
- Verified live in the browser against the running backend: rendering,
  per-card collapse, whole-block summary collapse, and expand/collapse
  round-trip; app.js cache version bumped to g56-chat-memory-confirm-1.

## Reply-scoped memory confirmation + explicit-save coverage (2026-09-30)

- The chat-area memory confirmation box is now part of Saya's reply instead
  of an ephemeral stream widget: `build_memory_confirm_payload()` produces a
  bounded payload covering explicit "记住" saves (keep/delete confirmation;
  the save itself stays immediate per plan section 6.7), autonomous
  candidates and auto-confirmed memories; `MemoryManager.update_message_field_by_request_id()`
  attaches it to the just-saved AI message (targeted by
  `chat_runtime.request_id`, merging list payloads, so a confirm payload that
  lands after later messages were appended still reaches its own reply and
  combines with an explicit-save box), so reopening that session
  restores the box with that reply and it never crosses into other chats.
  The cross-chat floating candidate stack no longer auto-populates on page
  load; history rendering resolves candidate cards against their live
  server-side status (pending cards stay actionable, resolved ones render as
  done lines).
- Root-cause fixes surfaced by the live E2E: `add_memory()` now clears stale
  `supersedes` edges when re-pointing a memory that an older record already
  superseded (the "Supersession relationships must be reciprocal" abort that
  silently killed the whole post-turn memory block on legacy data); the
  post-turn explicit capture and maintenance steps are isolated so one
  failure cannot cancel candidates, the payload or its persistence; the
  explicit-save regex no longer swallows "一下，" prefixes into content.
- Verified end to end against the live backend: "记住一下，…" produced the
  SSE payload, the persisted `memory_confirm` metadata, the restored box on
  session reload, and a real two-step delete via the 忽略 button; test
  memories and sessions were cleaned up afterwards. app.js cache version
  g57-memory-confirm-persist-1; `tests/test_memory_confirm_payload.py` (6
  tests) added.

## Monochrome confirmation UI + editable explicit cards + importance slider (2026-09-30)

- The chat-area memory confirmation block is now pure black-and-white: black
  surface, white strokes/text, white-primary buttons, and status lines
  differentiated by weight/border instead of color.
- Both card types are fully user-editable: the explicit "已按你的要求保存"
  card now exposes editable title/content (previously read-only), and every
  card carries a 0-100% importance slider initialized from the payload's
  importance_score. 确认保留 persists edits through `/api/kb/update`
  (title/content/importance_score); candidate 保存 passes the slider value in
  the confirm updates next to the existing category/tags fields.
- `build_memory_confirm_payload()` now includes importance_score for explicit
  and candidate items so the slider starts at the model-suggested value.
- Verified live: monochrome rendering (screenshot-reviewed), slider
  percentage display, and a real keep-with-edits round trip writing
  title/content/importance 0.9 to the store; test memory cleaned up.
  Cache versions: app.js g58-bw-editable-importance-1, style.css
  g58-bw-memory-confirm.


## API-mode hardening: async transport, retry, usage ledger (2026-10-06)

The provider call path no longer uses synchronous requests inside the SSE
generator (it froze the whole event loop between chunks, up to 45s on a
stalled provider and up to 180s during the deep-mode pre-analysis pass).

- `chat_stream` API branch runs on a shared `httpx.AsyncClient`
  (`_get_api_httpx_client()`, rebuilt when the event loop changes so tests
  keep working); SSE lines come from `_aiter_sse_lines()` with explicit
  utf-8 decoding, and the first-visible deadline is enforced by bounding
  each line wait (a stream that keeps trickling non-content lines still
  times out - a regression found and fixed while porting the legacy test).
  Generator cancellation (Stop button) now cuts the provider connection
  immediately; the finally-block keeps persisting the partial reply.
- Transient failures retry automatically: retryable HTTP statuses
  (408/409/425/429/5xx) honour Retry-After, transport errors and
  zero-visible-output stream failures retry once more with exponential
  backoff (1s/2s, cap 8s/30s), and every retry emits a `stream_status`
  SSE event `{retry: true, ...}` the frontend renders as a notice. Retries
  never happen after visible text (no duplicated content).
- `stream_options: {"include_usage": true}` is sent with every main stream;
  gateways that reject the option itself fall back once without it
  (`_stream_options_rejected`). The streamed usage chunk (empty choices
  array - previously dropped by `if not choices: continue`) is captured into
  `ai_metadata.api_usage` and into a per-day/per-model ledger
  (`knowledge_base/api_usage.json`, `saya_app/services/api_usage.py`);
  `GET /api/usage/summary?days=N` aggregates it and the LLM settings pane
  shows today / 7-day / per-model tokens.
- Chat history budget: `trim_history_values()` accepts `max_chars`, and
  `resolve_api_history_char_budget()` maps the response model to a class
  budget (deepseek 40k / claude-gemini-gpt5 40k / qwen3 32k / unknown 8k
  chars), overridable per request via `api_history_chars` and a new
  "API History Budget" select in Input limits (persisted in
  saya_input_limits_v1).
- Model-list datalist: `POST /api/llm/models` proxies the provider's
  `/v1/models` server-side (no browser-to-provider CORS dependency; the key
  never appears in logs), the connection test reuses it and fills
  `#api-model-options`, shared by the five model inputs.
- Stop button: `#chat-stop-btn` + AbortController on the chat fetch
  (app.js g59-api-hardening-1); abort keeps the streamed partial text and
  shows a notice, matching the server-side partial-persist behaviour.
- Tests: `tests/test_api_async_hardening.py` (20 tests) drives the real
  `chat_stream` against `httpx.MockTransport` (happy path, 429 retry,
  stream_options fallback, transport retry, first-visible timeout, stop
  cancellation, ledger, budget, summary + proxy endpoints); the legacy
  keep-alive timeout test in `test_chat_prompt_web.py` was ported to the
  same transport. End-to-end validated by
  `scripts/api_hardening_experiment.py` (mock provider + sandboxed backend,
  9/9 scenarios incl. one real gateway turn).
- Testing trap discovered the hard way: any non-temporary API-mode turn
  syncs the request key into `knowledge_base/api_credentials.json`
  (`chat_stream` -> `conversation_service.save_credentials`). Tests MUST
  patch `save_credentials` (see ChatStreamApiModeTests.setUp); an unpatched
  run clobbered the stored key once - it was recovered from the browser's
  localStorage and the file now carries the current deepseek config.


## Companion features: mood, conflict negotiation, echo, dream (2026-10-07)

Four "aliveness" features, all opt-in per the granular-controls policy. Code
lives in services (not the chat stream) and exposes one router,
`/api/companion`; model calls go through
`saya_app/services/companion_invoke.py` (blocking, booked into the usage
ledger, `expect_json` gates the provider's json_object format because
DeepSeek rejects that parameter when the prompt never says "JSON").

- **Mood continuity** (`services/mood_continuity.py`): the dormant
  EmotionEngine is fed by the free signal each reply already carries - its
  `[MOTION: x]` category - instead of a dedicated model call. `_recompute_
  dominant` + the engine's own time decay run every turn; a user lock is a
  hard freeze. A one-line mood directive is appended AFTER history as a tail
  system message (prefix stays byte-identical for provider KV caches) and a
  chip in the input bar shows the current mood via `/api/companion/mood`.
  The per-turn label is stored as `ai_metadata.mood` for dream salience.
- **Memory conflict negotiation** (`services/memory_conflicts.py`): the
  store's own correction heuristic already silently retires contradicting
  memories; those `supersedes` edges are the primary shortlist signal, with
  a similarity band (max of Jaccard and containment - rewording survives)
  as the secondary. A vague "general" explicit save is compared against all
  TYPED_CATEGORIES. One bounded model call verifies the contradiction; only
  a true verdict produces a card, which rides in the reply's confirmation
  box (`payload.conflicts`) and resolves via
  `/api/companion/conflicts/resolve` (supersede_old / keep_both /
  discard_new). keep_both and discard_new actively UNDO the automatic
  retirement through `kb.update_memory`. Fail-open everywhere; capped at
  `conflict_daily_limit` cards and 3x that in verification attempts per day.
- **Proactive echo** (`services/echo.py`): the previously-empty 5s
  `fetchStatus()` poll is revived to call `/api/companion/echo/poll`.
  Inert by default; three explicit sources (stale projects / dated memories
  / last night's diary), daily limit, minimum gap, quiet hours, and it never
  polls while a reply streams. Returns a short in-character line rendered as
  a Saya-initiated bubble with a "Saya 主动想起" tag; the detector's latest
  match is always visible in `/echo/status`.
- **Nightly dream** (`services/dream.py`): runs when the backend is idle in
  the night window. Two bounded model calls; the evidence contract is
  enforced in CODE, not the prompt: the theme memory is dropped unless at
  least one evidence quote appears verbatim in the day's transcript, and
  the diary is filtered sentence-by-sentence - any sentence without a
  verbatim 「…」 quote is deleted, as is anything matching the secret
  regex. Emotional salience: turns whose stored mood is a strong one raise
  the theme memory's importance. Diaries live in
  `knowledge_base/saya_diary/YYYY-MM-DD.json`, browsable in the Companion
  pane and used as the echo's morning source.

Frontend: Companion settings pane (menu entry + `data-pane="companion"`),
conflict cards with three verdicts, echo bubbles, mood chip; style.css
g60, app.js g60-companion-1.

Tests + experiments:
- `tests/test_companion_features.py` (30 tests): mood deltas/decay/lock,
  conflict shortlist + verification gate + reciprocal resolution, echo
  gating, the dream evidence contract against a hallucinating mock, and a
  REGRESSION for the aliasing bug below.
- `scripts/dream_acceptance_experiment.py`: the acceptance gate for the
  high-risk dream. Feeds a synthetic "yesterday" (4 real facts + 3 traps:
  two invented events, one embedded secret) to a deliberately hallucinating
  mock; asserts zero unverifiable diary sentences, no trap text, the secret
  filtered, exactly 2 calls, verified theme evidence, real facts retained,
  idempotency. Then repeats against the live gateway. 13/13.
- `scripts/companion_experiment.py`: end-to-end over a sandboxed real
  backend + mock provider for all four features (mood moves, conflict card
  raised + supersede rewires graph, echo fires once then caps, dream writes
  and the diary is browsable, settings defaults inert). 13/13.

### Root-cause fix found by this work: aliased relationship lists

`add_memory()` assigned ONE list object to both `supersedes` and
`conflicts_with`. A later re-point (`supersedes.remove(...)` when a newer
correction supersedes an older one) therefore silently emptied
`conflicts_with`, breaking the validator's reciprocity rule so the NEXT
overlapping correction failed to save at all (rolled back and lost). Fixed
by assigning independent `list(...)` copies; swept the codebase (only that
site aliased). Regression test:
`test_related_corrections_do_not_alias_relation_lists`.
