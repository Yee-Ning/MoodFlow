"""Environment preparation contracts; no Gold data or restart acceptance."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from app.agent.checkpointing import create_checkpoint_serializer
from app.agent.foundation.subgraphs.worldline.retrieval import WorldLineContextAdapter
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionState
from app.rag.indexing import IndexDocumentProjector, load_indexing_config
from app.rag.retrieval import HybridRetrievalService, load_retrieval_config
from app.rag.retrieval.qdrant import QdrantHybridSearchBackend
from app.rag.retrieval.selection import _candidate_from_hit
from app.rag.retrieval.fusion import FusedSearchHit
from app.rag.retrieval.models import ChannelEvidence
from scripts.worldline_environment_readiness import audit_corpus, checkpoint_smoke, http_probe, probe_postgres
from tests.test_rag_indexing import _child, _config, _parent
from tests.test_retrieval_service import FakeEncoder, plan
from tests.test_worldline_runtime_integration import (
    QdrantTransport,
    native_inputs,
    profile,
    runtime,
    assert_relations,
    QUERY,
)
from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService


def test_native_document_metadata_reaches_context_pack_and_worldline():
    inputs = native_inputs()
    documents = []
    for child, parent in inputs:
        metadata = deepcopy(child.metadata)
        metadata["document_metadata"]["source_metadata"] = {
            "content_hash": "sha256:" + "b" * 64,
            "publisher": child.enrichment.source.source_name,
            "raw_path": "data/raw/independent-engineering-input.html",
        }
        child = child.model_copy(update={"metadata": metadata})
        documents.append(IndexDocumentProjector(_config()).project(
            child, parent, source_release_id="engineering-release-v4",
        ))
    config = load_retrieval_config()
    backend = QdrantHybridSearchBackend(config, client=QdrantTransport(documents, config))
    binding = SimpleNamespace(profile=profile(), manifest=SimpleNamespace(
        projected_count=4, dense_distance="cosine", bm25_idf_modifier=True,
    ))
    service = HybridRetrievalService(binding, config=config, encoder=FakeEncoder(), backend=backend)
    query_plan = plan(index_profile=profile())
    result = asyncio.run(service.retrieve(query_plan))
    adapter = WorldLineContextAdapter(config)
    pack = adapter.build_pack(query_plan, result)
    adapted = adapter.adapt(result, query_plan)
    expected = {d.chunk_id: d.payload for d in documents}
    assert len(pack.items) == len(adapted) == 4
    assert sum(len(i.relation_source_contexts) for i in pack.items) == 2
    for item, document in zip(pack.items, adapted, strict=True):
        original = expected[item.child_id]
        assert item.document_metadata == original["document_metadata"]
        assert document.metadata["document_metadata"] == item.document_metadata
        assert item.source_provenance["source_response_sha256"] == "b" * 64
        assert document.metadata["source_response_sha256"] == "b" * 64
        for key in ("source_content_sha256", "parent_content_sha256", "index_input_hash"):
            assert item.source_provenance[key] == original[key]
        assert [c.model_dump(mode="json") for c in item.relation_source_contexts] == original["relation_source_contexts"]


@pytest.mark.parametrize("source_metadata", [{}, {"content_hash": "unverified"}])
def test_missing_source_capture_and_response_hash_are_not_invented(source_metadata):
    child = _child().model_copy(update={"metadata": {
        "document_metadata": {"source_metadata": source_metadata},
    }})
    payload = IndexDocumentProjector(_config()).project(
        child, _parent(), source_release_id="engineering-release-v4",
    ).payload
    assert payload["relation_source_contexts"] == []
    assert "source_response_sha256" not in payload


def test_malformed_document_metadata_is_rejected():
    child = _child().model_copy(update={"metadata": {"document_metadata": "invalid"}})
    with pytest.raises(ValueError, match="index_document_metadata_invalid"):
        IndexDocumentProjector(_config()).project(child, _parent(), source_release_id="engineering-release-v4")


def test_malformed_retrieved_metadata_is_rejected():
    document = IndexDocumentProjector(_config()).project(_child(), _parent(), source_release_id="engineering-release-v4")
    payload = {**document.payload, "document_metadata": []}
    with pytest.raises(ValueError, match="retrieval_document_metadata_invalid"):
        _candidate_from_hit(FusedSearchHit(document.point_id, 0.5, payload, []))


def test_source_whitespace_survives_hash_validation_and_tampering_is_rejected():
    child = _child().model_copy(update={"content": "\n" + _child().content + "  "})
    parent = _parent().model_copy(update={"content": "  " + _parent().content + "\n"})
    document = IndexDocumentProjector(_config()).project(child, parent, source_release_id="engineering-release-v4")
    evidence = ChannelEvidence(
        chunk_id=child.chunk_id, subquery_id="q0:original", retrieval_leg="q0:original:dense",
        variant_index=0, variant_kind="original", channel="dense", rank=1, raw_score=1, weight=1,
    )
    hit = FusedSearchHit(document.point_id, 0.5, document.payload, [evidence])
    candidate = _candidate_from_hit(hit)
    assert candidate.primary_child_content == child.content
    assert candidate.parent_context.content == parent.content
    assert candidate.metadata["source_content_sha256"] == document.payload["source_content_sha256"]
    tampered = FusedSearchHit(
        document.point_id, 0.5, {**document.payload, "content": child.content + "tampered"}, [evidence],
    )
    with pytest.raises(ValueError, match="retrieval_source_content_hash_mismatch"):
        _candidate_from_hit(tampered)


def test_corpus_gap_audit_verifies_raw_body_without_inventing_roles(tmp_path):
    from hashlib import sha256

    raw = tmp_path / "data/raw/source.html"
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"independent engineering raw response")
    metadata = {"document_metadata": {"source_metadata": {
        "url": "https://example.com/engineering", "publisher": "Engineering source",
        "title": "Engineering source", "raw_path": "data/raw/source.html",
        "content_hash": "sha256:" + sha256(raw.read_bytes()).hexdigest(),
    }}}
    chunks = [c.model_copy(update={"metadata": metadata}) for c in [_parent(), _child()]]
    corpus = tmp_path / "knowledge.jsonl"
    corpus.write_text("\n".join(c.model_dump_json() for c in chunks), encoding="utf-8")
    report = audit_corpus(tmp_path, corpus)
    assert not report["ready"]
    assert report["documents"][0]["response_body_sha_verified"]
    assert report["children_with_capture_count"] == 0
    assert len(report["gaps"]) == 4
    assert all(g.startswith("missing_source_context_role:") for g in report["gaps"])
    raw.write_bytes(b"tampered")
    assert "raw_response_body_hash_not_verified_for_all_documents" in audit_corpus(tmp_path, corpus)["gaps"]


def test_probe_errors_never_emit_credential_messages(monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("secret-api-key-and-password")

    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/engineering")
    monkeypatch.setattr("scripts.worldline_environment_readiness.connect", unavailable)
    monkeypatch.setattr("scripts.worldline_environment_readiness.httpx.get", unavailable)
    result = {"postgres": probe_postgres(), "http": http_probe("http://localhost:1")}
    assert "secret-api-key-and-password" not in json.dumps(result)
    assert not result["postgres"]["reachable"]
    assert not result["http"]["passed"]


def test_checkpoint_probe_cannot_report_memory_as_postgres(monkeypatch, tmp_path):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "memory")
    with pytest.raises(ValueError, match="checkpoint_probe_requires_postgres_backend"):
        asyncio.run(checkpoint_smoke(tmp_path, tmp_path / "unused.json"))


def test_worldline_v4_config_isolated_and_matches_query_embedding():
    from pathlib import Path

    config = load_indexing_config(Path("app/rag/indexing/config/worldline_v4.yaml"))
    retrieval = load_retrieval_config()
    assert config.projection_version == "v4"
    assert config.qdrant.collection_name == "worldline_knowledge_v4"
    assert not config.qdrant.prune_stale
    assert config.dense.model_revision == retrieval.dense.model_revision
    assert config.dense.dimension == retrieval.dense.dimension
    assert config.bm25.document_options() == retrieval.bm25.query_options()


def test_relation_conflict_snapshot_uses_postgres_serializer_contract():
    async def check():
        # Only serialization is checked here. This memory fixture is never
        # reported as a live PostgreSQL write/read or a restart acceptance.
        service = WorldLineSessionService(create_memory_checkpointer(), turn_graph=runtime().graph)
        created = await service.create(QUERY)
        assert_relations(created.model_dump(mode="json"))
        snapshot = await service.load(created.session_id)
        serde = create_checkpoint_serializer()
        restored = WorldLineSessionState.model_validate(
            serde.loads_typed(serde.dumps_typed(snapshot.model_dump(mode="json")))
        )
        assert restored == snapshot
        assert restored.current_graph == snapshot.current_graph
        assert restored.evidence_history == snapshot.evidence_history
        assert restored.last_result.influence_relations[0].metadata["conflict_flags"] == ["attribution_conflict"]

    asyncio.run(check())
