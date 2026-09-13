"""Validation of projected documents and generated dense vectors."""

from __future__ import annotations

import math
import uuid

from .config import IndexingConfig
from .models import IndexPoint, PreparedIndexDocument


class IndexingValidator:
    def __init__(self, config: IndexingConfig) -> None:
        self.config = config

    def validate_document(self, document: PreparedIndexDocument) -> None:
        parsed = uuid.UUID(document.point_id)
        if parsed.version != 5:
            raise ValueError(f"point_id_not_uuid5:{document.chunk_id}")
        if document.payload.get("record_type") != "child":
            raise ValueError(f"non_child_index_document:{document.chunk_id}")
        if document.payload.get("chunk_id") != document.chunk_id:
            raise ValueError(f"payload_chunk_id_mismatch:{document.chunk_id}")
        if document.payload.get("parent_id") != document.parent_id:
            raise ValueError(f"payload_parent_id_mismatch:{document.chunk_id}")
        parent_context = document.payload.get("parent_context")
        if not isinstance(parent_context, dict):
            raise ValueError(f"payload_parent_context_missing:{document.chunk_id}")
        if parent_context.get("chunk_id") != document.parent_id:
            raise ValueError(f"payload_parent_context_mismatch:{document.chunk_id}")
        if not str(parent_context.get("content", "")).strip():
            raise ValueError(f"payload_parent_content_empty:{document.chunk_id}")
        release_id = document.payload.get("source_release_id")
        if not isinstance(release_id, str) or not release_id:
            raise ValueError(f"payload_source_release_missing:{document.chunk_id}")
        supported_claim_ids = document.payload.get("supported_claim_ids")
        supported_claims = document.payload.get("supported_claims")
        if not isinstance(supported_claim_ids, list) or not isinstance(
            supported_claims, list
        ):
            raise ValueError(f"payload_supported_claims_invalid:{document.chunk_id}")
        projected_claim_ids = [
            item.get("claim_id") for item in supported_claims if isinstance(item, dict)
        ]
        if projected_claim_ids != supported_claim_ids:
            raise ValueError(f"payload_supported_claim_ids_mismatch:{document.chunk_id}")
        if document.payload.get("index_input_hash") != document.input_hash:
            raise ValueError(f"payload_input_hash_mismatch:{document.chunk_id}")

    def validate_point(self, point: IndexPoint) -> None:
        self.validate_document(point.document)
        vector = point.dense_vector
        if len(vector) != self.config.dense.dimension:
            raise ValueError(
                f"dense_dimension_invalid:{point.document.chunk_id}:"
                f"expected={self.config.dense.dimension}:actual={len(vector)}"
            )
        if not all(math.isfinite(value) for value in vector):
            raise ValueError(f"dense_vector_non_finite:{point.document.chunk_id}")
        if self.config.dense.normalize:
            norm = math.sqrt(sum(value * value for value in vector))
            if not math.isclose(norm, 1.0, rel_tol=1e-3, abs_tol=1e-3):
                raise ValueError(
                    f"dense_vector_not_normalized:{point.document.chunk_id}:{norm}"
                )
