import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import source_ingestion_v2

from app.rag.source_ingestion.manifest import write_candidates
from app.rag.source_ingestion.models import CandidateSource


AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = AI_SERVICE_ROOT / "scripts/source_ingestion_v2.py"


class SourceIngestionV2CliTests(unittest.TestCase):
    def test_reopen_flag_defaults_off_and_reaches_service(self) -> None:
        self.assertFalse(source_ingestion_v2.parse_args(["run"]).reopen_failed)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            candidates = root / "candidates.json"
            discovery = root / "discovery.json"
            write_candidates(candidates, [])
            discovery.write_text("[]", encoding="utf-8")
            with patch.object(source_ingestion_v2, "AutoSourceIngestionService") as service:
                service.return_value.run.return_value.model_dump.return_value = {}
                with patch.object(source_ingestion_v2, "PolicyAwareHttpClient"):
                    source_ingestion_v2.main([
                        "run", "--project-root", str(root), "--candidate-manifest", str(candidates),
                        "--discovery-manifest", str(discovery), "--reopen-failed",
                    ])
            self.assertTrue(service.return_value.run.call_args.kwargs["reopen_failed"])
            self.assertTrue(service.call_args.kwargs["retry_config"].enabled)
            self.assertEqual(service.call_args.kwargs["retry_config"].max_source_attempts, 3)

    def test_validate_does_not_require_a_discovery_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate_manifest = root / "candidates.json"
            write_candidates(candidate_manifest, [])
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "validate",
                    "--project-root",
                    str(root),
                    "--candidate-manifest",
                    str(candidate_manifest),
                ],
                cwd=AI_SERVICE_ROOT,
                env=environment,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2, result.stderr.decode())
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(payload["total_valid_snapshots"], 0)

    def test_report_command_writes_empty_audit_and_returns_not_ready(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            candidate_manifest = root / "candidates.json"
            discovery_manifest = root / "discovered.json"
            write_candidates(
                candidate_manifest,
                [
                    CandidateSource(
                        source_id="existing-source",
                        title="周杰伦资料",
                        url="https://example.test/source.html",
                        publisher="Example",
                        source_type="media_article",
                        file_type="html",
                        artist="周杰伦",
                        topics=["作品影响"],
                        source_tier=2,
                        source_quality="high",
                        reason="Traceable Jay Chou source.",
                        status="approved",
                    )
                ],
            )
            discovery_manifest.write_text("[]\n", encoding="utf-8")
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "utf-8"

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "report",
                    "--project-root",
                    str(root),
                    "--candidate-manifest",
                    str(candidate_manifest),
                    "--discovery-manifest",
                    str(discovery_manifest),
                ],
                cwd=AI_SERVICE_ROOT,
                env=environment,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 2, result.stderr.decode())
            payload = json.loads(result.stdout.decode("utf-8"))
            self.assertEqual(payload["total_valid_snapshots"], 0)
            self.assertFalse(payload["ready_for_parse_normalize"])
            self.assertTrue((root / "data/raw/raw_corpus_manifest.jsonl").is_file())
            self.assertTrue((root / "data/raw/source_corpus_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
