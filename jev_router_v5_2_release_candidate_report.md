# Jev Router V5.2 Deployment and Shadow Acceptance

Date: 2026-09-29 (Asia/Shanghai)

ECS application commit: `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72`

Decision: **Deployment and live V5 Shadow activation passed. Full Release Candidate acceptance is on hold. No Primary Canary.**

## Deployment evidence

- Before rollout, local `main`, `origin/main` and clean ECS `main` were `4f3c72199815b74a29b30cd6bb578cf9408ffecd`. Updates used fast-forward commits and the existing `deploy/update.sh` process. Unrelated local dirty/untracked files were preserved.
- The final dry-run targeted `0fdf4f7` from `1039afc`, rebuilt only `ai-service`, required backup and listed no migrations. `/secure/moodflow/deployments/20260929T121030Z-0fdf4f71.json` reports `status=passed`, `phase=smoke`, and the expected previous/new commits.
- The predeploy manifest reports `status=complete`, `restore_verified=true`, and PostgreSQL/Qdrant restore entries. No production volume reset, database clearing, initial restore or frozen knowledge change was performed.
- The final deployment smoke passed `/`, `/api/auth/status`, `/healthz`, SSE and Worldline (9 nodes). Post-browser Compose inspection found ai-service, frontend, java, netease, postgres and qdrant **healthy**. CPU was 0–0.16% per container in one snapshot; RAM available 5.3 GiB of 7.3 GiB; root disk 41% used. These are snapshots, not load-test or SLA evidence. Nginx, `snap.certbot.renew.timer` and the MoodFlow backup timer were active; `nginx -t` passed.
- The live container contained `router_v5.py`, `task_orchestrator_v5.py`, `shadow_v5.py` and the frozen V3 strategy file; `frozen_thresholds()` returned four routes. The independent TypeSafe key file was mounted. Only file metadata and mount existence were checked, never key content.
- Live flags were `JEV_ENABLED=true`, `JEV_SHADOW_MODE=true`, `JEV_ROUTER_V5_SHADOW_ENABLED=true`, `JEV_ROUTER_ENABLED=false`, `JEV_ROUTER_V4_SHADOW_ENABLED=false`. Legacy remains Production Primary; V5 remains Shadow Candidate.

## Anonymous Shadow summary after browser testing

| Bucket | Samples | Single / Multi | Route agreement | Task-count agreement | Fallback / timeout / failure | Normal latency avg / P50 / P95 | Calls/query | Tokens/query |
| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: | ---: |
| acceptance | 4 | 4 / 0 | 4 / 4 | 4 / 4 | 0 / 0 / 0 | 520.51 / 462.58 / 817.42 ms | 1.00 | 1086.00 |
| production-labeled | 9 | 3 / 5 | 6 / 8 | 5 / 8 | 1 / 0 / 0 | 360.63 / 356.86 / 408.28 ms | 1.67 | 2734.56 |
| synthetic | 0 | 0 / 0 | n/a | n/a | 0 / 0 / 0 | n/a | n/a | n/a |

The production-labeled bucket includes the later agent-run browser tests after the in-app tab was recreated. It cannot be used as a count of real users. Its one fallback averaged/P50/P95 2479.11 ms; there was one clarify disagreement. Across all three buckets, `candidate_subgraph_dispatch_count=0`, `candidate_tool_action_dispatch_count=0`, `shadow_failure_count=0`, and `dropped_count=0`. Counters are emitted by the Shadow path itself, not independent downstream tracing. The observed unrequested `set_liked` action came through Primary; it remains a browser safety failure, without evidence of a Shadow dispatch.

Two rollout defects were repaired before the deployed commit: the AI image initially omitted the frozen multi-strategy JSON (`1039afc` packaged it), and INFO-level Shadow success events were suppressed by server logging configuration (`0fdf4f7` installed a dedicated logger). The final aggregate had zero Shadow failures. An ad hoc Python constructor command failed because it bypassed the service runtime's secret-file loader; the live Shadow sample verifies the actual runtime path.

## Verification

| Check | Result |
| --- | --- |
| AI service suite from `ai-service` | 1279 passed, 14 skipped, 228 subtests passed |
| Deployment tests after logging change | 72 passed |
| V5 router tests after image fix | 15 passed |
| Frontend AI stream/layout/trigger/Worldline tests, lint and build | Passed |
| Production Compose config validation | Passed |
| ECS updater smoke and backup restore verification | Passed per deployment records |
| Public browser | Partial; see [browser acceptance](moodflow_prelaunch_acceptance_report.md) |

The local router tests cover timeout, low confidence, invalid schema, task conflict, missing SongContext, unsupported and negated action paths. Production fault injection was avoided. The initial host check used `certbot.timer`, which was inactive; the project's actual `snap.certbot.renew.timer` was then confirmed active. The updater's own preflight passed.

## Required 18 answers

| # | Question | Answer |
| --- | --- | --- |
| 1 | ECS commit | `0fdf4f71dfa19a9a7fb57b04ed6b73719c02cd72` |
| 2 | V5 loaded | Yes: live files, frozen strategy and successful Shadow events |
| 3 | Legacy Primary | Yes, `JEV_ROUTER_ENABLED=false` |
| 4 | V5 Shadow only | Yes, candidate flags enabled with no candidate dispatch |
| 5 | Candidate dispatch count | Subgraph 0; tool action 0 |
| 6 | Shadow side effects | No observed candidate action; structural counters 0 |
| 7 | Login | QR login, cookie flags and refresh passed; across-restart continuity unverified |
| 8 | CurrentSong | With-song success and no-song clarification passed; switch-song sequence not done |
| 9 | GeneralChat | Sampled questions passed without Worldline route |
| 10 | Worldline | Partial: graph/sources/citations/refresh passed; album continuation and AI influence request failed |
| 11 | ToolCalling browser actions | Search, lyrics, play and favorite worked individually; search+play emitted an unrequested favorite |
| 12 | Multi Intent | Partial: sampled dual/triple cases mixed success and failure; full matrix stopped |
| 13 | Same-route multi-task | Local tests passed; browser case not verified |
| 14 | Triple-intent safety | One read-only triple-route request passed; next attempt interrupted |
| 15 | Fallback | Local failure tests passed; one live Shadow fallback was safely recorded; no production fault injection |
| 16 | Production health | Six services healthy post-browser; Nginx, Snap Certbot renew timer and backup timer active; `nginx -t` passed |
| 17 | Send URL to early users | **No — hold** |
| 18 | Blockers | Unrequested Primary favorite action, incomplete Worldline workflow, unreliable acceptance telemetry label after tab recreation, incomplete browser matrix |

V5.2 deployment is complete. Full Release Candidate approval is not. Preserve Legacy Primary and V5 Shadow; do not change frozen thresholds or begin Primary Canary.
