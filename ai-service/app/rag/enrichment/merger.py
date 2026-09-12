"""Priority-aware merge for inherited, deterministic, and LLM metadata."""

from __future__ import annotations

from .models import (
    ChunkEnrichment,
    ContentType,
    EnrichmentStatus,
    EntityMention,
    MetadataConflict,
    ProvenanceMetadata,
    QualityMetadata,
    RuleEnrichmentResult,
    SemanticEnrichmentOutput,
)
from .normalizer import (
    EnrichmentNormalizer,
    deduplicate_strings,
    normalized_entity_key,
)


class MetadataMerger:
    """Apply existing/inherited > rule > LLM precedence without silent overwrite."""

    def __init__(self, normalizer: EnrichmentNormalizer | None = None) -> None:
        self.normalizer = normalizer or EnrichmentNormalizer()

    def merge(
        self,
        rule: RuleEnrichmentResult,
        semantic: SemanticEnrichmentOutput | None,
        *,
        provenance: ProvenanceMetadata,
        status: EnrichmentStatus,
        warnings: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> ChunkEnrichment:
        conflicts: list[MetadataConflict] = []
        content_type = rule.content.content_type
        if semantic is not None:
            if content_type is ContentType.UNKNOWN:
                content_type = semantic.content_type
            elif (
                semantic.content_type is not ContentType.UNKNOWN
                and semantic.content_type is not content_type
            ):
                conflicts.append(
                    MetadataConflict(
                        field="content.content_type",
                        existing=content_type.value,
                        candidate=semantic.content_type.value,
                    )
                )

        topics = list(rule.semantic.topics)
        keywords = list(rule.semantic.keywords)
        mentions = list(rule.entities.mentions)
        primary = rule.entities.primary_subject
        if semantic is not None:
            topics.extend(semantic.topics)
            keywords.extend(semantic.keywords)
            mentions, mention_conflicts = self._merge_mentions(
                mentions, self.normalizer.semantic_mentions(semantic)
            )
            conflicts.extend(mention_conflicts)
            candidate_primary = self.normalizer.semantic_primary(semantic)
            if primary is None:
                primary = candidate_primary
            elif candidate_primary is not None and normalized_entity_key(
                primary.text
            ) != normalized_entity_key(candidate_primary.text):
                conflicts.append(
                    MetadataConflict(
                        field="entities.primary_subject",
                        existing=primary.model_dump(mode="json"),
                        candidate=candidate_primary.model_dump(mode="json"),
                    )
                )

        return ChunkEnrichment(
            content={
                "language": rule.content.language,
                "content_type": content_type,
            },
            semantic={
                "topics": deduplicate_strings(topics, limit=8),
                "keywords": deduplicate_strings(keywords, limit=12),
            },
            temporal=rule.temporal,
            entities={"primary_subject": primary, "mentions": mentions},
            source=rule.source,
            quality=QualityMetadata(
                enrichment_status=status,
                warnings=deduplicate_strings(warnings or [], limit=100),
                errors=deduplicate_strings(errors or [], limit=20),
                metadata_conflicts=conflicts,
            ),
            provenance=provenance,
        )

    @staticmethod
    def _merge_mentions(
        existing: list[EntityMention], candidates: list[EntityMention]
    ) -> tuple[list[EntityMention], list[MetadataConflict]]:
        result = list(existing)
        by_key = {normalized_entity_key(item.normalized_text): item for item in result}
        conflicts: list[MetadataConflict] = []
        for candidate in candidates:
            key = normalized_entity_key(candidate.normalized_text)
            current = by_key.get(key)
            if current is None:
                by_key[key] = candidate
                result.append(candidate)
                continue
            if current.type_hint is not candidate.type_hint:
                conflicts.append(
                    MetadataConflict(
                        field=f"entities.mentions[{current.normalized_text}]",
                        existing=current.model_dump(mode="json"),
                        candidate=candidate.model_dump(mode="json"),
                    )
                )
        return result[:100], conflicts[:100]
