# Music RAG Source Ingestion V2 Design

## Objective

Strengthen Jay Chou Corpus V1 from 36 validated raw snapshots to 55-70 without
running Parse + Normalize or any downstream cleaning, chunking, linking, claim,
embedding, or indexing stage.

The run combines replacement discovery for 14 failed V1 sources with broader
source expansion. It discovers 30-50 new candidates, applies deterministic
quality and duplicate gates, fetches accepted candidates without human approval,
validates the response before persistence, and stops after raw-corpus reporting.

## Architecture

```text
Discovery pool / provider
  -> AutoSourceQualityFilter
  -> CandidateDeduplicator
  -> PolicyAwareHttpClient + format fetcher
  -> SnapshotValidator
  -> RawSnapshotStore.save_auto_validated
  -> sources_manifest.jsonl (valid snapshots only)

Every decision and attempt
  -> source_attempts.jsonl (append-only)

Validated manifest + candidate metadata + attempt ledger
  -> raw_corpus_manifest.jsonl
  -> source_corpus_report.json
  -> source_corpus_report.md
```

Existing V1 approval-gated APIs remain compatible. V2 adds a separate automatic
service; it never weakens the V1 approval boundary. Only the V2 service may call
the auto-save method, and only after an accepted filter decision and successful
snapshot validation.

## Contracts

`DiscoveredCandidate` carries the existing source metadata plus discovery query,
discovery reason, optional replacement source id, replacement group, priority,
known external id, and optional canonical URL. Source ids are new and unique.

`SourceAttemptRecord` is append-only and records one of:

- `filtered_out`
- `fetch_failed`
- `snapshot_valid`
- `unresolved_source_gap`
- `replacement_attempt`
- `duplicate_candidate`

It stores the candidate identity, URL, topic/replacement group, attempt number,
timestamp, failure or decision reason, and raw path when a snapshot was accepted.
The existing `sources_manifest.jsonl` continues to contain only strict
`RawSnapshotRecord` objects so Parser consumers remain stable.

## Automatic Quality Filter

Candidates pass only when all rules hold:

- artist is Jay Chou / 周杰伦 and topics intersect the configured corpus topics;
- source tier is 1-3 and source quality is not low;
- the HTTP(S) URL is not a search, login, ad, tracking-only, forum, generic
  aggregator, or known AI-content page;
- the title and reason have a corpus-topic relevance signal;
- publisher is nonblank and belongs to an allowed trust class: official,
  record-label, interview, mainstream/professional media, academic, encyclopedia,
  government, or metadata API.

Rejection is fail-closed and emits `filtered_out` with stable reason codes.

## Candidate Deduplication

Before fetching, V2 removes duplicates against existing V1/V2 candidates,
validated raw snapshots, and earlier V2 decisions using:

- normalized URL (lowercase host, normalized path, sorted non-tracking query);
- declared canonical URL;
- normalized `(publisher, title)`;
- known external id.

This is URL/identity deduplication only. It does not inspect or deduplicate article
body semantics.

## Fetch, Validation, and Replacement

The existing client continues to enforce robots.txt, redirect validation,
per-origin pacing/crawl delay, User-Agent, timeout, and size limits. V2 never
bypasses authentication, anti-bot, 403, or robots denial.

Validation occurs before persistence:

- HTML: nonempty, compatible content type, not a 404/access/login/search error page;
- JSON: decodes and parses, is not an error envelope;
- PDF: nonempty, starts with `%PDF-`, is not an HTML error response;
- TXT: nonempty and decodable using supported common encodings.

Each discovery group contains ranked alternatives. On a failed fetch or validation,
the service records the failure and attempts the next accepted candidate, capped
at `max_replacement_attempts = 3`. Exhausted groups emit
`unresolved_source_gap`. Expansion candidates use the same mechanism.

## Completion Gates

The corpus is ready for Parse + Normalize only when all of these are true:

- 55-70 validated raw snapshots;
- 5-8 total PDF snapshots, including at least three new valid PDFs;
- at least 8 distinct HTML publishers or clearly distinct templates;
- structured JSON remains represented;
- every configured core topic has at least one validated source;
- every raw record points to a nonempty file whose SHA-256 matches the ledger;
- every JSON parses and every PDF has a valid magic header;
- no untracked parse or downstream processing was run by this workflow.

## Outputs

- `data/raw/sources_manifest.jsonl`: existing stable success-only ledger
- `data/raw/source_attempts.jsonl`: append-only filter/fetch/replacement ledger
- `data/raw/raw_corpus_manifest.jsonl`: validated corpus joined with source metadata
- `data/raw/source_corpus_report.json`: machine-readable totals and gate results
- `data/raw/source_corpus_report.md`: technical reader-facing report
- `data/source_ingestion/jay_chou_corpus_v1/discovered_candidates_v2.json`:
  source-backed discovery pool for this run

## Testing

Unit tests cover filter decisions, every duplicate key, each validator failure
class, validation-before-write, append-only attempts, replacement caps, batch
continuation, report aggregation, and V1 compatibility. Integration tests use
local HTTP mocks and temporary directories. The production run then validates
every real snapshot by path, hash, media signature, metadata join, topic coverage,
and distribution counts.
