import json
from types import SimpleNamespace

from app.rag.chunk import ChunkConfig, ChunkEngine, ChunkPipelineService, ChunkType
from app.rag.chunk.narrative import NarrativeChunker
from app.rag.chunk.stats import summarize_chunk_quality
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter
from app.rag.parse_normalize.models import BlockType, DocumentBlock
from app.rag.parse_normalize.parsers.json import JsonParser
from tests.test_parse_normalize_json import _parse_input
from tests.test_rag_chunk import _document, _small_config


def base_document():
    cleaned, normalized = _document([DocumentBlock(
        block_id="text", block_type=BlockType.PARAGRAPH, text="完整句子。" * 30, order_index=0,
    )])
    return cleaned, normalized, ChunkEngine().chunk(cleaned, normalized)


def test_batch_quantiles_cut_merge_and_type_ratios_use_explicit_denominators():
    _, _, base = base_document()
    parent = base.parents[0]
    p1 = parent.model_copy(update={"chunk_id": "p1", "token_count": 100,
        "metadata": {"small_merge": {"input_child_count": 3, "small_candidate_count": 1, "merge_operation_count": 1}}})
    p2 = parent.model_copy(update={"chunk_id": "p2", "token_count": 200, "chunk_type": ChunkType.TABLE})
    p3 = parent.model_copy(update={"chunk_id": "p3", "token_count": 300, "chunk_type": ChunkType.STRUCTURED_RECORD})
    child = base.children[0]
    children = [child.model_copy(update={"chunk_id": f"c{i}", "parent_id": "p1", "chunk_index": i,
                                        "token_count": count, "content": text})
                for i, (count, text) in enumerate([(10, "半句话"), (20, "完整句。”"), (30, "末尾")])]
    children.append(child.model_copy(update={"chunk_id": "t", "parent_id": "p2", "token_count": 40, "chunk_type": ChunkType.TABLE}))
    first = base.model_copy(update={"parents": [p1, p2, p3], "children": children})
    second = base.model_copy(update={
        "parents": [parent.model_copy(update={"chunk_id": "p4", "token_count": 1000,
            "metadata": {"small_merge": {"input_child_count": 1, "small_candidate_count": 0, "merge_operation_count": 0}}})],
        "children": [child.model_copy(update={"parent_id": "p4", "token_count": 10})],
    })
    quality = summarize_chunk_quality([first, second])
    assert quality["length_tokens"]["all"] == {"count": 9, "p50": 40, "p99": 1000}
    assert quality["length_tokens"]["parent"] == {"count": 4, "p50": 200, "p99": 1000}
    assert quality["length_tokens"]["child"] == {"count": 5, "p50": 20, "p99": 40}
    assert quality["sentence_cut"]["boundary_count"] == 2
    assert quality["sentence_cut"]["mid_sentence_cut_count"] == 1
    assert quality["sentence_cut"]["rate"] == .5
    assert quality["small_merge"]["trigger_rate"] == .5
    assert quality["small_merge"]["input_child_count"] == 4
    assert quality["small_merge"]["merge_operation_count"] == 1
    assert quality["chunk_type_counts"] == {"narrative": 6, "table": 2, "structured_record": 1}
    assert quality["table_chunk_ratio"] == round(2 / 9, 6)
    assert quality["structured_record_ratio"] == round(1 / 9, 6)


def test_empty_metrics_are_defined_and_legacy_merge_history_is_not_fabricated():
    empty = summarize_chunk_quality([])
    assert empty["length_tokens"]["all"] == {"count": 0, "p50": 0, "p99": 0}
    assert empty["sentence_cut"]["rate"] == empty["small_merge"]["trigger_rate"] == 0
    assert empty["table_chunk_ratio"] == empty["structured_record_ratio"] == 0
    _, _, base = base_document()
    legacy = base.model_copy(update={"parents": [base.parents[0].model_copy(update={"metadata": {}})]})
    metric = summarize_chunk_quality([legacy])["small_merge"]
    assert metric["observed_parent_count"] == 0
    assert metric["unobserved_parent_count"] == 1


def test_merge_instrumentation_counts_actual_operations_without_changing_output():
    chunker = NarrativeChunker(_small_config(), UnicodeHeuristicTokenCounter())
    values = ["一二三四五", "短", "六七八九十"]
    before = chunker._merge_small(values)
    metrics = {}
    after = chunker._merge_small(values, metrics=metrics)
    assert before == after == ["一二三四五\n\n短", "六七八九十"]
    assert metrics == {"input_child_count": 3, "small_candidate_count": 1, "merge_operation_count": 1}
    _, _, document = base_document()
    assert "small_merge" in document.parents[0].metadata


def test_summary_persists_quality_and_validate_only_does_not_write(tmp_path, monkeypatch):
    cleaned, normalized, _ = base_document()
    service = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=tmp_path / "cleaned")
    monkeypatch.setattr(service.reader, "load_all", lambda **kwargs: [SimpleNamespace(cleaned=cleaned, normalized=normalized)])
    preview = service.run(validate_only=True)
    assert not service.output_root.exists()
    written = service.run()
    persisted = json.loads((service.output_root / "chunk_summary.json").read_text(encoding="utf-8"))
    assert preview.summary["quality"] == written.summary["quality"] == persisted["quality"]
    assert persisted["quality"]["length_tokens"]["all"]["count"] == persisted["parent_count"] + persisted["child_count"]
    assert written.documents[0].parents == preview.documents[0].parents
    assert written.documents[0].children == preview.documents[0].children


def test_oversized_record_is_quarantined_without_truncation(tmp_path, monkeypatch):
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "Entity", "body": "汉" * 2000}))
    cleaned, normalized = _document(parsed.blocks)
    service = ChunkPipelineService(tmp_path, ChunkConfig(), cleaned_root=tmp_path / "cleaned")
    monkeypatch.setattr(service.reader, "load_all", lambda **kwargs: [SimpleNamespace(cleaned=cleaned, normalized=normalized)])
    quarantined = service.run()
    assert quarantined.documents == []
    assert quarantined.summary["quarantined_document_count"] == 1
    assert quarantined.manifest_path.read_text(encoding="utf-8") == ""
    included = service.run(include_manual_review=True)
    assert len(included.documents[0].parents) == 1
    assert included.documents[0].children == []
    assert parsed.blocks[1].text in included.documents[0].parents[0].content
    assert not included.documents[0].validation.valid
    assert {i.code for i in included.documents[0].validation.issues} == {"chunk_too_long"}
    assert included.summary["manual_review_documents"][0]["disposition"] == "included_by_flag"


def test_parent_only_record_is_visible_in_statistics():
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "Entity"}))
    cleaned, normalized = _document(parsed.blocks)
    document = ChunkEngine().chunk(cleaned, normalized)
    assert document.stats.structured_record_parent_count == 1
    assert document.stats.structured_record_child_count == 0
    assert summarize_chunk_quality([document])["structured_record_ratio"] == 1
