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
CLI_PATH = AI_SERVICE_ROOT / "scripts" / "source_ingestion.py"


class SourceIngestionCliTests(unittest.TestCase):
    def test_review_emits_utf8_when_terminal_default_cannot_encode_title(self) -> None:
        candidate = CandidateSource(
            source_id="unicode-source",
            title="전통음악与周杰伦",
            url="https://example.test/unicode-source.html",
            publisher="Test Publisher",
            author=None,
            published_at=None,
            source_type="academic_paper",
            file_type="html",
            artist="周杰伦",
            album=None,
            topics=["音乐风格演变"],
            source_tier=2,
            source_quality="high",
            reason="Verifies portable CLI output encoding.",
            status="pending",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            manifest_path = Path(temporary_directory) / "sources.json"
            write_candidates(manifest_path, [candidate])
            environment = os.environ.copy()
            environment["PYTHONIOENCODING"] = "gbk"

            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI_PATH),
                    "review",
                    "--manifest",
                    str(manifest_path),
                    candidate.source_id,
                    "approved",
                ],
                cwd=AI_SERVICE_ROOT,
                env=environment,
                capture_output=True,
                check=False,
            )

        self.assertEqual(result.returncode, 0, result.stderr.decode("utf-8", errors="replace"))
        payload = json.loads(result.stdout.decode("utf-8"))
        self.assertEqual(payload["title"], "전통음악与周杰伦")
        self.assertEqual(payload["status"], "approved")


if __name__ == "__main__":
    unittest.main()
