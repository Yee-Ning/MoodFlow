"""Conservative normalization without alias merging or entity resolution."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

from .models import (
    EntityMention,
    MentionSource,
    PrimarySubject,
    RuleEnrichmentResult,
    SemanticEnrichmentOutput,
)


_SYMBOL_TRANSLATION = str.maketrans(
    {
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
        "‐": "-",
        "‑": "-",
        "–": "-",
        "—": "-",
        "〜": "~",
        "～": "~",
    }
)


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).translate(_SYMBOL_TRANSLATION)
    return re.sub(r"\s+", " ", value).strip()


def normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = normalize_text(value)
    return normalized or None


def deduplicate_strings(values: Iterable[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = normalize_text(value)
        key = normalized.casefold()
        if not normalized or key in seen:
            continue
        seen.add(key)
        result.append(normalized)
        if len(result) >= limit:
            break
    return result


def normalized_entity_key(value: str) -> str:
    return normalize_text(value).casefold()


class EnrichmentNormalizer:
    def normalize_rule(self, value: RuleEnrichmentResult) -> RuleEnrichmentResult:
        mentions = self._deduplicate_mentions(value.entities.mentions)
        primary = self._normalize_primary(value.entities.primary_subject)
        return RuleEnrichmentResult(
            content={
                "language": normalize_optional_text(value.content.language),
                "content_type": value.content.content_type,
            },
            semantic={
                "topics": deduplicate_strings(value.semantic.topics, limit=8),
                "keywords": deduplicate_strings(value.semantic.keywords, limit=12),
            },
            temporal={
                **value.temporal.model_dump(mode="python", exclude={"raw_mentions"}),
                "raw_mentions": deduplicate_strings(
                    value.temporal.raw_mentions, limit=50
                ),
            },
            entities={"primary_subject": primary, "mentions": mentions},
            source={
                key: normalize_optional_text(raw)
                for key, raw in value.source.model_dump(mode="python").items()
            },
        )

    def normalize_semantic(
        self, value: SemanticEnrichmentOutput
    ) -> SemanticEnrichmentOutput:
        mentions = []
        seen: set[str] = set()
        for mention in value.entity_mentions:
            text = normalize_text(mention.text)
            key = normalized_entity_key(text)
            if not text or key in seen:
                continue
            seen.add(key)
            mentions.append(mention.model_copy(update={"text": text}))
        primary = value.primary_subject
        if primary is not None:
            text = normalize_text(primary.text)
            primary = primary.model_copy(update={"text": text}) if text else None
        return SemanticEnrichmentOutput(
            content_type=value.content_type,
            topics=deduplicate_strings(value.topics, limit=8),
            keywords=deduplicate_strings(value.keywords, limit=12),
            entity_mentions=mentions,
            primary_subject=primary,
        )

    @staticmethod
    def semantic_mentions(value: SemanticEnrichmentOutput) -> list[EntityMention]:
        return [
            EntityMention(
                text=mention.text,
                normalized_text=normalize_text(mention.text),
                type_hint=mention.type_hint,
                confidence=mention.confidence,
                source=MentionSource.LLM,
            )
            for mention in value.entity_mentions
        ]

    @staticmethod
    def semantic_primary(value: SemanticEnrichmentOutput) -> PrimarySubject | None:
        if value.primary_subject is None:
            return None
        return PrimarySubject(**value.primary_subject.model_dump(mode="python"))

    @staticmethod
    def _normalize_primary(value: PrimarySubject | None) -> PrimarySubject | None:
        if value is None:
            return None
        text = normalize_text(value.text)
        if not text:
            return None
        return value.model_copy(update={"text": text})

    @staticmethod
    def _deduplicate_mentions(values: Iterable[EntityMention]) -> list[EntityMention]:
        result: list[EntityMention] = []
        seen: set[str] = set()
        for value in values:
            text = normalize_text(value.text)
            normalized = normalize_text(value.normalized_text or text)
            key = normalized_entity_key(normalized)
            if not text or not normalized or key in seen:
                continue
            seen.add(key)
            result.append(
                value.model_copy(update={"text": text, "normalized_text": normalized})
            )
        return result
