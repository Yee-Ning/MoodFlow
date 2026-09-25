# WorldLine Real Gold V1 Evaluation Report

## 1. Run Metadata

- Dataset version: `v1`
- Dataset kind: `real_gold_fixture`
- Dataset SHA256: `24acff750f04b87f2f362d42ff13982ecd117d2a0299de93337645b641307b8d`
- Source ledger SHA256: `003b27417e16afc1186e81185d48803d2016b66abc5af25686c25b2233f49e6f`
- Git commit: `365e013c85d71049418bb25bc691fd97cf876d08`
- Evaluation timestamp: `2026-09-25T06:59:47.752938+00:00`
- Dataset provenance: reviewed real-gold fixtures with loader-verified source-ledger integrity.

## 2. Summary

- Cases: 18
- Passed: 18
- Failed: 0
- Skipped: 0
- Errors: 0
- Execution success: 100.0% (18/18)

## 3. Quality Metrics

| Area | Metric | Result |
|---|---|---:|
| Semantic | Relation precision | 100.0% (8/8) |
| Semantic | Relation recall | 100.0% (8/8) |
| Semantic | Grounded relation precision | 100.0% (8/8) |
| Semantic | Expected rejection accuracy | N/A (0/0) |
| Evidence | Provenance validity | 100.0% (8/8) |
| Evidence | Citation validity | N/A (0/0) |
| Evidence | Validator classification accuracy | N/A (0/0) |
| Exploration | Success rate | 100.0% (2/2) |
| Exploration | Artist precision | 100.0% (2/2) |
| Exploration | Artist recall | 100.0% (2/2) |
| Exploration | Action gate accuracy | 100.0% (2/2) |
| Identity | Resolution success | 100.0% (8/8) |
| Identity | Catalog accuracy | 100.0% (8/8) |
| Identity | Ambiguity detection | N/A (0/0) |
| Reliability | Graph merge success | N/A (0/0) |
| Reliability | Invalid merge rejection | N/A (0/0) |
| Reliability | Session continuity | N/A (0/0) |
| Reliability | Checkpoint recovery | N/A (0/0) |
| Reliability | Post-recovery exploration | N/A (0/0) |
| Reliability | Failure atomicity | N/A (0/0) |

## 4. Yield Diagnostics

- Semantic accepted relation rate: 100.0% (8/8)
- Semantic rejection rate: 0.0% (0/8)
- Genre evidence acceptance: 100.0% (2/2)
- Genre catalog resolution yield: 100.0% (2/2)
- Genre projection yield: 100.0% (2/2)
- Valid artist yield mean/P50/P95: 1 / 1.0 / 1.0

Yield metrics are diagnostic baselines. A lower yield caused by a correct evidence or identity rejection is not treated as a quality failure.

## 5. Failure Audit

No failed, skipped, or errored cases in this run.

## 6. Scope and Limits

- The current scope covers artist → genre and executable Hip-Hop genre → artist only.
- Real Gold inputs remain evaluation-only and do not modify the production Knowledge Base or Entity Registry.
- PostgreSQL metrics are N/A unless the run includes the postgres tier with `RUN_WORLDLINE_EVAL_POSTGRES=1`.
- RAG ranking, Embedding, RRF and Reranker quality are outside this evaluation scope.
- No LLM Judge is used for deterministic relation, identity, graph, citation or checkpoint checks.
