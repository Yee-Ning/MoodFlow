"""Rule-first metadata extraction from chunk structure and inherited metadata."""

from __future__ import annotations

import hashlib
import json
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.rag.knowledge.entity_linking.registry import EntityRegistry

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
_WORK_PREFIX_RE = re.compile(
    r"(?P<cue>专辑|專輯|唱片|歌曲|单曲|單曲|album|song|track)\s*[:：]?\s*$",
    re.IGNORECASE,
)


class RuleEnricher:
    def __init__(
        self,
        temporal_extractor: TemporalExtractor | None = None,
        *,
        inheritance: MetadataInheritanceResolver | None = None,
        registry: EntityRegistry | None = None,
    ) -> None:
        self.temporal_extractor = temporal_extractor or TemporalExtractor()
        self.inheritance = inheritance or MetadataInheritanceResolver()
        if registry is None:
            # Import at construction time to avoid an enrichment/knowledge package
            # import cycle. Only the existing local vocabulary is read here.
            from app.rag.knowledge.entity_linking.registry import EntityRegistry

            registry = EntityRegistry.load()
        aliases = tuple(
            sorted(
                (
                    alias
                    for alias in registry.detectable_aliases()
                    if any(
                        entity.entity_type.value in {"album", "song"}
                        for entity in registry.candidates(alias)
                    )
                ),
                key=lambda value: (-len(value), value.casefold(), value),
            )
        )
        patterns = []
        for alias in aliases:
            pattern = re.escape(alias)
            if alias.isascii() and alias[0].isalnum() and alias[-1].isalnum():
                pattern = rf"(?<![A-Za-z0-9_]){pattern}(?![A-Za-z0-9_])"
            flags = re.IGNORECASE if re.search(r"[A-Za-z]", alias) else 0
            patterns.append(re.compile(pattern, flags))
        self._work_patterns = tuple(patterns)
        self._fingerprint = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(
                    {
                        "rule_version": "quoted_and_registry_works_v2",
                        "quoted_pattern": _QUOTED_WORK_RE.pattern,
                        "work_prefix_pattern": _WORK_PREFIX_RE.pattern,
                        "work_aliases": aliases,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
        )

    def fingerprint(self) -> str:
        return self._fingerprint

    def enrich(
        self, chunk: BaseChunk, context: EnrichmentContext | None = None
    ) -> RuleEnrichmentResult:
        context = context or self.inheritance.resolve(chunk)
        mentions = list(context.entity_mentions)
        occupied: list[tuple[int, int]] = []
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
            occupied.append(match.span())
        # Registry aliases are longest-first, so e.g. 依然范特西 owns its span
        # before 范特西. Only an adjacent explicit cue supplies a type hint;
        # ambiguous bare titles still need downstream entity resolution.
        for pattern in self._work_patterns:
            for match in pattern.finditer(chunk.content):
                start, end = match.span()
                if any(start < right and left < end for left, right in occupied):
                    continue
                occupied.append((start, end))
                text = normalize_text(match.group(0))
                mentions.append(
                    EntityMention(
                        text=text,
                        normalized_text=text,
                        type_hint=self._work_type_hint(chunk.content, start),
                        confidence=0.85,
                        source=MentionSource.RULE,
                    )
                )
        # Respect the existing 100-mention contract, preserving metadata priority.
        unique: list[EntityMention] = []
        seen: set[str] = set()
        for mention in mentions:
            key = normalize_text(mention.normalized_text).casefold()
            if key not in seen:
                seen.add(key)
                unique.append(mention)
            if len(unique) == 100:
                break
        return RuleEnrichmentResult(
            content={
                "language": context.language or self._detect_language(chunk.content),
                "content_type": context.content_type,
            },
            semantic={"topics": context.topics, "keywords": context.keywords},
            temporal=self.temporal_extractor.extract(chunk.content),
            entities={
                "primary_subject": context.primary_subject,
                "mentions": unique,
            },
            source=context.source,
        )

    @staticmethod
    def _work_type_hint(content: str, start: int) -> EntityTypeHint:
        match = _WORK_PREFIX_RE.search(content[max(0, start - 32) : start])
        if match is None:
            return EntityTypeHint.UNKNOWN
        if match.group("cue").casefold() in {"专辑", "專輯", "唱片", "album"}:
            return EntityTypeHint.ALBUM
        return EntityTypeHint.SONG

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
