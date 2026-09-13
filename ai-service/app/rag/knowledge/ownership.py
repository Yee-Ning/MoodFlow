"""Corpus-local ownership for duplicate claims emitted by Parent and Child chunks."""

from __future__ import annotations

import json
from collections import defaultdict

from app.rag.chunk.models import ChildChunk

from .models import KnowledgeEnrichedChunk, KnowledgeStats


def deduplicate_claim_ownership(
    chunks: list[KnowledgeEnrichedChunk],
) -> list[KnowledgeEnrichedChunk]:
    grouped: dict[tuple[str, ...], list[tuple[int, int]]] = defaultdict(list)
    for chunk_index, chunk in enumerate(chunks):
        for claim_index, item in enumerate(chunk.knowledge.claims):
            claim = item.claim
            key = (
                chunk.doc_id,
                claim.subject_entity_id,
                claim.predicate.value,
                claim.object_entity_id or "",
                json.dumps(claim.object_value, ensure_ascii=False, sort_keys=True),
                json.dumps(
                    claim.time.model_dump(mode="json") if claim.time else None,
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                claim.evidence_span,
            )
            grouped[key].append((chunk_index, claim_index))

    keep: set[tuple[int, int]] = set()
    for occurrences in grouped.values():
        keep.add(
            min(
                occurrences,
                key=lambda item: (
                    0 if isinstance(chunks[item[0]], ChildChunk) else 1,
                    len(chunks[item[0]].content),
                    chunks[item[0]].chunk_index,
                    chunks[item[0]].chunk_id,
                ),
            )
        )

    result: list[KnowledgeEnrichedChunk] = []
    for chunk_index, chunk in enumerate(chunks):
        claims = [
            item
            for claim_index, item in enumerate(chunk.knowledge.claims)
            if (chunk_index, claim_index) in keep
        ]
        knowledge = chunk.knowledge.model_copy(
            update={
                "claims": claims,
                "stats": KnowledgeStats.build(chunk.knowledge.entities, claims),
            }
        )
        result.append(chunk.model_copy(update={"knowledge": knowledge}))
    return result
