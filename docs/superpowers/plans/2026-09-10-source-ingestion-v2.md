# Music RAG Source Ingestion V2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run automatic source filtering, URL deduplication, compliant fetching, pre-persistence snapshot validation, bounded replacement, and corpus reporting until Jay Chou Corpus V1 has 55-70 validated raw snapshots.

**Architecture:** Add a V2 orchestration path beside the approval-gated V1 path. V2 consumes a source-backed discovery manifest, records every decision in an append-only attempt ledger, writes only validated responses to the existing success manifest, and produces auditable corpus manifests/reports.

**Tech Stack:** Python 3.12, Pydantic v2, httpx, Protego, unittest, existing MoodFlow source-ingestion primitives.

**Spec:** `docs/superpowers/specs/2026-09-10-source-ingestion-v2-design.md`

## Global Constraints

- Do not run Parse + Normalize, Cleaning, Deduplication, Chunking, Entity Linking, Claim Extraction, Embedding, or Vector DB stages.
- Do not bypass robots.txt, 403, authentication, access controls, or anti-bot systems.
- Preserve existing raw snapshots and the `RawSnapshotRecord` schema.
- Validate response bytes before appending to the success manifest.
- Discover 30-50 new candidates and target 55-70 total valid snapshots, including 3-6 new valid PDFs.

---

### Task 1: V2 contracts, filtering, and candidate deduplication

**Files:**
- Create: `ai-service/app/rag/source_ingestion/v2_models.py`
- Create: `ai-service/app/rag/source_ingestion/quality_filter.py`
- Create: `ai-service/app/rag/source_ingestion/dedup.py`
- Test: `ai-service/tests/test_source_ingestion_v2_filtering.py`

**Interfaces:**
- Consumes: `CandidateSource`, existing candidate and raw-manifest metadata.
- Produces: `DiscoveredCandidate`, `FilterDecision`, `CandidateDeduplicator.check(candidate)`, stable reason codes.

- [ ] Write tests proving relevant tier 1-3 Jay Chou sources pass and SEO/search/login/irrelevant/low-quality sources fail.
- [ ] Run `python -m unittest tests.test_source_ingestion_v2_filtering -v` and confirm failures are missing V2 contracts.
- [ ] Implement the minimum Pydantic contracts and deterministic filter rules.
- [ ] Add URL, canonical URL, `(publisher,title)`, and external-id duplicate tests and watch them fail.
- [ ] Implement normalized URL and identity-key deduplication without body-content inspection.
- [ ] Re-run the focused tests and commit only Task 1 files.

### Task 2: Snapshot validation and success-only persistence

**Files:**
- Create: `ai-service/app/rag/source_ingestion/validation.py`
- Modify: `ai-service/app/rag/source_ingestion/snapshot.py`
- Test: `ai-service/tests/test_source_snapshot_validation.py`

**Interfaces:**
- Consumes: `CandidateSource`, `RawHttpResponse`.
- Produces: `SnapshotValidationResult`; `RawSnapshotStore.save_auto_validated(candidate, response, validation)`.

- [ ] Write tests for valid HTML/JSON/PDF/TXT and for empty, 404 body, access/login/search page, error JSON, and fake PDF rejection.
- [ ] Run the focused test module and confirm it fails before production code exists.
- [ ] Implement byte-level validation with configured size/text heuristics and stable reason codes.
- [ ] Write a test proving invalid bytes never create a file or append `sources_manifest.jsonl`.
- [ ] Implement an auto-save path that requires a successful validation result while leaving V1 `save()` approval enforcement unchanged.
- [ ] Run V1 and V2 snapshot tests and commit only Task 2 files.

### Task 3: Attempt ledger, replacement loop, CLI, and reports

**Files:**
- Create: `ai-service/app/rag/source_ingestion/attempts.py`
- Create: `ai-service/app/rag/source_ingestion/auto_service.py`
- Create: `ai-service/app/rag/source_ingestion/report.py`
- Create: `ai-service/scripts/source_ingestion_v2.py`
- Test: `ai-service/tests/test_source_ingestion_v2_service.py`
- Test: `ai-service/tests/test_source_ingestion_v2_cli.py`

**Interfaces:**
- Consumes: discovery candidates grouped by `replacement_group`, filter, deduper, fetchers, validator, snapshot store.
- Produces: append-only `source_attempts.jsonl`, success-only raw records, `raw_corpus_manifest.jsonl`, JSON/Markdown corpus report.

- [ ] Write replacement-loop tests proving failures continue to the next candidate and stop after three attempts.
- [ ] Run focused tests and confirm the missing service/ledger behavior fails.
- [ ] Implement atomic append records for all required attempt statuses and batch continuation.
- [ ] Write CLI/report tests with literal expected counts, publisher distribution, topic coverage, and readiness gates.
- [ ] Implement `run`, `validate`, and `report` commands plus deterministic report aggregation.
- [ ] Run focused tests, then all source-ingestion tests, and commit only Task 3 files.

### Task 4: Real discovery, fetch, validation, and corpus audit

**Files:**
- Create: `ai-service/data/source_ingestion/jay_chou_corpus_v1/discovered_candidates_v2.json`
- Generate (Git-ignored): `ai-service/data/raw/source_attempts.jsonl`
- Generate (Git-ignored): `ai-service/data/raw/raw_corpus_manifest.jsonl`
- Generate (Git-ignored): `ai-service/data/raw/source_corpus_report.json`
- Generate (Git-ignored): `ai-service/data/raw/source_corpus_report.md`

**Interfaces:**
- Consumes: primary/official, mainstream/professional, academic, encyclopedia, and open metadata sources found through web discovery.
- Produces: 30-50 traceable candidates and a validated 55-70 snapshot corpus, or an evidence-backed unresolved-gap report if legal sources cannot satisfy a gate.

- [ ] Audit the existing 36 snapshots and failed 14-source metadata to define topic and format gaps.
- [ ] Search the public web and capture 30-50 source-backed candidates with title, publisher, tier, topics, reason, file type, group, and discovery URL evidence.
- [ ] Validate the discovery manifest and commit it separately from generated raw data.
- [ ] Run `python scripts/source_ingestion_v2.py run --target-count 70 --max-replacement-attempts 3` without invoking parser code.
- [ ] Re-run idempotently to prove existing valid source ids and duplicate candidates are not fetched twice.
- [ ] Run the corpus validator and generate both corpus manifests and both report formats.
- [ ] Verify every file/hash/signature, format and publisher counts, topic coverage, attempt outcomes, and readiness gates.
- [ ] Run the complete `ai-service` test suite and inspect `git status`/diff before the final commit.
