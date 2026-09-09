# Music RAG PDF Parse + Normalize V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a quality-gated PDF cascade that inspects immutable snapshots, routes to PyMuPDF4LLM or lazy MinerU, falls back to lazy Docling, and writes traceable normalized artifacts without installing heavyweight model runtimes.

**Architecture:** Extend the existing parser execution contract so a parser may supply a rich report and image artifacts while all existing format adapters keep their default behavior. A registered `PdfCascadeParser` owns inspection, PDF adapter routing, attempt capture, quality gating, and fallback; the existing service remains the single persistence boundary.

**Tech Stack:** Python 3.12, Pydantic 2, PyMuPDF4LLM 1.28.x, PyMuPDF 1.28.x, optional MinerU 3.4.x CLI, optional Docling 2.126.x API, unittest, ReportLab fixture generator, Poppler visual verification.

**Spec:** `docs/superpowers/specs/2026-09-09-music-rag-pdf-parse-normalize-v1-design.md`

## Global Constraints

- Do not implement Cleaning, Chunking, Embedding, Vector DB, claim extraction, or entity linking.
- Read raw snapshot bytes once, verify their ledger hash, and never modify raw files.
- Install PyMuPDF4LLM only; MinerU and Docling remain lazy optional runtimes and no model weights are downloaded.
- OCR is disabled for PyMuPDF4LLM and is requested from an optional backend only for scanned or text-unusable paths.
- Every threshold, timeout, and inspection heuristic is configuration, never an inline magic number.
- Every backend exception becomes a bounded `ParserAttempt`; no single PDF may crash a manifest batch.
- A low-quality result never receives `pass` status.
- Preserve source compatibility for HTML, Markdown, TXT, and JSON parsing and reports.
- PDF output image paths are confined below `data/normalized/{source_id}/images/`.
- Use TDD for every production behavior and create one Git commit per independently verified task.

---

### Task 1: Shared Execution, Tables, Images, Reports, and Artifact Persistence

**Files:**
- Modify: `ai-service/app/rag/parse_normalize/models.py`
- Modify: `ai-service/app/rag/parse_normalize/base.py`
- Modify: `ai-service/app/rag/parse_normalize/writer.py`
- Modify: `ai-service/app/rag/parse_normalize/service.py`
- Modify: `ai-service/app/rag/parse_normalize/__init__.py`
- Test: `ai-service/tests/test_pdf_parse_contracts.py`
- Test: `ai-service/tests/test_parse_normalize_contracts.py`
- Test: `ai-service/tests/test_parse_normalize_service.py`

**Interfaces:**
- Produces `DocumentTable`, `DocumentImage`, `OutputArtifact`, `ParserAttempt`, and `ParserExecution` Pydantic models.
- Produces `BaseParser.execute(parse_input) -> ParserExecution` and `BaseParser.fingerprint(parser_mode="auto") -> str` defaults for existing adapters.
- Extends `AtomicNormalizedWriter.write(document, report, artifacts=())` and fingerprint matching.
- Extends `ParseNormalizeService.parse_source(..., parser_mode="auto")` without adding PDF behavior yet.

- [ ] **Step 1: Write failing shared-contract tests**

Assert literal behavior:

```python
execution = ExistingFixtureParser().execute(parse_input)
self.assertEqual(execution.selected_parser, "fixture")
self.assertEqual(execution.selected_parser_version, "1.0.0")
self.assertIsNone(execution.report)
self.assertEqual(execution.artifacts, [])
self.assertEqual(ExistingFixtureParser().fingerprint(), "fixture:1.0.0:auto")
```

Validate that PDF metrics default to zero in an existing non-PDF report, that
`NormalizedDocument` accepts typed empty tables/images, and that duplicate table
or image IDs are rejected.

- [ ] **Step 2: Write failing artifact confinement tests**

Create a temporary normalized writer. Prove `images/cover.png` is persisted with
exact bytes, while `/absolute.png`, `../escape.png`, and `other/file.png` raise
`UnsafeOutputArtifactError`. Prove `write_failure` removes the prior `images`
directory without touching a sibling raw fixture.

- [ ] **Step 3: Run RED tests**

Run:

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_parse_contracts tests.test_parse_normalize_service -v
```

Expected: imports for the new models and writer behavior fail because they do not
exist.

- [ ] **Step 4: Implement compatible shared models and execution defaults**

Add:

```python
class ParserExecution(BaseModel):
    result: ParserResult | None
    selected_parser: str
    selected_parser_version: str
    report: ParseReport | None = None
    artifacts: list[OutputArtifact] = Field(default_factory=list)

class BaseParser(ABC):
    def execute(self, parse_input: ParseInput) -> ParserExecution:
        return ParserExecution(
            result=self.parse(parse_input),
            selected_parser=self.parser_name,
            selected_parser_version=self.parser_version,
        )

    def fingerprint(self, parser_mode: str = "auto") -> str:
        return f"{self.parser_name}:{self.parser_version}:{parser_mode}"
```

Extend the report and document fields exactly as the design specifies, with
defaults that validate old JSON. Validate unique table and image IDs.

- [ ] **Step 5: Implement safe atomic image persistence and parser fingerprints**

Persist only non-empty relative paths whose first component is `images` and that
contain no `..`. Atomically write each artifact. Replace the old parser-name
fingerprint comparison with a `metadata.parse_fingerprint` string; construct that
metadata from `parser.fingerprint(parser_mode)` before writing each document.

- [ ] **Step 6: Make the service consume `ParserExecution`**

Call `parser.execute`; use the supplied report when present, otherwise run the
existing evaluator. Use the selected adapter name/version in the document. Write
manual-review documents but write report-only failures. Map new PDF statuses to
`OutcomeStatus.FALLBACK_USED` and `OutcomeStatus.MANUAL_REVIEW`. Reject a non-auto
parser mode for any parser that does not support it.

- [ ] **Step 7: Run shared and all non-PDF parse tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_parse_contracts tests.test_parse_normalize_contracts tests.test_parse_normalize_service tests.test_parse_normalize_e2e -v
```

Expected: all tests pass and existing non-PDF artifacts remain valid.

- [ ] **Step 8: Commit shared contract evolution**

```powershell
git add ai-service/app/rag/parse_normalize ai-service/tests/test_pdf_parse_contracts.py ai-service/tests/test_parse_normalize_contracts.py ai-service/tests/test_parse_normalize_service.py
git commit -m "feat(ai): extend parse contracts for pdf artifacts"
```

---

### Task 2: PDF Inspector and Initial Adapter Router

**Files:**
- Create: `ai-service/app/rag/parse_normalize/pdf/__init__.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/models.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/inspector.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/router.py`
- Modify: `ai-service/requirements.txt`
- Test: `ai-service/tests/test_pdf_inspector.py`
- Create: `ai-service/tests/fixtures/pdf_parse/generate_fixtures.py`
- Generate: `ai-service/tests/fixtures/pdf_parse/raw/simple-text.pdf`
- Generate: `ai-service/tests/fixtures/pdf_parse/raw/complex-layout.pdf`
- Generate: `ai-service/tests/fixtures/pdf_parse/raw/scanned.pdf`
- Create: `ai-service/tests/fixtures/pdf_parse/.gitattributes`

**Interfaces:**
- Produces `PDFProfile`, `PDFInspection`, `PDFInspectorConfig`, and `PDFInspector.inspect(raw_bytes)`.
- Produces `PDFParserMode`, `PDFParserRouter.initial_parser(profile, mode)`.
- Makes PyMuPDF available through required `pymupdf4llm>=1.28.2,<2.0`.

- [ ] **Step 1: Write the deterministic PDF fixture generator**

Use ReportLab to generate: two native-text pages; a page with two separated text
columns and a grid table; and an image-only page made by embedding a Pillow image
in a PDF. Add `raw/** -text` so Git never changes PDF bytes. Before running the
generator, execute the PDF artifact-operation marker once as required by the PDF
workflow.

- [ ] **Step 2: Render and inspect fixture pages**

Render all three fixtures with Poppler to `tmp/pdfs/pdf-parse-fixtures/`, inspect
the PNGs, and verify the simple text is legible, complex columns/table do not
overlap, and the scan contains one raster page. Delete only the temporary PNG
directory after verification.

- [ ] **Step 3: Write failing profile and router tests**

Assert the three fixture byte streams produce literal profiles SIMPLE_TEXT,
COMPLEX_LAYOUT, and SCANNED. Assert all metrics and hints, plus configurable
threshold behavior. Assert auto route literals:

```python
self.assertEqual(router.initial_parser(PDFProfile.SIMPLE_TEXT, AUTO), "pymupdf")
self.assertEqual(router.initial_parser(PDFProfile.COMPLEX_LAYOUT, AUTO), "mineru")
self.assertEqual(router.initial_parser(PDFProfile.SCANNED, AUTO), "mineru")
self.assertEqual(router.initial_parser(PDFProfile.UNKNOWN, AUTO), "pymupdf")
```

Assert every explicit parser mode selects itself.

- [ ] **Step 4: Run RED tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_pdf_inspector -v`

Expected: the PDF inspector package import fails.

- [ ] **Step 5: Add and install the lightweight dependency**

Add `pymupdf4llm>=1.28.2,<2.0` to requirements and install that one package into
the project venv. Do not install MinerU or Docling.

- [ ] **Step 6: Implement configured inspection**

Open bytes with `pymupdf.open(stream=raw_bytes, filetype="pdf")`. Count native
text, images, text blocks, and drawing geometry without OCR. Put every numeric
decision in `PDFInspectorConfig`. Close the document through a context manager and
convert library exceptions into `PDFInspectionError`.

- [ ] **Step 7: Implement the mode router**

Reject unknown mode strings through the enum. Use the exact profile mapping from
the design and never inspect dependency availability in the router.

- [ ] **Step 8: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_inspector tests.test_parse_normalize_contracts -v
git add ai-service/requirements.txt ai-service/app/rag/parse_normalize/pdf ai-service/tests/fixtures/pdf_parse ai-service/tests/test_pdf_inspector.py
git commit -m "feat(ai): inspect and classify pdf snapshots"
```

---

### Task 3: PDF Adapter Boundary and Real PyMuPDF4LLM Adapter

**Files:**
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/__init__.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/base.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/markdown_mapping.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/pymupdf.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/vlm.py`
- Test: `ai-service/tests/test_pdf_pymupdf_adapter.py`

**Interfaces:**
- Produces `PDFAdapterOutput`, `PDFParserAdapter`, `PDFAdapterError`, and `PDFBackendUnavailableError`.
- Produces `PDFMarkdownMapper.map_page(...)` with document-wide stable IDs.
- Produces `PyMuPDF4LLMAdapter.parse(parse_input, inspection, allow_ocr=False)`.
- Reserves `parse_page` and `reparse_failed_pages` on adapters.
- Produces disabled `VLMPageParser`.

- [ ] **Step 1: Write failing real-adapter tests**

Run the real adapter against `simple-text.pdf`. Assert non-empty heading and
paragraph blocks, one-based page numbers on every block, two distinct pages,
parser identity, PDF metadata, and zero OCR requests. Assert the adapter returns
table/image metadata when those chunk fields are supplied to the mapper.

- [ ] **Step 2: Write failing reserved-interface tests**

Assert `parse_page` and `reparse_failed_pages` raise
`page_level_fallback_not_implemented`. Assert a default `VLMPageParser` raises
`vlm_page_parser_disabled` and performs no runtime call.

- [ ] **Step 3: Run RED tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_pdf_pymupdf_adapter -v`

Expected: adapter imports fail.

- [ ] **Step 4: Implement the adapter boundary and page mapper**

`PDFAdapterOutput` contains a shared `ParserResult`, `tables`, `images`, artifacts,
and JSON-safe backend metadata. Reuse `MarkdownParser` per page, then rewrite IDs,
order indexes, parent IDs, and metadata page numbers. Accept optional bboxes from
backend structure without guessing them from Markdown.

- [ ] **Step 5: Implement real PyMuPDF4LLM conversion**

Open an in-memory PyMuPDF document and call:

```python
pymupdf4llm.to_markdown(
    document,
    page_chunks=True,
    use_ocr=False,
    force_ocr=False,
    write_images=True,
    embed_images=False,
    image_path=temporary_image_directory,
    show_progress=False,
)
```

Require a list with one mapping per page, map Markdown and structured fields, and
collect only generated image files as `images/{safe_name}` artifacts. Convert all
third-party errors into bounded adapter error codes.

- [ ] **Step 6: Implement page and VLM placeholders**

Keep the signatures narrow and raise the exact explicit errors from Step 2. No
model, network, OCR, or page merge code is allowed.

- [ ] **Step 7: Run tests and commit**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_pymupdf_adapter tests.test_pdf_inspector -v
git add ai-service/app/rag/parse_normalize/pdf ai-service/tests/test_pdf_pymupdf_adapter.py
git commit -m "feat(ai): adapt pymupdf4llm into normalized blocks"
```

---

### Task 4: Lazy MinerU and Docling Adapters, PDF Quality Gate, and Cascade

**Files:**
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/mineru.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/adapters/docling.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/quality.py`
- Create: `ai-service/app/rag/parse_normalize/pdf/cascade.py`
- Modify: `ai-service/app/rag/parse_normalize/pdf/__init__.py`
- Modify: `ai-service/.gitignore`
- Test: `ai-service/tests/test_pdf_optional_adapters.py`
- Test: `ai-service/tests/test_pdf_quality_cascade.py`
- Create: `ai-service/tests/fixtures/pdf_parse/backends/mineru/content_list.json`
- Create: `ai-service/tests/fixtures/pdf_parse/backends/mineru/middle.json`
- Create: `ai-service/tests/fixtures/pdf_parse/backends/docling/document.json`

**Interfaces:**
- Produces `MinerURunner`, `SubprocessMinerURunner`, and `MinerUAdapter`.
- Produces `DoclingRuntime`, `LocalDoclingRuntime`, and `DoclingAdapter`.
- Produces `PDFParseQualityConfig` and `PDFParseQualityEvaluator`.
- Produces registered `PdfCascadeParser(BaseParser)` whose `execute` returns the final `ParserExecution`.

- [ ] **Step 1: Write failing MinerU contract tests**

Use a fake runner that writes the complete official-format content list, middle
JSON, Markdown, and an image into a temporary fingerprint cache. Assert the
adapter prefers structured content, preserves one-based page and bbox metadata,
maps title/list/table/image types, returns the exact image bytes, and retains
bounded raw MinerU structures in parser metadata. Assert the default runner emits
`mineru_backend_unavailable` when the executable is absent and a stable timeout
error when a controlled subprocess exceeds its configured timeout.

- [ ] **Step 2: Write failing Docling contract tests**

Use a fake runtime returning Markdown plus a complete Docling-like export dict.
Assert blocks, tables, images, provenance page/bbox, and raw structured output are
retained. Assert the runtime receives `allow_ocr=False` for native text and
`allow_ocr=True` for scanned input. Assert absent Docling produces
`docling_backend_unavailable`.

- [ ] **Step 3: Write failing quality and cascade tests**

Hand-derive metrics and scores for clean, duplicated, replacement-heavy,
line-break-heavy, and empty outputs. Assert configurable pass/fallback thresholds.
Then inject deterministic adapters and prove:

- SIMPLE_TEXT clean primary -> PyMuPDF4LLM `pass`
- COMPLEX_LAYOUT clean primary -> MinerU `pass`
- SCANNED weak MinerU -> Docling `fallback_used`
- primary exception -> Docling attempt records the error/fallback reason
- final medium score -> `manual_review` with a document
- final unusable output -> `failed` with no document result
- every attempt records elapsed time and one failure does not escape the parser

- [ ] **Step 4: Run RED tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_optional_adapters tests.test_pdf_quality_cascade -v
```

Expected: lazy adapters, evaluator, and cascade imports fail.

- [ ] **Step 5: Implement the MinerU adapter without installation**

The subprocess runner writes input bytes to a temporary file, runs the configured
`mineru -p <input> -o <cache> -b pipeline` command with captured output and a
configured timeout, and searches recursively for structured outputs. The adapter
parses `content_list_v2`, then `content_list`, then `middle`, using Markdown only
as a secondary source. Limit cached metadata size through configuration and do
not import or install MinerU.

- [ ] **Step 6: Implement the lazy Docling adapter**

`LocalDoclingRuntime` imports Docling inside `convert`, creates an in-memory
`DocumentStream`, constructs `PdfPipelineOptions` with `do_ocr=allow_ocr`, applies
a document timeout, converts, and returns both exports. The adapter recursively
maps item labels and `prov` entries without importing Docling model classes.

- [ ] **Step 7: Implement quality scoring and cascade state transitions**

Compute every required metric and score with configured weights. Cascade auto
mode attempts the routed primary, falls back whenever it raises or scores below
the pass threshold, and emits the exact final statuses in the design. Forced mode
runs only the requested adapter. Fill `ParserAttempt` after each attempt and
return report-only failures as `ParserExecution(result=None, ...)`.

- [ ] **Step 8: Ignore generated cache and run tests**

Add `data/parsed_cache/**` to `.gitignore`. Run all PDF tests plus all existing
parse-normalize tests. Expected: green with no MinerU or Docling package installed.

- [ ] **Step 9: Commit optional adapters and cascade**

```powershell
git add ai-service/.gitignore ai-service/app/rag/parse_normalize/pdf ai-service/tests/fixtures/pdf_parse/backends ai-service/tests/test_pdf_optional_adapters.py ai-service/tests/test_pdf_quality_cascade.py
git commit -m "feat(ai): add lazy pdf cascade and quality fallback"
```

---

### Task 5: Service Registration, CLI, Three-PDF End-to-End Tests, and Documentation

**Files:**
- Modify: `ai-service/app/rag/parse_normalize/service.py`
- Modify: `ai-service/app/rag/parse_normalize/router.py`
- Modify: `ai-service/app/rag/parse_normalize/__init__.py`
- Modify: `ai-service/run_parse.py`
- Modify: `ai-service/README.md`
- Test: `ai-service/tests/test_pdf_parse_e2e.py`
- Modify: `ai-service/tests/test_parse_normalize_e2e.py`
- Create: `ai-service/tests/fixtures/pdf_parse/sources_manifest.jsonl`

**Interfaces:**
- Registers `PdfCascadeParser` in `create_default_service`.
- Makes a registered PDF parser routable while preserving
  `unsupported_for_current_stage` for registries without one.
- Adds CLI `--parser auto|pymupdf|mineru|docling`, default `auto`.

- [ ] **Step 1: Write failing integration and CLI tests**

Build a valid raw ledger containing literal hashes for the three PDF fixtures.
Use the real Inspector, real PyMuPDF4LLM, MinerU structured fixture runner, and
Docling structured fixture runtime. Assert final outcomes and persisted files:

```text
simple-text       SIMPLE_TEXT    pymupdf4llm   pass
complex-layout    COMPLEX_LAYOUT mineru         pass
scanned           SCANNED        docling        fallback_used
```

Assert every output has `document.md`, valid `document.json`, valid
`parse_report.json`, and `images/`; every block has page metadata. Assert the scan
report has two attempts and OCR is requested only on the Docling attempt.

Run CLI `--source-id ... --parser auto` and each forced parser spelling. Assert
fallback-used exits 0, manual-review/failed exits 2, and forcing a PDF backend on
HTML exits 2 without changing the prior HTML document.

- [ ] **Step 2: Run RED tests**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_parse_e2e tests.test_parse_normalize_e2e -v
```

Expected: PDF remains unsupported and the CLI rejects `--parser`.

- [ ] **Step 3: Register and route the PDF cascade**

Change the top-level router so PDF is unsupported only when no PDF parser is
registered. Construct one `PdfCascadeParser` with shared Inspector, PDF router,
quality evaluator, PyMuPDF adapter, MinerU adapter/cache, and Docling adapter.
Keep optional imports out of service module import time.

- [ ] **Step 4: Add CLI parser mode and statuses**

Parse the mode through `PDFParserMode`, pass it to every service selection method,
include `fallback_used` in successful exit statuses, and keep `manual_review` as
exit 2. Preserve all previous command forms.

- [ ] **Step 5: Document installation and operational behavior**

Document architecture, routing rules, thresholds, output tree, parser overrides,
how to install optional MinerU/Docling runtimes later, the fact that V1 did not
install/download them, OCR gating, cache location, and all explicit non-goals.
Include one concise output/report example for each final parser.

- [ ] **Step 6: Run targeted, full, and immutable-raw verification**

Run:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_pdf_parse_contracts tests.test_pdf_inspector tests.test_pdf_pymupdf_adapter tests.test_pdf_optional_adapters tests.test_pdf_quality_cascade tests.test_pdf_parse_e2e -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q app run_parse.py tests
git diff --check
```

Recompute all three raw fixture SHA-256 values after parsing and compare them with
the ledger. Confirm `pip show mineru docling` still reports both absent.

- [ ] **Step 7: Commit the PDF delivery**

```powershell
git add ai-service/app/rag/parse_normalize ai-service/run_parse.py ai-service/README.md ai-service/tests/fixtures/pdf_parse/sources_manifest.jsonl ai-service/tests/test_pdf_parse_e2e.py ai-service/tests/test_parse_normalize_e2e.py
git commit -m "feat(ai): integrate pdf cascade into parse cli"
```
