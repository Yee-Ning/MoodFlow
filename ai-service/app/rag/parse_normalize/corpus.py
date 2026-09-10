"""Corpus-level execution metadata for Parse + Normalize V1.

This module never fetches or cleans content. It only selects deterministic
validation samples and summarizes artifacts produced from immutable snapshots.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .manifest import RawSnapshotLoader
from .models import NormalizedDocument, ParseReport, ParseStatus
from .service import ParseNormalizeService, ParseOutcome, create_default_service


class RepresentativeSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    file_type: str
    publisher: str
    source_type: str
    selected_reason: str
    expected_parser: str


class NormalizedCorpusManifestRow(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str
    document_id: str | None = None
    file_type: str
    parser_name: str
    parse_status: str
    quality_score: float = Field(ge=0, le=1)
    normalized_path: str
    raw_path: str
    corpus_version: str
    parser_version: str | None = None
    normalization_version: str | None = None
    fallback_used: bool = False
    manual_review: bool = False


def _file_type(metadata: dict[str, object]) -> str:
    explicit = metadata.get("file_type")
    if isinstance(explicit, str) and explicit:
        return explicit.lower()
    content_type = str(metadata.get("content_type", "")).lower()
    raw_suffix = Path(str(metadata.get("raw_path", ""))).suffix.lower()
    if "pdf" in content_type or raw_suffix == ".pdf":
        return "pdf"
    if "json" in content_type or raw_suffix == ".json":
        return "json"
    if "markdown" in content_type or raw_suffix in {".md", ".markdown"}:
        return "markdown"
    if "html" in content_type or raw_suffix in {".html", ".htm"}:
        return "html"
    if content_type.startswith("text/") or raw_suffix == ".txt":
        return "txt"
    return "unknown"


def _expected_parser(file_type: str) -> str:
    return "pdf-cascade" if file_type == "pdf" else file_type


class RepresentativeBatchSelector:
    """Choose a deterministic format/publisher-diverse validation batch."""

    def __init__(self, project_root: Path, manifest_path: Path) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path
        self.loader = RawSnapshotLoader(self.project_root)

    def select(
        self, *, html_count: int = 7, json_count: int = 2
    ) -> list[RepresentativeSample]:
        entries = self.loader.latest_entries(self.manifest_path)
        rows = [metadata for _, metadata in entries.values()]
        html_rows = self._diverse_html(rows, html_count)
        pdf_rows = sorted(
            (row for row in rows if _file_type(row) == "pdf"),
            key=lambda row: str(row["source_id"]),
        )
        json_rows = self._diverse_json(rows, json_count)
        selected: list[RepresentativeSample] = []
        selected.extend(
            self._sample(row, "publisher_and_template_diversity")
            for row in html_rows
        )
        selected.extend(
            self._sample(row, "all_pdf_profiles_require_validation")
            for row in pdf_rows
        )
        selected.extend(
            self._sample(row, "structured_schema_diversity")
            for row in json_rows
        )
        return selected

    @staticmethod
    def _diverse_html(
        rows: list[dict[str, object]], count: int
    ) -> list[dict[str, object]]:
        candidates = sorted(
            (row for row in rows if _file_type(row) == "html"),
            key=lambda row: (
                str(row.get("publisher", "")).casefold(),
                str(row.get("source_type", "")),
                str(row["source_id"]),
            ),
        )
        selected: list[dict[str, object]] = []
        seen_publishers: set[str] = set()
        for row in candidates:
            publisher = str(row.get("publisher") or "unknown").casefold()
            if publisher in seen_publishers:
                continue
            selected.append(row)
            seen_publishers.add(publisher)
            if len(selected) == count:
                return selected
        selected_ids = {str(row["source_id"]) for row in selected}
        selected.extend(
            row
            for row in candidates
            if str(row["source_id"]) not in selected_ids
        )
        return selected[:count]

    @staticmethod
    def _diverse_json(
        rows: list[dict[str, object]], count: int
    ) -> list[dict[str, object]]:
        candidates = sorted(
            (row for row in rows if _file_type(row) == "json"),
            key=lambda row: (
                not bool(row.get("album")),
                str(row.get("publisher", "")).casefold(),
                str(row["source_id"]),
            ),
        )
        return candidates[:count]

    @staticmethod
    def _sample(
        row: dict[str, object], selected_reason: str
    ) -> RepresentativeSample:
        file_type = _file_type(row)
        return RepresentativeSample(
            source_id=str(row["source_id"]),
            file_type=file_type,
            publisher=str(row.get("publisher") or "unknown"),
            source_type=str(row.get("source_type") or "unknown"),
            selected_reason=selected_reason,
            expected_parser=_expected_parser(file_type),
        )


class CorpusParseReporter:
    """Validate and publish a Cleaning-ready normalized corpus ledger."""

    def __init__(
        self,
        project_root: Path,
        manifest_path: Path,
        *,
        low_quality_threshold: float = 0.78,
    ) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path
        self.low_quality_threshold = low_quality_threshold
        self.loader = RawSnapshotLoader(self.project_root)
        self.output_root = self.project_root / "data" / "normalized"

    def build(
        self,
    ) -> tuple[list[NormalizedCorpusManifestRow], dict[str, Any]]:
        entries = self.loader.latest_entries(self.manifest_path)
        rows: list[NormalizedCorpusManifestRow] = []
        source_results: list[dict[str, Any]] = []
        raw_checks: list[dict[str, object]] = []
        for source_id in sorted(entries):
            record, metadata = entries[source_id]
            raw_checks.append(self._raw_check(record.raw_path, record.content_hash))
            row, result = self._source_result(source_id, metadata)
            rows.append(row)
            source_results.append(result)

        statuses = Counter(row.parse_status for row in rows)
        file_types = Counter(row.file_type for row in rows)
        parsers = Counter(row.parser_name for row in rows)
        quality_scores = [row.quality_score for row in rows]
        fallback_sources = [row.source_id for row in rows if row.fallback_used]
        manual_review_sources = [
            row.source_id for row in rows if row.manual_review
        ]
        failed_sources = [
            row.source_id
            for row in rows
            if row.parse_status in {ParseStatus.FAILED.value, "missing_output"}
        ]
        low_quality_sources = [
            row.source_id
            for row in rows
            if row.quality_score < self.low_quality_threshold
        ]
        report: dict[str, Any] = {
            "total_sources": len(entries),
            "parse_success": statuses[ParseStatus.PASS.value],
            "parsed_success": statuses[ParseStatus.PASS.value],
            "parsed_warning": statuses[ParseStatus.WARNING.value],
            "fallback_used": len(fallback_sources),
            "manual_review": len(manual_review_sources),
            "failed": len(failed_sources),
            "file_type_counts": dict(sorted(file_types.items())),
            "parser_usage_distribution": dict(sorted(parsers.items())),
            "average_quality_score": (
                round(sum(quality_scores) / len(quality_scores), 4)
                if quality_scores
                else 0.0
            ),
            "low_quality_sources": low_quality_sources,
            "fallback_sources": fallback_sources,
            "manual_review_sources": manual_review_sources,
            "failed_sources": failed_sources,
            "source_results": source_results,
            "raw_integrity": {
                "checked": len(raw_checks),
                "all_hashes_match": all(
                    bool(item["hash_matches"]) for item in raw_checks
                ),
                "failures": [
                    item for item in raw_checks if not item["hash_matches"]
                ],
            },
        }
        return rows, report

    def write(
        self,
        manifest_rows: list[NormalizedCorpusManifestRow],
        report: dict[str, Any],
    ) -> None:
        self.output_root.mkdir(parents=True, exist_ok=True)
        manifest_text = "".join(
            json.dumps(row.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for row in manifest_rows
        )
        self._atomic_write(
            self.output_root / "normalized_corpus_manifest.jsonl", manifest_text
        )
        self._atomic_write(
            self.output_root / "parse_corpus_report.json",
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        )
        self._atomic_write(
            self.output_root / "parse_corpus_report.md", self._markdown(report)
        )

    def _source_result(
        self, source_id: str, metadata: dict[str, object]
    ) -> tuple[NormalizedCorpusManifestRow, dict[str, Any]]:
        output_directory = self.output_root / source_id
        document_path = output_directory / "document.json"
        report_path = output_directory / "parse_report.json"
        file_type = _file_type(metadata)
        document: NormalizedDocument | None = None
        report: ParseReport | None = None
        try:
            document = NormalizedDocument.model_validate_json(
                document_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            document = None
        try:
            report = ParseReport.model_validate_json(
                report_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            report = None

        fallback_used = bool(
            report
            and (
                report.status is ParseStatus.FALLBACK_USED
                or len(report.parser_attempts) > 1
                or any(attempt.fallback_reason for attempt in report.parser_attempts)
            )
        )
        parse_status = report.status.value if report else "missing_output"
        parser_name = (
            document.parser_name
            if document
            else report.parser_name
            if report
            else _expected_parser(file_type)
        )
        quality_score = report.quality_score if report else 0.0
        normalized_path = (
            document_path if document else report_path
        ).relative_to(self.project_root).as_posix()
        row = NormalizedCorpusManifestRow(
            source_id=source_id,
            document_id=document.document_id if document else None,
            file_type=file_type,
            parser_name=parser_name,
            parse_status=parse_status,
            quality_score=quality_score,
            normalized_path=normalized_path,
            raw_path=str(metadata["raw_path"]),
            corpus_version=str(metadata["corpus_version"]),
            parser_version=document.parser_version if document else None,
            normalization_version=(
                document.normalization_version if document else None
            ),
            fallback_used=fallback_used,
            manual_review=bool(
                report and report.status is ParseStatus.MANUAL_REVIEW
            ),
        )
        result = {
            **row.model_dump(mode="json"),
            "warnings": report.warnings if report else ["missing_parse_output"],
            "elapsed_ms": report.elapsed_ms if report else 0.0,
        }
        return row, result

    def _raw_check(self, raw_path: str, expected_hash: str) -> dict[str, object]:
        path = (self.project_root / raw_path).resolve()
        if not path.is_relative_to(self.project_root):
            return {"raw_path": raw_path, "hash_matches": False}
        try:
            actual_hash = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return {"raw_path": raw_path, "hash_matches": False}
        return {
            "raw_path": raw_path,
            "hash_matches": actual_hash == expected_hash,
        }

    @staticmethod
    def _markdown(report: dict[str, Any]) -> str:
        file_types = ", ".join(
            f"{name}: {count}"
            for name, count in report["file_type_counts"].items()
        )
        parsers = ", ".join(
            f"{name}: {count}"
            for name, count in report["parser_usage_distribution"].items()
        )
        return (
            "# Normalized Corpus V1 Parse Summary\n\n"
            f"- Total sources: {report['total_sources']}\n"
            f"- Parse success: {report['parse_success']}\n"
            f"- Warnings: {report['parsed_warning']}\n"
            f"- Fallback used: {report['fallback_used']}\n"
            f"- Manual review: {report['manual_review']}\n"
            f"- Failed: {report['failed']}\n"
            f"- Average quality score: {report['average_quality_score']}\n"
            f"- Raw hashes valid: {report['raw_integrity']['all_hashes_match']}\n\n"
            f"## File types\n\n{file_types or 'None'}\n\n"
            f"## Parser usage\n\n{parsers or 'None'}\n\n"
            "## Low-quality sources\n\n"
            + (", ".join(report["low_quality_sources"]) or "None")
            + "\n\n## Manual review sources\n\n"
            + (", ".join(report["manual_review_sources"]) or "None")
            + "\n\n## Failed sources\n\n"
            + (", ".join(report["failed_sources"]) or "None")
            + "\n"
        )

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                delete=False,
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
            ) as handle:
                temporary_path = Path(handle.name)
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, path)
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)


class CorpusParseRunner:
    """Run every validated snapshot and always refresh corpus reports."""

    def __init__(
        self,
        project_root: Path,
        manifest_path: Path,
        *,
        service: ParseNormalizeService | object | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.manifest_path = manifest_path.resolve()
        self.service = service or create_default_service(
            self.project_root, raw_manifest_path=self.manifest_path
        )
        self.reporter = CorpusParseReporter(self.project_root, self.manifest_path)

    def run_all(
        self, *, force: bool = False, parser_mode: str = "auto"
    ) -> tuple[
        list[ParseOutcome],
        list[NormalizedCorpusManifestRow],
        dict[str, Any],
    ]:
        outcomes = self.service.parse_manifest(
            self.manifest_path, force=force, parser_mode=parser_mode
        )
        manifest_rows, report = self.reporter.build()
        report["batch_outcome_counts"] = dict(
            sorted(Counter(outcome.status.value for outcome in outcomes).items())
        )
        self.reporter.write(manifest_rows, report)
        return outcomes, manifest_rows, report
