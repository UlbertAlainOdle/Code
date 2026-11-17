# QQ implementation continuity

When working on QQ features, read `docs/qq-implementation-status.md` before making changes.
It records completed work, evidence, unfinished acceptance cases, and the exact next step.
Do not reimplement completed stages. Check the recorded source fingerprints and run only
the verification justified by current changes. Preserve unrelated working-tree changes.

The stage order and compatibility constraints are in `docs/qq-roadmap-2026-10-26.md` and
`docs/qq-regression-baseline-2026-10-26.md`. Implementation completion and live acceptance
are separate states; never describe mock tests as real QQ verification.

After each QQ work session, update the status document with files changed, test commands
and results, pending blockers, deployment state, and concrete instructions for resuming.
Keep private backups, credentials, raw conversations and runtime logs out of public docs.

Use the latest source manifest referenced by the status document; older manifests are
historical evidence and must not be overwritten. Preserve the online observation ledger,
including sampling gaps. Never reset it or credit earlier uptime to manufacture a passed
24-hour gate. Preserve the user's current supervision and startup choices on later runs.

The user explicitly reaffirmed strict sequential acceptance on 2026-10-27 (session 3).
Do not start QQ-03 before QQ-01/02 gates pass, or ask again to relax the order unless
the user initiates a scope change. The isolated source rollback and real two-page
configuration-conflict checks are completed evidence; repeat them only for relevant changes.

The user imported their existing browser API configuration into the managed local file
in session 4. Preserve this file and browser drafts; do not re-import defaults or old caches.
Brave is configured: its historical USAGE_LIMIT_EXCEEDED (HTTP 402) is not a missing
key. Session 7 activated DeepSeek and passed real independent Search/Fetch probes;
do not wait for Brave quota or repeat paid probes. Real QQ receipt is separate.
Never ask the user to send credentials in chat.

Session 5 enforces one active main chat page per backend across browsers. Release or
close your test owner's page before the user opens their correct Chrome profile.
Non-API settings have a 22-entry inventory and explicit local archive, including live
3D view/frame state. Session 6 enabled local runtime control and verified the user's
16 saved entries. Preserve runtime_enabled=true and the original values; never
re-import defaults. Existing profile/style files remain authoritative projections.
Do not overwrite a user-confirmed browser archive with defaults from an automation tab.

Session 7 adds independent DeepSeek Search/Fetch alongside Brave. One persisted
search_provider selects the backend for both web and QQ. Provider activation must
use the versioned local-profile endpoint, preserve both keys, and never auto-fallback
or send one provider's key to the other. DeepSeek's strict safesearch is unsupported;
fail explicitly rather than silently removing that filter. The session 6 final
handoff was interrupted by approval quota; recover its private evidence instead of
reimplementing local settings. Record its completed API cache pull/restore live lab.
