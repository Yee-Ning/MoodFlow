"""Offline orchestration from Normalized Dataset to Cleaned Dataset."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from .config import CleanPipelineConfig
from .corpus import NormalizedCorpusReader
from .engine import CleanEngine
from .guard import SemanticGuard
from .models import CleanExecutionResult
from .registry import RuleRegistry
from .reporting import CleanReportPaths, CleanReporter
from .writer import CleanedDatasetWriter


@dataclass(frozen=True, slots=True)
class CleanRunOutcome:
    summary: dict[str, object]
    report_paths: CleanReportPaths
    results: list[CleanExecutionResult]
    cleaned_manifest_path: Path | None


class CleanPipelineService:
    def __init__(
        self,
        project_root: Path,
        config: CleanPipelineConfig,
        *,
        registry_checksum: str,
        normalized_root: Path | None = None,
        normalized_manifest_path: Path | None = None,
        cleaned_output_root: Path | None = None,
        report_root: Path | None = None,
    ) -> None:
        self.project_root = project_root.resolve()
        self.config = config
        self.registry_checksum = registry_checksum
        self.reader = NormalizedCorpusReader(
            self.project_root,
            normalized_root=normalized_root,
            manifest_path=normalized_manifest_path,
            document_type_sidecar=config.document_type_sidecar,
            document_type_sidecar_checksum=config.document_type_sidecar_checksum,
        )
        self.registry = RuleRegistry(config)
        self.engine = CleanEngine(
            self.registry,
            SemanticGuard(config.semantic_guard),
            registry_checksum=registry_checksum,
        )
        self.cleaned_output_root = cleaned_output_root
        self.reporter = CleanReporter(
            self.project_root,
            config.reporting,
            clean_version=config.clean_version,
            registry_checksum=registry_checksum,
            report_root=report_root,
        )

    def run(
        self, *, dry_run: bool, run_id: str | None = None
    ) -> CleanRunOutcome:
        documents = self.reader.load_all()
        results = [self.engine.clean(document) for document in documents]
        cleaned_manifest: Path | None = None
        if not dry_run:
            cleaned_manifest = CleanedDatasetWriter(
                self.project_root,
                clean_version=self.config.clean_version,
                registry_checksum=self.registry_checksum,
                output_root=self.cleaned_output_root,
            ).write(results)
        selected_run_id = run_id or datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        summary, paths = self.reporter.write(
            results,
            dry_run=dry_run,
            run_id=selected_run_id,
            cleaned_manifest_path=cleaned_manifest,
        )
        return CleanRunOutcome(
            summary=summary,
            report_paths=paths,
            results=results,
            cleaned_manifest_path=cleaned_manifest,
        )
