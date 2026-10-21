"""Offline orchestration from Normalized Dataset to Cleaned Dataset."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

from .config import CleanPipelineConfig
from .corpus import NormalizedCorpusReader
from .drift import ConceptDriftMonitor
from .engine import CleanEngine
from .guard import SemanticGuard
from .language import LanguageDetector
from .models import CleanExecutionResult
from .registry import RuleRegistry
from .reporting import CleanReportPaths, CleanReporter
from .structural import StructuralCleaner
from .template_dedup import TemplateDeduplicator
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
            structural_cleaner=StructuralCleaner(config.structural_clean),
            template_deduplicator=TemplateDeduplicator(config.template_dedup),
            language_detector=LanguageDetector(config.language_detection),
            drift_monitor=ConceptDriftMonitor(config.concept_drift),
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
        self, *, dry_run: bool, run_id: str | None = None, include_manual_review: bool = False
    ) -> CleanRunOutcome:
        documents = self.reader.load_all()

        # Pre-train cross-document site template clusters across the corpus
        site_corpus = [
            (ctx.document.document_id, ctx.source, ctx.document.markdown)
            for ctx in documents
        ]
        self.engine.template_deduplicator.train_site_templates(site_corpus)

        results = []
        accepted = []
        manual_review = []
        for context in documents:
            reasons = list(context.review_reasons)
            if reasons and not include_manual_review:
                manual_review.append({"document_id": context.document.document_id, "source_id": context.document.source_id, "reasons": reasons, "disposition": "quarantined"})
                continue
            result = self.engine.clean(context)
            if context.review_reasons:
                # A per-run override never erases the upstream pending state.
                result = replace(result, document=result.document.model_copy(update={"metadata": {**result.document.metadata, "parse_status": "manual_review"}}))
            if result.document.needs_review:
                reasons.append("clean_needs_review")
            results.append(result)
            if reasons:
                manual_review.append({"document_id": result.document.document_id, "source_id": result.document.source_id, "reasons": reasons, "disposition": "included_by_flag" if include_manual_review else "quarantined"})
            if not reasons or include_manual_review:
                accepted.append(result)
        cleaned_manifest: Path | None = None
        if not dry_run:
            cleaned_manifest = CleanedDatasetWriter(
                self.project_root,
                clean_version=self.config.clean_version,
                registry_checksum=self.registry_checksum,
                output_root=self.cleaned_output_root,
            ).write(accepted)
        selected_run_id = run_id or datetime.now(UTC).strftime(
            "%Y%m%dT%H%M%S.%fZ"
        )
        summary, paths = self.reporter.write(
            results,
            dry_run=dry_run,
            run_id=selected_run_id,
            cleaned_manifest_path=cleaned_manifest,
            manual_review=manual_review,
            output_document_count=len(accepted),
            input_document_count=len(documents),
            include_manual_review=include_manual_review,
        )
        return CleanRunOutcome(
            summary=summary,
            report_paths=paths,
            results=results,
            cleaned_manifest_path=cleaned_manifest,
        )
