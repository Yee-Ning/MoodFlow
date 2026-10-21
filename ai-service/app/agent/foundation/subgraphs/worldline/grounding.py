"""Deterministic citation grounding checks for WorldLine narratives."""

from __future__ import annotations

import re
from collections.abc import Sequence

from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource,
    NarrativeOutput,
)
from .narrative_boundary import boundary_template_check
from .narrative_normalization import emit_narrative_audit

_INLINE_CITATION = re.compile(r"\[([^\[\]\s]{1,128})\]")
_ABSTENTION_SEPARATORS = re.compile(r"[\s,，.。．!！?？;；]+")
_ABSTENTION_PARAGRAPH = re.compile(
    r"(?:当前提供的证据不足以回答这个问题暂时无法确认|请提供具体歌曲名称或相关资料)+"
)


def _is_abstention_paragraph(paragraph: str) -> bool:
    """Recognize only complete fixed sentences, never keywords or free text.

    Keep citation brackets and all non-separator characters intact so neither
    a citation nor an appended factual clause can borrow this exemption.
    """
    return _ABSTENTION_PARAGRAPH.fullmatch(_ABSTENTION_SEPARATORS.sub("", paragraph)) is not None


def inline_citation_ids(narrative: str) -> list[str]:
    """Return each citation used in the prose, in first-use order."""
    return list(dict.fromkeys(_INLINE_CITATION.findall(narrative)))


class WorldLineGroundingError(ValueError):
    """Raised when generated citations cannot be traced to supplied evidence."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class WorldLineGroundingValidator:
    def validate(
        self,
        output: NarrativeOutput,
        sources: Sequence[EvidenceSource],
        *,
        boundary_context=None,
    ) -> None:
        allowed = {item.id: item for item in sources}
        inline_ids = inline_citation_ids(output.narrative)
        if output.citation_ids != inline_ids:
            raise WorldLineGroundingError(
                "citation_order_mismatch",
                "citation ids must match inline citations in first-use order"
            )
        unknown = set(output.citation_ids).difference(allowed)
        if unknown:
            raise WorldLineGroundingError(
                "unknown_citation", "narrative cites an unknown evidence source"
            )
        paragraphs = [item.strip() for item in output.narrative.split("\n") if item.strip()]
        exempt = set()
        for index, paragraph in enumerate(paragraphs):
            if _is_abstention_paragraph(paragraph):
                exempt.add(index)
            check = boundary_template_check(paragraph, boundary_context)
            if check is not None:
                emit_narrative_audit({"event": "worldline.boundary_template_check.v1",
                    "invocation_id": boundary_context.invocation_id, "attempt": boundary_context.attempt,
                    "paragraph_index": index + 1, **check})
                if check["matched"]:
                    exempt.add(index)
        pure_abstention = bool(paragraphs) and len(exempt) == len(paragraphs)
        if allowed and not output.citation_ids and not pure_abstention:
            raise WorldLineGroundingError(
                "missing_citation", "narrative omitted citations despite available evidence"
            )
        for citation_id in output.citation_ids:
            source = allowed[citation_id]
            if source.source_type.value == "knowledge" and not source.excerpt:
                raise WorldLineGroundingError(
                    "missing_knowledge_excerpt",
                    "knowledge citation requires a nonempty evidence excerpt"
                )
        if allowed:
            self._validate_paragraph_anchors(output.narrative, exempt=exempt)

    @staticmethod
    def _validate_paragraph_anchors(narrative: str, *, exempt=frozenset()) -> None:
        paragraphs = [item.strip() for item in narrative.split("\n") if item.strip()]
        for index, paragraph in enumerate(paragraphs):
            if index in exempt or _is_abstention_paragraph(paragraph):
                continue
            if paragraph == "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。":
                continue
            plain = _INLINE_CITATION.sub("", paragraph).strip()
            if len(plain) >= 12 and _INLINE_CITATION.search(paragraph) is None:
                raise WorldLineGroundingError(
                    "paragraph_missing_citation",
                    "each substantive narrative paragraph requires a citation"
                )
