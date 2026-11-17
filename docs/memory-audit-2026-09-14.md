# Memory Baseline Audit (read-only)

Generated: 2026-09-14 12:22:51  
Store: `D:\Dear\Talk\mistral_models\Code\knowledge_base` — nothing was modified.

## Totals

| metric | count |
|---|---:|
| all | 22 |
| active | 22 |
| archived | 0 |
| locked_or_starred | 16 |
| core_layer | 6 |
| auto_explicit | 1 |
| session_summaries | 6 |
| session_summaries_protected | 5 |
| pending_candidates | 0 |
| aged_pending_candidates_ge3d | 0 |

## Active by source

- learner: 8
- session_archive: 6
- about_you: 2
- system_activity: 2
- system: 1
- assistant_role: 1
- manual: 1
- auto_explicit: 1

## Active by layer

- semantic: 10
- episodic: 6
- core: 6

## Auto-adopt setting

- in settings file: False (value: <absent: code default now False>)

## Suspicious auto_explicit records (1)

- `88f98eab-b147-4cdc-ba1e-102849b1a60b` ['dangling_start'] — “你的人，所以是有作用的。但是遇到那种会随时忘掉你的人，你的所谓一直看着也会被忽略掉吧。” (2026-09-13)

## Daily-activity records in active store (1)

- `system-daily-activity-current` “今日活动状态 · 2026-09-14” layer=core locked=True starred=True

## Core layer inventory (6)

- `1766334309.303881` “Life Quests Summary” src=system locked=True starred=True chars=423
- `about-you-personality` “About You · Personality Profile” src=about_you locked=True starred=True chars=1624
- `about-you-possession-1` “About You · Important Possession 1: Computer” src=about_you locked=True starred=True chars=197
- `system-daily-activity-current` “今日活动状态 · 2026-09-14” src=system_activity locked=True starred=True chars=223
- `system-anki-checkin-status` “Anki 学习打卡状态（自动更新）” src=system_activity locked=True starred=True chars=378
- `a30f8f0d-7bca-4e00-9835-d7121a90a971` “Important memory” src=manual locked=False starred=True chars=793

## Phase-1 migration notes

- Daily Activity records should migrate to DayState D0 and leave Core (plan section 16.2).
- Locked/starred session summaries must be surfaced as suggest_unlock/suggest_unpin in the
  migration preview; this audit lists them but changes nothing.
- Suspicious auto_explicit records become `quarantined` proposals, never auto-deleted.
