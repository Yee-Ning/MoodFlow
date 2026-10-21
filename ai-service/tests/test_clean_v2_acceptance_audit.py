"""Acceptance audit tests for Clean v2:
1. Rule version and registry checksum binding across documents, manifests, and reports.
2. End-to-end idempotency (same input + same rule version -> identical cleaned_sha256 and outputs).
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.rag.clean.config import load_clean_config
from app.rag.clean.models import CleanDocumentReport, CleanedDocument
from app.rag.clean.service import CleanPipelineService
from tests.clean_helpers import normalized_document, write_normalized_corpus


class CleanV2AcceptanceAuditTests(unittest.TestCase):
    def _create_sample_corpus(self, root: Path) -> Path:
        """Create a diverse sample corpus with tables, lists, multilingual, and potential template blocks."""
        return write_normalized_corpus(
            root,
            [
                normalized_document(
                    "doc-music-001",
                    "# 周杰伦《范特西》\n\n"
                    "发行于2001年，是一张经典专辑。\n\n"
                    "| 序号 | 歌名 | 作词 |\n"
                    "| :--- | :--- | :--- |\n"
                    "| 1 | 爱在公元前 | 方文山 |\n"
                    "| 2 | 简单爱 | 徐若瑄 |\n\n"
                    "- 获得第13届金曲奖最佳流行音乐演唱专辑奖\n"
                    "- 包含多首经典曲目\n\n"
                    "版权所有 © 2001 杰威尔音乐。保留所有权利。\n",
                    title="范特西专辑介绍",
                    publisher="jvr_music",
                    document_type="album",
                    source_type="HTML",
                ),
                normalized_document(
                    "doc-music-002",
                    "# 周杰伦《八度空间》\n\n"
                    "发行于2002年，展现了天马行空的想象力。\n\n"
                    "| 序号 | 歌名 | 作词 |\n"
                    "| :--- | :--- | :--- |\n"
                    "| 1 | 半岛铁盒 | 周杰伦 |\n"
                    "| 2 | 暗号 | 许世昌 |\n\n"
                    "- 融合多种音乐风格\n"
                    "- 视觉与听觉的双重探索\n\n"
                    "版权所有 © 2001 杰威尔音乐。保留所有权利。\n",
                    title="八度空间专辑介绍",
                    publisher="jvr_music",
                    document_type="album",
                    source_type="HTML",
                ),
                normalized_document(
                    "doc-bilingual-003",
                    "# Music Therapy Research Notes\n\n"
                    "This study explores cognitive behavioral therapy combined with ambient music intervention.\n\n"
                    "研究表明，音乐干预能显著缓解轻度焦虑情绪。\n\n"
                    "1. 第一阶段：情绪基线采集\n"
                    "2. 第二阶段：双耳节拍音频刺激\n",
                    title="Music Therapy Notes",
                    publisher="pubmed_subset",
                    document_type="research",
                    source_type="PDF",
                ),
            ],
        )

    def test_rule_version_and_data_binding(self) -> None:
        """Requirement 3: Confirm that manifest, document.json, and reports record clean_version and rule_registry_checksum."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self._create_sample_corpus(project_root)
            config, checksum = load_clean_config()

            service = CleanPipelineService(
                project_root,
                config,
                registry_checksum=checksum,
            )
            outcome = service.run(
                dry_run=False,
                run_id="audit-binding-run",
                include_manual_review=True,
            )

            # 1. Verify CleanRunOutcome and manifest path
            self.assertIsNotNone(outcome.cleaned_manifest_path)
            assert outcome.cleaned_manifest_path is not None
            self.assertTrue(outcome.cleaned_manifest_path.is_file())

            # 2. Verify cleaned_corpus_manifest.jsonl contains version and checksum on EVERY row
            manifest_lines = [
                json.loads(line)
                for line in outcome.cleaned_manifest_path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            self.assertEqual(len(manifest_lines), 3)
            for row in manifest_lines:
                self.assertIn("clean_version", row)
                self.assertEqual(row["clean_version"], "v0.2")
                self.assertIn("rule_registry_checksum", row)
                self.assertEqual(row["rule_registry_checksum"], checksum)
                self.assertTrue(row["cleaned_sha256"].startswith("sha256:"))

            # 3. Verify single document.json contains clean_version and rule_registry_checksum
            cleaned_doc_path = (
                project_root
                / "data"
                / "cleaned"
                / "v0.2"
                / "doc-music-001"
                / "document.json"
            )
            self.assertTrue(cleaned_doc_path.is_file())
            cleaned_doc = CleanedDocument.model_validate_json(
                cleaned_doc_path.read_text(encoding="utf-8")
            )
            self.assertEqual(cleaned_doc.clean_version, "v0.2")
            self.assertEqual(cleaned_doc.rule_registry_checksum, checksum)

            # 4. Verify clean_summary.json records top-level version binding
            summary_path = outcome.report_paths.summary
            self.assertTrue(summary_path.is_file())
            summary_json = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertEqual(summary_json.get("clean_version"), "v0.2")
            self.assertEqual(summary_json.get("rule_registry_checksum"), checksum)

            # 5. Verify single document report records clean_version and rule_registry_checksum
            single_report_path = (
                project_root
                / "data"
                / "clean"
                / "reports"
                / "v0.2"
                / "audit-binding-run"
                / "documents"
                / "doc-doc-music-001.json"
            )
            self.assertTrue(single_report_path.is_file())
            single_report = CleanDocumentReport.model_validate_json(
                single_report_path.read_text(encoding="utf-8")
            )
            self.assertEqual(single_report.clean_version, "v0.2")
            self.assertEqual(single_report.rule_registry_checksum, checksum)

    def test_idempotency_same_input_and_rules_produces_identical_output(self) -> None:
        """Requirement 4: Verify idempotency: same input + same rule version -> identical output and cleaned_sha256."""
        with tempfile.TemporaryDirectory() as temp_dir:
            project_root = Path(temp_dir)
            self._create_sample_corpus(project_root)
            config, checksum = load_clean_config()

            # First execution
            cleaned_root_1 = project_root / "data" / "cleaned_run1"
            service1 = CleanPipelineService(
                project_root,
                config,
                registry_checksum=checksum,
                cleaned_output_root=cleaned_root_1,
            )
            outcome1 = service1.run(
                dry_run=False,
                run_id="run-idempotency-1",
                include_manual_review=True,
            )

            # Second execution on identical input and rules
            cleaned_root_2 = project_root / "data" / "cleaned_run2"
            service2 = CleanPipelineService(
                project_root,
                config,
                registry_checksum=checksum,
                cleaned_output_root=cleaned_root_2,
            )
            outcome2 = service2.run(
                dry_run=False,
                run_id="run-idempotency-2",
                include_manual_review=True,
            )

            # Compare results
            manifest_1 = [
                json.loads(line)
                for line in (cleaned_root_1 / "cleaned_corpus_manifest.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            manifest_2 = [
                json.loads(line)
                for line in (cleaned_root_2 / "cleaned_corpus_manifest.jsonl").read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]

            self.assertEqual(len(manifest_1), len(manifest_2))

            for row1, row2 in zip(manifest_1, manifest_2, strict=True):
                # Document identity must match
                self.assertEqual(row1["document_id"], row2["document_id"])
                # Cleaned content hash MUST be 100% strictly identical
                self.assertEqual(row1["cleaned_sha256"], row2["cleaned_sha256"])
                # Applied rules must match
                self.assertEqual(row1["rules_applied"], row2["rules_applied"])
                # Change status must match
                self.assertEqual(row1["changed"], row2["changed"])

                # Document.md content must be byte-for-byte identical
                md1 = (cleaned_root_1 / row1["source_id"] / "document.md").read_bytes()
                md2 = (cleaned_root_2 / row2["source_id"] / "document.md").read_bytes()
                self.assertEqual(md1, md2)

                # Document.json content must be identical
                doc1 = CleanedDocument.model_validate_json(
                    (cleaned_root_1 / row1["source_id"] / "document.json").read_text(encoding="utf-8")
                )
                doc2 = CleanedDocument.model_validate_json(
                    (cleaned_root_2 / row2["source_id"] / "document.json").read_text(encoding="utf-8")
                )
                self.assertEqual(doc1.cleaned_text, doc2.cleaned_text)
                self.assertEqual(doc1.rules_applied, doc2.rules_applied)
                self.assertEqual(doc1.rule_registry_checksum, doc2.rule_registry_checksum)


if __name__ == "__main__":
    unittest.main()
