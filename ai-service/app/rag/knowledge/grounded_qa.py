"""QA v2: sample only body-grounded links, retaining exact original quotes."""

from __future__ import annotations

import hashlib
from collections import Counter
from pathlib import Path

from app.rag.clean.io import atomic_write_json

from .entity_linking.grounded import GroundedLinkAudit, boilerplate_reason
from .models import KnowledgeEnrichedChunk
from .qa import KnowledgeQASampler


def validate_entity_sample(row: dict, chunk: KnowledgeEnrichedChunk) -> None:
    if row["chunk_id"] != chunk.chunk_id or row["document_id"] != chunk.doc_id:
        raise ValueError("qa_sample_chunk_identity_mismatch")
    if boilerplate_reason(chunk.content, chunk.section_path):
        raise ValueError("qa_sample_boilerplate")
    surfaces, matches = row.get("matched_surfaces", []), row.get("body_matches", [])
    if not surfaces or not matches or set(surfaces) != {m["quote"] for m in matches}:
        raise ValueError("qa_sample_missing_body_matches")
    for match in matches:
        start, end = match["start"], match["end"]
        if not (0 <= start < end <= len(chunk.content)):
            raise ValueError("qa_sample_body_offset_invalid")
        if (
            match["quote"] != chunk.content[start:end]
            or match["entity_id"] != row["entity_id"]
        ):
            raise ValueError("qa_sample_body_quote_mismatch")
    if any(not surface or surface not in chunk.content for surface in surfaces):
        raise ValueError("qa_sample_surface_absent_from_body")


class GroundedKnowledgeQASampler(KnowledgeQASampler):
    """Keep the established ID/stratification rules; filter before sampling."""

    def __init__(self, audits: dict[str, GroundedLinkAudit], **kwargs):
        super().__init__(**kwargs)
        self.audits = audits
        self.population_count = 0
        self.population_by_type: dict[str, int] = {}
        self.excluded_chunks: dict[str, int] = {}

    def population_rows(self, chunks: list[KnowledgeEnrichedChunk]) -> list[dict]:
        rows = []
        excluded = Counter()
        for chunk in chunks:
            audit = self.audits[chunk.chunk_id]
            if (
                audit.body_sha256
                != hashlib.sha256(chunk.content.encode("utf-8")).hexdigest()
            ):
                raise ValueError("qa_body_evidence_hash_mismatch")
            if audit.chunk_id != chunk.chunk_id:
                raise ValueError("qa_body_evidence_identity_mismatch")
            if audit.excluded_reason:
                excluded[audit.excluded_reason] += 1
                continue
            linked_ids = {e.entity_id for e in chunk.knowledge.entities.linked_entities}
            if linked_ids != {e.entity_id for e in audit.result.linked_entities}:
                raise ValueError("qa_body_evidence_entity_set_mismatch")
            for entity in chunk.knowledge.entities.linked_entities:
                matches = [
                    m for m in audit.body_matches if m["entity_id"] == entity.entity_id
                ]
                surfaces = sorted({m["quote"] for m in matches})
                row = {
                    "sample_id": self._sample_id(
                        "entity-review", chunk.chunk_id, entity.entity_id
                    ),
                    "sample_kind": "entity_link",
                    "document_id": chunk.doc_id,
                    "chunk_id": chunk.chunk_id,
                    "chunk_type": chunk.chunk_type.value,
                    "section_path": chunk.section_path,
                    "entity_id": entity.entity_id,
                    "entity_type": entity.entity_type.value,
                    "canonical_name": entity.canonical_name,
                    "matched_surfaces": surfaces,
                    "body_matches": matches,
                    "body_sha256": audit.body_sha256,
                    "confidence": entity.confidence,
                    "context_excerpt": self._excerpt(chunk.content, surfaces),
                    "review_status": "pending",
                    "review_notes": None,
                }
                validate_entity_sample(row, chunk)
                rows.append(row)
        self.population_count = len(rows)
        self.population_by_type = dict(
            sorted(Counter(row["entity_type"] for row in rows).items())
        )
        self.excluded_chunks = dict(sorted(excluded.items()))
        return sorted(rows, key=lambda row: row["sample_id"])

    def entity_rows(self, chunks: list[KnowledgeEnrichedChunk]) -> list[dict]:
        return self._stratified(
            self.population_rows(chunks), "entity_type", self.entity_sample_size
        )

    def write(self, chunks: list[KnowledgeEnrichedChunk], output_root: Path):
        claim_path, entity_path, manifest_path, manifest = super().write(
            chunks, output_root
        )
        manifest.update(
            {
                "qa_sampling_version": "v2",
                "selection": "body_validated_then_deterministic_stratified_then_content_hash",
                "eligible_entity_link_population": self.population_count,
                "eligible_entity_population_by_type": self.population_by_type,
                "excluded_chunks_by_reason": self.excluded_chunks,
                "entity_sample_shortfall": max(
                    0, self.entity_sample_size - manifest["entity_link_sample_count"]
                ),
            }
        )
        atomic_write_json(manifest_path, manifest)
        return claim_path, entity_path, manifest_path, manifest
