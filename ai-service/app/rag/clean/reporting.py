"""Auditable per-run Clean summary, rule-hit, and review reports."""

from __future__ import annotations

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from .config import ReportingConfig
from .errors import CleanPipelineError
from .io import atomic_write_json, atomic_write_jsonl, atomic_write_text
from .models import CleanExecutionResult


@dataclass(frozen=True, slots=True)
class CleanReportPaths:
    directory: Path
    summary: Path
    document_changes: Path
    rule_hits_json: Path
    rule_hits_csv: Path
    suspicious_documents: Path
    before_after_samples: Path
    single_reports_dir: Path
    stage_metrics_json: Path


class CleanReporter:
    _RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$")

    def __init__(
        self,
        project_root: Path,
        config: ReportingConfig,
        *,
        clean_version: str,
        registry_checksum: str,
        report_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.clean_version = clean_version
        self.registry_checksum = registry_checksum
        self.report_root = (
            report_root.resolve()
            if report_root is not None
            else self.project_root / "data" / "clean" / "reports" / clean_version
        )

    def write(
        self,
        results: list[CleanExecutionResult],
        *,
        dry_run: bool,
        run_id: str,
        cleaned_manifest_path: Path | None,
        manual_review: list[dict[str, object]] | None = None,
        output_document_count: int | None = None,
        input_document_count: int | None = None,
        include_manual_review: bool = False,
    ) -> tuple[dict[str, object], CleanReportPaths]:
        if not self._RUN_ID.fullmatch(run_id):
            raise CleanPipelineError("invalid_clean_run_id")
        directory = (self.report_root / run_id).resolve()
        if not directory.is_relative_to(self.report_root.resolve()):
            raise CleanPipelineError("clean_report_path_outside_root")

        audits = [result.audit for result in results]
        before_chars = sum(audit.before_length for audit in audits)
        after_chars = sum(audit.after_length for audit in audits)
        removed_chars = max(0, before_chars - after_chars)
        rule_hits: Counter[str] = Counter()
        for audit in audits:
            rule_hits.update(audit.rule_hits)
        changed_count = sum(audit.changed for audit in audits)
        suspicious_count = sum(audit.needs_review for audit in audits)
        document_type_resolution_statistics = Counter(
            audit.document_type_origin or "missing" for audit in audits
        )

        # Stage metrics aggregation
        stage_summary: dict[str, dict[str, int]] = defaultdict(lambda: {"removed_chars": 0, "removed_blocks": 0})
        language_distribution: Counter[str] = Counter()
        docs_with_english_blocks = 0
        total_english_blocks = 0
        concept_drift_docs = 0

        for res in results:
            active_stages = res.stages or (tuple(res.single_report.stages) if res.single_report else ())
            for st in active_stages:
                stage_summary[st.stage_name]["removed_chars"] += st.removed_chars
                stage_summary[st.stage_name]["removed_blocks"] += st.removed_blocks
            if res.language_annotation:
                language_distribution[res.language_annotation.primary_language] += 1
                if res.language_annotation.has_english_blocks:
                    docs_with_english_blocks += 1
                total_english_blocks += res.language_annotation.english_block_count
            if any("length_ratio_" in w for w in res.audit.warnings):
                concept_drift_docs += 1

        summary: dict[str, object] = {
            "report_version": "v0.2",
            "run_id": run_id,
            "dry_run": dry_run,
            "clean_version": self.clean_version,
            "rule_registry_checksum": self.registry_checksum,
            "document_count": len(audits),
            "total_documents": len(audits),
            "input_document_count": len(audits) if input_document_count is None else input_document_count,
            "include_manual_review": include_manual_review,
            "manual_review_document_count": len(manual_review or []),
            "output_document_count": len(audits) if output_document_count is None else output_document_count,
            "manual_review_documents": manual_review or [],
            "quarantined_document_count": sum(row["disposition"] == "quarantined" for row in (manual_review or [])),
            "changed_documents": changed_count,
            "unchanged_documents": len(audits) - changed_count,
            "characters_before": before_chars,
            "characters_after": after_chars,
            "removed_chars": removed_chars,
            "removed_ratio": round(
                removed_chars / before_chars if before_chars else 0.0, 8
            ),
            "stage_metrics_summary": dict(stage_summary),
            "language_detection_summary": {
                "language_distribution": dict(sorted(language_distribution.items())),
                "documents_with_english_blocks": docs_with_english_blocks,
                "total_english_blocks": total_english_blocks,
                "english_block_policy": "retained_unfiltered",
                "pending_decision": "English block filtering policy awaits West decision",
            },
            "concept_drift_summary": {
                "concept_drift_documents": concept_drift_docs,
            },
            "rule_hit_statistics": dict(sorted(rule_hits.items())),
            "suspicious_documents": suspicious_count,
            "document_type_resolution_statistics": dict(
                sorted(document_type_resolution_statistics.items())
            ),
            "cleaned_manifest_path": (
                cleaned_manifest_path.relative_to(self.project_root).as_posix()
                if cleaned_manifest_path is not None
                else None
            ),
        }
        single_reports_dir = directory / "documents"
        paths = CleanReportPaths(
            directory=directory,
            summary=directory / "clean_summary.json",
            document_changes=directory / "document_changes.jsonl",
            rule_hits_json=directory / "rule_hits.json",
            rule_hits_csv=directory / "rule_hits.csv",
            suspicious_documents=directory / "suspicious_documents.jsonl",
            before_after_samples=directory / "before_after_samples.jsonl",
            single_reports_dir=single_reports_dir,
            stage_metrics_json=directory / "stage_metrics.json",
        )
        atomic_write_json(paths.summary, summary)
        atomic_write_jsonl(directory / "manual_review_documents.jsonl", manual_review or [])
        atomic_write_jsonl(
            paths.document_changes,
            (audit.model_dump(mode="json") for audit in audits),
        )
        hit_rows = [
            {"rule_id": rule_id, "hit_count": hit_count}
            for rule_id, hit_count in sorted(rule_hits.items())
        ]
        atomic_write_json(paths.rule_hits_json, hit_rows)
        csv_buffer = io.StringIO(newline="")
        writer = csv.DictWriter(csv_buffer, fieldnames=["rule_id", "hit_count"])
        writer.writeheader()
        writer.writerows(hit_rows)
        atomic_write_text(paths.rule_hits_csv, csv_buffer.getvalue())

        # Write stage metrics summary artifact
        stage_metric_rows = [
            {"stage_name": stage_name, **metrics}
            for stage_name, metrics in sorted(stage_summary.items())
        ]
        atomic_write_json(paths.stage_metrics_json, stage_metric_rows)

        # Write per-document independent reports if configured
        if getattr(self.config, "write_per_document_reports", True):
            for result in results:
                if result.single_report:
                    doc_report_path = single_reports_dir / f"{result.document.document_id}.json"
                    atomic_write_json(doc_report_path, result.single_report.model_dump(mode="json"))

        suspicious = [
            audit.model_dump(mode="json") for audit in audits if audit.needs_review
        ]
        atomic_write_jsonl(paths.suspicious_documents, suspicious)
        samples = sorted(
            (result for result in results if result.audit.changed),
            key=lambda result: (
                not result.audit.needs_review,
                -result.audit.removed_ratio,
                result.audit.document_id,
            ),
        )[: self.config.before_after_sample_size]
        atomic_write_jsonl(
            paths.before_after_samples,
            (self._sample_row(result) for result in samples),
        )
        return summary, paths

    def _sample_row(self, result: CleanExecutionResult) -> dict[str, object]:
        before, before_truncated = self._truncate(result.before_text)
        after, after_truncated = self._truncate(result.after_text)
        return {
            "document_id": result.audit.document_id,
            "source_id": result.audit.source_id,
            "applied_rules": result.audit.applied_rules,
            "warnings": result.audit.warnings,
            "before": before,
            "after": after,
            "before_truncated": before_truncated,
            "after_truncated": after_truncated,
        }

    def _truncate(self, value: str) -> tuple[str, bool]:
        maximum = self.config.max_sample_text_chars
        if len(value) <= maximum:
            return value, False
        half = maximum // 2
        return value[:half] + "\n[…TRUNCATED…]\n" + value[-half:], True
