"""Reject mixed generations after interrupted Clean/Chunk publication."""

import json
from unittest.mock import patch

import pytest

from app.rag.chunk.config import ChunkConfig
from app.rag.chunk.corpus import ChunkCorpusError, ChunkCorpusReader
from app.rag.chunk.service import ChunkPipelineService
from app.rag.clean.config import load_clean_config
from app.rag.clean.service import CleanPipelineService
from app.rag.enrichment.corpus import EnrichmentCorpusError, EnrichmentCorpusReader
from tests.clean_helpers import normalized_document, write_normalized_corpus


def clean_service(root):
    document = normalized_document(
        "audit-source", "第一段正文事实。\n", title="离线夹具"
    )
    write_normalized_corpus(root, [document])
    config, checksum = load_clean_config()
    return CleanPipelineService(root, config, registry_checksum=checksum)


def test_clean_interruption_is_rejected_by_chunk_reader(tmp_path):
    service = clean_service(tmp_path)
    outcome = service.run(dry_run=False, include_manual_review=True)
    cleaned_root = outcome.cleaned_manifest_path.parent
    manifest_before = outcome.cleaned_manifest_path.read_bytes()
    context = service.reader.load_all()[0]
    modified = context.document.model_copy(update={"markdown": "新的正文事实。\n"})
    write_normalized_corpus(tmp_path, [modified])
    with patch(
        "app.rag.clean.writer.atomic_write_jsonl",
        side_effect=OSError("interrupted before manifest"),
    ):
        with pytest.raises(OSError, match="interrupted"):
            service.run(dry_run=False, include_manual_review=True)
    assert outcome.cleaned_manifest_path.read_bytes() == manifest_before
    with pytest.raises(ChunkCorpusError, match="manifest_hash_mismatch"):
        ChunkCorpusReader(tmp_path, cleaned_root=cleaned_root).load_all(include_manual_review=True)
    service.run(dry_run=False, include_manual_review=True)
    assert len(ChunkCorpusReader(tmp_path, cleaned_root=cleaned_root).load_all(include_manual_review=True)) == 1


def test_chunk_interruption_with_same_chunk_ids_is_rejected_by_enrichment_reader(
    tmp_path,
):
    cleaned = clean_service(tmp_path).run(dry_run=False, include_manual_review=True).cleaned_manifest_path.parent
    chunker = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned)
    first = chunker.run(include_manual_review=True)
    before = first.manifest_path.read_bytes()
    changed = ChunkPipelineService(
        tmp_path, ChunkConfig(min_child_tokens=79), cleaned_root=cleaned
    )
    with patch(
        "app.rag.chunk.writer.atomic_write_jsonl",
        side_effect=OSError("interrupted before manifest"),
    ):
        with pytest.raises(OSError, match="interrupted"):
            changed.run(include_manual_review=True)
    assert first.manifest_path.read_bytes() == before
    with pytest.raises(EnrichmentCorpusError, match="manifest_hash_mismatch"):
        EnrichmentCorpusReader(
            tmp_path, chunked_root=first.manifest_path.parent
        ).load_all()
    final = changed.run(include_manual_review=True)
    rows = EnrichmentCorpusReader(
        tmp_path, chunked_root=final.manifest_path.parent
    ).load_all()
    assert rows[0].children[0].chunk_id == first.documents[0].children[0].chunk_id


@pytest.mark.parametrize("stage", ["clean", "chunk"])
def test_missing_hash_and_manifest_identity_mismatch_fail_closed(tmp_path, stage):
    cleaned = clean_service(tmp_path).run(dry_run=False, include_manual_review=True).cleaned_manifest_path
    if stage == "clean":
        manifest = cleaned
        hash_field = "cleaned_sha256"
        reader = ChunkCorpusReader(tmp_path, cleaned_root=manifest.parent)
        error_type = ChunkCorpusError
    else:
        manifest = (
            ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned.parent)
            .run(include_manual_review=True)
            .manifest_path
        )
        hash_field = "chunks_sha256"
        reader = EnrichmentCorpusReader(tmp_path, chunked_root=manifest.parent)
        error_type = EnrichmentCorpusError
    # These tests exercise artifact integrity even for pending-review fixtures.
    def load():
        return reader.load_all(include_manual_review=True) if isinstance(reader, ChunkCorpusReader) else reader.load_all()
    assert len(load()) == 1
    row = json.loads(manifest.read_text(encoding="utf-8"))
    bad = {**row, "document_id": "unrelated-document"}
    manifest.write_text(json.dumps(bad) + "\n", encoding="utf-8")
    with pytest.raises(error_type, match="identity_mismatch"):
        load()
    row.pop(hash_field)
    manifest.write_text(json.dumps(row) + "\n", encoding="utf-8")
    with pytest.raises(error_type, match="hash_mismatch_or_missing"):
        load()
