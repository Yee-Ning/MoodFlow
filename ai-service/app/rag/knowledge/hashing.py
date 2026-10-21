"""Stable version-aware hashing for knowledge-stage idempotency."""

from __future__ import annotations

import hashlib
import json

from app.rag.enrichment.models import EnrichedChunk

from .config import KnowledgeConfig


def knowledge_input_hash(chunk: EnrichedChunk, config: KnowledgeConfig) -> str:
    payload = {
        "chunk": chunk.model_dump(mode="json", exclude={"knowledge"}),
        "entity_linking_version": config.entity_linking_version,
        "claim_extraction_version": config.claim_extraction_version,
        "claim_verification_version": config.claim_verification_version,
        "registry_version": config.registry_version,
        "prompt_version": config.prompt_version,
        "llm_enabled": config.llm_enabled,
    }
    serialized = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
