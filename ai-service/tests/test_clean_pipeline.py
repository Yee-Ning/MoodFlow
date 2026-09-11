import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from app.rag.clean.config import load_clean_config
from app.rag.clean.errors import RuleRegistryError
from app.rag.clean.models import CleanedDocument
from app.rag.clean.service import CleanPipelineService
from tests.clean_helpers import normalized_document, write_normalized_corpus
import run_clean


class CleanPipelineServiceTests(unittest.TestCase):
    def _corpus(self, root: Path) -> Path:
        return write_normalized_corpus(
            root,
            [
                normalized_document(
                    "utada-song",
                    "歌手：宇多田光  \r\n歌曲：First Love\r\n发行：1999\r\n",
                    title="First Love",
                    publisher="Label Example",
                    document_type="song",
                ),
                normalized_document(
                    "kendrick-album",
                    "Artist: Kendrick Lamar\n\nAlbum: DAMN.\n\nRank: 1\n",
                    title="DAMN.",
                    publisher="Review Example",
                    document_type="album",
                ),
            ],
        )

    def test_dry_run_writes_reports_but_never_cleaned_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._corpus(root)
            config, checksum = load_clean_config()
            normalized_hashes = {
                path: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in (root / "data" / "normalized").glob("*/document.json")
            }
            cleaned_root = root / "data" / "cleaned" / "v0.1"
            cleaned_root.mkdir(parents=True)
            sentinel = cleaned_root / "sentinel.txt"
            sentinel.write_text("do not touch", encoding="utf-8")
            service = CleanPipelineService(
                root,
                config,
                registry_checksum=checksum,
                cleaned_output_root=cleaned_root,
            )

            outcome = service.run(dry_run=True, run_id="dry-run-test")

            self.assertIsNone(outcome.cleaned_manifest_path)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "do not touch")
            self.assertFalse((cleaned_root / "cleaned_corpus_manifest.jsonl").exists())
            self.assertEqual(
                normalized_hashes,
                {
                    path: hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in normalized_hashes
                },
            )
            self.assertTrue(outcome.report_paths.summary.is_file())
            self.assertTrue(outcome.report_paths.document_changes.is_file())
            self.assertTrue(outcome.report_paths.rule_hits_csv.is_file())
            self.assertTrue(outcome.report_paths.suspicious_documents.is_file())
            self.assertTrue(outcome.report_paths.before_after_samples.is_file())
            self.assertTrue(outcome.summary["dry_run"])
            self.assertEqual(outcome.summary["document_count"], 2)
            changes = [
                json.loads(line)
                for line in outcome.report_paths.document_changes.read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            self.assertEqual(len(changes), 2)
            self.assertTrue(
                all(
                    {
                        "document_id",
                        "before_length",
                        "after_length",
                        "removed_chars",
                        "removed_ratio",
                        "applied_rules",
                    }.issubset(row)
                    for row in changes
                )
            )

    def test_formal_run_writes_versioned_traceable_cleaned_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._corpus(root)
            config, checksum = load_clean_config()
            outcome = CleanPipelineService(
                root, config, registry_checksum=checksum
            ).run(dry_run=False, run_id="formal-test")

            self.assertIsNotNone(outcome.cleaned_manifest_path)
            assert outcome.cleaned_manifest_path is not None
            self.assertEqual(
                outcome.cleaned_manifest_path,
                root
                / "data"
                / "cleaned"
                / "v0.1"
                / "cleaned_corpus_manifest.jsonl",
            )
            cleaned = CleanedDocument.model_validate_json(
                (
                    root
                    / "data"
                    / "cleaned"
                    / "v0.1"
                    / "utada-song"
                    / "document.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(cleaned.document_id, "doc-utada-song")
            self.assertEqual(cleaned.clean_version, "v0.1")
            self.assertEqual(cleaned.rule_registry_checksum, checksum)
            self.assertIn("1999", cleaned.cleaned_text)
            self.assertTrue(cleaned.rules_applied)
            self.assertTrue(cleaned.needs_review)
            self.assertEqual(outcome.summary["changed_documents"], 1)
            self.assertEqual(outcome.summary["unchanged_documents"], 1)

    def test_same_version_refuses_a_different_registry_checksum(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._corpus(root)
            config, checksum = load_clean_config()
            CleanPipelineService(root, config, registry_checksum=checksum).run(
                dry_run=False, run_id="first"
            )

            with self.assertRaises(RuleRegistryError):
                CleanPipelineService(
                    root,
                    config,
                    registry_checksum="sha256:" + "b" * 64,
                ).run(dry_run=False, run_id="conflict")


class CleanCliTests(unittest.TestCase):
    def test_profile_sample_and_dry_run_commands(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_normalized_corpus(
                root,
                [
                    normalized_document(
                        "simone-artist",
                        "Artist: Nina Simone\nYear: 1965\n",
                        title="Nina Simone",
                    )
                ],
            )

            self.assertEqual(run_clean.main(["profile"], project_root=root), 0)
            self.assertEqual(
                run_clean.main(["sample", "--size", "1"], project_root=root), 0
            )
            self.assertEqual(
                run_clean.main(
                    ["clean", "--dry-run", "--run-id", "cli-dry-run"],
                    project_root=root,
                ),
                0,
            )
            self.assertFalse((root / "data" / "cleaned").exists())
            self.assertTrue(
                (
                    root
                    / "data"
                    / "clean"
                    / "reports"
                    / "v0.1"
                    / "cli-dry-run"
                    / "clean_summary.json"
                ).is_file()
            )


if __name__ == "__main__":
    unittest.main()
