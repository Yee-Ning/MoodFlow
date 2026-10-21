import hashlib
import io
import json
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from app.rag.parse_normalize.models import NormalizedDocument, ParseReport, ParseStatus
from app.rag.parse_normalize.pdf.adapters import (
    DoclingAdapter,
    MinerUAdapter,
    PyMuPDF4LLMAdapter,
)
from app.rag.parse_normalize.pdf.cascade import PdfCascadeParser
from app.rag.parse_normalize.pdf.models import PDFBackend
from app.rag.parse_normalize.service import OutcomeStatus, create_default_service
from run_parse import main, parse_args
from tests.test_pdf_optional_adapters import (
    FixtureDoclingRuntime,
    FixtureMinerURunner,
)


FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "pdf_parse"


class SelectiveMinerURunner:
    def __init__(self) -> None:
        self.full = FixtureMinerURunner()
        self.calls: list[tuple[str, bool]] = []

    def run(self, parse_input, output_directory: Path, allow_ocr: bool) -> None:
        self.calls.append((parse_input.snapshot.source_id, allow_ocr))
        if parse_input.snapshot.source_id == "pdf-scanned":
            output_directory.mkdir(parents=True, exist_ok=True)
            (output_directory / "content_list.json").write_text(
                json.dumps(
                    [
                        {
                            "type": "text",
                            "text": "x",
                            "page_idx": 0,
                            "bbox": [1, 1, 2, 2],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            return
        self.full.run(parse_input, output_directory, allow_ocr)


def fixture_cascade(cache_root: Path):
    mineru_runner = SelectiveMinerURunner()
    docling_runtime = FixtureDoclingRuntime()
    parser = PdfCascadeParser(
        adapters={
            PDFBackend.PYMUPDF4LLM: PyMuPDF4LLMAdapter(),
            PDFBackend.MINERU: MinerUAdapter(cache_root, runner=mineru_runner),
            PDFBackend.DOCLING: DoclingAdapter(runtime=docling_runtime),
        }
    )
    return parser, mineru_runner, docling_runtime


class PDFParseEndToEndTests(unittest.TestCase):
    def test_cli_force_bypasses_normalized_and_mineru_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
            parser, runner, _ = fixture_cascade(root / "cache")
            service = create_default_service(root, raw_manifest_path=root / "sources_manifest.jsonl", pdf_parser=parser)
            first = service.parse_source("pdf-complex-layout", parser_mode="mineru")
            self.assertEqual(first.status, OutcomeStatus.PARSED)
            self.assertEqual(service.parse_source("pdf-complex-layout", parser_mode="mineru").status, OutcomeStatus.SKIPPED)
            with patch("run_parse.create_default_service", return_value=service), redirect_stdout(io.StringIO()):
                code = main(["--source-id", "pdf-complex-layout", "--parser", "mineru", "--force"], project_root=root)
            self.assertEqual(code, 0)
            self.assertEqual(runner.calls, [("pdf-complex-layout", False), ("pdf-complex-layout", False)])

    def test_cli_parser_modes_are_explicit_and_auto_is_default(self) -> None:
        self.assertEqual(parse_args(["--source-id", "pdf-source"]).parser, "auto")
        for mode in ("pymupdf", "mineru", "docling"):
            with self.subTest(mode=mode):
                self.assertEqual(
                    parse_args(
                        ["--source-id", "pdf-source", "--parser", mode]
                    ).parser,
                    mode,
                )

    def test_three_pdf_profiles_produce_traceable_normalized_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
            manifest = root / "sources_manifest.jsonl"
            parser, mineru_runner, docling_runtime = fixture_cascade(
                root / "data" / "parsed_cache" / "mineru"
            )
            before = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / "raw").glob("*.pdf")
            }

            outcomes = create_default_service(
                root,
                raw_manifest_path=manifest,
                candidate_manifest_path=None,
                pdf_parser=parser,
            ).parse_manifest(manifest)

            self.assertEqual(
                [(item.source_id, item.parser_name, item.status) for item in outcomes],
                [
                    ("pdf-simple-text", "pymupdf4llm", OutcomeStatus.PARSED),
                    ("pdf-complex-layout", "mineru", OutcomeStatus.PARSED),
                    ("pdf-scanned", "docling", OutcomeStatus.FALLBACK_USED),
                ],
            )
            self.assertEqual(
                mineru_runner.calls,
                [("pdf-complex-layout", False), ("pdf-scanned", True)],
            )
            self.assertEqual(docling_runtime.calls, [True])

            for outcome in outcomes:
                output = root / "data" / "normalized" / outcome.source_id
                self.assertTrue((output / "document.md").is_file())
                self.assertTrue((output / "document.json").is_file())
                self.assertTrue((output / "parse_report.json").is_file())
                self.assertTrue((output / "images").is_dir())
                document = NormalizedDocument.model_validate_json(
                    (output / "document.json").read_text(encoding="utf-8")
                )
                report = ParseReport.model_validate_json(
                    (output / "parse_report.json").read_text(encoding="utf-8")
                )
                self.assertTrue(document.markdown.strip())
                self.assertTrue(document.blocks)
                self.assertTrue(
                    all(
                        isinstance(block.metadata.get("page_number"), int)
                        for block in document.blocks
                    )
                )
                self.assertEqual(report.selected_parser, outcome.parser_name)

            scan_report = ParseReport.model_validate_json(
                (
                    root
                    / "data"
                    / "normalized"
                    / "pdf-scanned"
                    / "parse_report.json"
                ).read_text(encoding="utf-8")
            )
            self.assertIs(scan_report.status, ParseStatus.FALLBACK_USED)
            self.assertEqual(
                [attempt.parser for attempt in scan_report.parser_attempts],
                ["mineru", "docling"],
            )
            self.assertTrue(all(attempt.ocr_used for attempt in scan_report.parser_attempts))
            after = {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / "raw").glob("*.pdf")
            }
            self.assertEqual(after, before)

    def test_cli_accepts_parser_override_and_auto_is_default(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
            manifest = root / "sources_manifest.jsonl"
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["--source-id", "pdf-simple-text", "--parser", "pymupdf"],
                    project_root=root,
                    raw_manifest_path=manifest,
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["results"][0]["parser_name"], "pymupdf4llm")
            self.assertEqual(payload["results"][0]["status"], "parsed")


if __name__ == "__main__":
    unittest.main()
