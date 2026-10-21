import json
import sqlite3
from unittest.mock import patch

import pytest

from app.rag.indexing import OfflineIndexingService
from app.rag.indexing.cache import LocalEmbeddingCache
from tests.test_indexing_structured_records import _record
from tests.test_rag_indexing import MemoryStore, StaticEncoder, _child, _config, _parent, _write_corpus


def _cache_path(root):
    return root / "data/indexing/embedding_cache/v1/embeddings.sqlite3"


def _run(root, path, *, encoder=None, store=None):
    return OfflineIndexingService(root, _config(), knowledge_path=path,
                                  encoder=encoder, store=store or MemoryStore()).run()


@pytest.mark.parametrize("record_only", [False, True])
def test_first_run_persists_then_new_service_and_empty_store_use_local_cache(tmp_path, record_only):
    path = (_write_corpus(tmp_path, [], parents=[_record()]) if record_only
            else _write_corpus(tmp_path, [_child()]))
    encoder = StaticEncoder()
    first_store = MemoryStore()
    first = _run(tmp_path, path, encoder=encoder, store=first_store)
    assert first.summary["embedding_computed_count"] == encoder.calls == 1
    assert first.summary["embedding_cache_hit_count"] == 0
    assert first.summary["embedding_cache_path"] == "data/indexing/embedding_cache/v1/embeddings.sqlite3"
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        row, = connection.execute("SELECT input_hash, dense_vector FROM dense_embeddings_v1").fetchall()
    point, = first_store.points.values()
    assert row[0] == point.document.input_hash
    assert json.loads(row[1]) == point.dense_vector
    second_store = MemoryStore()
    with patch("app.rag.indexing.service.QwenDenseEncoder", side_effect=AssertionError("model_loaded")):
        second = _run(tmp_path, path, store=second_store)
    assert second.summary["embedding_cache_hit_count"] == second.summary["indexed_count"] == 1
    assert second.summary["embedding_computed_count"] == second.summary["skipped_count"] == 0
    assert second.summary["ready_for_retrieval"]
    assert second_store.points == first_store.points
    manifest = json.loads(second.manifest_path.read_text(encoding="utf-8"))
    assert manifest["embedding_cache_hit_count"] == 1


def test_local_lookup_precedes_qdrant_hash_comparison_and_both_can_skip(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    store = MemoryStore()
    _run(tmp_path, path, encoder=StaticEncoder(), store=store)
    events = []
    original_get = LocalEmbeddingCache.get
    original_hashes = store.existing_hashes
    def get(cache, key):
        events.append("local")
        return original_get(cache, key)
    def hashes(ids):
        events.append("qdrant")
        return original_hashes(ids)
    with patch.object(LocalEmbeddingCache, "get", get), patch.object(store, "existing_hashes", hashes), \
         patch("app.rag.indexing.service.QwenDenseEncoder", side_effect=AssertionError("model_loaded")):
        result = _run(tmp_path, path, store=store)
    assert events == ["local", "qdrant"]
    assert result.summary["embedding_cache_hit_count"] == result.summary["skipped_count"] == 1
    assert result.summary["indexed_count"] == result.summary["embedding_computed_count"] == 0


def test_existing_qdrant_hash_skip_still_works_with_no_local_entry(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    store = MemoryStore()
    _run(tmp_path, path, encoder=StaticEncoder(), store=store)
    _cache_path(tmp_path).unlink()
    with patch("app.rag.indexing.service.QwenDenseEncoder", side_effect=AssertionError("model_loaded")):
        result = _run(tmp_path, path, store=store)
    assert result.summary["skipped_count"] == 1
    assert result.summary["embedding_cache_hit_count"] == result.summary["embedding_computed_count"] == 0


@pytest.mark.parametrize("change", ["text", "parent_payload", "aliases"])
def test_changed_input_hash_misses_and_recomputes(tmp_path, change):
    encoder = StaticEncoder()
    store = MemoryStore()
    path = _write_corpus(tmp_path, [_child()])
    _run(tmp_path, path, encoder=encoder, store=store)
    old_hash = next(iter(store.points.values())).document.input_hash
    child, parent = _child(), _parent()
    if change == "text":
        child = _child(content="更新后的实体内容。")
    elif change == "parent_payload":
        parent = parent.model_copy(update={"content": parent.content + " 新的上下文。"})
    else:
        entities = child.knowledge.entities
        linked = [entities.linked_entities[0].model_copy(update={"aliases": ["新别名"]})]
        child = child.model_copy(update={"knowledge": child.knowledge.model_copy(update={
            "entities": entities.model_copy(update={"linked_entities": linked}),
        })})
    path = _write_corpus(tmp_path, [child], parents=[parent])
    result = _run(tmp_path, path, encoder=encoder, store=store)
    assert result.summary["embedding_cache_hit_count"] == result.summary["skipped_count"] == 0
    assert result.summary["embedding_computed_count"] == result.summary["indexed_count"] == 1
    assert encoder.calls == 2
    assert next(iter(store.points.values())).document.input_hash != old_hash
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dense_embeddings_v1").fetchone()[0] == 2


@pytest.mark.parametrize("serialized", [
    "not json", "{}", "[]", "[true]", "[1.0]",
    json.dumps([2.0] + [0.0]*31), json.dumps([float("nan")] + [0.0]*31),
])
def test_invalid_cached_vectors_are_rejected_and_replaced(tmp_path, serialized):
    path = _write_corpus(tmp_path, [_child()])
    _run(tmp_path, path, encoder=StaticEncoder())
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        connection.execute("UPDATE dense_embeddings_v1 SET dense_vector = ?", (serialized,))
    encoder = StaticEncoder()
    result = _run(tmp_path, path, encoder=encoder)
    assert result.summary["embedding_cache_hit_count"] == 0
    assert result.summary["embedding_computed_count"] == encoder.calls == 1
    assert result.summary["ready_for_retrieval"]
    assert "embedding_cache_entry_invalid" in result.summary["warnings"]
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        vector = json.loads(connection.execute("SELECT dense_vector FROM dense_embeddings_v1").fetchone()[0])
    assert vector == [1.0] + [0.0]*31


@pytest.mark.parametrize("failure", ["unwritable", "corrupt"])
def test_cache_unavailable_falls_back_to_embedding_and_reports_warning(tmp_path, failure):
    path = _write_corpus(tmp_path, [_child()])
    cache_path = _cache_path(tmp_path)
    cache_path.parent.mkdir(parents=True)
    if failure == "corrupt":
        cache_path.write_bytes(b"not a sqlite file")
    else:
        cache_path.mkdir()
    encoder = StaticEncoder()
    result = _run(tmp_path, path, encoder=encoder)
    assert result.summary["ready_for_retrieval"]
    assert result.summary["embedding_computed_count"] == encoder.calls == 1
    assert any(w.startswith("embedding_cache_unavailable:") for w in result.summary["warnings"])


def test_failed_embedding_is_not_cached(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    first = _run(tmp_path, path, encoder=StaticEncoder("周杰伦"))
    assert first.summary["failed_count"] == 1
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dense_embeddings_v1").fetchone()[0] == 0
    second = _run(tmp_path, path, encoder=StaticEncoder())
    assert second.summary["embedding_computed_count"] == 1
    assert second.summary["embedding_cache_hit_count"] == 0


def test_cache_write_failure_does_not_fail_valid_embedding(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    with LocalEmbeddingCache(_cache_path(tmp_path)):
        pass
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        connection.execute("CREATE TRIGGER fail_insert BEFORE INSERT ON dense_embeddings_v1 "
                           "BEGIN SELECT RAISE(ABORT, 'test_cache_write_failure'); END")
    result = _run(tmp_path, path, encoder=StaticEncoder())
    assert result.summary["ready_for_retrieval"]
    assert result.summary["embedding_computed_count"] == result.summary["indexed_count"] == 1
    assert "embedding_cache_unavailable:IntegrityError" in result.summary["warnings"]


def test_interrupted_run_preserves_completed_embedding_batches(tmp_path):
    path = _write_corpus(tmp_path, [_child(), _child(chunk_id="child-2")])
    class InterruptedEncoder(StaticEncoder):
        def encode_documents(self, texts):
            if self.calls:
                raise KeyboardInterrupt("test_interrupted_after_first_batch")
            return super().encode_documents(texts)
    with pytest.raises(KeyboardInterrupt, match="test_interrupted_after_first_batch"):
        OfflineIndexingService(tmp_path, _config(embedding_batch_size=1), knowledge_path=path,
                               encoder=InterruptedEncoder(), store=MemoryStore()).run()
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dense_embeddings_v1").fetchone()[0] == 1
    second = _run(tmp_path, path, encoder=StaticEncoder())
    assert second.summary["embedding_cache_hit_count"] == second.summary["embedding_computed_count"] == 1
    assert second.summary["indexed_count"] == 2


def test_failed_upsert_keeps_validated_vector_for_retry(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    first = _run(tmp_path, path, encoder=StaticEncoder(), store=MemoryStore(failing_chunk="child-1"))
    assert first.summary["failed_count"] == 1 and first.failures[0].stage.value == "upsert"
    with patch("app.rag.indexing.service.QwenDenseEncoder", side_effect=AssertionError("model_loaded")):
        second = _run(tmp_path, path)
    assert second.summary["embedding_cache_hit_count"] == second.summary["indexed_count"] == 1
    assert second.summary["embedding_computed_count"] == 0


def test_mixed_hits_misses_and_embedding_failure_preserve_good_points(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    _run(tmp_path, path, encoder=StaticEncoder())
    path = _write_corpus(tmp_path, [
        _child(), _child(chunk_id="new-good"), _child(chunk_id="new-bad", content="坏向量"),
    ])
    store = MemoryStore()
    result = _run(tmp_path, path, encoder=StaticEncoder("坏向量"), store=store)
    assert result.summary["embedding_cache_hit_count"] == result.summary["embedding_computed_count"] == 1
    assert result.summary["failed_count"] == 1 and result.summary["indexed_count"] == 2
    assert {p.document.chunk_id for p in store.points.values()} == {"child-1", "new-good"}
    with sqlite3.connect(_cache_path(tmp_path)) as connection:
        assert connection.execute("SELECT COUNT(*) FROM dense_embeddings_v1").fetchone()[0] == 2


def test_cleared_cache_rebuilds_on_demand(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    encoder = StaticEncoder()
    _run(tmp_path, path, encoder=encoder)
    _cache_path(tmp_path).unlink()
    result = _run(tmp_path, path, encoder=encoder)
    assert encoder.calls == 2 and _cache_path(tmp_path).is_file()
    assert result.summary["embedding_cache_hit_count"] == 0
    assert result.summary["embedding_computed_count"] == 1


def test_validate_only_does_not_open_or_create_embedding_cache(tmp_path):
    path = _write_corpus(tmp_path, [_child()])
    with patch("app.rag.indexing.service.LocalEmbeddingCache", side_effect=AssertionError("cache_opened")):
        result = OfflineIndexingService(tmp_path, _config(), knowledge_path=path).run(validate_only=True)
    assert result.summary["status"] == "validated"
    assert not _cache_path(tmp_path).exists()
