# MoodFlow V5.2 Browser Acceptance Bugfix Closure

Date: 2026-09-29 (Asia/Shanghai)

Status: **V5.2 browser acceptance bugfix closure complete for the requested routing, action, Worldline and telemetry checks.**

## Scope and safety boundary

This closure addresses only the browser acceptance blockers observed on deployed commit `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72`. Legacy remains Primary and V5 remains Shadow. Jev thresholds and criteria, the deployment architecture, HTTPS, database, Qdrant, backups, and frozen knowledge were not changed. No V6 Canary work is included.

## Findings and changes

| Blocker | Cause | Fix | Local verification |
| --- | --- | --- | --- |
| Search → play emitted an extra `set_liked` | The ToolCalling model selected the action after receiving conversation history. Tool validation checked schemas, entity provenance and duplicates, but not whether the current assigned task authorized a player or library mutation. SSE serialization merely forwarded the resulting action. | Check mutating tool calls against affirmative action intent in the current task clause before tool execution. Reject an invalid batch and let the assistant retry. The prompt limits history to entity reference. | Regression injects an unauthorized favorite alongside search, then retries search and play; only `play_song` is emitted. Explicit favorite remains allowed and negated favorite is rejected. |
| Analyze current song → favorite added GeneralChat | The CurrentSong rule matched only the noun phrase, leaving the analysis complement as an unresolved fragment for the model router. | Keep the analysis complement within the CurrentSong clause while stopping at a following task connector. | Hybrid decision has exactly CurrentSong + ToolCalling, without model/LLM escalation. |
| “不要切歌，分析一下当前这首歌” omitted analysis | A negated player clause remained unresolved, causing an all-or-nothing clarification despite a complete positive CurrentSong clause. | Ignore a cancelled action clause for coverage only when another positive rule match exists. Standalone negations keep their existing LLM review path. | Hybrid decision has exactly one CurrentSong task and no ToolCalling action. |
| Pablo Honey continuation failed | The initial failure involved graph capacity. After bounding the merge, live browser regression still failed at `branch_generation_failed`: the narrative and graph were ready, but the structured branch output could not be validated. The failed turn left its checkpoint intact. | In session continuation only, bound the merge to the existing schema while preserving evidence. If the exploration branch model exhausts structured validation, offer up to three unvisited song/artist/album nodes already connected by graph edges with retained source IDs. The fallback is logged without query or identity data. | Bounded merge and branch fallback tests cover success, absent evidence and unchanged strict behavior. Live Creep → Pablo Honey now reaches depth 2, revision 3, 15 nodes/15 edges; the sanitized fallback counter is 1. |
| Search Radiohead → explore “his influence” failed | The Worldline task received only the pronoun clause. Context resolution did not recognize a pronoun embedded after “探索”, and the same-turn search entity was not passed into Worldline; the catalog search then failed. | Bind a unique artist that was both named in the preceding user search clause and present in trusted ToolCalling search results. Unbound pronouns ask for clarification before catalog search. | Adapter passes `探索歌手《Radiohead》的影响关系`; unmatched or failed searches do not bind. |
| Manual browser requests entered `production` telemetry | The acceptance marker depended on `sessionStorage`; an embedded browser can reject storage, so later same-tab requests lost the header. | Apply the explicit URL marker immediately and retain it in module memory for SPA navigation, while still persisting to session storage where available. `?__moodflow_acceptance=0` clears it. | Frontend streaming test covers normal, storage-denied, navigation and clear paths. |

Bounded Worldline continuation remains subject to the existing graph size limits. If all slots are occupied, the session should report no new connections rather than invent or rewrite evidence. Artist influence exploration still depends on actual catalog and indexed evidence coverage; no frozen knowledge was altered.

## Validation completed locally

- Backend: `python -m pytest tests -q --tb=short` from `ai-service`: **1291 passed, 15 skipped, 230 subtests passed**. Non-secret fixture environment values were used. The isolated worktree required unchanged local frozen attachments and their original line endings for historical hash tests. Focused Worldline generation/session/graph tests: **37 passed**.
- Deployment checks: `python -m pytest deploy/tests -q --tb=short`: **72 passed**.
- Frontend: `npm run test:ai-stream`, `npm run build`, and `npm run lint`: **passed**. The build retained an existing large-chunk warning.
- Commits: `ae3174f` (ToolCalling and routing), `8592252` (Worldline merge/context), `44e57d9` (telemetry), `38f5d2e` (grounded continuation fallback). All were fast-forward pushed to `origin/main` from `0fdf4f7`.

## Live deployment and targeted browser regression

The existing updater first deployed `44e57d95dbf927f2a393ba9e0146609485cff442` from `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72`, rebuilding only `ai-service` and `frontend`, with no database migration paths. `/secure/moodflow/deployments/20260929T132240Z-44e57d95.json` reports healthy services and passing HTTP/SSE/Worldline smoke. Its predeploy PostgreSQL/Qdrant backup reports `restore_verified=true`.

The SSH session dropped during the build, but the updater continued independently and its persisted record reports `status=passed`; the final running commit and service health were checked after reconnecting.

The final AI-service-only update selected `38f5d2ebd4f98b73d344f6e9c5a9c7bab50a8387`, with no migration paths. `/secure/moodflow/deployments/20260929T151350Z-38f5d2eb.json` reports `status=passed`, all six persistent services healthy, migration exited 0, and HTTP/SSE/Worldline smoke passed. Its backup manifest reports complete PostgreSQL/Qdrant restore verification. ECS Git is at the target commit with clean tracked status. Runtime flags remain `JEV_ENABLED=true`, `JEV_SHADOW_MODE=true`, `JEV_ROUTER_ENABLED=false`, and `JEV_ROUTER_V5_SHADOW_ENABLED=true`.

| Check | Live result |
| --- | --- |
| Search → play; no `set_liked` | Passed on the final deployment in the authenticated browser: SSE emitted only `play_song`, Creep was the selected song, and the player showed “暂停” (playing). No favorite action appeared. An earlier unauthenticated attempt returned 401, resolved by the user's QR login. |
| Analyze current song → favorite; no GeneralChat | Passed: final tasks were exactly `current_song=success` and `tool_calling=success`, with only the explicitly requested `set_liked(liked=true)` action. A subsequent browser AI request reported `current_song_liked=true`. The existing `songLikes` adapter is not registered, so this confirms local player state, not NetEase favorite persistence. |
| Negated action + CurrentSong analysis | Passed again on the final deployment: `current_song=success`, zero player actions; Creep analysis was rendered while the current song remained selected. |
| Pablo Honey Worldline continuation | Passed after the final update: Radiohead → Creep → Pablo Honey advanced depth 0→1→2 and revision 1→2→3; graph grew 9→10→15 nodes with validated sources. A prior browser attempt had returned `branch_generation_failed`, and the final run logged one grounded branch fallback. |
| ToolCalling + Worldline influence | Passed again on the final deployment: Radiohead search and Worldline tasks both returned success with a cited answer. The frozen knowledge does not establish a direct Radiohead influence edge for this request; the Pixies→Nirvana edge is separate evidence, not proof of Radiohead influence. |
| Acceptance/synthetic/production telemetry buckets | Browser AI requests carried `X-MoodFlow-Telemetry-Class: acceptance`. The final anonymized Shadow summary counted 6 acceptance samples and 0 production samples, with 0 Shadow failures and no candidate dispatches; synthetic had no live sample. The frontend test covers storage-denied, SPA continuation and marker clearing. Candidate/Legacy task counts agreed on 5/6 comparable samples, retained for Shadow observation rather than threshold tuning. |

The earlier nine browser acceptance requests mislabeled as `production` remain historical test traffic and must not be counted as organic usage. The summary reports no raw query, user ID, cookie or token.

## Closure decision

The requested V5.2 flow is usable, and later product feature development can continue. The explicit favorite action updates local player state, but NetEase favorite persistence is not implemented because no `songLikes` sync adapter is registered; that existing feature gap must not be described as an accepted remote write. Radiohead influence evidence remains limited, and the Worldline graph retains its 15-node/20-edge/30-source bounds. Legacy remains Primary, V5 remains Shadow, and no V6 Canary work is authorized.
