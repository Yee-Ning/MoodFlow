# Jev Router V5 refactor and verification

## Changes and scope

- Added `router_v5.py`: RuleGuard, frozen Jev Single/Multi decision heads, per-phase trace and lazy Legacy fallback. Jev success never calls the full Legacy router or classifier.
- Added `task_orchestrator_v5.py`: syntax-only clause boundaries, exact source spans, independent task cardinality, dependency and ordering, context/capability clarify and strict `RouteDecision` construction. Existing `RouteTask` and Subgraph APIs are unchanged.
- Added `shadow_v5.py`: bounded, background candidate comparison with anonymous structured logs and process-local counters. Fallback results are excluded from agreement statistics because they reuse the baseline.
- MainGraph keeps Legacy as the only production dispatch decision. It schedules V5 Shadow under the feature flags and does not await candidate completion before responding. Old V4 Shadow and all Legacy Rule/Classifier/LLM code remain present.
- Added a V5 flag with migration alias from `JEV_ROUTER_V4_SHADOW_ENABLED`; the effective local flags were checked without displaying secrets. The V5 candidate still validates the frozen Capability Contract hash and V3 thresholds at construction.
- Added unit and controlled MainGraph→Subgraph tests. No business logic in the four Subgraphs, production data, V3/V4 Gold, criteria or thresholds was changed.

The existing LLM router remains a Legacy fallback component. Deterministic parsing handles the tested simple and compound task boundaries without invoking an additional LLM for orchestration. Highly uncertain compact decomposition enters fallback. This avoids a full Legacy→Jev double route on normal candidate requests, but fallback can still incur both Jev and Legacy latency.

## Fallback and safety

V5 uses Legacy fallback for Jev timeout/API error, bad schema, Choice confidence below .8, route-set/fragment conflict and hard orchestration failure. Deterministic unsupported external-service or counterfactual requests and missing context produce clarify without calling Jev or Legacy. The fallback decision is schema validated before leaving V5. Tests cover timeout, low confidence, invalid schema and conflict. First-pass fallback occurred 4/30; the seen-sample repeat had 1/30, a remaining triple-route conflict.

Negated actions are excluded before capability preflight. A negated unsupported action does not cancel a separate supported request. Worldline's unresolved-root rule treats `其他艺人` as an unidentified entity phrase; it does not misread the `他` inside `其他` as a pronoun. These are finite deterministic guards; they are not a full semantic unsupported-intent detector.

## Verification

| Check | Result |
|---|---|
| `python -m pytest tests/test_jev_router_v5.py tests/test_jev_router_v5_e2e.py -q` | 20 passed after final V5 code edits |
| `python -m pytest tests -q` | 1,276 passed, 14 skipped, 228 subtests passed; run began before the last small guard/telemetry edits, which the targeted V5 rerun covers |
| `python -m pytest -q` | Collection blocked: optional `deepeval` is not installed for `evals/tests/test_judges.py`; no result claimed for that suite |
| Targeted Ruff on all V5 changed/new Python files | Passed |
| `python -m ruff check . --output-format concise` | 45 findings in unrelated existing files/scripts; no V5 changed/new file in the findings |
| `python -m compileall -q app evals/jev_router_e2e_v5.py tests/test_jev_router_v5.py tests/test_jev_router_v5_e2e.py` | Passed |
| Frozen Gold SHA/unique IDs/source spans test | Passed |
| Controlled search→queued play handoff | Passed with fixture search tool; no real browser execution |

The first V5 E2E benchmark and an explicitly labeled seen-sample repeat both completed 30/30 cases. Their raw results and metric tables are in `jev_router_v5_e2e_report.md` and adjacent JSONL/JSON files. Candidate outcomes were not dispatched by either benchmark.

## Risks and operating limit

The first-pass fallback tail is high, the new Gold was partly used for parser checks before first API evaluation, one triple-route case still fails, and no actual production Shadow window is observable here. The local flag is on, but remote deployment and browser action completion are unverified. Primary Canary remains blocked; no production switch occurred.
