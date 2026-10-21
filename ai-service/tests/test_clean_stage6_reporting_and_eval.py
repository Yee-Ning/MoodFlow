"""Unit tests for Stage 6: Reporting and Evaluation Loops."""

from __future__ import annotations

import json

from app.rag.clean.config import ReportingConfig
from app.rag.clean.evaluation_hooks import (
    CleanEvaluationSample,
    CleanEvaluationService,
)
from app.rag.clean.models import (
    CleanDocumentAudit,
    CleanDocumentReport,
    CleanedDocument,
    CleanExecutionResult,
    LanguageAnnotation,
    LineDiffStats,
    StageMetrics,
)
from app.rag.clean.reporting import CleanReporter


def make_dummy_result(doc_id: str, before: str, after: str) -> CleanExecutionResult:
    doc = CleanedDocument(
        document_id=doc_id,
        source_id=doc_id,
        source="test_site",
        source_type="HTML",
        document_type="news",
        language="zh",
        original_file_type="html",
        corpus_version="v1",
        cleaned_text=after,
        clean_version="v0.2",
        rule_registry_checksum="sha256:" + "0" * 64,
        rules_applied=["RULE_001"],
        warnings=[],
        needs_review=False,
        normalized_path=f"data/normalized/{doc_id}/document.json",
        normalized_content_hash="sha256:" + "1" * 64,
        metadata={},
    )
    audit = CleanDocumentAudit(
        document_id=doc_id,
        source_id=doc_id,
        source="test_site",
        source_type="HTML",
        document_type="news",
        before_length=len(before),
        after_length=len(after),
        removed_chars=len(before) - len(after),
        removed_ratio=0.1,
        changed=before != after,
        applied_rules=["RULE_001"],
        rule_hits={"RULE_001": 1},
        needs_review=False,
        warnings=[],
    )
    single_report = CleanDocumentReport(
        report_version="v0.2",
        document_id=doc_id,
        source_id=doc_id,
        source="test_site",
        source_type="HTML",
        document_type="news",
        clean_version="v0.2",
        rule_registry_checksum="sha256:" + "0" * 64,
        before_length=len(before),
        after_length=len(after),
        removed_chars=len(before) - len(after),
        removed_ratio=0.1,
        changed=True,
        rules_applied=["RULE_001"],
        rule_hits={"RULE_001": 1},
        warnings=[],
        needs_review=False,
        line_diff=LineDiffStats(
            before_lines=2,
            after_lines=2,
            deleted_lines=0,
            added_lines=0,
            unchanged_lines=2,
            diff_ratio=0.0,
        ),
        stages=[
            StageMetrics(
                stage_index=2,
                stage_name="structural_cleaning",
                before_chars=len(before),
                after_chars=len(before),
                removed_chars=0,
                removed_blocks=0,
            ),
            StageMetrics(
                stage_index=3,
                stage_name="text_content_cleaning",
                before_chars=len(before),
                after_chars=len(after),
                removed_chars=len(before) - len(after),
                removed_blocks=0,
            ),
        ],
        language=LanguageAnnotation(
            primary_language="zh",
            confidence=0.9,
            has_english_blocks=False,
            english_block_count=0,
            policy_action="retained_unfiltered",
        ),
    )
    return CleanExecutionResult(
        document=doc,
        audit=audit,
        before_text=before,
        after_text=after,
        stages=tuple(single_report.stages),
        single_report=single_report,
    )


def test_clean_reporter_writes_per_document_reports_and_stage_metrics(tmp_path):
    reporter = CleanReporter(
        tmp_path,
        ReportingConfig(write_per_document_reports=True),
        clean_version="v0.2",
        registry_checksum="sha256:" + "0" * 64,
        report_root=tmp_path / "reports",
    )
    r1 = make_dummy_result("doc-1", "before text 1", "after text 1")
    r2 = make_dummy_result("doc-2", "before text 2", "after text 2")

    manifest_path = tmp_path / "cleaned_manifest.jsonl"
    manifest_path.write_text("manifest content\n", encoding="utf-8")

    summary, paths = reporter.write(
        [r1, r2],
        dry_run=False,
        run_id="run-test-01",
        cleaned_manifest_path=manifest_path,
    )

    # Check stage metrics in clean_summary.json
    assert "stage_metrics_summary" in summary
    assert "text_content_cleaning" in summary["stage_metrics_summary"]
    assert "language_detection_summary" in summary
    assert summary["language_detection_summary"]["english_block_policy"] == "retained_unfiltered"

    # Check stage_metrics.json exists
    assert paths.stage_metrics_json.is_file()

    # Check independent single reports in documents/<doc_id>.json
    doc1_report = paths.single_reports_dir / "doc-1.json"
    doc2_report = paths.single_reports_dir / "doc-2.json"
    assert doc1_report.is_file()
    assert doc2_report.is_file()

    data = json.loads(doc1_report.read_text(encoding="utf-8"))
    assert data["document_id"] == "doc-1"
    assert len(data["stages"]) == 2


def test_clean_evaluation_service_sampling_and_metrics():
    r1 = make_dummy_result("doc-1", "before 1", "after 1")
    r2 = make_dummy_result("doc-2", "before 2", "after 2")

    eval_service = CleanEvaluationService(seed=42)
    samples = eval_service.sample_for_evaluation([r1, r2], sample_size=2)
    assert len(samples) == 2
    assert samples[0].document_id == "doc-1"

    # Simulate human label review: 1 false positive out of 2 rules, 0 false negatives
    labeled = [
        CleanEvaluationSample(
            document_id="doc-1",
            source="test",
            before_text="b",
            after_text="a",
            removed_chars=10,
            applied_rules=["RULE_1"],
            needs_review=False,
            manual_labeled_false_positives=1,
            manual_labeled_false_negatives=0,
        ),
        CleanEvaluationSample(
            document_id="doc-2",
            source="test",
            before_text="b",
            after_text="a",
            removed_chars=10,
            applied_rules=["RULE_2"],
            needs_review=False,
            manual_labeled_false_positives=0,
            manual_labeled_false_negatives=0,
        ),
    ]

    metrics = eval_service.compute_metrics(labeled)
    assert metrics.sample_size == 2
    assert metrics.total_false_positives == 1
    assert metrics.estimated_false_positive_rate == 0.5
    assert metrics.estimated_false_negative_rate == 0.0
