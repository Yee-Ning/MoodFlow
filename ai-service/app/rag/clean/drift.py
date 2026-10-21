"""Concept drift monitoring hooks for clean-stage text length ratio anomalies."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pydantic import BaseModel, ConfigDict, Field

from .config import ConceptDriftConfig

logger = logging.getLogger(__name__)


class ConceptDriftEvent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    source: str
    before_length: int = Field(ge=0)
    after_length: int = Field(ge=0)
    retention_ratio: float = Field(ge=0.0)
    drift_detected: bool
    reasons: list[str] = Field(default_factory=list)


@dataclass
class ConceptDriftMonitor:
    """Non-blocking concept drift monitor that sounds alarms when length ratios deviate."""

    config: ConceptDriftConfig
    _hooks: list[Callable[[ConceptDriftEvent], None]] = field(default_factory=list)

    def register_hook(self, hook: Callable[[ConceptDriftEvent], None]) -> None:
        self._hooks.append(hook)

    def evaluate(
        self,
        before_text: str,
        after_text: str,
        *,
        document_id: str,
        source: str,
    ) -> ConceptDriftEvent:
        if not self.config.enabled:
            return ConceptDriftEvent(
                document_id=document_id,
                source=source,
                before_length=len(before_text),
                after_length=len(after_text),
                retention_ratio=1.0 if not before_text else len(after_text) / len(before_text),
                drift_detected=False,
                reasons=[],
            )

        before_len = len(before_text)
        after_len = len(after_text)
        ratio = (after_len / before_len) if before_len > 0 else 1.0

        reasons: list[str] = []
        if ratio < self.config.min_retention_ratio:
            reasons.append(
                f"length_ratio_too_low:ratio={ratio:.4f},min_threshold={self.config.min_retention_ratio}"
            )
        if ratio > self.config.max_retention_ratio:
            reasons.append(
                f"length_ratio_expanded:ratio={ratio:.4f},max_threshold={self.config.max_retention_ratio}"
            )

        drift_detected = bool(reasons)
        event = ConceptDriftEvent(
            document_id=document_id,
            source=source,
            before_length=before_len,
            after_length=after_len,
            retention_ratio=round(ratio, 6),
            drift_detected=drift_detected,
            reasons=reasons,
        )

        if drift_detected:
            logger.warning(
                "Clean concept drift anomaly detected for doc %s (source=%s): %s",
                document_id,
                source,
                "; ".join(reasons),
            )

        for hook in self._hooks:
            try:
                hook(event)
            except Exception as exc:
                logger.error("Drift hook failed for %s: %s", document_id, exc)

        return event
