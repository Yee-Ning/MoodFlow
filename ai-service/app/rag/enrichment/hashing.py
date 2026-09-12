"""Stable, version-aware hashes for enrichment idempotency."""

from __future__ import annotations

import hashlib
import json

from .models import BaseChunk, PrimarySubject


def enrichment_input_hash(
    chunk: BaseChunk,
    *,
    title: str | None,
    inherited_metadata: dict[str, object],
    parent_primary_subject: PrimarySubject | None,
    enrichment_version: str,
) -> str:
    payload = {
        "content": chunk.content,
        "title": title,
        "section_path": chunk.section_path,
        "inherited_metadata": inherited_metadata,
        "parent_primary_subject": (
            parent_primary_subject.model_dump(mode="json")
            if parent_primary_subject is not None
            else None
        ),
        "enrichment_version": enrichment_version,
    }
    serialized = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()
