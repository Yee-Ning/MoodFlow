"""Pure deterministic CleanEngine with auditable per-rule changes."""

from __future__ import annotations

import hashlib
import re
import unicodedata

from .corpus import NormalizedDocumentContext
from .guard import SemanticGuard
from .models import CleanDocumentAudit, CleanedDocument, CleanExecutionResult
from .registry import CleanRule, RuleAction, RuleRegistry


class CleanEngine:
    def __init__(
        self,
        registry: RuleRegistry,
        guard: SemanticGuard,
        *,
        registry_checksum: str,
    ) -> None:
        self.registry = registry
        self.guard = guard
        self.registry_checksum = registry_checksum

    def clean(self, context: NormalizedDocumentContext) -> CleanExecutionResult:
        before = context.document.markdown
        current = before
        applied_rules: list[str] = []
        rule_hits: dict[str, int] = {}
        for rule in self.registry.rules_for(context):
            updated, hits = self._apply_rule(current, rule)
            if updated != current:
                applied_rules.append(rule.id)
                rule_hits[rule.id] = hits
                current = updated

        warnings = self.guard.evaluate(before, current, context, self.registry)
        needs_review = bool(warnings)
        removed_chars = max(0, len(before) - len(current))
        removed_ratio = removed_chars / len(before) if before else 0.0
        normalized_hash = "sha256:" + hashlib.sha256(
            before.encode("utf-8")
        ).hexdigest()
        document = context.document
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
            metadata=document.metadata,
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
        return CleanExecutionResult(
            document=cleaned,
            audit=audit,
            before_text=before,
            after_text=current,
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
