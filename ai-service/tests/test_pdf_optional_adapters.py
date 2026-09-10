import hashlib
import json
import shutil
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path

from app.rag.parse_normalize.base import ParserError
from app.rag.parse_normalize.models import BlockType, OutputArtifact, ParseInput
from app.rag.parse_normalize.pdf.adapters import (
    DoclingAdapter,
    DoclingRuntimeOutput,
    LocalDoclingRuntime,
    MinerUAdapter,
    SubprocessMinerURunner,
)
from app.rag.parse_normalize.pdf.inspector import PDFInspector
from app.rag.source_ingestion.models import RawSnapshotRecord, SourceQuality


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_parse"


def parse_input_for(name: str, source_id: str) -> ParseInput:
    raw_bytes = (FIXTURE_ROOT / "raw" / name).read_bytes()
    return ParseInput(
        snapshot=RawSnapshotRecord(
            source_id=source_id,
            original_url="https://example.com/test.pdf",
            content_type="application/pdf",
            content_hash=f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
            fetched_at=datetime(2026, 9, 9, tzinfo=timezone.utc),
            raw_path=f"tests/fixtures/pdf_parse/raw/{name}",
            http_status=200,
            source_quality=SourceQuality.HIGH,
            corpus_version="jay-chou-corpus-v1",
        ),
        raw_bytes=raw_bytes,
    )


class FixtureMinerURunner:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def run(self, parse_input, output_directory: Path, allow_ocr: bool) -> None:
        self.calls.append(allow_ocr)
        source = FIXTURE_ROOT / "backends" / "mineru"
        output_directory.mkdir(parents=True, exist_ok=True)
        shutil.copy(source / "content_list.json", output_directory)
        shutil.copy(source / "middle.json", output_directory)
        shutil.copy(source / "document.md", output_directory)
        image = output_directory / "images" / "layout.png"
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b"fixture-image")


class FixtureDoclingRuntime:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    def convert(self, raw_bytes: bytes, filename: str, allow_ocr: bool):
        self.calls.append(allow_ocr)
        structured = json.loads(
            (FIXTURE_ROOT / "backends" / "docling" / "document.json").read_text(
                encoding="utf-8"
            )
        )
        return DoclingRuntimeOutput(
            markdown="# Scanned Music Interview\n\nRecovered interview context.\n",
            structured=structured,
            artifacts=[
                OutputArtifact(
                    relative_path="images/scan-page.png",
                    content=b"scan-page-image",
                    content_type="image/png",
                )
            ],
        )


class MinerUAdapterTests(unittest.TestCase):
    def test_structured_output_maps_blocks_tables_images_and_cache(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-complex")
        inspection = PDFInspector().inspect(parse_input.raw_bytes)
        runner = FixtureMinerURunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            output = MinerUAdapter(Path(temp_dir), runner=runner).parse(
                parse_input, inspection
            )

            self.assertEqual(runner.calls, [False])
            self.assertEqual(output.result.title, "Two-column Album Production Study")
            self.assertEqual(
                [block.block_type for block in output.result.blocks],
                [
                    BlockType.HEADING,
                    BlockType.PARAGRAPH,
                    BlockType.LIST,
                    BlockType.TABLE,
                    BlockType.IMAGE,
                ],
            )
            self.assertTrue(
                all(block.metadata["page_number"] == 1 for block in output.result.blocks)
            )
            self.assertEqual(output.result.tables[0].rows[1], ["Fantasy", "2001"])
            self.assertEqual(output.result.images[0].path, "images/layout.png")
            self.assertEqual(output.artifacts[0].content, b"fixture-image")
            self.assertIn("content_list", output.result.metadata["mineru"])
            self.assertIn("middle", output.result.metadata["mineru"])

            second = MinerUAdapter(Path(temp_dir), runner=runner).parse(
                parse_input, inspection
            )
            self.assertEqual(runner.calls, [False])
            self.assertEqual(second.result.blocks, output.result.blocks)

    def test_missing_executable_and_timeout_are_bounded_errors(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-runner")
        with tempfile.TemporaryDirectory() as temp_dir:
            with self.assertRaisesRegex(ParserError, "mineru_backend_unavailable"):
                SubprocessMinerURunner(executable="definitely-no-mineru").run(
                    parse_input, Path(temp_dir), False
                )
            runner = SubprocessMinerURunner(
                timeout_seconds=0.01,
                command_builder=lambda input_path, output_path, allow_ocr: [
                    sys.executable,
                    "-c",
                    "import time; time.sleep(1)",
                ],
            )
            with self.assertRaisesRegex(ParserError, "mineru_backend_timeout"):
                runner.run(parse_input, Path(temp_dir), False)

    def test_incomplete_middle_only_cache_is_regenerated(self) -> None:
        parse_input = parse_input_for("complex-layout.pdf", "pdf-incomplete-cache")
        inspection = PDFInspector().inspect(parse_input.raw_bytes)
        runner = FixtureMinerURunner()
        with tempfile.TemporaryDirectory() as temp_dir:
            adapter = MinerUAdapter(Path(temp_dir), runner=runner)
            cache = adapter._cache_directory(parse_input)
            cache.mkdir(parents=True)
            (cache / "middle.json").write_text("{}", encoding="utf-8")

            output = adapter.parse(parse_input, inspection)

            self.assertEqual(runner.calls, [False])
            self.assertTrue(output.result.blocks)


class DoclingAdapterTests(unittest.TestCase):
    def test_local_runtime_uses_document_stream_from_official_module(self) -> None:
        captured = {}

        class Options:
            do_ocr = False
            document_timeout = None

        class Document:
            def export_to_markdown(self):
                return "# Converted\n"

            def export_to_dict(self):
                return {"name": "Converted"}

        class Converter:
            def __init__(self, *, format_options):
                captured["options"] = format_options["pdf"].pipeline_options

            def convert(self, source):
                captured["source"] = source
                return types.SimpleNamespace(document=Document())

        modules = {
            "docling.document_converter": types.SimpleNamespace(
                DocumentConverter=Converter,
                PdfFormatOption=lambda *, pipeline_options: types.SimpleNamespace(
                    pipeline_options=pipeline_options
                ),
            ),
            "docling.datamodel.base_models": types.SimpleNamespace(
                InputFormat=types.SimpleNamespace(PDF="pdf"),
                DocumentStream=lambda *, name, stream: types.SimpleNamespace(
                    name=name, stream=stream
                ),
            ),
            "docling.datamodel.pipeline_options": types.SimpleNamespace(
                PdfPipelineOptions=Options
            ),
        }
        runtime = LocalDoclingRuntime(import_module=modules.__getitem__)

        output = runtime.convert(b"pdf-bytes", "source.pdf", True)

        self.assertEqual(output.markdown, "# Converted\n")
        self.assertEqual(captured["source"].name, "source.pdf")
        self.assertEqual(captured["source"].stream.read(), b"pdf-bytes")
        self.assertTrue(captured["options"].do_ocr)
        self.assertEqual(captured["options"].document_timeout, 900.0)

    def test_structured_output_maps_provenance_and_ocr_is_scan_only(self) -> None:
        native = parse_input_for("simple-text.pdf", "pdf-docling-native")
        scanned = parse_input_for("scanned.pdf", "pdf-docling-scan")
        runtime = FixtureDoclingRuntime()
        adapter = DoclingAdapter(runtime=runtime)

        native_output = adapter.parse(native, PDFInspector().inspect(native.raw_bytes))
        scan_output = adapter.parse(scanned, PDFInspector().inspect(scanned.raw_bytes))

        self.assertEqual(runtime.calls, [False, True])
        self.assertFalse(native_output.ocr_used)
        self.assertTrue(scan_output.ocr_used)
        self.assertEqual(scan_output.result.title, "Scanned Music Interview")
        self.assertEqual(scan_output.result.blocks[0].metadata["page_number"], 1)
        self.assertEqual(scan_output.result.blocks[0].metadata["bbox"], [70, 710, 500, 675])
        self.assertEqual(scan_output.result.tables[0].rows[1], ["Year", "2001"])
        self.assertEqual(scan_output.result.images[0].path, "images/scan-page.png")
        self.assertTrue(
            any(block.block_type is BlockType.IMAGE for block in scan_output.result.blocks)
        )
        self.assertIn("structured_document", scan_output.result.metadata["docling"])

    def test_local_runtime_is_lazy_and_reports_missing_dependency(self) -> None:
        runtime = LocalDoclingRuntime(import_module=lambda name: (_ for _ in ()).throw(ImportError()))
        with self.assertRaisesRegex(ParserError, "docling_backend_unavailable"):
            runtime.convert(b"pdf", "source.pdf", False)

    def test_local_runtime_rejects_docling_timeout_partial_result(self) -> None:
        class Options:
            do_ocr = False
            document_timeout = None

        class Converter:
            def __init__(self, *, format_options):
                pass

            def convert(self, source):
                return types.SimpleNamespace(
                    document=object(), has_timeout_errors=lambda: True
                )

        modules = {
            "docling.document_converter": types.SimpleNamespace(
                DocumentConverter=Converter,
                PdfFormatOption=lambda *, pipeline_options: types.SimpleNamespace(
                    pipeline_options=pipeline_options
                ),
            ),
            "docling.datamodel.base_models": types.SimpleNamespace(
                InputFormat=types.SimpleNamespace(PDF="pdf"),
                DocumentStream=lambda *, name, stream: object(),
            ),
            "docling.datamodel.pipeline_options": types.SimpleNamespace(
                PdfPipelineOptions=Options
            ),
        }
        runtime = LocalDoclingRuntime(import_module=modules.__getitem__)

        with self.assertRaisesRegex(ParserError, "docling_backend_timeout"):
            runtime.convert(b"pdf", "source.pdf", False)


if __name__ == "__main__":
    unittest.main()
