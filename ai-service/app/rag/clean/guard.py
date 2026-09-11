"""Semantic-loss warnings that make aggressive cleaning observable."""

from __future__ import annotations

import re
from collections import Counter

from .config import SemanticGuardConfig
from .corpus import NormalizedDocumentContext
from .registry import RuleRegistry


class SemanticGuard:
    def __init__(self, config: SemanticGuardConfig) -> None:
        self.config = config
        self.numeric_pattern = re.compile(config.numeric_pattern)
        self.date_pattern = re.compile(config.date_pattern)
        self.structured_patterns = [
            re.compile(pattern) for pattern in config.structured_fact_patterns
        ]

    def evaluate(
        self,
        before: str,
        after: str,
        context: NormalizedDocumentContext,
        registry: RuleRegistry,
    ) -> list[str]:
        warnings: list[str] = []
        if not registry.is_known_source(context.source):
            warnings.append(f"unknown_source:{context.source}")
        if context.document_type is None:
            warnings.append("missing_document_type")
        elif not registry.is_known_document_type(context.document_type):
            warnings.append(f"unknown_document_type:{context.document_type}")
        if not before.strip():
            warnings.append("input_text_empty")
        elif not after.strip():
            warnings.append("cleaned_text_empty")

        removed_chars = max(0, len(before) - len(after))
        removed_ratio = removed_chars / len(before) if before else 0.0
        if removed_ratio >= self.config.removed_ratio_warning:
            warnings.append(f"removed_ratio_high:{removed_ratio:.4f}")
        if (
            removed_chars >= self.config.large_drop_min_chars
            and removed_ratio >= self.config.large_drop_ratio
        ):
            warnings.append(
                f"large_text_drop:chars={removed_chars},ratio={removed_ratio:.4f}"
            )

        numeric_before = self._numeric_facts(before)
        numeric_after = self._numeric_facts(after)
        self._append_fact_loss_warning(
            warnings,
            "numeric_or_date_facts_lost",
            numeric_before,
            numeric_after,
            minimum=self.config.min_numeric_facts,
        )
        structured_before = self._structured_facts(before)
        structured_after = self._structured_facts(after)
        self._append_fact_loss_warning(
            warnings,
            "structured_facts_lost",
            structured_before,
            structured_after,
            minimum=self.config.min_structured_facts,
        )
        return warnings

    def _append_fact_loss_warning(
        self,
        warnings: list[str],
        code: str,
        before: Counter[str],
        after: Counter[str],
        *,
        minimum: int,
    ) -> None:
        total = sum(before.values())
        if total < minimum:
            return
        retained = sum((before & after).values())
        lost = total - retained
        ratio = lost / total
        if ratio >= self.config.fact_loss_warning_ratio:
            warnings.append(f"{code}:count={lost},ratio={ratio:.4f}")

    def _numeric_facts(self, text: str) -> Counter[str]:
        facts = [f"number:{match.group(0)}" for match in self.numeric_pattern.finditer(text)]
        facts.extend(
            f"date:{match.group(0)}" for match in self.date_pattern.finditer(text)
        )
        return Counter(facts)

    def _structured_facts(self, text: str) -> Counter[str]:
        facts: list[str] = []
        for pattern in self.structured_patterns:
            facts.extend(match.group(0).strip() for match in pattern.finditer(text))
        return Counter(facts)
