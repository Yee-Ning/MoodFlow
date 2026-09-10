# Music RAG Normalized Corpus V1 Execution Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Validate the existing HTML, JSON, and PDF Parse + Normalize pipeline against representative immutable Raw Snapshots, repair only demonstrated parser-layer defects, then produce the complete 56-source Normalized Corpus V1 and auditable corpus reports.

**Architecture:** The existing `ParseNormalizeService` remains the only parse entry point. A corpus runner reads the validated raw corpus ledger, selects a deterministic publisher/format/complexity-stratified batch, invokes the service without network access, reviews the generated contracts and quality signals, and publishes a normalized manifest plus JSON/Markdown reports. Parser changes are permitted only after a representative snapshot exposes a reproducible defect.

**Tech Stack:** Python 3.12, Pydantic 2, PyMuPDF/PyMuPDF4LLM, Beautiful Soup, unittest.

**Specs:** `docs/superpowers/specs/2026-09-09-music-rag-parse-normalize-v1-design.md`, `docs/superpowers/specs/2026-09-09-music-rag-pdf-parse-normalize-v1-design.md`, and the user-approved corpus execution request dated 2026-09-10.

## Constraints

- Read only existing files under `ai-service/data/raw`; never fetch or modify a Raw Snapshot.
- Parse all and only the 56 records in `data/raw/raw_corpus_manifest.jsonl`.
- Keep MinerU and Docling optional and lazily loaded; do not install model weights.
- Stop after Parse + Normalize artifacts and reports. Do not implement Cleaning or later stages.
- Treat navigation, ads, footers, recommendations, copyright text, and template repetition as future Cleaning observations, not parser failures.

### Task 1: Establish Corpus Contracts and Baseline

**Files:**
- Modify: `ai-service/app/rag/parse_normalize/manifest.py`
- Modify: `ai-service/app/rag/parse_normalize/service.py`
- Test: `ai-service/tests/test_parse_normalize_service.py`
- Test: `ai-service/tests/test_parse_normalize_corpus.py`

- [ ] Add tests proving the enriched validated raw corpus manifest is accepted while core snapshot fields remain strictly validated.
- [ ] Add source metadata fallback so V2 sources retain publisher/title provenance in `NormalizedDocument`.
- [ ] Run all existing Parse + Normalize and PDF tests before corpus execution.

### Task 2: Add Deterministic Corpus Execution and Reporting

**Files:**
- Create: `ai-service/app/rag/parse_normalize/corpus.py`
- Create: `ai-service/run_parse_corpus.py`
- Modify: `ai-service/app/rag/parse_normalize/__init__.py`
- Test: `ai-service/tests/test_parse_normalize_corpus.py`

- [ ] Test deterministic representative selection: 7 diverse HTML, all 5 PDF, and 2 structurally distinct JSON snapshots.
- [ ] Test normalized manifest rows, aggregate counts, parser usage/success rates, quality issue buckets, low-quality/manual-review/failure/fallback lists, and Markdown report output.
- [ ] Implement commands for representative selection, representative parse/review reporting, full batch parse, and report-only regeneration.
- [ ] Ensure every source produces either the full normalized contract or at least `parse_report.json`.

### Task 3: Execute and Review the Representative Batch

**Generated artifacts:**
- `ai-service/data/normalized/representative_parse_batch.json`
- `ai-service/data/normalized/representative_parse_validation.json`
- `ai-service/data/normalized/representative_parse_validation.md`

- [ ] Generate the non-random 14-source batch from raw-ledger metadata.
- [ ] Parse the batch from local Raw Snapshots with `--parser auto --force`.
- [ ] Inspect Markdown, blocks, traceability, parser attempts, and quality scores for every sample.
- [ ] Record PASS/MINOR/MAJOR/FAILED findings and separate parser defects from future Cleaning noise.
- [ ] If a real parser defect is found, reproduce it in a failing test, implement the narrow fix, and rerun the batch.

### Task 4: Execute the Complete Corpus

**Generated artifacts:**
- `ai-service/data/normalized/normalized_corpus_manifest.jsonl`
- `ai-service/data/normalized/parse_corpus_report.json`
- `ai-service/data/normalized/parse_corpus_report.md`

- [ ] Parse all 56 validated raw records, continuing after per-source failure.
- [ ] Generate the normalized manifest only from actual output artifacts and reports.
- [ ] Generate JSON and Markdown corpus summaries with format, parser, outcome, latency, issue, fallback, manual-review, and failure statistics.
- [ ] Run the same batch without `--force` and verify successful unchanged sources are skipped.

### Task 5: Verify and Freeze

- [ ] Verify all 56 raw hashes before and after execution to prove Raw Snapshot immutability.
- [ ] Validate every `document.json` and `parse_report.json` against the Pydantic contracts and check contiguous block ordering plus Markdown consistency.
- [ ] Run focused corpus tests, the complete Parse + Normalize/PDF test suite, and the full project test suite.
- [ ] Review `git status` and diff, commit only task code/tests/docs, and report generated artifact paths and remaining optional-backend limitations.

