"""Deterministic citation grounding checks for WorldLine narratives."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource,
    NarrativeOutput,
)

_INLINE_CITATION = re.compile(r"\[([^\[\]\s]{1,128})\]")


def inline_citation_ids(narrative: str) -> list[str]:
    """Return each citation used in the prose, in first-use order."""
    return list(dict.fromkeys(_INLINE_CITATION.findall(narrative)))


class WorldLineGroundingError(ValueError):
    """Raised when generated citations cannot be traced to supplied evidence."""


class WorldLineGroundingValidator:
    def validate(
        self,
        output: NarrativeOutput,
        sources: Sequence[EvidenceSource],
    ) -> None:
        allowed = {item.id: item for item in sources}
        inline_ids = inline_citation_ids(output.narrative)
        if output.citation_ids != inline_ids:
            raise WorldLineGroundingError(
                "citation ids must match inline citations in first-use order"
            )
        unknown = set(output.citation_ids).difference(allowed)
        if unknown:
            raise WorldLineGroundingError("narrative cites an unknown evidence source")
        if allowed and not output.citation_ids:
            raise WorldLineGroundingError("narrative omitted citations despite available evidence")
        for citation_id in output.citation_ids:
            source = allowed[citation_id]
            if source.source_type.value == "knowledge" and not source.excerpt:
                raise WorldLineGroundingError(
                    "knowledge citation requires a nonempty evidence excerpt"
                )
        if allowed:
            self._validate_paragraph_anchors(output.narrative)

    @staticmethod
    def _validate_paragraph_anchors(narrative: str) -> None:
        paragraphs = [item.strip() for item in narrative.split("\n") if item.strip()]
        for paragraph in paragraphs:
            plain = _INLINE_CITATION.sub("", paragraph).strip()
            if len(plain) >= 12 and _INLINE_CITATION.search(paragraph) is None:
                raise WorldLineGroundingError(
                    "each substantive narrative paragraph requires a citation"
                )
