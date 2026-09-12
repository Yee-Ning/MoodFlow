"""Content-addressed stable Chunk V1 identifiers."""

from __future__ import annotations

import hashlib
import json


def stable_chunk_id(
    *,
    level: str,
    doc_id: str,
    section_path: tuple[str, ...],
    parent_index: int,
    child_index: int | None,
    content: str,
) -> str:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    payload = json.dumps(
        {
            "level": level,
            "doc_id": doc_id,
            "section_path": section_path,
            "parent_index": parent_index,
            "child_index": child_index,
            "content_hash": content_hash,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]
    return f"{level}-{digest}"
