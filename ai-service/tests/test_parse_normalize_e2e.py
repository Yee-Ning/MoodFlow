import io
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from app.rag.parse_normalize.models import NormalizedDocument, ParseReport
from app.rag.parse_normalize.service import OutcomeStatus, create_default_service
from app.rag.source_ingestion.manifest import write_candidates
from app.rag.source_ingestion.models import CandidateSource
from run_parse import main


AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = Path(__file__).resolve().parent / "fixtures" / "parse_normalize"
EXPECTED_PARSERS = {
    "source-html-one": "html",
    "source-html-two": "html",
    "source-markdown": "markdown",
    "source-text": "txt",
    "source-wikidata": "json",
    "source-musicbrainz": "json",
}


class ParseNormalizeCliTests(unittest.TestCase):
    def test_cli_requires_exactly_one_selection_mode(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(AI_SERVICE_ROOT / "run_parse.py")],
            cwd=AI_SERVICE_ROOT,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(completed.returncode, 0)
        self.assertIn(
            b"one of the arguments --source-id --all-approved --manifest is required",
            completed.stderr,
        )

    def test_cli_manifest_reports_pdf_as_unsupported(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            raw_path = root / "data" / "raw" / "pdf" / "pdf-source.pdf"
            raw_path.parent.mkdir(parents=True)
            raw_bytes = b"%PDF-1.7 fixture"
            raw_path.write_bytes(raw_bytes)
            import hashlib

            record = {
                "source_id": "pdf-source",
                "original_url": "https://example.test/pdf-source.pdf",
                "content_type": "application/pdf",
                "content_hash": f"sha256:{hashlib.sha256(raw_bytes).hexdigest()}",
                "fetched_at": "2026-09-09T10:00:00Z",
                "raw_path": "data/raw/pdf/pdf-source.pdf",
                "http_status": 200,
                "source_quality": "high",
                "corpus_version": "fixture-v1",
            }
            manifest_path = root / "data" / "raw" / "sources_manifest.jsonl"
            manifest_path.write_text(json.dumps(record) + "\n", encoding="utf-8")
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["--manifest", str(manifest_path)], project_root=root
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 2)
            self.assertEqual(
                payload["results"][0]["status"], "unsupported_for_current_stage"
            )
            normalized = root / "data" / "normalized" / "pdf-source"
            self.assertTrue((normalized / "parse_report.json").exists())
            self.assertFalse((normalized / "document.json").exists())


class ParseNormalizeEndToEndTests(unittest.TestCase):
    def test_six_raw_snapshots_produce_valid_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
            manifest_path = root / "sources_manifest.jsonl"

            outcomes = create_default_service(
                root,
                raw_manifest_path=manifest_path,
                candidate_manifest_path=None,
            ).parse_manifest(manifest_path)

            self.assertEqual(len(outcomes), 6)
            self.assertEqual(
                {outcome.source_id: outcome.parser_name for outcome in outcomes},
                EXPECTED_PARSERS,
            )
            self.assertTrue(
                all(
                    outcome.status in {OutcomeStatus.PARSED, OutcomeStatus.WARNING}
                    for outcome in outcomes
                )
            )
            for source_id in EXPECTED_PARSERS:
                output = root / "data" / "normalized" / source_id
                markdown = (output / "document.md").read_text(encoding="utf-8")
                document = NormalizedDocument.model_validate_json(
                    (output / "document.json").read_text(encoding="utf-8")
                )
                report = ParseReport.model_validate_json(
                    (output / "parse_report.json").read_text(encoding="utf-8")
                )
                self.assertTrue(markdown.strip())
                self.assertEqual(document.source_id, source_id)
                self.assertEqual(document.markdown, markdown)
                self.assertEqual(document.parse_quality_score, report.quality_score)

            wikidata = NormalizedDocument.model_validate_json(
                (
                    root
                    / "data"
                    / "normalized"
                    / "source-wikidata"
                    / "document.json"
                ).read_text(encoding="utf-8")
            )
            musicbrainz = NormalizedDocument.model_validate_json(
                (
                    root
                    / "data"
                    / "normalized"
                    / "source-musicbrainz"
                    / "document.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(
                wikidata.metadata["parser_metadata"]["json_profile"], "wikidata"
            )
            self.assertEqual(
                musicbrainz.metadata["parser_metadata"]["json_profile"],
                "musicbrainz",
            )

    def test_all_approved_only_processes_approved_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            shutil.copytree(FIXTURE_ROOT, root, dirs_exist_ok=True)
            source_payload = {
                "title": "Fixture source",
                "url": "https://example.test/source",
                "publisher": "Fixture publisher",
                "author": None,
                "published_at": None,
                "source_type": "metadata_api",
                "file_type": "json",
                "artist": "Jay Chou",
                "album": None,
                "topics": ["metadata"],
                "source_tier": 4,
                "source_quality": "medium",
                "reason": "End-to-end fixture",
            }
            candidates = [
                CandidateSource.model_validate(
                    {
                        **source_payload,
                        "source_id": "source-wikidata",
                        "status": "approved",
                    }
                ),
                CandidateSource.model_validate(
                    {
                        **source_payload,
                        "source_id": "source-musicbrainz",
                        "status": "pending",
                    }
                ),
            ]
            candidate_path = (
                root
                / "data"
                / "source_ingestion"
                / "jay_chou_corpus_v1"
                / "sources_candidates.json"
            )
            write_candidates(candidate_path, candidates)
            manifest_path = root / "sources_manifest.jsonl"
            output = io.StringIO()

            with redirect_stdout(output):
                exit_code = main(
                    ["--all-approved"],
                    project_root=root,
                    raw_manifest_path=manifest_path,
                )

            payload = json.loads(output.getvalue())
            self.assertEqual(exit_code, 0)
            self.assertEqual(payload["counts"], {"parsed": 1})
            self.assertEqual(payload["results"][0]["source_id"], "source-wikidata")
            self.assertFalse(
                (
                    root
                    / "data"
                    / "normalized"
                    / "source-musicbrainz"
                    / "document.json"
                ).exists()
            )


if __name__ == "__main__":
    unittest.main()
