# Jev Router V5 offline and controlled E2E report

## Dataset and run protocol

`router_e2e_blind_v5.jsonl` has 30 independently authored cases and SHA256 `24d190d630614a611f19ac2ea1b7fc61cbf529b4222dfe826ec1b2b64f5e6daf`. Each case freezes route set, ordered tasks, exact query fragments, dependency indexes, clarify expectation and unsupported part. It was committed before the first Jev/Hybrid API comparison. The V3/V4 Gold files and V2/V3 criteria and thresholds were untouched.

**Blind qualification limit:** two parser mismatches (`v5-09`, `v5-18`) were corrected after Gold freeze but before the first API run. Thus even the preserved first pass is an independent new dataset, but not a fully untouched parser blind. The safety and dependency changes after inspecting first-pass failures make the second run a **seen-sample repeat**. No labels changed. The first-pass raw results, metrics and failures are preserved separately.

Both routers saw the same 30 cases and frozen context in each run. Baseline and candidate execution order alternated by case. Candidate computation ended at `RouteDecision`; no Subgraph was dispatched by the offline benchmark. Remote calls and tokens count provider usage reported to the tracker; Jev calls that failed before returning usage may make token totals a lower bound. Percentiles use linear interpolation over the observed cases.

## Results

| Metric | First Legacy | First V5 | Repeat Legacy | Repeat V5 |
|---|---:|---:|---:|---:|
| Route exact | 18/30 | 25/30 | 18/30 | 29/30 |
| Route micro F1 | .7812 | .9091 | .7761 | .9842 |
| Route macro F1 | .7404 | .8829 | .7327 | .9833 |
| Over / under route cases | 7 / 7 | 3 / 2 | 8 / 6 | 0 / 1 |
| Task count accuracy | .4333 | .8667 | .4333 | 1.0000 |
| Task route / fragment accuracy | .5581 / .3488 | .9535 / .9302 | .5814 / .3488 | .9767 / .9535 |
| Dependency / ordering accuracy | .5581 / .3333 | .9535 / .8333 | .5814 / .3333 | 1.0000 / .9667 |
| Same-route multi exact | 0/8 | 7/8 | 0/8 | 8/8 |
| Complete decision exact | 8/30 | 24/30 | 7/30 | 29/30 |
| Clarify precision / recall | .4000 / .2857 | .8000 / .5714 | .3333 / .1429 | 1.0000 / 1.0000 |
| Candidate fallback | — | 4/30 | — | 1/30 |
| Average router latency, ms | 4041 | 1676 | 4321 | 826 |
| Router P50 / P95 / P99, ms | 4871 / 10459 / 10803 | 549 / 5243 / 12889 | 4810 / 10927 / 11185 | 545 / 2329 / 5183 |
| Remote calls/query | .80 | 1.67 | .80 | 1.50 |
| Tokens/query, reported | 1141 | 2452 | 1142 | 2344 |

The first pass is the honest comparison to use for effectiveness. The repeat shows the tested repair behavior, not a new generalization estimate. Legacy results vary slightly across runs because its LLM route decisions are nondeterministic.

## Normal and fallback latency

| V5 path | First pass | Seen-sample repeat |
|---|---:|---:|
| Normal count | 26 | 29 |
| Normal P50 / P95 / P99, ms | 540 / 3261 / 4410 | 543 / 968 / 2739 |
| Fallback count | 4 | 1 |
| Fallback P50 / P95 / P99, ms | 4118 / 14297 / 15504 | 5904 / 5904 / 5904 |
| Timeouts | 1 | 0 |
| Classifier hot-path calls | 0 | 0 |
| Classifier fallback calls | 7 | 3 |

The first pass had a Jev timeout followed by Legacy work. This confirms latency stacking on standalone fallback. Shadow reuses the already running Legacy baseline decision, so it does not rerun the full Hybrid router for fallback. The repeat fallback percentile is one observation, not a tail latency estimate. The five-second Jev timeout remains frozen.

## Error analysis

First-pass failures were `v5-17` (triple route conflict), `v5-19` (negation cleared the whole request), `v5-25` (external booking mixed with supported music task), `v5-26` (unsupported counterfactual), `v5-29` (unidentified artist), and `v5-30` (missing search-result dependency). General capability guards, affirmative-clause handling and result-dependency recognition repaired five cases on the seen-sample repeat. `v5-17` remains: GeneralChat Noul score .44 is below the frozen .50 threshold although fragment Choice identified GeneralChat; V5 correctly entered fallback, but Legacy omitted that route. The threshold was not changed.

Same-route task cardinality is represented independently of route-set cardinality. Tests cover two and three ToolCalling tasks, two GeneralChat tasks, ordered and independent pairs, negation and search-result dependencies. The 8/8 repeat result establishes those examples, but cannot prove all natural-language decompositions are solved. The remaining triple-route conflict and the first-pass errors prevent a canary readiness claim.

## Controlled MainGraph → Subgraph E2E

Five controlled async integration tests use the real MainGraph adapters and real CurrentSong, GeneralChat, Worldline and ToolCalling Subgraph graphs with fixture providers/assistants. Verified: CurrentSong analysis with a fixture song; missing SongContext clarifies before dispatch; GeneralChat explanation; Worldline evidence retrieval and generation for a named fixture entity; ambiguous entity clarifies; two same-route ToolCalling tasks dispatch in order; a fixture `search_music` result appears as the next task's prior entity; and `play_song` is queued as a client action. No live player action was executed.

The Worldline test exercises a controlled create/read path with fake service, retriever and generator, not representative online knowledge quality. The browser's execution of queued playback remains unverified and needs human acceptance. The CurrentSong missing-context result is a Router clarify with no Subgraph call; existing Subgraph-specific tests separately cover `NEEDS_INPUT` without a song.

## Commands and raw evidence

- `python -m evals.jev_router_e2e_v5 run` twice: completed 30/30 each.
- `pytest tests/test_jev_router_v5.py tests/test_jev_router_v5_e2e.py -q`: initially 17 passed before the additional search-result test; that test passed separately. Final suite status is recorded in the refactor report.
- First-pass: `jev_router_v5_e2e_first_pass_results.jsonl`, `jev_router_v5_e2e_first_pass_metrics.json`, `jev_router_v5_first_pass_failure_cases.jsonl`.
- Seen-sample repeat: `jev_router_v5_e2e_results.jsonl`, `jev_router_v5_e2e_metrics.json`, `jev_router_v5_failure_cases.jsonl`.
