"""Deterministic knowledge chunk to index document projection."""

from __future__ import annotations

import hashlib
import json
import uuid

from app.rag.chunk.models import ChunkType
from app.rag.knowledge.models import (
    KnowledgeEnrichedChildChunk,
    KnowledgeEnrichedParentChunk,
)
from app.rag.knowledge.verification.models import VerifiedClaim, VerificationStatus
from app.rag.knowledge.child_grounding import validate_grounding
from app.rag.enrichment.temporal import mentioned_year_bounds

from .config import IndexingConfig
from .models import PreparedIndexDocument
from .source_context import project_source_contexts, source_payload_provenance


POINT_NAMESPACE = uuid.UUID("725862e5-c12a-52e4-82d7-3abf42188867")


class IndexDocumentProjector:
    def __init__(self, config: IndexingConfig) -> None:
        self.config = config

    def project(
        self,
        chunk: KnowledgeEnrichedChildChunk,
        parent: KnowledgeEnrichedParentChunk,
        *,
        source_release_id: str,
    ) -> PreparedIndexDocument:
        if parent.chunk_id != chunk.parent_id:
            raise ValueError(f"parent_id_mismatch:{chunk.chunk_id}")
        if parent.doc_id != chunk.doc_id:
            raise ValueError(f"parent_document_mismatch:{chunk.chunk_id}")
        for item in chunk.knowledge.claims:
            validate_grounding(item, chunk, parent)
        dense_text = chunk.retrieval_content.strip()
        return self._prepare(chunk, parent, dense_text, source_release_id)

    def project_record(
        self,
        parent: KnowledgeEnrichedParentChunk,
        *,
        source_release_id: str,
    ) -> PreparedIndexDocument:
        # Child absence is checked against the complete corpus by the service.
        if parent.chunk_type is not ChunkType.STRUCTURED_RECORD:
            raise ValueError(f"non_record_parent_index_document:{parent.chunk_id}")
        for item in parent.knowledge.claims:
            validate_grounding(item, parent, parent)
        # The atomic Parent already contains the rendered heading and full JSON.
        return self._prepare(parent, parent, parent.content.strip(), source_release_id)

    def _prepare(
        self,
        chunk: KnowledgeEnrichedChildChunk | KnowledgeEnrichedParentChunk,
        parent: KnowledgeEnrichedParentChunk,
        dense_text: str,
        source_release_id: str,
    ) -> PreparedIndexDocument:
        lexical_terms = self._lexical_terms(chunk)
        bm25_parts = [dense_text]
        if lexical_terms:
            bm25_parts.append("检索词：" + "；".join(lexical_terms))
        bm25_text = "\n\n".join(bm25_parts)
        point_id = str(uuid.uuid5(POINT_NAMESPACE, chunk.chunk_id))
        payload = self._payload(chunk, parent, point_id, source_release_id, dense_text)
        input_hash = self._input_hash(chunk, dense_text, bm25_text, payload)
        payload["index_input_hash"] = input_hash
        return PreparedIndexDocument(
            point_id=point_id,
            chunk_id=chunk.chunk_id,
            parent_id=parent.chunk_id,
            doc_id=chunk.doc_id,
            dense_text=dense_text,
            bm25_text=bm25_text,
            input_hash=input_hash,
            payload=payload,
        )

    def _lexical_terms(
        self, chunk: KnowledgeEnrichedChildChunk | KnowledgeEnrichedParentChunk
    ) -> list[str]:
        candidates: list[str] = []
        candidates.extend(chunk.enrichment.semantic.topics)
        candidates.extend(chunk.enrichment.semantic.keywords)
        for entity in chunk.knowledge.entities.linked_entities:
            candidates.append(entity.canonical_name)
            candidates.extend(entity.aliases)
        seen: set[str] = set()
        result: list[str] = []
        for value in candidates:
            normalized = " ".join(value.split())
            key = normalized.casefold()
            if normalized and key not in seen:
                seen.add(key)
                result.append(normalized)
        return result

    def _payload(
        self,
        chunk: KnowledgeEnrichedChildChunk | KnowledgeEnrichedParentChunk,
        parent: KnowledgeEnrichedParentChunk,
        point_id: str,
        source_release_id: str,
        dense_text: str,
    ) -> dict[str, object]:
        entities = chunk.knowledge.entities.linked_entities
        claims = chunk.knowledge.claims
        linked_entities = [
            {
                "entity_id": entity.entity_id,
                "entity_type": entity.entity_type.value,
                "canonical_name": entity.canonical_name,
                "aliases": sorted(entity.aliases, key=str.casefold),
                "confidence": entity.confidence,
            }
            for entity in sorted(entities, key=lambda item: item.entity_id)
        ]
        supported_claims = [
            self._supported_claim(item)
            for item in sorted(claims, key=lambda value: value.claim.claim_id)
            if item.verification.status is VerificationStatus.SUPPORTED
        ]
        supported_claim_ids = [item["claim_id"] for item in supported_claims]
        verification_statuses = sorted(
            {item.verification.status.value for item in claims}
        )
        temporal = chunk.enrichment.temporal
        year_bounds = mentioned_year_bounds(temporal.raw_mentions)
        source = chunk.enrichment.source
        source_contexts = project_source_contexts(chunk, parent, supported_claims)
        payload: dict[str, object] = {
            **source_payload_provenance(chunk, parent),
            "point_id": point_id,
            "record_type": "child" if chunk.parent_id is not None else "parent",
            "chunk_id": chunk.chunk_id,
            "parent_id": parent.chunk_id,
            "doc_id": chunk.doc_id,
            "chunk_index": chunk.chunk_index,
            "chunk_type": chunk.chunk_type.value,
            "section_path": chunk.section_path,
            "breadcrumb": chunk.breadcrumb,
            "content": chunk.content,
            "retrieval_content": (
                chunk.retrieval_content
                if isinstance(chunk, KnowledgeEnrichedChildChunk)
                else dense_text
            ),
            "token_count": chunk.token_count,
            "parent_context": {
                "chunk_id": parent.chunk_id,
                "chunk_type": parent.chunk_type.value,
                "section_path": list(parent.section_path),
                "breadcrumb": parent.breadcrumb,
                "content": parent.content,
                "token_count": parent.token_count,
            },
            "language": chunk.enrichment.content.language,
            "content_type": chunk.enrichment.content.content_type.value,
            "topics": list(chunk.enrichment.semantic.topics),
            "keywords": list(chunk.enrichment.semantic.keywords),
            "year": temporal.year,
            "date": temporal.date.isoformat() if temporal.date else None,
            "start_year": temporal.start_year,
            "end_year": temporal.end_year,
            "min_mentioned_year": year_bounds[0] if year_bounds else None,
            "max_mentioned_year": year_bounds[1] if year_bounds else None,
            "source_id": source.source_id,
            "source_type": source.source_type,
            "source_name": source.source_name,
            "source_url": source.source_url,
            "source_published_at": source.source_published_at,
            "entity_ids": sorted(entity.entity_id for entity in entities),
            "entity_types": sorted({entity.entity_type.value for entity in entities}),
            "entity_names": sorted({entity.canonical_name for entity in entities}),
            "entities": linked_entities,
            "supported_claim_ids": supported_claim_ids,
            "supported_claims": supported_claims,
            "relation_source_contexts": source_contexts,
            "verification_statuses": verification_statuses,
            "knowledge_status": chunk.knowledge.quality.status.value,
            "knowledge_input_hash": chunk.knowledge.provenance.knowledge_input_hash,
            "source_release_id": source_release_id,
            "indexing_version": self.config.indexing_version,
            "index_schema_version": self.config.schema_version,
            "projection_version": self.config.projection_version,
            "dense_model_revision": self.config.dense.model_revision,
        }
        if source_contexts:
            payload["source_title"] = source_contexts[0]["title"]
        if chunk.chunk_type is ChunkType.STRUCTURED_RECORD:
            payload["record_id"] = chunk.metadata.get("record_id")
        return {key: value for key, value in payload.items() if value is not None}

    @staticmethod
    def _supported_claim(item: VerifiedClaim) -> dict[str, object]:
        claim = item.claim
        payload: dict[str, object] = {
            "claim_id": claim.claim_id,
            "statement": claim.statement,
            "subject_entity_id": claim.subject_entity_id,
            "predicate": claim.predicate.value,
            "object_entity_id": claim.object_entity_id,
            "object_value": claim.object_value,
            "time": claim.time.model_dump(mode="json") if claim.time else None,
            "source_chunk_id": claim.source_chunk_id,
            "evidence_span": item.verification.evidence_span or claim.evidence_span,
            "extraction_confidence": claim.confidence,
            "verification_confidence": item.verification.confidence,
            "extraction_source": claim.extraction_source.value,
        }
        if item.grounding is not None:
            payload["grounding"] = item.grounding.model_dump(mode="json")
        return {key: value for key, value in payload.items() if value is not None}

    def _input_hash(
        self,
        chunk: KnowledgeEnrichedChildChunk | KnowledgeEnrichedParentChunk,
        dense_text: str,
        bm25_text: str,
        payload: dict[str, object],
    ) -> str:
        material = {
            "chunk_id": chunk.chunk_id,
            "dense_text": dense_text,
            "bm25_text": bm25_text,
            "payload": payload,
            "indexing_version": self.config.indexing_version,
            "schema_version": self.config.schema_version,
            "projection_version": self.config.projection_version,
            "dense": self.config.dense.fingerprint_payload(),
            "bm25": self.config.bm25.fingerprint_payload(),
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
