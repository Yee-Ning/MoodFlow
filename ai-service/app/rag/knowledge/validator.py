"""Read-only validation for persisted knowledge-enriched corpora."""

from __future__ import annotations

from app.rag.chunk.models import ChildChunk
from app.rag.enrichment.models import EnrichedChunk

from .models import KnowledgeEnrichedChunk, KnowledgeStats


class KnowledgeValidationError(ValueError):
    pass


class KnowledgeValidator:
    def validate(
        self,
        source: EnrichedChunk,
        knowledge_chunk: KnowledgeEnrichedChunk,
    ) -> None:
        source_payload = source.model_dump(mode="json", exclude={"knowledge"})
        knowledge_payload = knowledge_chunk.model_dump(
            mode="json", exclude={"knowledge"}
        )
        if source_payload != knowledge_payload:
            raise KnowledgeValidationError("enriched_chunk_mutated")

        entities = knowledge_chunk.knowledge.entities
        claims = knowledge_chunk.knowledge.claims
        if KnowledgeStats.build(entities, claims) != knowledge_chunk.knowledge.stats:
            raise KnowledgeValidationError("knowledge_stats_mismatch")

        entity_ids = [item.entity_id for item in entities.linked_entities]
        if len(entity_ids) != len(set(entity_ids)):
            raise KnowledgeValidationError("duplicate_linked_entity_id")
        claim_ids = [item.claim.claim_id for item in claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise KnowledgeValidationError("duplicate_claim_id")

        for item in claims:
            claim = item.claim
            if claim.source_chunk_id != source.chunk_id:
                raise KnowledgeValidationError("claim_source_chunk_mismatch")
            if claim.evidence_span not in source.content:
                raise KnowledgeValidationError("claim_evidence_not_grounded")
            verification_evidence = item.verification.evidence_span
            if verification_evidence and verification_evidence not in source.content:
                raise KnowledgeValidationError("verification_evidence_not_grounded")

    def validate_parent_references(self, chunks: list[KnowledgeEnrichedChunk]) -> None:
        by_id = {chunk.chunk_id: chunk for chunk in chunks}
        for chunk in chunks:
            if not isinstance(chunk, ChildChunk):
                continue
            parent = by_id.get(chunk.parent_id)
            if parent is None or isinstance(parent, ChildChunk):
                raise KnowledgeValidationError("knowledge_parent_missing")
            if parent.doc_id != chunk.doc_id:
                raise KnowledgeValidationError("knowledge_parent_document_mismatch")
