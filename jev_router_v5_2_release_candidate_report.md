# Jev Router V5.2 Release Candidate Deployment

Date: 2026-09-29 (Asia/Shanghai)

Status: **IN PROGRESS — no release approval**.

## Verified preflight

- Local `main` and freshly fetched `origin/main` were `4f3c72199815b74a29b30cd6bb578cf9408ffecd` before rollout. The user ran a read-only check in Alibaba Cloud Workbench: ECS `main` was the same commit and its worktree was clean. Direct SSH from this agent's Windows terminal was closed by the remote host before key exchange; Workbench itself was connected.
- The V5 work branch descends from that commit. Its V5 source, tests and reports are committed. No `.env.production`, private certificate/key, dump or snapshot path was found in the tracked deployment diff. Existing unrelated local modifications and untracked data were left untouched.
- An independent TypeSafe/Jev secret file was provisioned by the user at `/secure/moodflow/typesafe_api_key`. Read-only metadata confirmed mode `0600`, UID `10001`, and nonzero size. Its content was not read or recorded.
- The first rollout boundary, commit `1c4c8409501cb6459038c178648204b59aa4ac01`, was fast-forward pushed to `origin/main`; local `main` was advanced to the same commit. It includes V5 code and the reviewed-Compose gate, while the production Compose at that boundary still sets `JEV_ENABLED=false`. ECS update is pending dry-run.
- The later local commits `3276663`, `eba7002`, and `6896130` prepare the separate Jev secret mount, require `JEV_ENABLED=true` together with V5 Shadow and `JEV_ROUTER_ENABLED=false`, label acceptance telemetry, and isolate test environment variables. These commits have **not** been pushed to production `main`.

## Local validation

| Check | Result |
| --- | --- |
| `ai-service/.venv/Scripts/python.exe -m pytest deploy/tests -q` | 68 passed |
| `ai-service/.venv/Scripts/python.exe -m pytest tests -q` from `ai-service` | 1279 passed, 14 skipped, 228 subtests passed |
| `docker compose --env-file .env.example -f docker-compose.production.yml config --quiet` | Passed locally |
| Compose config with `JEV_ENABLED=true`, `JEV_ROUTER_V5_SHADOW_ENABLED=true` | Parsed values: Jev true, Shadow true, Primary router false, V5 Shadow true |

The first attempted combined test invocation from the repository root reported four failures: three tests used paths relative to `ai-service`, and one new test leaked a fixture key into a later test. The fixture isolation was fixed and committed; the suites above then passed from their intended working directories. No production key value was used in tests.

## Deployment and safety gates

| Gate | Current result |
| --- | --- |
| ECS running commit / V5 files in current ai-service container | Not verified after rollout; ECS last confirmed at `4f3c721` |
| `bash deploy/update.sh --dry-run` / actual update | Pending Workbench execution |
| PostgreSQL dump, Qdrant snapshot, isolated restore verification | Pending updater execution and manifest inspection |
| `JEV_ENABLED=true`, `JEV_SHADOW_MODE=true`, `JEV_ROUTER_V5_SHADOW_ENABLED=true` in running container | Not enabled or verified |
| Legacy Primary / V5 Shadow only | Enforced by local production contract and tests; not yet verified in ECS |
| Candidate Subgraph dispatch / ToolCalling side effects | Local Shadow runner exposes zero-by-construction counters and tests the baseline handoff; no running ECS measurement yet |
| Post-browser service health, logs, nginx, Certbot, backup timer, CPU/memory/disk | Not yet verified |
| Normal/fallback latency and API/token smoke | Not yet measured on deployed V5 |

The update flow must run in two boundaries: (1) deploy `1c4c840` with Jev disabled and verify service health; (2) add only secret **path** and V5 Shadow flags to the ignored `.env.production`, push the later reviewed Compose commit, run dry-run with `--approve-contract`, then deploy and verify the live container. At each actual update, the updater creates a PostgreSQL backup and Qdrant snapshot and requires isolated restore verification before building or restarting services. Production volumes and frozen Worldline data are not to be reset.

## Required 18 answers

| # | Question | Current answer |
| --- | --- | --- |
| 1 | ECS deployed commit | Last verified `4f3c721`; first rollout pending |
| 2 | V5 actually loaded | Not verified |
| 3 | Legacy still Primary | Expected by local contract; ECS not verified |
| 4 | V5 Shadow only | Expected by local contract; ECS not verified |
| 5 | Shadow candidate dispatch count | No live sample; local structural counter 0 |
| 6 | Shadow side-effect count | No live sample; not independently measured |
| 7 | Real login | Passed in browser on old revision only |
| 8 | CurrentSong | Not verified after deployment |
| 9 | GeneralChat | Passed one browser query on old revision only |
| 10 | Worldline | Not verified after deployment |
| 11 | ToolCalling browser actions | Not verified after deployment |
| 12 | Multi Intent | Not verified after deployment |
| 13 | Same-route multi-task | Local tests passed; browser not verified |
| 14 | Triple intent safety | Local tests only; browser not verified |
| 15 | Fallback | Local tests only; ECS/browser not verified |
| 16 | Production services healthy | Not rechecked after rollout |
| 17 | URL ready for early users | **No** |
| 18 | Remaining blockers | ECS dry-run/update, live configuration and safety checks, full browser acceptance and post-test health |

No Jev Primary Canary, frozen-threshold adjustment, Legacy removal, production volume reset or knowledge-data rewrite is part of this rollout.
