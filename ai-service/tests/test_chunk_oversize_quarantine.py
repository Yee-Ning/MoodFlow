import io
import json
from contextlib import redirect_stdout
from hashlib import sha256

import pytest

import run_chunk
from app.rag.chunk import ChunkConfig, ChunkEngine, ChunkPipelineService, ChunkType
from app.rag.chunk.models import ChunkedDocument
from app.rag.chunk.service import ChunkValidationError
from app.rag.chunk.validator import ChunkValidator
from app.rag.clean.io import atomic_write_json, atomic_write_jsonl
from app.rag.enrichment.corpus import EnrichmentCorpusReader
from app.rag.parse_normalize.models import BlockType, DocumentBlock
from app.rag.parse_normalize.parsers.json import JsonParser
from tests.test_parse_normalize_json import _parse_input
from tests.test_rag_chunk import _document


REASON = "chunk_structured_record_too_long"


def record_pair(source="oversize", length=2000, *, mixed=False, upstream_review=False):
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "Entity", "body": "汉" * length}, source))
    blocks = list(parsed.blocks)
    if mixed:
        blocks.append(DocumentBlock(block_id=source + "-text", block_type=BlockType.PARAGRAPH,
                                    text="独立正文保持完整。" * 20, order_index=len(blocks)))
    cleaned, normalized = _document(blocks, source_id=source)
    if upstream_review:
        cleaned = cleaned.model_copy(update={"needs_review": True})
    return cleaned, normalized


def normal_pair(source):
    return _document([DocumentBlock(block_id=source + "-text", block_type=BlockType.PARAGRAPH,
                                    text="普通文档完整句子。" * 20, order_index=0)], source_id=source)


def write_corpus(root, pairs):
    cleaned_root = root / "data/cleaned/v0.2"
    rows = []
    for cleaned, normalized in pairs:
        atomic_write_json(root / cleaned.normalized_path, normalized.model_dump(mode="json"))
        path = cleaned_root / cleaned.source_id / "document.json"
        atomic_write_json(path, cleaned.model_dump(mode="json"))
        rows.append({"document_id": cleaned.document_id, "source_id": cleaned.source_id,
                     "cleaned_path": path.relative_to(root).as_posix(),
                     "cleaned_sha256": "sha256:" + sha256(path.read_bytes()).hexdigest()})
    atomic_write_jsonl(cleaned_root / "cleaned_corpus_manifest.jsonl", rows)
    return cleaned_root


def manifest_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("normal_count", [0, 1, 3])
def test_mixed_batch_quarantines_whole_oversize_document_and_writes_complete_normal_manifest(tmp_path, normal_count):
    oversized = record_pair(mixed=True)
    normal = [normal_pair(f"normal-{i}") for i in range(normal_count)]
    cleaned_root = write_corpus(tmp_path, [oversized, *normal])
    service = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned_root)
    outcome = service.run()
    rows = manifest_rows(outcome.manifest_path)
    assert {row["source_id"] for row in rows} == {pair[0].source_id for pair in normal}
    assert outcome.summary["document_count"] == normal_count
    assert outcome.summary["input_document_count"] == normal_count + 1
    assert outcome.summary["quarantined_document_count"] == 1
    assert outcome.summary["manual_review_document_count"] == 1
    review = outcome.summary["manual_review_documents"][0]
    assert review["source_id"] == "oversize"
    assert review["reasons"] == [REASON]
    assert review["disposition"] == "quarantined"
    assert review["max_parent_tokens"] == 1500
    assert review["validation_error_codes"] == ["chunk_too_long"]
    assert not (service.output_root / "oversize/chunks.json").exists()
    for row in rows:
        data = (tmp_path / row["chunks_path"]).read_bytes()
        assert row["chunks_sha256"] == "sha256:" + sha256(data).hexdigest()
        actual = ChunkedDocument.model_validate_json(data)
        expected = next(ChunkEngine().chunk(*pair) for pair in normal if pair[0].source_id == row["source_id"])
        assert actual == expected
        assert row["valid"] and "disposition" not in row
    accepted = EnrichmentCorpusReader(tmp_path, chunked_root=service.output_root).load_all()
    assert len(accepted) == normal_count
    persisted = json.loads((service.output_root / "chunk_summary.json").read_text(encoding="utf-8"))
    assert persisted["manual_review_documents"] == outcome.summary["manual_review_documents"]


def test_cli_override_preserves_error_review_marker_and_full_document_then_default_reisolates(tmp_path):
    pair = record_pair(mixed=True)
    cleaned_root = write_corpus(tmp_path, [pair, normal_pair("normal")])
    before = {path: sha256(path.read_bytes()).hexdigest() for path in
              [tmp_path / pair[0].normalized_path, cleaned_root / "oversize/document.json"]}

    def invoke(args):
        stream = io.StringIO()
        with redirect_stdout(stream):
            assert run_chunk.main(["--cleaned-root", str(cleaned_root), *args], project_root=tmp_path) == 0
        return json.loads(stream.getvalue())

    assert invoke([])["document_count"] == 1
    included = invoke(["--include-manual-review"])
    assert included["document_count"] == 2 and included["quarantined_document_count"] == 0
    assert included["input_document_count"] == 2
    assert included["manual_review_documents"][0]["validation_error_codes"] == ["chunk_too_long"]
    row = next(row for row in manifest_rows(tmp_path / included["manifest"]) if row["source_id"] == "oversize")
    assert row["disposition"] == "included_by_flag" and row["needs_review"]
    assert row["review_reasons"] == [REASON] and not row["valid"]
    path = tmp_path / row["chunks_path"]
    assert row["chunks_sha256"] == "sha256:" + sha256(path.read_bytes()).hexdigest()
    document = ChunkedDocument.model_validate_json(path.read_bytes())
    original = ChunkEngine().chunk(*pair)
    assert not document.validation.valid
    assert document.validation == original.validation
    assert {issue.code for issue in document.validation.issues} == {"chunk_too_long"}
    assert [chunk.content for chunk in document.parents] == [chunk.content for chunk in original.parents]
    assert [chunk.chunk_id for chunk in document.parents] == [chunk.chunk_id for chunk in original.parents]
    record = next(p for p in document.parents if p.chunk_type is ChunkType.STRUCTURED_RECORD)
    assert record.token_count > 1500 and not any(c.parent_id == record.chunk_id for c in document.children)
    for chunk in [*document.parents, *document.children]:
        assert chunk.metadata["chunk_review"] == {"needs_review": True, "reasons": [REASON], "disposition": "included_by_flag"}
    assert invoke([])["quarantined_document_count"] == 1
    assert {row["source_id"] for row in manifest_rows(tmp_path / included["manifest"])} == {"normal"}
    assert path.exists()  # Retain old files; the current manifest controls membership.
    assert all(sha256(path.read_bytes()).hexdigest() == digest for path, digest in before.items())


@pytest.mark.parametrize("include", [False, True])
def test_validate_only_reports_review_without_writes_and_upstream_reasons_do_not_double_count(tmp_path, include):
    cleaned_root = write_corpus(tmp_path, [record_pair(upstream_review=True), normal_pair("normal")])
    service = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned_root)
    first = service.run(validate_only=True, include_manual_review=include)
    second = service.run(validate_only=True, include_manual_review=include)
    assert first.summary == second.summary
    assert first.manifest_path is None and not service.output_root.exists()
    assert first.summary["input_document_count"] == 2
    assert first.summary["manual_review_document_count"] == 1
    review = first.summary["manual_review_documents"][0]
    assert review["reasons"] == (["clean_needs_review", REASON] if include else ["clean_needs_review"])
    assert review["disposition"] == ("included_by_flag" if include else "quarantined")
    assert first.summary["document_count"] == (2 if include else 1)


@pytest.mark.parametrize("excess", [0, 1])
def test_exact_parent_limit_passes_and_one_token_over_is_quarantined(tmp_path, excess):
    empty = record_pair("edge", 0)
    overhead = ChunkEngine().chunk(*empty).parents[0].token_count
    pair = record_pair("edge", 1500 - overhead + excess)
    assert ChunkEngine().chunk(*pair).parents[0].token_count == 1500 + excess
    cleaned_root = write_corpus(tmp_path, [pair])
    result = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned_root).run()
    assert result.summary["document_count"] == (0 if excess else 1)
    assert result.summary["quarantined_document_count"] == excess


@pytest.mark.parametrize("failure", ["ordinary_long", "record_with_other_error"])
@pytest.mark.parametrize("include", [False, True])
def test_other_validation_errors_still_fail_batch_even_with_override(tmp_path, monkeypatch, failure, include):
    pair = normal_pair("bad") if failure == "ordinary_long" else record_pair("bad")
    document = ChunkEngine().chunk(*pair)
    parents = list(document.parents)
    update = {"token_count": 1501} if failure == "ordinary_long" else {"breadcrumb": "corrupted"}
    parents[0] = parents[0].model_copy(update=update)
    report = ChunkValidator(ChunkConfig()).validate(pair[0], parents, document.children, [],
                                                  {b.block_id: b for b in pair[1].blocks})
    assert not report.valid
    bad = document.model_copy(update={"parents": parents, "validation": report})
    cleaned_root = write_corpus(tmp_path, [pair, record_pair("oversize"), normal_pair("normal")])
    service = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=cleaned_root)
    real_chunk = service.engine.chunk
    monkeypatch.setattr(service.engine, "chunk", lambda cleaned, normalized:
                        bad if cleaned.source_id == "bad" else real_chunk(cleaned, normalized))
    with pytest.raises(ChunkValidationError, match="chunk_validation_failed"):
        service.run(include_manual_review=include)
    assert not service.output_root.exists()
