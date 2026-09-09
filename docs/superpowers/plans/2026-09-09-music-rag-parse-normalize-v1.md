# Music RAG Parse + Normalize V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Convert immutable HTML, Markdown, TXT, and JSON raw snapshots into deterministic `NormalizedDocument`, Markdown, and parse-report artifacts without implementing downstream cleaning or retrieval work.

**Architecture:** A raw snapshot loader verifies provenance, a detector and registry-backed router choose one format adapter, and a shared normalizer and quality evaluator create parser-independent output. A service and atomic writer isolate per-source failures and implement fingerprint-based repeat skipping.

**Tech Stack:** Python 3.12, Pydantic 2, Beautiful Soup 4.15, markdown-it-py 4.2, PyYAML 6.0, charset-normalizer 3.5, unittest.

**Spec:** `docs/superpowers/specs/2026-09-09-music-rag-parse-normalize-v1-design.md`

## Global Constraints

- Process only HTML, Markdown, TXT, and JSON; PDF returns `unsupported_for_current_stage`.
- Raw snapshots and raw manifests are read-only.
- Do not implement Cleaning, Deduplication, Chunking, Entity Linking, Claim Extraction, Embedding, or Vector DB behavior.
- All format decisions go through `FileTypeDetector`, `ParserRegistry`, and `ParserRouter`.
- All successful adapters return the same `ParserResult`; downstream code depends only on shared models.
- Generated Markdown is UTF-8 with LF line endings, one blank line between blocks, and one trailing newline.
- Repeated work is skipped only when raw content hash, parser version, and normalization version all match; `--force` bypasses the skip.
- Batch processing records per-source failures and continues.

---

### Task 1: Core Models, Detection, and Registry Router

**Files:**
- Create: `ai-service/app/rag/parse_normalize/__init__.py`
- Create: `ai-service/app/rag/parse_normalize/models.py`
- Create: `ai-service/app/rag/parse_normalize/base.py`
- Create: `ai-service/app/rag/parse_normalize/detection.py`
- Create: `ai-service/app/rag/parse_normalize/registry.py`
- Create: `ai-service/app/rag/parse_normalize/router.py`
- Test: `ai-service/tests/test_parse_normalize_contracts.py`

**Interfaces:**
- Consumes: `CandidateSource` and `RawSnapshotRecord` from `app.rag.source_ingestion.models`.
- Produces: `DetectedFileType`, `BlockType`, `ParseStatus`, `DocumentBlock`, `ParseInput`, `ParserResult`, `ParseMetrics`, `ParseReport`, `NormalizedDocument`, `BaseParser`, `FileTypeDetector.detect(record)`, `ParserRegistry.register(parser)`, and `ParserRouter.select(record)`.

- [ ] **Step 1: Write failing contract and routing tests**

```python
class CoreContractTests(unittest.TestCase):
    def test_detector_prefers_specific_mime_and_uses_extension_for_octet_stream(self):
        detector = FileTypeDetector()
        self.assertEqual(detector.detect(snapshot("text/markdown", "data/raw/txt/a.txt")), DetectedFileType.MARKDOWN)
        self.assertEqual(detector.detect(snapshot("application/octet-stream", "data/raw/json/a.json")), DetectedFileType.JSON)
        self.assertEqual(detector.detect(snapshot("application/pdf", "data/raw/pdf/a.pdf")), DetectedFileType.PDF)

    def test_registry_router_selects_adapter_without_format_branches(self):
        parser = StubParser(DetectedFileType.HTML)
        registry = ParserRegistry([parser])
        selected = ParserRouter(FileTypeDetector(), registry).select(snapshot("text/html", "data/raw/html/a.html"))
        self.assertIs(selected, parser)

    def test_router_rejects_pdf_explicitly(self):
        with self.assertRaisesRegex(UnsupportedCurrentStageError, "unsupported_for_current_stage"):
            ParserRouter(FileTypeDetector(), ParserRegistry()).select(snapshot("application/pdf", "data/raw/pdf/a.pdf"))

    def test_normalized_document_rejects_non_contiguous_block_order(self):
        with self.assertRaises(ValidationError):
            NormalizedDocument(**document_payload(blocks=[block(0), block(2)]))
```

- [ ] **Step 2: Run tests and verify the missing-package failure**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_contracts -v`

Expected: import failure for `app.rag.parse_normalize`.

- [ ] **Step 3: Implement strict shared models and parser contract**

```python
class DocumentBlock(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    block_id: str
    block_type: BlockType
    text: str
    level: int | None = Field(default=None, ge=1, le=6)
    order_index: int = Field(ge=0)
    parent_block_id: str | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

class BaseParser(ABC):
    parser_name: str
    parser_version: str
    supported_file_types: frozenset[DetectedFileType]

    @abstractmethod
    def parse(self, parse_input: ParseInput) -> ParserResult:
        raise NotImplementedError
```

Use `model_validator(mode="after")` on `NormalizedDocument` to require unique block IDs and contiguous order indexes. The service constructs document and report from the same evaluated score, and its integration test asserts their equality. Keep `ParseInput.raw_bytes` excluded from JSON serialization.

- [ ] **Step 4: Implement detector, registry, and router**

```python
class ParserRegistry:
    def register(self, parser: BaseParser) -> None:
        for file_type in parser.supported_file_types:
            if file_type in self._parsers:
                raise DuplicateParserError(file_type.value)
            self._parsers[file_type] = parser

class ParserRouter:
    def select(self, record: RawSnapshotRecord) -> BaseParser:
        detected = self.detector.detect(record)
        if detected is DetectedFileType.PDF:
            raise UnsupportedCurrentStageError("unsupported_for_current_stage: pdf")
        return self.registry.resolve(detected)
```

Use exact media-type matching after stripping `;` parameters; handle `application/*+json` by suffix and defer generic media types to `Path(record.raw_path).suffix.lower()`.

- [ ] **Step 5: Run the contract tests**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_contracts -v`

Expected: all contract and routing tests pass.

- [ ] **Step 6: Commit the core contracts**

```powershell
git add ai-service/app/rag/parse_normalize ai-service/tests/test_parse_normalize_contracts.py
git commit -m "feat(ai): add parse normalize contracts and routing"
```

---

### Task 2: Shared Markdown Normalizer and HTML Adapter

**Files:**
- Create: `ai-service/app/rag/parse_normalize/normalizer.py`
- Create: `ai-service/app/rag/parse_normalize/parsers/__init__.py`
- Create: `ai-service/app/rag/parse_normalize/parsers/html.py`
- Modify: `ai-service/requirements.txt`
- Test: `ai-service/tests/test_parse_normalize_html.py`

**Interfaces:**
- Consumes: `ParseInput`, `ParserResult`, `DocumentBlock`, `BlockType`, and `BaseParser` from Task 1.
- Produces: `MarkdownNormalizer.render(blocks) -> str` and `HtmlParser.parse(parse_input) -> ParserResult`.

- [ ] **Step 1: Write failing HTML and normalization tests**

```python
class HtmlParserTests(unittest.TestCase):
    def test_html_emits_metadata_and_semantic_blocks_without_script_content(self):
        raw = b'''<html><head><title>Seven Miles</title><meta name="author" content="Jay"></head>
        <body><h1>Title</h1><p>First paragraph.</p><script>discard()</script>
        <ul><li>one</li><li>two</li></ul><blockquote>quoted</blockquote>
        <table><tr><th>Song</th><th>Year</th></tr><tr><td>Qi Li Xiang</td><td>2004</td></tr></table>
        <img src="cover.jpg" alt="cover"></body></html>'''
        result = HtmlParser().parse(parse_input(raw, "text/html"))
        self.assertEqual(result.title, "Seven Miles")
        self.assertEqual([b.block_type for b in result.blocks], [
            BlockType.HEADING, BlockType.PARAGRAPH, BlockType.LIST,
            BlockType.QUOTE, BlockType.TABLE, BlockType.IMAGE,
        ])
        self.assertNotIn("discard", " ".join(b.text for b in result.blocks))
        self.assertEqual(result.blocks[4].metadata["rows"][1], ["Qi Li Xiang", "2004"])

    def test_normalizer_renders_one_blank_line_between_blocks(self):
        markdown = MarkdownNormalizer().render(HtmlParser().parse(parse_input(raw_html, "text/html")).blocks)
        self.assertIn("# Title\n\nFirst paragraph.\n\n- one\n- two\n", markdown)
        self.assertTrue(markdown.endswith("\n"))
        self.assertNotIn("\n\n\n", markdown)
```

- [ ] **Step 2: Run tests and verify imports fail**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_html -v`

Expected: missing `HtmlParser` or `MarkdownNormalizer`.

- [ ] **Step 3: Add and install the HTML dependency**

Add `beautifulsoup4>=4.15,<5.0` to `requirements.txt`, then run:

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m pip install "beautifulsoup4>=4.15,<5.0"
```

- [ ] **Step 4: Implement deterministic block rendering**

```python
def render_block(block: DocumentBlock) -> str:
    if block.block_type is BlockType.HEADING:
        return f"{'#' * (block.level or 1)} {block.text.strip()}"
    if block.block_type is BlockType.QUOTE:
        return "\n".join(f"> {line}" for line in block.text.splitlines())
    if block.block_type is BlockType.LIST:
        marker = "1." if block.metadata.get("ordered") else "-"
        return "\n".join(f"{marker} {item}" for item in block.metadata["items"])
    if block.block_type is BlockType.TABLE:
        return render_table(block.metadata["rows"])
    if block.block_type is BlockType.IMAGE:
        return f"![{block.metadata.get('alt', '')}]({block.metadata.get('src', '')})"
    return block.text.rstrip()
```

Escape table pipes and normalize CRLF/CR to LF. Join non-empty rendered blocks with exactly `\n\n` and append one newline.

- [ ] **Step 5: Implement the DOM adapter**

Parse with `BeautifulSoup(raw_bytes, "html.parser")`; decompose only `script`, `style`, and `noscript`. Traverse `h1`-`h6`, `p`, `ul`, `ol`, `blockquote`, `pre`, `table`, and `img` in document order, skipping nodes whose semantic ancestor is already emitted. Resolve relative image URLs with `urljoin(str(record.original_url), src)`. Track the latest heading at each level to assign parent IDs.

- [ ] **Step 6: Run HTML and core tests**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_html tests.test_parse_normalize_contracts -v`

Expected: all tests pass.

- [ ] **Step 7: Commit HTML parsing**

```powershell
git add ai-service/requirements.txt ai-service/app/rag/parse_normalize/normalizer.py ai-service/app/rag/parse_normalize/parsers ai-service/tests/test_parse_normalize_html.py
git commit -m "feat(ai): parse and normalize html snapshots"
```

---

### Task 3: Markdown and TXT Adapters

**Files:**
- Create: `ai-service/app/rag/parse_normalize/parsers/markdown.py`
- Create: `ai-service/app/rag/parse_normalize/parsers/text.py`
- Modify: `ai-service/requirements.txt`
- Test: `ai-service/tests/test_parse_normalize_text_formats.py`

**Interfaces:**
- Consumes: Task 1 parser contracts and Task 2 `MarkdownNormalizer`.
- Produces: `MarkdownParser.parse(parse_input) -> ParserResult`, `TxtParser.parse(parse_input) -> ParserResult`, and `TextParserConfig`.

- [ ] **Step 1: Write failing Markdown behavior tests**

```python
def test_markdown_front_matter_and_blocks_are_preserved():
    raw = b"---\ntitle: Fantasy\nauthor: Jay\n---\n#Title\n\n* first\n* second\n\n> quote\n\n```python\nprint(1)\n```\n"
    result = MarkdownParser().parse(parse_input(raw, "text/markdown"))
    self.assertEqual(result.title, "Fantasy")
    self.assertEqual(result.metadata["front_matter"]["author"], "Jay")
    self.assertEqual([b.block_type for b in result.blocks], [
        BlockType.HEADING, BlockType.LIST, BlockType.QUOTE, BlockType.CODE,
    ])
    normalized = MarkdownNormalizer().render(result.blocks)
    self.assertIn("# Title\n\n- first\n- second", normalized)
```

- [ ] **Step 2: Write failing TXT decoding and conservative-heading tests**

```python
def test_txt_decodes_gb18030_and_does_not_invent_sentence_heading():
    raw = "七里香\r\n\r\n这是一段正文。\r\n普通句子。\r\n".encode("gb18030")
    result = TxtParser().parse(parse_input(raw, "text/plain; charset=gb18030"))
    self.assertEqual(result.blocks[0].block_type, BlockType.HEADING)
    self.assertEqual(result.blocks[0].text, "七里香")
    self.assertEqual(result.blocks[1].block_type, BlockType.PARAGRAPH)
    self.assertIn("普通句子。", result.blocks[1].text)

def test_txt_decode_failure_is_explicit():
    with self.assertRaises(TextDecodeError):
        TxtParser(TextParserConfig(min_encoding_confidence=1.0)).parse(parse_input(b"\x81\x00\xff", "text/plain"))
```

- [ ] **Step 3: Run tests and verify adapters are missing**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_text_formats -v`

Expected: missing Markdown and TXT adapter imports.

- [ ] **Step 4: Add and install direct dependencies**

Add these bounded requirements:

```text
markdown-it-py>=4.2,<5.0
PyYAML>=6.0.3,<7.0
charset-normalizer>=3.5.1,<4.0
```

Run: `cd ai-service; .\.venv\Scripts\python.exe -m pip install -r requirements.txt`

- [ ] **Step 5: Implement Markdown token mapping**

Use `MarkdownIt("commonmark").enable("table")`. Strip a leading front matter region only when the first line is exactly `---` and a closing `---` exists; load it with `yaml.safe_load` and require a mapping. Walk block tokens with an index, collect inline child text and image attributes, collect list items until the corresponding close token, preserve fence content and language, and store table rows in block metadata. Feed all blocks to the shared normalizer.

- [ ] **Step 6: Implement bounded TXT decoding and paragraph recognition**

```python
@dataclass(frozen=True)
class TextParserConfig:
    heading_max_chars: int = 80
    min_encoding_confidence: float = 0.5
    fallback_encodings: tuple[str, ...] = ("utf-8", "utf-16", "gb18030", "big5")
```

Honor BOM and declared charset first. Strictly decode candidates; use `charset_normalizer.from_bytes(raw).best()` only after deterministic attempts and reject results below the configured coherence threshold. Split paragraphs on blank lines. Classify a paragraph as heading only when it is one physical line, within the length limit, lacks `.?!。？！；;` at the end, and has body content after it.

- [ ] **Step 7: Run text-format and normalizer tests**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_text_formats tests.test_parse_normalize_html -v`

Expected: all tests pass.

- [ ] **Step 8: Commit Markdown and TXT parsing**

```powershell
git add ai-service/requirements.txt ai-service/app/rag/parse_normalize/parsers/markdown.py ai-service/app/rag/parse_normalize/parsers/text.py ai-service/tests/test_parse_normalize_text_formats.py
git commit -m "feat(ai): parse markdown and text snapshots"
```

---

### Task 4: Structured and Bounded JSON Adapter

**Files:**
- Create: `ai-service/app/rag/parse_normalize/parsers/json.py`
- Test: `ai-service/tests/test_parse_normalize_json.py`

**Interfaces:**
- Consumes: Task 1 parser contracts.
- Produces: `JsonParser.parse(parse_input) -> ParserResult`, `JsonParserConfig`, and JSON profile values `wikidata`, `musicbrainz`, and `generic` in parser metadata.

- [ ] **Step 1: Write failing Wikidata preservation test**

```python
def test_wikidata_keeps_raw_structure_and_extracts_entity_fields():
    payload = {"entities": {"Q238819": {
        "id": "Q238819", "type": "item",
        "labels": {"zh": {"language": "zh", "value": "周杰伦"}},
        "aliases": {"zh": [{"language": "zh", "value": "周董"}]},
        "claims": {"P434": [{"mainsnak": {"datavalue": {"value": "mbid-1"}}}]},
    }}}
    result = JsonParser().parse(parse_input(json.dumps(payload).encode(), "application/json"))
    self.assertEqual(result.title, "周杰伦")
    self.assertEqual(result.metadata["json_profile"], "wikidata")
    self.assertEqual(result.metadata["entity_ids"], ["Q238819"])
    self.assertEqual(result.metadata["aliases"], ["周董"])
    self.assertEqual(result.metadata["raw_json"], payload)
```

- [ ] **Step 2: Write failing MusicBrainz and generic-limit tests**

```python
def test_musicbrainz_extracts_mbid_aliases_relations_and_release_groups():
    payload = {"id": "artist-mbid", "name": "Jay Chou", "type": "Person",
               "aliases": [{"name": "周杰伦"}], "relations": [{"type": "official homepage", "url": {"resource": "https://example.test"}}],
               "release-groups": [{"id": "rg-1", "title": "Fantasy", "primary-type": "Album"}]}
    result = JsonParser().parse(parse_input(json.dumps(payload).encode(), "application/json"))
    self.assertEqual(result.metadata["json_profile"], "musicbrainz")
    self.assertEqual(result.metadata["entity_id"], "artist-mbid")
    self.assertIn("Fantasy", " ".join(block.text for block in result.blocks))

def test_generic_json_bounds_depth_field_and_total_markdown():
    parser = JsonParser(JsonParserConfig(max_depth=2, max_field_chars=8, max_output_chars=120, max_items=2))
    result = parser.parse(parse_input(json.dumps({"long": "x" * 50, "nested": {"a": {"b": {"c": 1}}}, "many": [1, 2, 3]}).encode(), "application/json"))
    self.assertLessEqual(sum(len(block.text) for block in result.blocks), 120)
    self.assertTrue(result.metadata["truncated"])
    self.assertEqual(result.metadata["raw_json"]["long"], "x" * 50)
```

- [ ] **Step 3: Run tests and verify the JSON adapter is missing**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_json -v`

Expected: missing `JsonParser` import.

- [ ] **Step 4: Implement schema profiles and bounded recursion**

```python
@dataclass(frozen=True)
class JsonParserConfig:
    max_depth: int = 6
    max_field_chars: int = 2_000
    max_output_chars: int = 200_000
    max_items: int = 100
```

Decode UTF-8/UTF-8-SIG, call `json.loads`, store the full value at `raw_json`, and identify Wikidata by a mapping-valued `entities` key. Identify MusicBrainz by UUID-like `id` plus any of `artist-credit`, `release-groups`, `recordings`, `relations`, or `aliases`. Extract well-known fields into metadata and blocks. For generic recursion, track depth, item count, scalar length, and a shared output-character budget; emit explicit truncation metadata whenever a bound is reached.

- [ ] **Step 5: Run JSON and core tests**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_json tests.test_parse_normalize_contracts -v`

Expected: all tests pass.

- [ ] **Step 6: Commit JSON parsing**

```powershell
git add ai-service/app/rag/parse_normalize/parsers/json.py ai-service/tests/test_parse_normalize_json.py
git commit -m "feat(ai): normalize structured json snapshots"
```

---

### Task 5: Quality Evaluation, Raw Loading, Atomic Output, and Service

**Files:**
- Create: `ai-service/app/rag/parse_normalize/quality.py`
- Create: `ai-service/app/rag/parse_normalize/manifest.py`
- Create: `ai-service/app/rag/parse_normalize/writer.py`
- Create: `ai-service/app/rag/parse_normalize/service.py`
- Modify: `ai-service/app/rag/parse_normalize/__init__.py`
- Modify: `ai-service/.gitignore`
- Test: `ai-service/tests/test_parse_normalize_service.py`

**Interfaces:**
- Consumes: Tasks 1-4 contracts, router, adapters, and normalizer.
- Produces: `ParseQualityConfig`, `ParseQualityEvaluator.evaluate(blocks)`, `RawSnapshotLoader`, `AtomicNormalizedWriter`, `ParseNormalizeService.parse_source(source_id, force=False)`, `parse_manifest(path, force=False)`, and `ParseOutcome`.

- [ ] **Step 1: Write failing quality-status tests**

```python
def test_quality_evaluator_warns_for_duplicated_lines():
    blocks = [paragraph(0, "same\nsame\nsame\nunique")]
    report = ParseQualityEvaluator(ParseQualityConfig(warning_score=0.9, duplicated_line_warning_ratio=0.4)).evaluate("source", "txt", blocks, 12.5)
    self.assertEqual(report.status, ParseStatus.WARNING)
    self.assertEqual(report.metrics.duplicated_line_ratio, 0.5)
    self.assertIn("high_duplicated_line_ratio", report.warnings)

def test_quality_evaluator_fails_empty_content():
    report = ParseQualityEvaluator().evaluate("source", "html", [], 1.0)
    self.assertEqual(report.status, ParseStatus.FAILED)
    self.assertTrue(report.metrics.empty_content)
```

- [ ] **Step 2: Write failing loader, idempotency, and atomic-output tests**

```python
def test_loader_rejects_hash_mismatch_and_never_mutates_raw_file():
    raw_path, manifest_path = write_snapshot_fixture(root, b"original", declared_hash="sha256:" + "0" * 64)
    before = raw_path.read_bytes()
    with self.assertRaises(ContentHashMismatchError):
        RawSnapshotLoader(root).load_latest(manifest_path, "source")
    self.assertEqual(raw_path.read_bytes(), before)

def test_same_fingerprint_skips_and_force_rewrites_atomically():
    service = make_service(root, snapshot_fixture(root, b"<h1>Title</h1>"))
    first = service.parse_source("source")
    first_mtime = (root / "data/normalized/source/document.json").stat().st_mtime_ns
    skipped = service.parse_source("source")
    self.assertEqual(skipped.status, OutcomeStatus.SKIPPED)
    self.assertEqual((root / "data/normalized/source/document.json").stat().st_mtime_ns, first_mtime)
    forced = service.parse_source("source", force=True)
    self.assertEqual(forced.status, OutcomeStatus.PARSED)
```

- [ ] **Step 3: Run service tests and verify missing modules**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_service -v`

Expected: missing quality, manifest, writer, or service imports.

- [ ] **Step 4: Implement configurable quality metrics**

```python
@dataclass(frozen=True)
class ParseQualityConfig:
    warning_score: float = 0.65
    garbled_char_warning_ratio: float = 0.02
    duplicated_line_warning_ratio: float = 0.35
    empty_penalty: float = 1.0
    garbled_penalty_weight: float = 4.0
    duplication_penalty_weight: float = 0.75
```

Compute duplicated-line ratio as `(non_empty_line_count - unique_line_count) / non_empty_line_count`. Count U+FFFD, private-use code points, and disallowed control characters for garbling. Clamp the score to `[0, 1]` and round to four decimal places.

- [ ] **Step 5: Implement confined raw loading and latest selection**

Load each non-empty JSONL line through `RawSnapshotRecord.model_validate_json`, reject duplicate ledger rows with identical source/hash/path, select maximum `fetched_at` per source, resolve paths under `project_root`, reject escapes, read bytes once, and compare `sha256:{digest}` with the record.

- [ ] **Step 6: Implement idempotent atomic writing**

Write `document.md`, `document.json`, and `parse_report.json` through named temporary sibling files, flush and `fsync`, then `os.replace`. Persist JSON with `ensure_ascii=False`, two-space indentation, and LF. Compare existing document provenance before adapter execution. On a failed current fingerprint, atomically write the report and remove only the two explicit stale normalized document files if they exist.

- [ ] **Step 7: Implement orchestration and default registry composition**

Build one registry containing `HtmlParser`, `MarkdownParser`, `TxtParser`, and `JsonParser`. `parse_source` loads the latest snapshot, routes it, checks the fingerprint, times adapter plus normalization, evaluates quality, constructs `NormalizedDocument`, writes output, and returns `ParseOutcome`. Convert known per-source exceptions into failed or unsupported outcomes; batch methods continue processing remaining records.

- [ ] **Step 8: Ignore generated normalized data and run service tests**

Add `data/normalized/**` with a negated `.gitkeep` rule to `ai-service/.gitignore`. Run:

`cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_service tests.test_parse_normalize_html tests.test_parse_normalize_text_formats tests.test_parse_normalize_json -v`

Expected: all tests pass.

- [ ] **Step 9: Commit service and persistence**

```powershell
git add ai-service/.gitignore ai-service/app/rag/parse_normalize ai-service/tests/test_parse_normalize_service.py
git commit -m "feat(ai): persist quality-gated normalized documents"
```

---

### Task 6: CLI, End-to-End Fixtures, Live Snapshot Validation, and Documentation

**Files:**
- Create: `ai-service/run_parse.py`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/html/source-html-one.html`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/html/source-html-two.html`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/md/source-markdown.md`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/txt/source-text.txt`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/json/source-wikidata.json`
- Create: `ai-service/tests/fixtures/parse_normalize/raw/json/source-musicbrainz.json`
- Create: `ai-service/tests/fixtures/parse_normalize/sources_manifest.jsonl`
- Create: `ai-service/tests/test_parse_normalize_e2e.py`
- Modify: `ai-service/README.md`

**Interfaces:**
- Consumes: Task 5 `ParseNormalizeService` and default composition.
- Produces: CLI exit behavior and a reproducible six-format-snapshot end-to-end test suite.

- [ ] **Step 1: Write failing CLI selection tests**

```python
def test_cli_requires_exactly_one_selection_mode():
    result = run_cli([])
    self.assertNotEqual(result.returncode, 0)
    self.assertIn("one of the arguments --source-id --all-approved --manifest is required", result.stderr.decode())

def test_cli_reports_pdf_as_unsupported_without_document(self):
    with tempfile.TemporaryDirectory() as temporary_directory:
        project_root = Path(temporary_directory)
        manifest = write_pdf_snapshot(project_root)
        result = run_cli(["--manifest", str(manifest)], project_root=project_root)
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(payload["results"][0]["status"], "unsupported_for_current_stage")
        self.assertFalse((project_root / "data/normalized/pdf-source/document.json").exists())
```

- [ ] **Step 2: Write failing six-snapshot end-to-end test**

```python
def test_six_raw_snapshots_produce_valid_artifacts():
    outcomes = fixture_service.parse_manifest(FIXTURE_MANIFEST)
    self.assertEqual(len(outcomes), 6)
    for source_id in EXPECTED_SOURCE_IDS:
        output = fixture_root / "data/normalized" / source_id
        markdown = (output / "document.md").read_text(encoding="utf-8")
        document = NormalizedDocument.model_validate_json((output / "document.json").read_text(encoding="utf-8"))
        report = ParseReport.model_validate_json((output / "parse_report.json").read_text(encoding="utf-8"))
        self.assertTrue(markdown.strip())
        self.assertEqual(document.source_id, source_id)
        self.assertEqual(document.parse_quality_score, report.quality_score)
```

- [ ] **Step 3: Run CLI/end-to-end tests and verify failures**

Run: `cd ai-service; .\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_e2e -v`

Expected: missing `run_parse.py` and fixtures.

- [ ] **Step 4: Add concise raw-format fixtures and a valid ledger**

Use two distinct semantic HTML samples, a Markdown sample containing front matter and every supported block type, a GB18030-encoded TXT sample, a Wikidata entity sample, and a MusicBrainz artist sample. Generate literal SHA-256 values for the ledger and validate every line with `RawSnapshotRecord`; do not add fixtures to the production candidate manifest.

- [ ] **Step 5: Implement CLI composition and structured summaries**

Use an argparse mutually exclusive required group for `--source-id`, `--all-approved`, and `--manifest`, plus `--force`. Default project root is the directory containing `run_parse.py`; tests may override it through an internal `main(argv, project_root)` function. Configure UTF-8 stdout before rendering. Return exit 0 when every result is parsed, warning, or skipped; return exit 2 when any result is failed or unsupported. Always print one JSON object with counts and per-source results.

- [ ] **Step 6: Run the real fetched-snapshot integration**

Run:

```powershell
cd ai-service
.\.venv\Scripts\python.exe run_parse.py --source-id jc-v1-001 --force
.\.venv\Scripts\python.exe run_parse.py --source-id jc-v1-007 --force
.\.venv\Scripts\python.exe run_parse.py --source-id jc-v1-038 --force
.\.venv\Scripts\python.exe run_parse.py --source-id jc-v1-040 --force
```

Validate each generated `document.json` and `parse_report.json` with the Pydantic models and confirm the four raw file hashes remain equal to their ledger hashes.

- [ ] **Step 7: Document architecture, CLI, outputs, and non-goals**

Add a README section with the pipeline, exact commands, output tree, skip fingerprint, PDF unsupported behavior, fixture/live verification distinction, and the explicit downstream non-goals.

- [ ] **Step 8: Run targeted and full verification**

```powershell
cd ai-service
.\.venv\Scripts\python.exe -m unittest tests.test_parse_normalize_contracts tests.test_parse_normalize_html tests.test_parse_normalize_text_formats tests.test_parse_normalize_json tests.test_parse_normalize_service tests.test_parse_normalize_e2e -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
.\.venv\Scripts\python.exe -m compileall -q app run_parse.py tests
git diff --check
```

Expected: all targeted and full tests pass, compilation succeeds, and Git reports no whitespace errors.

- [ ] **Step 9: Commit CLI and end-to-end delivery**

```powershell
git add ai-service/run_parse.py ai-service/tests/fixtures/parse_normalize ai-service/tests/test_parse_normalize_e2e.py ai-service/README.md
git commit -m "feat(ai): add parse normalize cli and fixtures"
```
