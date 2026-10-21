"""Pure deterministic CleanEngine executing the new 6-stage architecture."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .config import (
    ConceptDriftConfig,
    LanguageDetectionConfig,
    StructuralCleanConfig,
    TemplateDeduplicationConfig,
)
from .corpus import NormalizedDocumentContext
from .diff_tracker import calculate_line_diff
from .drift import ConceptDriftMonitor
from .guard import SemanticGuard
from .language import LanguageDetector
from .models import (
    CleanDocumentAudit,
    CleanDocumentReport,
    CleanedDocument,
    CleanExecutionResult,
    StageMetrics,
)
from .registry import CleanRule, RuleAction, RuleRegistry
from .structural import StructuralCleaner
from .template_dedup import TemplateDeduplicator


class CleanEngine:
    def __init__(
        self,
        registry: RuleRegistry,
        guard: SemanticGuard,
        *,
        registry_checksum: str,
        structural_cleaner: StructuralCleaner | None = None,
        template_deduplicator: TemplateDeduplicator | None = None,
        language_detector: LanguageDetector | None = None,
        drift_monitor: ConceptDriftMonitor | None = None,
    ) -> None:
        self.registry = registry
        self.guard = guard
        self.registry_checksum = registry_checksum
        self.structural_cleaner = structural_cleaner or StructuralCleaner(
            StructuralCleanConfig()
        )
        self.template_deduplicator = template_deduplicator or TemplateDeduplicator(
            TemplateDeduplicationConfig()
        )
        self.language_detector = language_detector or LanguageDetector(
            LanguageDetectionConfig()
        )
        self.drift_monitor = drift_monitor or ConceptDriftMonitor(
            ConceptDriftConfig()
        )

    def clean(
        self,
        context: NormalizedDocumentContext,
        template_deduplicator: TemplateDeduplicator | None = None,
    ) -> CleanExecutionResult:
        before = context.document.markdown
        current = before
        stages: list[StageMetrics] = []

        # Stage 2: Structural Data Cleaning (DOM de-noising & whitelist tables/lists)
        struct_res = self.structural_cleaner.clean(current)
        current = struct_res.cleaned_text
        stages.append(struct_res.stage_metrics)

        # Stage 3: Text Content Cleaning (Rule Registry + SequenceMatcher line diff)
        stage3_before = current
        applied_rules: list[str] = []
        rule_hits: dict[str, int] = {}
        for rule in self.registry.rules_for(context):
            updated, hits = self._apply_rule(current, rule)
            if updated != current:
                applied_rules.append(rule.id)
                rule_hits[rule.id] = hits
                current = updated

        line_diff_stats, diff_sample = calculate_line_diff(stage3_before, current)
        stage3_metrics = StageMetrics(
            stage_index=3,
            stage_name="text_content_cleaning",
            before_chars=len(stage3_before),
            after_chars=len(current),
            removed_chars=max(0, len(stage3_before) - len(current)),
            removed_blocks=line_diff_stats.deleted_lines,
            details={
                "applied_rules": applied_rules,
                "diff_ratio": line_diff_stats.diff_ratio,
                "deleted_lines": line_diff_stats.deleted_lines,
                "added_lines": line_diff_stats.added_lines,
            },
        )
        stages.append(stage3_metrics)

        # Stage 4: Template Deduplication (Boilerplate removal across site documents)
        active_dedup = template_deduplicator or self.template_deduplicator
        dedup_res = active_dedup.deduplicate(
            current,
            doc_id=context.document.document_id,
            site=context.source,
        )
        current = dedup_res.cleaned_text
        stages.append(dedup_res.stage_metrics)

        # Stage 5: Language Detection (Metadata annotation only; retains English blocks)
        lang_annotation, lang_metrics = self.language_detector.detect(current)
        stages.append(lang_metrics)

        # Guard evaluation & Concept drift check (non-blocking monitoring hook)
        warnings = list(self.guard.evaluate(before, current, context, self.registry))
        drift_event = self.drift_monitor.evaluate(
            before,
            current,
            document_id=context.document.document_id,
            source=context.source,
        )
        needs_review = bool(warnings)
        removed_chars = max(0, len(before) - len(current))
        removed_ratio = removed_chars / len(before) if before else 0.0
        normalized_hash = "sha256:" + hashlib.sha256(
            before.encode("utf-8")
        ).hexdigest()

        document = context.document
        doc_metadata = dict(document.metadata)
        doc_metadata["language_detection"] = lang_annotation.model_dump(mode="json")
        doc_metadata["concept_drift"] = drift_event.model_dump(mode="json")

        cleaned = CleanedDocument(
            document_id=document.document_id,
            source_id=document.source_id,
            source=context.source,
            source_type=context.source_type,
            document_type=context.document_type,
            document_type_origin=context.document_type_origin,
            document_type_sidecar_version=context.document_type_sidecar_version,
            document_type_sidecar_checksum=context.document_type_sidecar_checksum,
            title=document.title,
            author=document.author,
            published_at=document.published_at,
            language=document.language,
            original_file_type=document.original_file_type.value,
            corpus_version=document.corpus_version,
            cleaned_text=current,
            clean_version=self.registry.clean_version,
            rule_registry_checksum=self.registry_checksum,
            rules_applied=applied_rules,
            warnings=warnings,
            needs_review=needs_review,
            normalized_path=context.relative_normalized_path,
            normalized_content_hash=normalized_hash,
            metadata=doc_metadata,
        )

        audit = CleanDocumentAudit(
            document_id=document.document_id,
            source_id=document.source_id,
            source=context.source,
            source_type=context.source_type,
            document_type=context.document_type,
            document_type_origin=context.document_type_origin,
            document_type_sidecar_version=context.document_type_sidecar_version,
            document_type_sidecar_checksum=context.document_type_sidecar_checksum,
            before_length=len(before),
            after_length=len(current),
            removed_chars=removed_chars,
            removed_ratio=round(removed_ratio, 8),
            changed=current != before,
            applied_rules=applied_rules,
            rule_hits=rule_hits,
            needs_review=needs_review,
            warnings=warnings,
        )

        single_report = CleanDocumentReport(
            report_version="v0.2",
            document_id=document.document_id,
            source_id=document.source_id,
            source=context.source,
            source_type=context.source_type,
            document_type=context.document_type,
            clean_version=self.registry.clean_version,
            rule_registry_checksum=self.registry_checksum,
            before_length=len(before),
            after_length=len(current),
            removed_chars=removed_chars,
            removed_ratio=round(removed_ratio, 8),
            changed=current != before,
            rules_applied=applied_rules,
            rule_hits=rule_hits,
            warnings=warnings,
            needs_review=needs_review,
            line_diff=line_diff_stats,
            stages=stages,
            language=lang_annotation,
            sample_diff=diff_sample,
        )

        return CleanExecutionResult(
            document=cleaned,
            audit=audit,
            before_text=before,
            after_text=current,
            line_diff=line_diff_stats,
            stages=tuple(stages),
            language_annotation=lang_annotation,
            single_report=single_report,
        )

    def _apply_rule(self, text: str, rule: CleanRule) -> tuple[str, int]:
        if rule.action is RuleAction.NORMALIZE:
            return self._normalize(text, rule.pattern)
        assert rule.compiled_pattern is not None
        if rule.action is RuleAction.REPLACE:
            return rule.compiled_pattern.subn(rule.replacement or "", text)
        if rule.action is RuleAction.DROP_BLOCK:
            return rule.compiled_pattern.subn("", text)
        if rule.action is RuleAction.DROP_LINE:
            output: list[str] = []
            hits = 0
            for line in text.splitlines(keepends=True):
                candidate = line.rstrip("\r\n")
                if rule.compiled_pattern.search(candidate):
                    hits += 1
                else:
                    output.append(line)
            return "".join(output), hits
        raise AssertionError(f"unsupported rule action:{rule.action}")

    @staticmethod
    def _normalize(text: str, operation: str) -> tuple[str, int]:
        if operation == "UNICODE_NFC":
            updated = unicodedata.normalize("NFC", text)
            return updated, int(updated != text)
        if operation == "NEWLINES":
            hits = text.count("\r\n") + text.replace("\r\n", "").count("\r")
            return text.replace("\r\n", "\n").replace("\r", "\n"), hits
        if operation == "TRAILING_WHITESPACE":
            return re.subn(r"[ \t]+(?=\n|$)", "", text)
        if operation == "BLANK_LINES":
            return re.subn(r"\n{3,}", "\n\n", text)
        if operation == "FINAL_NEWLINE":
            updated = text.rstrip("\n") + ("\n" if text else "")
            return updated, int(updated != text)
        raise AssertionError(f"unsupported normalize operation:{operation}")
