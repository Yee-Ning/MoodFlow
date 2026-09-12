"""Post-merge validation for content preservation and normalized metadata."""

from __future__ import annotations

from .models import BaseChunk, EnrichedChunk
from .normalizer import normalize_text


class EnrichmentValidationError(ValueError):
    pass


class EnrichmentValidator:
    def validate(self, source: BaseChunk, enriched: EnrichedChunk) -> None:
        source_payload = source.model_dump(mode="json", exclude={"enrichment"})
        enriched_payload = enriched.model_dump(mode="json", exclude={"enrichment"})
        if source_payload != enriched_payload:
            raise EnrichmentValidationError("source_chunk_mutated")
        if enriched.enrichment.source.source_id is not None:
            inherited_source_id = source.metadata.get("source_id")
            if (
                isinstance(inherited_source_id, str)
                and enriched.enrichment.source.source_id != inherited_source_id
            ):
                raise EnrichmentValidationError("source_id_mismatch")
        for mention in enriched.enrichment.entities.mentions:
            if normalize_text(mention.normalized_text) != mention.normalized_text:
                raise EnrichmentValidationError("entity_mention_not_normalized")
        primary = enriched.enrichment.entities.primary_subject
        if primary is not None and normalize_text(primary.text) != primary.text:
            raise EnrichmentValidationError("primary_subject_not_normalized")
