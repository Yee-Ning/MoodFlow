"""Evaluation loop hooks and metrics for Clean-stage False Positive/Negative estimation."""

from __future__ import annotations

import random
from typing import Callable
from pydantic import BaseModel, ConfigDict, Field

from .models import CleanExecutionResult


class CleanEvaluationSample(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    source: str
    before_text: str
    after_text: str
    removed_chars: int = Field(ge=0)
    applied_rules: list[str]
    needs_review: bool
    manual_labeled_false_positives: int = 0  # Count of incorrectly removed benign items
    manual_labeled_false_negatives: int = 0  # Count of missed noise items that remained


class CleanEvaluationMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    sample_size: int = Field(ge=0)
    total_removed_chars: int = Field(ge=0)
    total_false_positives: int = Field(ge=0)
    total_false_negatives: int = Field(ge=0)
    estimated_false_positive_rate: float = Field(ge=0.0, le=1.0)
    estimated_false_negative_rate: float = Field(ge=0.0, le=1.0)


class CleanEvaluationService:
    """Provides hooks for sampling cleaned outputs and computing precision/recall/FPR metrics."""

    def __init__(self, seed: int = 104729) -> None:
        self.seed = seed
        self._sampling_hooks: list[Callable[[list[CleanEvaluationSample]], None]] = []

    def register_sampling_hook(
        self, hook: Callable[[list[CleanEvaluationSample]], None]
    ) -> None:
        self._sampling_hooks.append(hook)

    def sample_for_evaluation(
        self,
        results: list[CleanExecutionResult],
        sample_size: int = 20,
    ) -> list[CleanEvaluationSample]:
        """Reproducibly sample documents for human verification of false positive / false negative rates."""
        rng = random.Random(self.seed)
        eligible = [r for r in results if r.audit.changed]
        if not eligible:
            eligible = list(results)

        chosen = rng.sample(eligible, min(sample_size, len(eligible)))
        samples = [
            CleanEvaluationSample(
                document_id=r.document.document_id,
                source=r.document.source,
                before_text=r.before_text,
                after_text=r.after_text,
                removed_chars=r.audit.removed_chars,
                applied_rules=r.audit.applied_rules,
                needs_review=r.document.needs_review,
            )
            for r in sorted(chosen, key=lambda item: item.document.document_id)
        ]

        for hook in self._sampling_hooks:
            hook(samples)

        return samples

    @staticmethod
    def compute_metrics(
        evaluated_samples: list[CleanEvaluationSample],
    ) -> CleanEvaluationMetrics:
        """Compute estimated FPR and FNR from human-reviewed samples."""
        if not evaluated_samples:
            return CleanEvaluationMetrics(
                sample_size=0,
                total_removed_chars=0,
                total_false_positives=0,
                total_false_negatives=0,
                estimated_false_positive_rate=0.0,
                estimated_false_negative_rate=0.0,
            )

        total_fp = sum(s.manual_labeled_false_positives for s in evaluated_samples)
        total_fn = sum(s.manual_labeled_false_negatives for s in evaluated_samples)
        total_rem = sum(s.removed_chars for s in evaluated_samples)
        total_rules_fired = sum(len(s.applied_rules) for s in evaluated_samples)

        # FPR = false positives / total rule deletions (or 0.0 if none)
        denom_fp = total_rules_fired if total_rules_fired > 0 else 1
        fpr = min(1.0, round(total_fp / denom_fp, 6))

        # FNR = false negatives / (total rules fired + false negatives)
        denom_fn = total_rules_fired + total_fn
        fnr = min(1.0, round(total_fn / denom_fn, 6)) if denom_fn > 0 else 0.0

        return CleanEvaluationMetrics(
            sample_size=len(evaluated_samples),
            total_removed_chars=total_rem,
            total_false_positives=total_fp,
            total_false_negatives=total_fn,
            estimated_false_positive_rate=fpr,
            estimated_false_negative_rate=fnr,
        )
