# Jev Router V5 production readiness

**Decision: do not start Jev Primary Canary in this phase.** Production dispatch remains on Legacy Hybrid. V5 Shadow stays enabled by its gated local configuration; no production primary switch or Subgraph business change was made.

| Gate | Evidence | State |
|---|---|---|
| MainGraph-compatible `RouteDecision` | Strict schema validation in V5; controlled graph runs across all four route adapters | Pass in controlled tests |
| Same-route task cardinality | Two/three ToolCalling and two GeneralChat task tests; first V5 7/8 and seen-sample repeat 8/8 | Improved; needs independent regression |
| Order and dependency | Exact task IDs, source spans and dependency checks; controlled search result reaches next Tool task | Pass in controlled tests |
| Clarify and unsupported | First pass missed three capability boundaries; repeat repaired them on seen cases | Needs new untouched safety holdout |
| Fallback | Timeout, low confidence, schema and route-conflict tests; first pass 4/30 fallback | Functional; wrong Legacy answer on `v5-17` remains |
| Shadow side effects and isolation | Synthetic delayed candidate test; no candidate dispatch and no leaked query in captured event | Pass in controlled test; no real window |
| Independent V5 Gold | New 30-case freeze; first route exact 25/30 vs Legacy 18/30 | Favorable, with parser exposure caveat |
| Router tail latency | First V5 total P95 5.24 s, P99 12.89 s; fallback n=4 P95 14.30 s | Blocker for canary tail gate |
| Controlled Subgraph E2E | Fixture CurrentSong, GeneralChat, Worldline, ToolCalling and two-step tool flow | Pass in controlled tests; browser playback unverified |
| Real Production Shadow sample | No accessible deployed traffic or telemetry on this revision | 0 confirmed real requests |

## Remaining blockers

1. `v5-17`: Noul GeneralChat score .44 is below the frozen .50 threshold while fragment Choice selects GeneralChat. V5 falls back, but Legacy omits that route. Do not retune V3 threshold from this case. Resolve the conflict policy and test it on a new untouched holdout.
2. The new 30-case Gold is independent of earlier V3/V4 corpora but had two parser corrections before first API comparison, and the 29/30 repeat is explicitly seen-sample. Obtain a fully untouched next holdout before canary.
3. No verified real Production Shadow requests, route distribution, agreement, side-effect audit or stable multi-instance telemetry window exist. A running local feature flag is not a deployment observation.
4. First-pass fallback tail is high and sample size is small; timeout plus full Legacy fallback can stack. A canary needs an agreed latency budget and a larger observed tail sample without changing the frozen Jev timeout in this phase.
5. Browser playback completion from a queued client action requires human acceptance in a real browser/test account. Controlled tests only show queueing, not execution.
6. Repo-wide `pytest -q` collection is blocked by missing optional `deepeval`; repo-wide Ruff reports unrelated existing findings. Application-test result and targeted checks are documented separately.

The next authorized step would be an observation and safety-validation phase, followed by a separate explicit Canary decision. This report does not authorize Primary routing.
