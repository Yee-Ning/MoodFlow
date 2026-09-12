"""Stable identities for extracted claims."""

from __future__ import annotations

import hashlib
import json

from .models import ClaimCandidate


def build_claim_id(candidate: ClaimCandidate, source_chunk_id: str) -> str:
    identity = {
        "source_chunk_id": source_chunk_id,
        "statement": candidate.statement,
        "subject_entity_id": candidate.subject_entity_id,
        "predicate": candidate.predicate.value,
        "object_entity_id": candidate.object_entity_id,
        "object_value": candidate.object_value,
        "time": candidate.time.model_dump(mode="json") if candidate.time else None,
        "evidence_span": candidate.evidence_span,
    }
    encoded = json.dumps(
        identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"claim:{hashlib.sha256(encoded).hexdigest()[:24]}"
