"""Deterministic KnowledgeEnrichedChildChunk to index document projection."""

from __future__ import annotations

import hashlib
import json
import uuid

from app.rag.knowledge.models import KnowledgeEnrichedChildChunk
from app.rag.knowledge.verification.models import VerificationStatus

from .config import IndexingConfig
from .models import PreparedIndexDocument


POINT_NAMESPACE = uuid.UUID("725862e5-c12a-52e4-82d7-3abf42188867")


class IndexDocumentProjector:
    def __init__(self, config: IndexingConfig) -> None:
        self.config = config

    def project(self, chunk: KnowledgeEnrichedChildChunk) -> PreparedIndexDocument:
        dense_text = chunk.retrieval_content.strip()
        lexical_terms = self._lexical_terms(chunk)
        bm25_parts = [dense_text]
        if lexical_terms:
            bm25_parts.append("检索词：" + "；".join(lexical_terms))
        bm25_text = "\n\n".join(bm25_parts)
        point_id = str(uuid.uuid5(POINT_NAMESPACE, chunk.chunk_id))
        payload = self._payload(chunk, point_id)
        input_hash = self._input_hash(chunk, dense_text, bm25_text, payload)
        payload["index_input_hash"] = input_hash
        return PreparedIndexDocument(
            point_id=point_id,
            chunk_id=chunk.chunk_id,
            parent_id=chunk.parent_id,
            doc_id=chunk.doc_id,
            dense_text=dense_text,
            bm25_text=bm25_text,
            input_hash=input_hash,
            payload=payload,
        )

    def _lexical_terms(self, chunk: KnowledgeEnrichedChildChunk) -> list[str]:
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
        self, chunk: KnowledgeEnrichedChildChunk, point_id: str
    ) -> dict[str, object]:
        entities = chunk.knowledge.entities.linked_entities
        claims = chunk.knowledge.claims
        supported_claim_ids = sorted(
            item.claim.claim_id
            for item in claims
            if item.verification.status is VerificationStatus.SUPPORTED
        )
        verification_statuses = sorted(
            {item.verification.status.value for item in claims}
        )
        temporal = chunk.enrichment.temporal
        source = chunk.enrichment.source
        payload: dict[str, object] = {
            "point_id": point_id,
            "record_type": "child",
            "chunk_id": chunk.chunk_id,
            "parent_id": chunk.parent_id,
            "doc_id": chunk.doc_id,
            "chunk_index": chunk.chunk_index,
            "chunk_type": chunk.chunk_type.value,
            "section_path": chunk.section_path,
            "breadcrumb": chunk.breadcrumb,
            "content": chunk.content,
            "retrieval_content": chunk.retrieval_content,
            "token_count": chunk.token_count,
            "language": chunk.enrichment.content.language,
            "content_type": chunk.enrichment.content.content_type.value,
            "topics": list(chunk.enrichment.semantic.topics),
            "keywords": list(chunk.enrichment.semantic.keywords),
            "year": temporal.year,
            "date": temporal.date.isoformat() if temporal.date else None,
            "start_year": temporal.start_year,
            "end_year": temporal.end_year,
            "source_id": source.source_id,
            "source_type": source.source_type,
            "source_name": source.source_name,
            "source_url": source.source_url,
            "source_published_at": source.source_published_at,
            "entity_ids": sorted(entity.entity_id for entity in entities),
            "entity_types": sorted({entity.entity_type.value for entity in entities}),
            "entity_names": sorted({entity.canonical_name for entity in entities}),
            "supported_claim_ids": supported_claim_ids,
            "verification_statuses": verification_statuses,
            "knowledge_status": chunk.knowledge.quality.status.value,
            "knowledge_input_hash": chunk.knowledge.provenance.knowledge_input_hash,
            "indexing_version": self.config.indexing_version,
            "index_schema_version": self.config.schema_version,
            "projection_version": self.config.projection_version,
            "dense_model_revision": self.config.dense.model_revision,
        }
        return {key: value for key, value in payload.items() if value is not None}

    def _input_hash(
        self,
        chunk: KnowledgeEnrichedChildChunk,
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
            "dense": self.config.dense.model_dump(mode="json"),
            "bm25": self.config.bm25.model_dump(mode="json"),
        }
        encoded = json.dumps(
            material, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
        return "sha256:" + hashlib.sha256(encoded).hexdigest()
