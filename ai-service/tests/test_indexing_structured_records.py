import math
import uuid
from unittest.mock import patch

import pytest

from app.rag.chunk import ChunkEngine, ChunkType
from app.rag.indexing import (
    IndexDocumentProjector, IndexingValidator, OfflineIndexingService, QdrantIndexStore,
)
from app.rag.indexing.models import IndexPoint
from app.rag.indexing.projection import POINT_NAMESPACE
from app.rag.knowledge.models import KnowledgeEnrichedParentChunk, KnowledgeStats, KnowledgeStatus
from app.rag.parse_normalize.parsers.json import JsonParser
from tests.test_parse_normalize_json import _parse_input
from tests.test_rag_chunk import _document
from tests.test_rag_indexing import (
    RELEASE_ID, MemoryStore, StaticEncoder, _child, _config, _parent, _write_corpus,
)


def _record():
    parsed = JsonParser().parse(_parse_input({
        "id": "jay", "name": "周杰伦", "aliases": ["周董"], "extra": {"year": 2000},
    }))
    cleaned, normalized = _document(parsed.blocks)
    chunked = ChunkEngine().chunk(cleaned, normalized)
    assert not chunked.children
    base = _parent()
    knowledge = base.knowledge.model_copy(update={
        "claims": [], "stats": KnowledgeStats.build(base.knowledge.entities, []),
    })
    return KnowledgeEnrichedParentChunk(
        **chunked.parents[0].model_dump(), enrichment=base.enrichment, knowledge=knowledge,
    )


def test_atomic_record_projection_preserves_heading_json_provenance_and_uuid():
    record = _record()
    original = record.model_dump(mode="json")
    document = IndexDocumentProjector(_config()).project_record(record, source_release_id=RELEASE_ID)
    repeated = IndexDocumentProjector(_config()).project_record(record, source_release_id=RELEASE_ID)
    assert document == repeated
    assert document.dense_text == record.content.strip()
    assert document.dense_text.startswith("# 周杰伦\n\n```json\n")
    assert '"extra":{"year":2000}' in document.dense_text
    assert document.bm25_text.startswith(document.dense_text + "\n\n检索词：")
    assert "Jay Chou" in document.bm25_text and "周董" in document.bm25_text
    assert document.point_id == str(uuid.uuid5(POINT_NAMESPACE, record.chunk_id))
    assert document.parent_id == record.chunk_id
    assert document.payload["record_type"] == "parent"
    assert document.payload["record_id"] == record.metadata["record_id"]
    assert document.payload["content"] == record.content
    assert document.payload["source_url"] == record.enrichment.source.source_url
    assert document.payload["parent_context"]["content"] == record.content
    IndexingValidator(_config()).validate_document(document)
    assert record.model_dump(mode="json") == original and record.parent_id is None


def test_parent_only_record_reaches_dense_and_bm25_point(tmp_path):
    record = _record()
    path = _write_corpus(tmp_path, [], parents=[record])
    store = MemoryStore()
    result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path,
                                   encoder=StaticEncoder(), store=store).run()
    assert result.summary["eligible_count"] == result.summary["indexed_count"] == 1
    assert result.summary["child_count"] == result.summary["failed_count"] == 0
    assert result.summary["ready_for_retrieval"]
    point, = store.points.values()
    assert point.document.bm25_text
    assert len(point.dense_vector) == 32
    assert math.isclose(math.sqrt(sum(x*x for x in point.dense_vector)), 1.0)


@pytest.mark.parametrize("with_child", [False, True])
def test_ordinary_parent_never_gets_its_own_point(tmp_path, with_child):
    path = _write_corpus(tmp_path, [_child()] if with_child else [])
    store = MemoryStore()
    result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path,
                                   encoder=StaticEncoder(), store=store).run()
    assert result.summary["indexed_count"] == int(with_child)
    assert all(p.document.chunk_id != _parent().chunk_id for p in store.points.values())
    if with_child:
        point, = store.points.values()
        assert point.document.payload["parent_context"]["content"] == _parent().content


def test_mixed_batch_indexes_children_and_only_childless_records(tmp_path):
    record = _record()
    path = _write_corpus(tmp_path, [_child()], parents=[_parent(), record])
    store = MemoryStore()
    result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path,
                                   encoder=StaticEncoder(), store=store).run()
    assert result.summary["parent_count"] == result.summary["indexed_count"] == 2
    assert result.summary["child_count"] == 1
    assert result.summary["ready_for_retrieval"]
    assert {p.document.chunk_id for p in store.points.values()} == {"child-1", record.chunk_id}


@pytest.mark.parametrize("status", [KnowledgeStatus.SUCCESS, KnowledgeStatus.FAILED])
def test_record_with_child_does_not_get_duplicate_parent_point(tmp_path, status):
    record = _record()
    child = _child().model_copy(update={"doc_id": record.doc_id, "parent_id": record.chunk_id})
    child = child.model_copy(update={"knowledge": child.knowledge.model_copy(update={
        "quality": child.knowledge.quality.model_copy(update={"status": status}),
    })})
    path = _write_corpus(tmp_path, [child], parents=[record])
    result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path).run(validate_only=True)
    assert result.summary["eligible_count"] == int(status is KnowledgeStatus.SUCCESS)
    assert result.summary["excluded_count"] == int(status is KnowledgeStatus.FAILED)


def test_record_point_uses_the_same_dense_bm25_store_path():
    class CapturingClient:
        def upsert(self, *, collection_name, points, wait):
            self.points = points
    client = CapturingClient()
    document = IndexDocumentProjector(_config()).project_record(_record(), source_release_id=RELEASE_ID)
    point = IndexPoint(document=document, dense_vector=[1.0] + [0.0]*31)
    QdrantIndexStore(_config(), client=client).upsert([point])
    stored, = client.points
    assert stored.vector["dense"] == point.dense_vector
    assert stored.vector["bm25"].text == document.bm25_text
    assert stored.payload["record_id"] == document.payload["record_id"]


@pytest.mark.parametrize("status,allow_partial,expected", [
    (KnowledgeStatus.SUCCESS, False, 1), (KnowledgeStatus.SKIPPED, False, 1),
    (KnowledgeStatus.PARTIAL, True, 1), (KnowledgeStatus.PARTIAL, False, 0),
    (KnowledgeStatus.FAILED, True, 0),
])
def test_record_uses_existing_knowledge_quality_gate(tmp_path, status, allow_partial, expected):
    record = _record()
    record = record.model_copy(update={"knowledge": record.knowledge.model_copy(update={
        "quality": record.knowledge.quality.model_copy(update={"status": status}),
    })})
    path = _write_corpus(tmp_path, [], parents=[record])
    result = OfflineIndexingService(tmp_path, _config(index_partial_knowledge=allow_partial),
                                   knowledge_path=path).run(validate_only=True)
    assert result.summary["eligible_count"] == result.summary["projected_count"] == expected
    assert result.summary["excluded_count"] == 1 - expected


@pytest.mark.parametrize("chunk_type", [ChunkType.NARRATIVE, ChunkType.TABLE])
def test_projector_and_validator_reject_other_parent_types(chunk_type):
    record = _record().model_copy(update={"chunk_type": chunk_type})
    projector = IndexDocumentProjector(_config())
    with pytest.raises(ValueError, match="non_record_parent"):
        projector.project_record(record, source_release_id=RELEASE_ID)
    document = projector.project_record(_record(), source_release_id=RELEASE_ID)
    payload = {**document.payload, "chunk_type": chunk_type.value}
    with pytest.raises(ValueError, match="non_child_index_document"):
        IndexingValidator(_config()).validate_document(document.model_copy(update={"payload": payload}))


@pytest.mark.parametrize("field,value,error", [
    ("parent_context", None, "payload_parent_context_missing"),
    ("source_release_id", "", "payload_source_release_missing"),
    ("index_input_hash", "wrong", "payload_input_hash_mismatch"),
    ("chunk_id", "wrong", "payload_chunk_id_mismatch"),
])
def test_record_keeps_other_payload_checks(field, value, error):
    document = IndexDocumentProjector(_config()).project_record(_record(), source_release_id=RELEASE_ID)
    document = document.model_copy(update={"payload": {**document.payload, field: value}})
    with pytest.raises(ValueError, match=error):
        IndexingValidator(_config()).validate_document(document)


@pytest.mark.parametrize("vector,error", [
    ([1.0], "dense_dimension_invalid"), ([2.0] + [0.0]*31, "dense_vector_not_normalized"),
    ([float("nan")] + [0.0]*31, "dense_vector_non_finite"),
])
def test_record_keeps_vector_validation_and_failure_isolation(tmp_path, vector, error):
    record = _record()
    document = IndexDocumentProjector(_config()).project_record(record, source_release_id=RELEASE_ID)
    with pytest.raises(ValueError, match=error):
        IndexingValidator(_config()).validate_point(IndexPoint(document=document, dense_vector=vector))
    path = _write_corpus(tmp_path, [_child()], parents=[_parent(), record])
    encoder = StaticEncoder()
    original_encode = encoder.encode_documents
    def encode(texts):
        return [vector if text == record.content.strip() else original_encode([text])[0] for text in texts]
    with patch.object(encoder, "encode_documents", side_effect=encode):
        result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path,
                                       encoder=encoder, store=MemoryStore()).run()
    assert result.summary["indexed_count"] == result.summary["failed_count"] == 1
    assert result.failures[0].chunk_id == record.chunk_id
    assert not result.summary["ready_for_retrieval"]
