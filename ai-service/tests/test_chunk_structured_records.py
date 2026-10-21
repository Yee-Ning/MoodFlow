import json

import pytest

from app.rag.chunk import ChunkConfig, ChunkEngine, ChunkType
from app.rag.chunk.models import ChildChunk
from app.rag.chunk.routing import ChunkRouter, ChunkRoutingError
from app.rag.chunk.validator import ChunkValidator
from app.rag.parse_normalize.models import BlockType, DocumentBlock, DocumentTable
from app.rag.parse_normalize.parsers.json import JsonParser, JsonParserConfig
from app.rag.parse_normalize.base import ParserError
from tests.test_parse_normalize_json import WIKIDATA_PAYLOAD, _parse_input
from tests.test_rag_chunk import _document


@pytest.mark.parametrize("length", [0, 50, 350, 1000, 2000, 4000])
def test_json_record_is_lossless_and_emits_only_one_parent(length):
    payload = {"id": "album-1", "name": "范特西", "description": "汉" * length,
               "extra": {"unrendered_field": [1, 2, 3]}}
    parsed = JsonParser().parse(_parse_input(payload))
    assert json.loads(parsed.blocks[1].text) == payload
    assert all(block.metadata["record_id"] == parsed.blocks[0].metadata["record_id"] for block in parsed.blocks)
    cleaned, normalized = _document(parsed.blocks)
    result = ChunkEngine().chunk(cleaned, normalized)
    assert len(result.parents) == 1
    assert result.children == []
    assert result.parents[0].chunk_type is ChunkType.STRUCTURED_RECORD
    assert parsed.blocks[1].text in result.parents[0].content
    assert result.validation.valid == (result.parents[0].token_count <= ChunkConfig().max_parent_tokens)
    if not result.validation.valid:
        assert "chunk_too_long" in {issue.code for issue in result.validation.issues}


@pytest.mark.parametrize("profile", ["wikidata", "musicbrainz", "array"])
def test_record_id_tracks_complete_entity_boundaries_and_is_stable(profile):
    if profile == "wikidata":
        payload = {"entities": {**WIKIDATA_PAYLOAD["entities"], "Q2": {"id": "Q2", "labels": {"en": {"value": "Other"}}}}}
        expected = list(payload["entities"].values())
    elif profile == "musicbrainz":
        payload = {"id": "artist-1", "name": "Singer", "aliases": [{"name": "Alias"}]}
        expected = [payload]
    else:
        payload = [{"id": "same-id", "name": "A"}, {"id": "same-id", "name": "B"}]
        expected = payload
    first = JsonParser().parse(_parse_input(payload))
    second = JsonParser().parse(_parse_input(payload))
    assert [b.metadata["record_id"] for b in first.blocks] == [b.metadata["record_id"] for b in second.blocks]
    assert [json.loads(b.text) for b in first.blocks if b.block_type is BlockType.CODE] == expected
    cleaned, normalized = _document(first.blocks)
    result = ChunkEngine().chunk(cleaned, normalized)
    assert result.validation.valid, result.validation.issues
    assert len(result.parents) == len(expected)
    assert len({p.metadata["record_id"] for p in result.parents}) == len(expected)
    assert not result.children


def test_mixed_document_routes_text_table_and_atomic_record_without_heading_leak():
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "实体", "value": "字段值"}))
    blocks = [DocumentBlock(block_id="h", block_type=BlockType.HEADING, text="外部章节", level=1, order_index=0),
              DocumentBlock(block_id="p", block_type=BlockType.PARAGRAPH, text="普通正文。" * 30, order_index=1)]
    blocks += [block.model_copy(update={"order_index": index + 2}) for index, block in enumerate(parsed.blocks)]
    blocks += [DocumentBlock(block_id="p2", block_type=BlockType.PARAGRAPH, text="后续普通正文。" * 20, order_index=4),
               DocumentBlock(block_id="t", block_type=BlockType.TABLE, text="", order_index=5,
                             metadata={"rows": [["专辑", "年份"], ["范特西", "2001"]], "has_header": True})]
    table = DocumentTable(table_id="t1", markdown="", rows=blocks[-1].metadata["rows"], metadata={"source_block_id": "t", "has_header": True})
    cleaned, normalized = _document(blocks, tables=[table])
    result = ChunkEngine().chunk(cleaned, normalized)
    assert result.validation.valid, result.validation.issues
    assert {p.chunk_type for p in result.parents} == set(ChunkType)
    records = [p for p in result.parents if p.chunk_type is ChunkType.STRUCTURED_RECORD]
    assert len(records) == 1
    assert not any(c.parent_id == records[0].chunk_id for c in result.children)
    assert all(p.section_path == ["外部章节"] for p in result.parents)


def test_validator_rejects_split_missing_changed_or_child_record():
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "Entity"}))
    cleaned, normalized = _document(parsed.blocks)
    parent = ChunkEngine().chunk(cleaned, normalized).parents[0]
    validator = ChunkValidator(ChunkConfig())
    blocks = {b.block_id: b for b in normalized.blocks}
    duplicate = parent.model_copy(update={"chunk_id": "other"})
    assert "record_parent_count" in {i.code for i in validator.validate(cleaned, [parent, duplicate], [], [], blocks).issues}
    assert "record_parent_count" in {i.code for i in validator.validate(cleaned, [], [], [], blocks).issues}
    changed = parent.model_copy(update={"content": "partial"})
    assert "record_not_atomic" in {i.code for i in validator.validate(cleaned, [changed], [], [], blocks).issues}
    child = ChildChunk(**{**parent.model_dump(), "chunk_id": "child", "parent_id": parent.chunk_id,
                          "retrieval_content": parent.breadcrumb + "\n\n" + parent.content})
    assert "record_has_children" in {i.code for i in validator.validate(cleaned, [parent], [child], [], blocks).issues}


def test_nonrecords_keep_bounded_renderer_and_atomic_budget_never_truncates():
    parsed = JsonParser().parse(_parse_input({"text": "plain", "nested": {"name": "not an entity"}}))
    assert all("record_id" not in b.metadata for b in parsed.blocks)
    with pytest.raises(ParserError, match="structured_record_output_limit_exceeded"):
        JsonParser(JsonParserConfig(max_output_chars=20)).parse(_parse_input({"id": "r1", "name": "X" * 50}))
    with pytest.raises(ParserError, match="structured_record_item_limit_exceeded"):
        JsonParser(JsonParserConfig(max_items=1)).parse(_parse_input([{"id": "1", "name": "A"}, {"id": "2", "name": "B"}]))


def test_record_projection_loss_or_noncontiguous_reuse_is_rejected():
    parsed = JsonParser().parse(_parse_input({"id": "r1", "name": "Entity"}))
    cleaned, normalized = _document(parsed.blocks)
    with pytest.raises(ChunkRoutingError, match="projection_missing"):
        ChunkRouter().route(normalized, {})
    middle = DocumentBlock(block_id="middle", block_type=BlockType.PARAGRAPH, text="Other", order_index=1)
    normalized = normalized.model_copy(update={"blocks": [parsed.blocks[0], middle, parsed.blocks[1]]})
    projected = {b.block_id: b.text for b in normalized.blocks}
    with pytest.raises(ChunkRoutingError, match="noncontiguous"):
        ChunkRouter().route(normalized, projected)
