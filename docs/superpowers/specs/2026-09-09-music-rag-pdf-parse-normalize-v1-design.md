# Music RAG PDF Parse + Normalize V1 Design

## Scope

Extend the existing Parse + Normalize V1 pipeline with a PDF-only cascade. The
pipeline reads immutable raw snapshot bytes and produces the existing
`NormalizedDocument` contract, normalized Markdown, a PDF-specific parse report,
and extracted images. It does not implement Cleaning, Chunking, Embedding, Vector
DB, or VLM parsing.

The approved deployment policy is **lightweight main environment plus lazy
adapters**:

- PyMuPDF4LLM is the only new required PDF runtime in the main environment.
- MinerU and Docling are optional runtimes loaded only when their adapters run.
- V1 does not install MinerU or Docling, download model weights, or make an
  implicit network request for a model.
- Missing optional runtimes are recorded as parser attempts and participate in
  fallback instead of crashing the batch.

## Pipeline

```text
RawSnapshot bytes
  -> PDFInspector
  -> PDFProfile: SIMPLE_TEXT | COMPLEX_LAYOUT | SCANNED | UNKNOWN
  -> PDFParserRouter
       SIMPLE_TEXT                    -> PyMuPDF4LLM
       COMPLEX_LAYOUT / SCANNED       -> MinerU
       UNKNOWN                        -> PyMuPDF4LLM
  -> PDFParseQualityEvaluator
  -> if primary score < pass threshold or primary raises
       Docling fallback
  -> Quality Gate
       accepted primary               -> pass | warning
       accepted Docling               -> fallback_used
       inspectable low-quality output -> manual_review
       no usable output               -> failed
  -> NormalizedDocument + PDFParseReport + image artifacts
```

A forced CLI parser performs one requested adapter attempt and still applies the
quality gate. Forced runs do not silently cascade to another adapter, because the
override is a diagnostic operation. `auto` is the default and the only mode that
uses the full cascade.

## Shared Contract Evolution

Existing HTML, Markdown, TXT, and JSON behavior remains source-compatible.

`NormalizedDocument` gains typed, default-empty `tables` and `images` fields.
`DocumentTable` stores `table_id`, optional `page_number`, optional `bbox`,
Markdown, rows, and metadata. `DocumentImage` stores `image_id`, optional page and
bbox, relative path, content type, optional caption, and metadata. PDF blocks
store one-based `page_number` and, when the backend provides it, `bbox` in block
metadata.

`ParseStatus` gains `fallback_used` and `manual_review`. `ParseMetrics` gains
defaulted PDF metrics so existing non-PDF reports remain valid:

- `chars_per_page`
- `empty_page_ratio`
- `replacement_char_ratio`
- `suspicious_line_break_ratio`
- `image_count`
- `parser_error_count`

`ParseReport` gains optional `selected_parser` and default-empty
`parser_attempts`. Each `ParserAttempt` records parser name/version, outcome,
quality score, error, elapsed time, and fallback reason. Existing `parser_name`
is retained and equals the selected parser for PDF reports.

The parser execution boundary returns a `ParserExecution`: the normalized parser
result, selected adapter identity, optional backend-supplied report, and bounded
output artifacts. Existing `BaseParser` implementations receive a default
execution method, so their code does not depend on PDF classes.

## PDF Inspector

`PDFInspector` uses PyMuPDF only and never invokes OCR. It reads the raw bytes in
memory and returns `PDFInspection` with:

- page count and document metadata
- native text availability and total extracted characters
- average characters per page and empty-text page ratio
- image count
- scan, multi-column, and table hints
- final `PDFProfile`

Every threshold lives in immutable `PDFInspectorConfig`. Defaults are:

- an empty text page has fewer than 20 stripped characters
- a likely scan has at least 70% empty-text pages and at least 0.5 images per
  page, or no native text and at least one image
- a native simple document has at least 80 characters per page and no more than
  20% empty-text pages
- multi-column detection needs at least two non-crossing text blocks on both
  sides of the page midpoint on at least 30% of pages
- a table hint needs at least two horizontal and two vertical drawing segments
- more than 1.5 images per page is a complex-layout hint

Classification precedence is SCANNED, COMPLEX_LAYOUT, SIMPLE_TEXT, then UNKNOWN.
Malformed/encrypted PDFs raise a confined inspection error that is written to the
parse report.

## Adapter Boundary

All PDF engines implement `PDFParserAdapter`. The interface accepts immutable
bytes plus the inspection profile and returns `PDFAdapterOutput` containing
Markdown-derived blocks, tables, images, backend metadata, and binary image
artifacts. The base interface also reserves `parse_page(...)` and
`reparse_failed_pages(...)`; V1 implementations raise an explicit
`page_level_fallback_not_implemented` error rather than pretending to merge
pages.

### PyMuPDF4LLM

The adapter lazily imports `pymupdf` and `pymupdf4llm`, opens the byte stream in
memory, and calls `to_markdown(..., page_chunks=True, use_ocr=False,
force_ocr=False)`. Page chunks provide page metadata, tables, images, and page
Markdown. The existing Markdown adapter maps each page to shared blocks, after
which the PDF adapter reassigns stable document-wide block IDs and attaches the
one-based page number. It never enables OCR.

### MinerU

The default runtime invokes a configurable `mineru` executable with a timeout and
an explicit output directory. It uses a fingerprinted cache below
`data/parsed_cache/mineru/{source_id}/` and never writes to the raw snapshot.
V1 reads, in priority order, `content_list_v2.json`, `content_list.json`,
`middle.json`, and Markdown. Structured content is the source for page numbers,
bboxes, headings, tables, images, lists, and reading order; Markdown is retained
as a secondary representation. An injectable runner permits deterministic
contract tests without installing MinerU or model weights.

### Docling

The adapter lazily imports Docling and converts an in-memory `DocumentStream`.
It consumes both `export_to_markdown()` and `export_to_dict()` and maps the lossless
document representation to the shared model. OCR is disabled by default and is
enabled only when the inspection profile is SCANNED or the fallback reason says
native text is unusable. An injectable runtime permits deterministic contract
tests without installing Docling or its models.

### VLM Reservation

`VLMPageParser` exists behind `enabled=False`. Calling it while disabled raises
`vlm_page_parser_disabled`; it performs no network or model calls in V1.

## PDF Quality Gate

`PDFParseQualityConfig` contains all scoring weights and the
`quality_pass_threshold` and `quality_fallback_threshold`. The evaluator records:

- text length and characters per page
- empty page, garbled character, and replacement character ratios
- heading, paragraph, table, and image counts
- duplicated line and suspicious line-break ratios
- parser error count

Primary output at or above the pass threshold is accepted. Primary output below
the pass threshold triggers Docling in auto mode. A Docling result at or above
the pass threshold is accepted as `fallback_used`. A final result between the
fallback and pass thresholds is written with `manual_review`; the report makes
the low quality explicit. A final score below the fallback threshold, empty
content, or no successful result writes only `parse_report.json` with `failed`.

Warnings are stable machine-readable codes. No low-quality Markdown receives a
`pass` status.

## Persistence and Idempotency

Successful and manual-review PDF outputs are:

```text
data/normalized/{source_id}/
  document.md
  document.json
  parse_report.json
  images/
```

Binary artifacts are written atomically after rejecting absolute paths and `..`
segments. The PDF image directory always exists for a persisted PDF document.
Failure still produces `parse_report.json` and removes stale normalized document
and image outputs, never raw files.

Auto-mode idempotency compares raw content hash, PDF cascade version,
normalization version, and requested parser mode. This avoids rerunning the
cascade merely because a previous fallback selected a different backend.
`--force` bypasses the check.

## CLI

`run_parse.py` adds:

```text
--parser auto|pymupdf|mineru|docling
```

The default is `auto`. An explicit PDF backend on a non-PDF source returns a
per-source failure. Accepted `pass`, `warning`, and `fallback_used` results use
exit code 0; `manual_review`, `failed`, unsupported, or missing snapshots use exit
code 2.

## Testing

Tests use three genuine PDF byte streams generated once with ReportLab and stored
as immutable fixtures:

1. a native text PDF classified SIMPLE_TEXT and parsed by the real installed
   PyMuPDF4LLM runtime;
2. a two-column/table PDF classified COMPLEX_LAYOUT and mapped through the MinerU
   adapter using a complete official-format structured-output fixture;
3. an image-only PDF classified SCANNED, receiving an inadequate MinerU result,
   then an accepted Docling structured-output fixture and `fallback_used` status.

Separate tests prove missing optional dependencies, timeouts, malformed output,
low-quality manual review, total failure, batch continuation, forced parser
behavior, path-safe image persistence, page fallback placeholders, and disabled
VLM behavior. Tests never download MinerU or Docling models.

## Explicit Non-goals

- No Cleaning or template removal
- No Chunking or page-level merge implementation
- No Embedding or Vector DB
- No VLM calls
- No automatic model download
- No claim or entity extraction
