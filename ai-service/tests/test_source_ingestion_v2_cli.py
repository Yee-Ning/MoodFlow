import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from app.rag.source_ingestion.manifest import write_candidates
from app.rag.source_ingestion.models import CandidateSource


AI_SERVICE_ROOT = Path(__file__).resolve().parents[1]
CLI_PATH = AI_SERVICE_ROOT / "scripts/source_ingestion_v2.py"


class SourceIngestionV2CliTests(unittest.TestCase):
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
