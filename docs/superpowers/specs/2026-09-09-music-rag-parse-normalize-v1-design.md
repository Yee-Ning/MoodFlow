# Music RAG Parse + Normalize V1 Design

## Purpose

Build the offline stage that converts immutable HTML, Markdown, TXT, and JSON
raw snapshots into one parser-independent `NormalizedDocument` contract. The
stage writes Markdown, structured JSON, and a lightweight parse-quality report.
It does not clean, deduplicate, chunk, link entities, extract claims, embed, or
index content.

PDF is intentionally unsupported in this version. A PDF input must produce an
explicit `unsupported_for_current_stage` result and must not be routed to any
other parser.

## Existing Inputs and Provenance

The default raw ledger is `ai-service/data/raw/sources_manifest.jsonl`. Each
line is an existing `RawSnapshotRecord` containing the source ID, content type,
SHA-256 content hash, raw path, source quality, and corpus version. Raw payloads
remain immutable and are only read by this stage.

The default candidate manifest is
`ai-service/data/source_ingestion/jay_chou_corpus_v1/sources_candidates.json`.
It supplies reviewed source metadata such as title, author, publication date,
and approval status. Parsing never changes either manifest.

When a source has multiple raw ledger entries, selection uses the entry with
the latest timezone-aware `fetched_at`. The selected file's SHA-256 digest is
recomputed before parsing and must equal the ledger value. A mismatch produces
a failed parse report and no normalized document.

## Architecture

```text
RawSnapshotRecord + raw bytes + optional CandidateSource
  -> RawSnapshotLoader
  -> FileTypeDetector
  -> ParserRouter
       -> ParserRegistry[DetectedFileType]
       -> HtmlParser | MarkdownParser | TxtParser | JsonParser
  -> ParserResult containing semantic DocumentBlock values
  -> MarkdownNormalizer
  -> ParseQualityEvaluator
  -> NormalizedDocument
  -> AtomicNormalizedWriter
       -> document.md
       -> document.json
       -> parse_report.json
```

The format adapters understand source syntax. They do not select files, write
artifacts, evaluate quality, or implement downstream cleaning. The shared
normalizer owns Markdown serialization, so formatting rules do not diverge
between adapters. Future Cleaning and Chunking code may depend only on
`NormalizedDocument` and `DocumentBlock`, never on a concrete parser class.

## Modules and Responsibilities

The implementation lives under `ai-service/app/rag/parse_normalize/`:

- `models.py`: strict Pydantic document, block, report, metrics, input, and
  parser-result contracts.
- `detection.py`: content-type and extension classification.
- `base.py`: the `BaseParser` abstract interface and parser exceptions.
- `registry.py`: parser registration and lookup without format conditionals in
  orchestration code.
- `router.py`: detection, registry lookup, and explicit unsupported handling.
- `parsers/html.py`: DOM-based HTML adapter.
- `parsers/markdown.py`: token-based Markdown adapter and front matter reader.
- `parsers/text.py`: byte decoding and conservative plain-text structure.
- `parsers/json.py`: Wikidata, MusicBrainz, and bounded generic JSON adapters.
- `normalizer.py`: deterministic block-to-Markdown serialization.
- `quality.py`: lightweight metrics, warnings, score, and status.
- `manifest.py`: raw JSONL loading, validation, and latest-snapshot selection.
- `writer.py`: idempotency check and atomic normalized artifact writes.
- `service.py`: one-source and batch orchestration.

`ai-service/run_parse.py` is the operator CLI. It composes these modules but
contains no parser-specific branches.

## Unified Models

### DocumentBlock

`DocumentBlock` is strict and contains:

- `block_id`: deterministic within the document, using source ID and order.
- `block_type`: `heading`, `paragraph`, `list`, `quote`, `code`, `table`,
  `image`, or `other`.
- `text`: unrewritten semantic text represented by the block.
- `level`: heading level when applicable; otherwise null.
- `order_index`: zero-based document order.
- `parent_block_id`: parent heading or container block when known.
- `metadata`: format-specific, JSON-serializable details such as link targets,
  code language, list ordering, table cells, or image attributes.

All blocks validate unique IDs and contiguous `order_index` values inside a
document. Heading levels are restricted to 1 through 6.

### NormalizedDocument

`NormalizedDocument` is strict and contains the requested fields:

- `document_id`, `source_id`, `title`, `author`, `published_at`, `language`
- `original_file_type`, `raw_path`
- `parser_name`, `parser_version`
- `markdown`, `blocks`, `metadata`
- `parse_quality_score`, `corpus_version`

It additionally contains `raw_content_hash` and `normalization_version` as
top-level provenance fields. `document_id` is deterministic from source ID,
raw hash, parser name/version, and normalization version. Source-manifest
metadata takes precedence over parser-derived metadata; parser-derived title,
author, date, and language are retained in `metadata.parser_metadata`.

### ParseReport

`ParseReport` contains `source_id`, `parser_name`, `quality_score`, `status`,
`warnings`, `metrics`, and `elapsed_ms`. Status is `pass`, `warning`, or
`failed`. Metrics contain `text_length`, `heading_count`, `paragraph_count`,
`table_count`, `empty_content`, `garbled_char_ratio`, and
`duplicated_line_ratio`.

Operational failures also produce a report. Its `parser_name` is the selected
parser name when selection succeeded, otherwise `unselected`; the warning list
contains a stable error code rather than a traceback or raw exception detail.

## File-Type Detection and Routing

`FileTypeDetector` normalizes the media type by removing parameters and uses
this precedence:

1. A recognized specific content type: HTML, Markdown, JSON, plain text, or
   PDF.
2. For generic or absent content types, a recognized file extension.
3. Otherwise `unknown`.

Recognized JSON types include `application/json` and `application/*+json`.
Markdown includes `text/markdown` and `.md`/`.markdown`. Plain text includes
`text/plain` and `.txt`. Generic `application/octet-stream` defers to the file
extension.

`ParserRegistry` maps a detected type to a `BaseParser` instance. Registration
rejects duplicate types. `ParserRouter` performs detection once, explicitly
rejects PDF as `unsupported_for_current_stage`, rejects unknown types as
`unsupported_file_type`, and otherwise resolves the adapter through the
registry. No calling service branches on HTML, Markdown, TXT, or JSON.

## Parser Contract

Every adapter implements:

```python
class BaseParser(ABC):
    parser_name: str
    parser_version: str
    supported_file_types: frozenset[DetectedFileType]

    @abstractmethod
    def parse(self, parse_input: ParseInput) -> ParserResult: ...
```

`ParseInput` carries the validated snapshot record, immutable raw bytes, and
optional reviewed candidate metadata. `ParserResult` carries title, author,
publication date, language, ordered blocks, and parser metadata. It does not
carry output paths or quality decisions.

## HTML Adapter

The HTML adapter uses Beautiful Soup's DOM model. It removes only `script`,
`style`, and `noscript`. It does not identify ads, remove navigation or footer
templates, filter SEO text, or deduplicate content.

It reads title and standard page metadata from `<title>`, canonical link,
`meta[name]`, Open Graph, and article metadata. A document-order walker emits
headings, paragraphs, lists, block quotes, preformatted code, tables, and
images. Atomic containers such as lists and tables are emitted once so nested
text is not duplicated. Heading ancestry supplies `parent_block_id`.

Table cells and image URL/alt/title remain in block metadata. Images are not
downloaded. Relative links and image URLs are resolved against the original
source URL and recorded; this does not fetch them.

## Markdown Adapter

The Markdown adapter uses a CommonMark token stream with table support. A
leading YAML front matter document is parsed with safe YAML loading and stored
under `metadata.front_matter`; it is not rendered as body text.

Headings, paragraphs, ordered and unordered lists, quotes, fenced or indented
code, tables, and images become blocks. Inline text and link targets are
preserved. Re-rendering standardizes heading spaces, list markers, line endings,
and blank lines without paraphrasing or correcting the source text.

## TXT Adapter

The TXT adapter decodes in this order: BOM-declared encoding, charset from the
snapshot content type, strict UTF-8, then bounded common-encoding detection for
UTF-16, GB18030, and Big5. Replacement decoding is not silently accepted; a
low-confidence or failed decode produces a parser error and failed report.

Line endings become LF. Blank-line-separated content becomes paragraphs. A
line is treated as a heading only when a conservative configurable heuristic
matches: it is short, isolated by a blank line, has no sentence-ending
punctuation, and is followed by body content. Otherwise it remains a paragraph.
No title or structure is invented by a model.

## JSON Adapter

JSON is decoded as UTF-8 variants and parsed to Python structured data. The
complete source structure remains in `NormalizedDocument.metadata.raw_json`.
Limits affect generated Markdown and extracted display fields, not the retained
raw structure.

Schema profiles are selected inside the JSON adapter:

- Wikidata: recognizes `entities`, records entity IDs, multilingual labels,
  descriptions, aliases, claims, external IDs, and relation-like entity-valued
  claims.
- MusicBrainz: records MBIDs, entity names/types, aliases, relations, releases,
  release groups, artists, recordings, tags, genres, and external URLs when
  present.
- Generic: recursively renders scalar fields and bounded lists/maps.

Generic rendering is controlled by one immutable configuration object with
maximum recursion depth, scalar display length, collection items per level,
and total Markdown characters. Truncation is explicit in block metadata and
warnings. The parser never implements `json.dumps(raw)` as its Markdown
representation.

## Markdown Normalization

The normalizer serializes blocks deterministically:

- Headings use one through six `#` characters followed by one space.
- Paragraphs are separated by one blank line.
- Quotes prefix each line with `> `.
- Lists use `- ` for unordered items and `1. ` for ordered items.
- Code uses fenced blocks and preserves a declared language when available.
- Simple rectangular tables use Markdown table syntax with escaped cell pipes.
- Images use Markdown image syntax while retaining all attributes in metadata.
- Unsupported complex structures remain in block metadata and receive a
  conservative textual representation rather than being discarded.

The final document uses LF and UTF-8, has one trailing newline, and never has
more than one blank line between blocks.

## Parse Quality Evaluation

`ParseQualityConfig` contains all thresholds. The evaluator joins block text
without changing it and calculates:

- text length and empty-content status
- heading, paragraph, and table counts
- garbled-character ratio from replacement characters, disallowed control
  characters, and private-use characters
- duplicated-line ratio from repeated non-empty normalized lines

The score ranges from 0 to 1 and applies documented configurable penalties for
empty content, garbling, and excessive duplication. Empty content or a parser
failure is `failed`; content below the warning score or above a ratio threshold
is `warning`; other content is `pass`. `NormalizedDocument.parse_quality_score`
must equal the report score.

## Output and Idempotency

Successful and warning parses write:

```text
ai-service/data/normalized/{source_id}/
  document.md
  document.json
  parse_report.json
```

Writes use UTF-8, temporary sibling files, flush plus `fsync`, then atomic
replacement. Raw files are never opened for writing. A failed parse writes only
`parse_report.json`; stale `document.md` and `document.json` from a different
fingerprint are removed through explicit file targets, never by deleting the
source directory.

Before parsing, the writer loads an existing `document.json`. Matching
`raw_content_hash`, `parser_version`, and `normalization_version` returns
`skipped` without rewriting artifacts. `--force` bypasses the skip and atomically
replaces normalized artifacts. A changed fingerprint always reparses.

## CLI

`ai-service/run_parse.py` supports one mutually exclusive selection mode:

```text
python run_parse.py --source-id SOURCE_ID [--force]
python run_parse.py --all-approved [--force]
python run_parse.py --manifest RAW_MANIFEST.jsonl [--force]
```

`--source-id` selects the latest default-ledger snapshot for one source.
`--all-approved` intersects approved candidates with available default-ledger
snapshots and reports approved sources that have not yet been fetched without
crashing the batch. `--manifest` parses the latest record per source from the
specified raw ledger. Batch processing isolates failures per source and prints
a JSON summary containing parsed, skipped, warning, failed, unsupported, and
missing-snapshot results.

A PDF source produces `unsupported_for_current_stage`, writes no normalized
document, and is visible in both single and batch results. An unknown type
produces `unsupported_file_type`.

## Error Handling

Manifest validation, path confinement, hash verification, decoding, parsing,
normalization, quality evaluation, and output writes have distinct exception
types. The service converts expected per-source exceptions into structured
results and failed reports. Batch mode continues with other sources. Tracebacks
remain in operator logs only and are never written into document metadata.

Raw paths are resolved beneath the configured project root. Absolute paths and
paths escaping the root are rejected. JSON and Markdown output limits prevent
unbounded expansion, while the Source Ingestion response-size limit remains the
first input-size boundary.

## Dependencies

Declare direct, bounded dependencies for capabilities used by this subsystem:

- Beautiful Soup 4 for HTML DOM traversal.
- markdown-it-py for Markdown tokenization.
- PyYAML safe loading for front matter.
- charset-normalizer for conservative common-encoding detection.

No LLM, browser automation, PDF library, OCR engine, or vector database is
called by this stage.

## Test Strategy

Tests follow the real disk boundary: they create immutable raw payload files and
a valid raw snapshot JSONL ledger, then execute the loader, detector, router,
adapter, normalizer, evaluator, and writer. They assert the three output files
and validate `document.json` and `parse_report.json` through Pydantic models.

The committed hermetic fixtures cover two HTML snapshots, one Markdown
snapshot, one non-UTF-8 TXT snapshot, one Wikidata JSON snapshot, and one
MusicBrainz-shaped JSON snapshot. Fixtures are concise and clearly labeled as
test material; they are not added to the production Jay Chou corpus.

A local integration test command also parses the four genuinely fetched raw
snapshots currently present in `data/raw`: two official HTML pages and two
Wikidata JSON entities. This live-snapshot check is reported separately because
raw payloads are intentionally ignored by Git and are unavailable in a clean
checkout.

Tests additionally cover MIME/extension routing, PDF rejection, unknown input,
path traversal, content-hash mismatch, malformed input for each parser,
conservative TXT headings, bounded generic JSON rendering, metadata retention,
quality status transitions, atomic output, repeat-skip behavior, `--force`,
and batch failure isolation.

## Acceptance Criteria

- HTML, Markdown, TXT, and JSON raw snapshots reach the correct registered
  adapter and produce valid parser-independent documents.
- All requested block types and provenance fields are represented.
- Wikidata/MusicBrainz structure is retained and rendered for retrieval without
  unbounded Markdown expansion.
- Quality metrics and status are persisted and agree with the document score.
- Output is deterministic, UTF-8, atomic, repeatable, and forceable.
- PDF and unknown inputs are explicit, observable unsupported results.
- Raw snapshots remain byte-identical.
- No code for Cleaning, Deduplication, Chunking, Entity Linking, Claim
  Extraction, Embedding, or Vector DB is introduced.
