# V5 Production Shadow report

## Effective local configuration and dispatch

On this workspace host, a read-only settings check returned `JEV_ENABLED=true`, `JEV_SHADOW_MODE=true`, `JEV_ROUTER_V5_SHADOW_ENABLED=true`, `JEV_MODEL=jev-latest`, timeout 5 seconds and Choice confidence threshold .8; the Jev API key was checked for presence without reading or printing it. The code path is enabled under these gates. This is **not evidence that a remote production deployment has loaded this revision**.

MainGraph starts the existing Legacy router as its primary decision and schedules V5 as a bounded background task (at most 32 pending). It awaits only Legacy before choosing Subgraphs. The candidate returns a `RouteDecision` for comparison, and its tasks are never dispatched. Timeout and candidate exceptions are counted and logged by exception type, without failing the primary response. A full queue drops the candidate rather than delaying the primary. The V5 candidate uses the already running Legacy decision when it needs fallback. The V5 flag can disable the branch; the prior V4 flag is accepted if the new flag is unset. Existing V4 code remains available.

Classifier hot/fallback counters in the V5 trace describe the **candidate path**. The production Legacy primary still uses its classifier as before, including on successful V5 Shadow requests; no production classifier removal is claimed.

## Anonymous event schema

The `jev.v5_shadow` event contains timestamp, response model, source, route sets, task counts, route/task-count agreement, clarify disagreement, dependency-edge count, numeric confidence/scores, phase latencies, call/token counts, classifier counters and a categorized fallback reason. Fallback events set comparison fields to `null`, because their returned decision is the baseline itself; agreement rate and candidate route distribution include only comparable Jev decisions. The event contains no original query, user ID, Song ID, cookie, key, access token, raw SDK response or task fragment. `jev.v5_shadow_failure` contains only an exception class. The process-local snapshot exposes sample size, single/multi/fallback/timeout/clarify counts, comparable agreement, route distribution, P50/P95/P99 and tokens per request. It is not durable cross-instance telemetry.

## Observed window

| Measurement | Observed result |
|---|---:|
| Confirmed real production requests on this revision | 0 |
| Controlled synthetic shadow test requests | 1 in a test process |
| Real single/multi proportion, agreement, fallback, timeout, clarify | No real sample |
| Real route distribution, tokens/request, P50/P95/P99 | No real sample |
| Candidate Subgraph dispatch in controlled test | 0 |
| Candidate browser/player mutation in controlled test | 0 |

The synthetic test held the candidate behind an event and verified the Legacy response returned first. It then allowed candidate completion, captured a structured event at INFO level, and checked the secret fixture query was absent from logs. This supports isolation and privacy for the tested code path. It does not prove zero side effects across a real production window. No actual production traffic or deployment telemetry was accessible from this workspace; none is counted or inferred from offline benchmark calls.

## Operational limits

Scheduling and candidate construction add a small unmeasured local overhead before awaiting Legacy. Background Jev calls consume provider capacity even though the primary response does not await them. Process-local counters reset on restart, so an actual rollout needs central anonymous metric aggregation and a fixed observation window before a canary decision. Set `JEV_ROUTER_V5_SHADOW_ENABLED=false` for immediate branch rollback; keep `JEV_SHADOW_MODE=true` for other Jev shadow consumers unless operational policy says otherwise.
