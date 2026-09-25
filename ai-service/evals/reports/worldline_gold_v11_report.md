# WorldLine Real Gold V1.1 Evaluation Report

## 1. Run Metadata

- Dataset version: `v1.1`
- Dataset kind: `real_gold_fixture`
- Dataset SHA256: `05f7a75c8cdcec7b112867df181d32cc9043b2b243e0956846280233d4621e45`
- Source ledger SHA256: `317dbbe0fe6945a2ad85ec7b79db7fbe7fbe23965235940eecfe0470a62a7cd3`
- Git commit: `8b59362fc7155b495a4a74990ad0d2af0874f326`
- Evaluation timestamp: `2026-09-25T11:01:09.253031+00:00`
- Dataset provenance: reviewed real-gold fixtures with loader-verified source-ledger integrity.

## 2. Summary

- Cases: 28
- Passed: 28
- Failed: 0
- Skipped: 0
- Errors: 0
- Execution success: 100.0% (28/28)

## 3. Quality Metrics

| Area | Metric | Result |
|---|---|---:|
| Semantic | Relation precision | 100.0% (9/9) |
| Semantic | Relation recall | 100.0% (9/9) |
| Semantic | Grounded relation precision | 100.0% (9/9) |
| Semantic | Expected rejection accuracy | 100.0% (4/4) |
| Evidence | Provenance validity | 100.0% (9/9) |
| Evidence | Citation validity | N/A (0/0) |
| Evidence | Validator classification accuracy | N/A (0/0) |
| Exploration | Success rate | 100.0% (2/2) |
| Exploration | Artist precision | 100.0% (2/2) |
| Exploration | Artist recall | 100.0% (2/2) |
| Exploration | Action gate accuracy | 100.0% (4/4) |
| Identity | Resolution success | 100.0% (8/8) |
| Identity | Catalog accuracy | 100.0% (8/8) |
| Identity | Ambiguity detection | 100.0% (1/1) |
| Identity | Unresolved reason accuracy | 100.0% (1/1) |
| Identity | False executable identity | 0.0% (0/2) |
| Reliability | Graph merge success | N/A (0/0) |
| Reliability | Invalid merge rejection | 100.0% (1/1) |
| Reliability | Session continuity | 100.0% (2/2) |
| Reliability | Checkpoint recovery | N/A (0/0) |
| Reliability | Post-recovery exploration | N/A (0/0) |
| Reliability | Failure atomicity | 100.0% (1/1) |

## 4. Yield Diagnostics

- Semantic accepted relation rate: 100.0% (10/10)
- Semantic rejection rate: 0.0% (0/10)
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
