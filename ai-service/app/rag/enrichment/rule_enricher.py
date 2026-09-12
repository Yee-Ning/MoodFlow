"""Rule-first metadata extraction from chunk structure and inherited metadata."""

from __future__ import annotations

import re

from .inheritance import EnrichmentContext, MetadataInheritanceResolver
from .models import (
    BaseChunk,
    EntityMention,
    EntityTypeHint,
    MentionSource,
    RuleEnrichmentResult,
)
from .normalizer import normalize_text
from .temporal import TemporalExtractor


_QUOTED_WORK_RE = re.compile(r"[《〈](?P<text>[^》〉\n]{1,100})[》〉]")


class RuleEnricher:
    def __init__(
        self,
        temporal_extractor: TemporalExtractor | None = None,
        *,
        inheritance: MetadataInheritanceResolver | None = None,
    ) -> None:
        self.temporal_extractor = temporal_extractor or TemporalExtractor()
        self.inheritance = inheritance or MetadataInheritanceResolver()

    def enrich(
        self, chunk: BaseChunk, context: EnrichmentContext | None = None
    ) -> RuleEnrichmentResult:
        context = context or self.inheritance.resolve(chunk)
        mentions = list(context.entity_mentions)
        for match in _QUOTED_WORK_RE.finditer(chunk.content):
            text = normalize_text(match.group("text"))
            if not text:
                continue
            mentions.append(
                EntityMention(
                    text=text,
                    normalized_text=text,
                    type_hint=EntityTypeHint.UNKNOWN,
                    confidence=0.65,
                    source=MentionSource.RULE,
                )
            )
        return RuleEnrichmentResult(
            content={
                "language": context.language or self._detect_language(chunk.content),
                "content_type": context.content_type,
            },
            semantic={"topics": context.topics, "keywords": context.keywords},
            temporal=self.temporal_extractor.extract(chunk.content),
            entities={
                "primary_subject": context.primary_subject,
                "mentions": mentions,
            },
            source=context.source,
        )

    @staticmethod
    def _detect_language(content: str) -> str | None:
        if not content.strip():
            return None
        counts = {
            "zh": len(re.findall(r"[\u4e00-\u9fff]", content)),
            "ja": len(re.findall(r"[\u3040-\u30ff]", content)),
            "ko": len(re.findall(r"[\uac00-\ud7af]", content)),
            "en": len(re.findall(r"[A-Za-z]", content)),
        }
        language, count = max(counts.items(), key=lambda item: item[1])
        return language if count else "und"
