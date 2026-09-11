"""Hermetic multi-artist normalized corpus fixtures for Clean tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from app.rag.parse_normalize.models import (
    BlockType,
    DetectedFileType,
    DocumentBlock,
    NormalizedDocument,
)


def normalized_document(
    source_id: str,
    text: str,
    *,
    title: str,
    publisher: str | None = "Example Music",
    source_type: str | None = "media_article",
    document_type: str | None = None,
) -> NormalizedDocument:
    source_metadata: dict[str, object] = {
        "original_url": f"https://example.test/{source_id}",
    }
    if publisher is not None:
        source_metadata["publisher"] = publisher
    if source_type is not None:
        source_metadata["source_type"] = source_type
    if document_type is not None:
        source_metadata["document_type"] = document_type
    blocks = [
        DocumentBlock(
            block_id=f"{source_id}-b{index:06d}",
            block_type=BlockType.PARAGRAPH,
            text=value,
            order_index=index,
        )
        for index, value in enumerate(text.strip().split("\n\n"))
        if value
    ]
    return NormalizedDocument(
        document_id=f"doc-{source_id}",
        source_id=source_id,
        title=title,
        language="zh" if any("\u4e00" <= char <= "\u9fff" for char in text) else "en",
        original_file_type=DetectedFileType.HTML,
        raw_path=f"data/raw/html/{source_id}.html",
        parser_name="html",
        parser_version="1.1.0",
        normalization_version="1.0.0",
        raw_content_hash="sha256:" + hashlib.sha256(text.encode()).hexdigest(),
        markdown=text,
        blocks=blocks,
        metadata={
            "source_metadata": source_metadata,
            "parser_metadata": {},
            "parse_fingerprint": "html:1.1.0:auto",
        },
        parse_quality_score=1.0,
        corpus_version="multi-artist-test-v1",
    )


def write_normalized_corpus(
    root: Path, documents: list[NormalizedDocument]
) -> Path:
    normalized_root = root / "data" / "normalized"
    normalized_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for document in documents:
        output = normalized_root / document.source_id
        output.mkdir(parents=True, exist_ok=True)
        path = output / "document.json"
        path.write_text(
            document.model_dump_json(indent=2), encoding="utf-8", newline="\n"
        )
        rows.append(
            {
                "source_id": document.source_id,
                "document_id": document.document_id,
                "normalized_path": path.relative_to(root).as_posix(),
            }
        )
    manifest = normalized_root / "normalized_corpus_manifest.jsonl"
    manifest.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
        newline="\n",
    )
    return manifest
