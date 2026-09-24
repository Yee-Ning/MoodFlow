# WorldLine Evaluation V0 Report

## 1. Run Metadata

- Dataset version: `v0`
- Dataset SHA256: `f89d84f41ec270151bf0e52cd152bf4d51ae00f9961c8284aa1e18542efb3772`
- Git commit: `307978105663e1e9f9eed5f8fa02fdb2946f18e7`
- Evaluation timestamp: `2026-09-24T13:11:09.393293+00:00`
- Dataset provenance: synthetic contract fixtures approved for deterministic regression; these labels are not independent real-world music fact annotations.

## 2. Summary

- Cases: 38
- Passed: 38
- Failed: 0
- Skipped: 0
- Errors: 0
- Execution success: 100.0% (38/38)

## 3. Quality Metrics

| Area | Metric | Result |
|---|---|---:|
| Semantic | Relation precision | 100.0% (8/8) |
| Semantic | Relation recall | 100.0% (8/8) |
| Semantic | Grounded relation precision | 100.0% (8/8) |
| Semantic | Expected rejection accuracy | 100.0% (6/6) |
| Evidence | Provenance validity | 100.0% (8/8) |
| Evidence | Citation validity | 100.0% (2/2) |
| Evidence | Validator classification accuracy | 100.0% (1/1) |
| Exploration | Success rate | 100.0% (6/6) |
| Exploration | Artist precision | 100.0% (6/6) |
| Exploration | Artist recall | 100.0% (6/6) |
| Exploration | Action gate accuracy | 100.0% (10/10) |
| Identity | Resolution success | 100.0% (2/2) |
| Identity | Catalog accuracy | 100.0% (2/2) |
| Identity | Ambiguity detection | 100.0% (1/1) |
| Reliability | Graph merge success | 100.0% (2/2) |
| Reliability | Invalid merge rejection | 100.0% (1/1) |
| Reliability | Session continuity | 100.0% (3/3) |
| Reliability | Checkpoint recovery | 100.0% (2/2) |
| Reliability | Post-recovery exploration | 100.0% (2/2) |
| Reliability | Failure atomicity | 100.0% (5/5) |

## 4. Yield Diagnostics

- Semantic accepted relation rate: 81.8% (9/11)
- Semantic rejection rate: 18.2% (2/11)
- Genre evidence acceptance: 100.0% (9/9)
- Genre catalog resolution yield: 66.7% (6/9)
- Genre projection yield: 66.7% (6/9)
- Valid artist yield mean/P50/P95: 1 / 1.0 / 1.0

Yield metrics are diagnostic baselines. A lower yield caused by a correct evidence or identity rejection is not treated as a quality failure.

## 5. Failure Audit

No failed, skipped, or errored cases in this run.

## 6. Scope and Limits

- V0 covers artist → genre and executable Hip-Hop genre → artist only.
- Component and session fixtures are frozen synthetic contracts. They verify implementation behavior but do not establish real-world music knowledge accuracy.
- PostgreSQL metrics are N/A unless the run includes the postgres tier with `RUN_WORLDLINE_EVAL_POSTGRES=1`.
- RAG ranking, Embedding, RRF and Reranker quality are outside this evaluation scope.
- No LLM Judge is used for deterministic relation, identity, graph, citation or checkpoint checks.
