"""Engineering integration through runtime services, with external I/O doubles.

These fixtures are independent unit-test prose, never Gold or review inputs.
Set WORLDLINE_SMOKE_TRACE_PATH to save the inspectable runtime trace.
"""

import asyncio
from copy import deepcopy
from hashlib import sha256
import json
import os
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation import build_main_graph, create_main_state
from app.agent.foundation.hybrid_routing import HybridIntentRouter
from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
from app.agent.foundation.subgraphs.worldline.scene_expansion import (
    SceneExpansionService,
)
from app.agent.foundation.subgraphs.worldline.influence_expansion import (
    InfluenceExpansionService,
)
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType, MusicEntityFacts
from app.agent.foundation.subgraphs.worldline.merge import (
    merge_worldline_graph,
    WorldLineMergeError,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    HybridMusicKnowledgeRetriever,
    WorldLineContextAdapter,
)
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService
from app.api import worldline as worldline_api
from app.rag.context_packing import ContextPackBuilder
from app.rag.knowledge.claims.models import Claim
from app.rag.knowledge.models import KnowledgeEntities, KnowledgeStats
from app.rag.knowledge.entity_linking.models import (
    CanonicalEntity,
    EntityRegistryDocument,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.knowledge.verification.models import VerifiedClaim, ClaimVerification
from app.rag.indexing import IndexDocumentProjector
from app.rag.query_understanding.service import QueryUnderstandingService
from app.rag.query_understanding.models import IndexProfile
from app.rag.query_understanding.entity_resolver import QueryEntityResolver
from app.rag.retrieval import HybridRetrievalService, load_retrieval_config
from app.rag.retrieval.qdrant import QdrantHybridSearchBackend, PAYLOAD_FIELDS
from tests.test_rag_indexing import _child, _parent, _config
from tests.test_retrieval_service import FakeEncoder
from tests.test_worldline_graph import FakeGenerator, entity
from tests.test_worldline_graph import initial_state
from tests.test_worldline_influence_source_context import context_pair
from tests.test_worldline_influence_expansion import document


QUERY = "探索歌手《A》的音乐世界线、发行年代、音乐场景与音乐影响"


def profile():
    return IndexProfile(
        collection_name="engineering_worldline_v4",
        source_release_id="engineering-release-v4",
        index_schema_version="v2",
        projection_version="v4",
        registry_version="v3",
    )


def registry():
    return EntityRegistry(
        EntityRegistryDocument(
            registry_version="v3",
            entities=[
                {
                    "entity_id": "artist:a",
                    "entity_type": "artist",
                    "canonical_name": "A",
                },
                {
                    "entity_id": "artist:b",
                    "entity_type": "artist",
                    "canonical_name": "B",
                },
                {
                    "entity_id": "song:b",
                    "entity_type": "song",
                    "canonical_name": "Song B",
                },
            ],
        )
    )


def native_inputs():
    docs = list(
        context_pair(
            question="Other artists were influenced by A. Is that also true for you?"
        )
    )
    docs += [
        document(
            303,
            "Song B was released in 1988.",
            predicate="belongs_to_era",
            subject="song:b",
        ).model_copy(
            update={
                "metadata": {
                    "chunk_id": "child-303",
                    "supported_claims": [
                        {
                            "claim_id": f"claim:{303:024x}",
                            "subject_entity_id": "song:b",
                            "predicate": "belongs_to_era",
                            "object_value": "1980s",
                            "time": {"year": 1988},
                            "source_chunk_id": "child-303",
                            "evidence_span": "Song B was released in 1988.",
                            "extraction_confidence": 0.97,
                            "verification_confidence": 0.97,
                        }
                    ],
                },
            }
        ),
        document(
            304,
            "B was part of the Neon movement.",
            predicate="belongs_to_scene",
            subject="artist:b",
        ).model_copy(
            update={
                "metadata": {
                    "chunk_id": "child-304",
                    "supported_claims": [
                        {
                            "claim_id": f"claim:{304:024x}",
                            "subject_entity_id": "artist:b",
                            "predicate": "belongs_to_scene",
                            "object_value": "Neon movement",
                            "source_chunk_id": "child-304",
                            "evidence_span": "B was part of the Neon movement.",
                            "extraction_confidence": 0.97,
                            "verification_confidence": 0.97,
                        }
                    ],
                },
            }
        ),
    ]
    result = []
    for doc in docs:
        raw = doc.metadata["supported_claims"][0]
        original = raw["evidence_span"]
        context = doc.metadata.get("relation_source_context")
        full = context["content"] if context else original
        claim = Claim(
            **{
                k: raw[k]
                for k in (
                    "claim_id",
                    "subject_entity_id",
                    "predicate",
                    "object_entity_id",
                    "object_value",
                    "time",
                    "source_chunk_id",
                    "evidence_span",
                )
                if k in raw
            },
            statement=original,
            confidence=0.97,
            extraction_source="rule",
        )
        verified = VerifiedClaim(
            claim=claim,
            verification=ClaimVerification(
                status="SUPPORTED",
                confidence=0.97,
                evidence_span=original,
                reason="engineering fixture",
            ),
        )
        base = _child(chunk_id=raw["source_chunk_id"], content=original)
        source = base.enrichment.source.model_copy(
            update={
                "source_id": "native:" + doc.id,
                "source_name": doc.source,
                "source_url": str(doc.url),
            }
        )
        metadata = {"title": doc.title}
        if context:
            # Ingested document capture has no claim/chunk IDs. The indexer
            # binds this original text to native verified claim identities.
            metadata["source_context_captures"] = [
                {
                    key: context[key]
                    for key in (
                        "source_url",
                        "publisher",
                        "title",
                        "locator",
                        "captured_at",
                        "capture_method",
                        "response_sha256",
                        "content",
                        "content_sha256",
                        "segments",
                    )
                }
            ]
        entities = KnowledgeEntities(
            linked_entities=[
                CanonicalEntity(
                    entity_id=e.entity_id,
                    entity_type=e.entity_type,
                    canonical_name=e.canonical_name,
                    confidence=0.99,
                )
                for e in [
                    registry().get(key) for key in ("artist:a", "artist:b", "song:b")
                ]
            ]
        )
        knowledge = base.knowledge.model_copy(
            update={
                "entities": entities,
                "claims": [verified],
                "stats": KnowledgeStats.build(entities, [verified]),
            }
        )
        child = base.model_copy(
            update={
                "parent_id": doc.id,
                "doc_id": "native-doc:" + doc.id,
                "breadcrumb": doc.title,
                "retrieval_content": original,
                "token_count": len(original),
                "metadata": {"title": doc.title, "document_metadata": metadata},
                "enrichment": base.enrichment.model_copy(update={"source": source}),
                "knowledge": knowledge,
            }
        )
        parent = _parent().model_copy(
            update={
                "chunk_id": doc.id,
                "doc_id": child.doc_id,
                "breadcrumb": doc.title,
                "content": full,
                "token_count": len(full),
                "metadata": child.metadata,
            }
        )
        result.append((child, parent))
    return result


class QdrantTransport:
    """Only the remote HTTP boundary is replaced; Qdrant request code is real."""

    def __init__(self, documents, config):
        self.documents, self.config, self.requests = documents, config, []

    async def info(self):
        return SimpleNamespace(version=self.config.qdrant.minimum_server_version)

    async def get_collection(self, name):
        return SimpleNamespace(
            points_count=len(self.documents),
            config=SimpleNamespace(
                params=SimpleNamespace(
                    vectors={
                        self.config.dense.vector_name: SimpleNamespace(
                            size=self.config.dense.dimension
                        )
                    },
                    sparse_vectors={self.config.bm25.vector_name: {}},
                )
            ),
        )

    async def query_batch_points(self, *, requests, **kwargs):
        self.requests.extend(requests)
        # Emulate Qdrant's with_payload selector: unrequested metadata is lost.
        points = [
            SimpleNamespace(
                id=d.point_id,
                score=1.0 - i * 0.01,
                payload={k: v for k, v in d.payload.items() if k in PAYLOAD_FIELDS},
            )
            for i, d in enumerate(self.documents)
        ]
        return [SimpleNamespace(points=points) for _ in requests]


class CaptureAdapter(WorldLineContextAdapter):
    def __init__(self):
        super().__init__()
        self.observations = []

    def build_pack(self, plan, result):
        pack = super().build_pack(plan, result)
        self.observations.append(
            {
                "search_plan": plan.model_dump(mode="json"),
                "retrieval": result.model_dump(mode="json"),
                "context_pack": pack.model_dump(mode="json"),
            }
        )
        return pack


class CatalogTransport:
    def __init__(self):
        self.entities = [
            entity(i, kind, name)
            for i, kind, name in [
                (1, EntityType.ARTIST, "A"),
                (2, EntityType.ARTIST, "B"),
                (3, EntityType.SONG, "Song B"),
            ]
        ]

    async def search_entities(self, query, expected_entity_type, *, limit=5):
        return [self.entities[0]]

    async def load_entity_facts(self, root):
        return MusicEntityFacts(
            entity=root,
            related_entities=[
                e for e in self.entities if e.entity_id != root.entity_id
            ],
        )


def runtime(*, catalog_registry=None):
    prepared = [
        IndexDocumentProjector(_config()).project(
            c, p, source_release_id="engineering-release-v4"
        )
        for c, p in native_inputs()
    ]
    config = load_retrieval_config()
    transport = QdrantTransport(prepared, config)
    binding = SimpleNamespace(
        profile=profile(), manifest=SimpleNamespace(projected_count=len(prepared))
    )
    retrieval = HybridRetrievalService(
        binding,
        encoder=FakeEncoder(),
        backend=QdrantHybridSearchBackend(config, client=transport),
    )
    adapter = CaptureAdapter()
    retriever = HybridMusicKnowledgeRetriever(retrieval, context_adapter=adapter)
    calls = {"era": [], "scene": [], "influence": []}

    def observe(service, key):
        original = service.expand

        def expand(*args, **kwargs):
            result = original(*args, **kwargs)
            calls[key].append(result)
            return result

        service.expand = expand
        return service

    graph = create_worldline_subgraph(
        music_service=CatalogTransport(),
        retriever=retriever,
        generator=FakeGenerator(),
        query_understanding=QueryUnderstandingService(
            index_profile=profile(),
            entity_resolver=QueryEntityResolver(catalog_registry or registry()),
        ),
        era_expansion=observe(EraExpansionService(), "era"),
        scene_expansion=observe(SceneExpansionService(), "scene"),
        influence_expansion=observe(InfluenceExpansionService(), "influence"),
    )
    return SimpleNamespace(
        graph=graph,
        adapter=adapter,
        calls=calls,
        transport=transport,
        prepared=prepared,
    )


def assert_relations(payload):
    assert (
        len(payload["era_relations"])
        == len(payload["scene_relations"])
        == len(payload["influence_relations"])
        == 1
    )
    relation = payload["influence_relations"][0]
    assert relation["metadata"]["conflict_flags"] == ["attribution_conflict"]
    assert {e["stance"] for e in relation["evidence"]} == {"support", "contradiction"}
    assert relation["source_entity"]["node_id"] == "netease:artist:1"
    assert relation["target_entity"]["node_id"] == "netease:artist:2"
    assert relation["direction"] == "influencer_to_influenced"
    sources = {s["id"]: s for s in payload["sources"]}
    for evidence in relation["evidence"]:
        source = sources[evidence["source_id"]]
        context = source["metadata"]["relation_source_context"]
        assert (
            source["excerpt"]
            == context["anchor"]["exact_span"]
            == evidence["exact_span"]
        )
        assert context["chunk_id"] == source["metadata"]["source_chunk_id"]
        assert context["claim_id"] == evidence["claim_id"]
        assert context["source_id"].startswith("native:")
        assert (
            source["metadata"]["source_content_sha256"]
            == sha256(source["excerpt"].encode()).hexdigest()
        )
        assert source["metadata"]["source_release_id"] == "engineering-release-v4"


def test_api_session_context_conflict_and_multiturn_checkpoint(monkeypatch):
    rt = runtime()
    saver = create_memory_checkpointer()
    session = WorldLineSessionService(saver, turn_graph=rt.graph)
    monkeypatch.setattr(worldline_api, "get_worldline_session_service", lambda: session)
    app = FastAPI()
    app.include_router(worldline_api.router)
    with TestClient(app) as client:
        assert (
            client.post("/api/ai/worldline/sessions", json={"query": ""}).status_code
            == 422
        )
        response = client.post("/api/ai/worldline/sessions", json={"query": QUERY})
        assert response.status_code == 200, response.text
        created = response.json()
        assert_relations(created)
        branch = next(
            b
            for b in created["branches"]
            if b["target_entity"]["id"] == "netease:artist:2"
        )
        response = client.post(
            "/api/ai/worldline/explore",
            json={"session_id": created["session_id"], "branch_id": branch["id"]},
        )
        assert response.status_code == 200, response.text
        explored = response.json()
        assert_relations(explored)
        for key in ("era_relations", "scene_relations", "influence_relations"):
            assert explored[key] == created[key]
        session = WorldLineSessionService(saver, turn_graph=rt.graph)
        recovered = client.get(
            "/api/ai/worldline/sessions/" + created["session_id"]
        ).json()
        assert recovered == {k: v for k, v in explored.items() if k != "outcome"} | {
            "outcome": None
        }
        assert (
            client.post(
                "/api/ai/worldline/explore",
                json={"session_id": created["session_id"], "branch_id": branch["id"]},
            ).status_code
            == 409
        )
        assert (
            client.get("/api/ai/worldline/sessions/missing-session").status_code == 404
        )
    assert all(len(results) == 2 for results in rt.calls.values())
    assert all(len(results[0].relations) == 1 for results in rt.calls.values())
    assert all(
        results[1].relations == [] for results in rt.calls.values()
    )  # Ordinary exploration is isolated.
    assert len(rt.adapter.observations[0]["context_pack"]["items"]) == 4
    assert {
        e["knowledge_entity_id"]
        for e in rt.adapter.observations[0]["search_plan"]["entities"]
    } == {"artist:a", "artist:b", "song:b"}
    if path := os.getenv("WORLDLINE_SMOKE_TRACE_PATH"):
        Path(path).write_text(
            json.dumps(
                {
                    "fixture_kind": "independent_engineering_prose_external_io_doubles",
                    "query": QUERY,
                    "indexed_documents": [
                        d.model_dump(mode="json") for d in rt.prepared
                    ],
                    "retrieval_to_context": rt.adapter.observations,
                    "expansion_traces": {
                        key: [result.model_dump(mode="json") for result in results]
                        for key, results in rt.calls.items()
                    },
                    "api_create": created,
                    "api_explore": explored,
                    "api_recover": recovered,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )


def test_main_router_dispatches_formal_worldline_services():
    rt = runtime()

    class UnusedExternalClassifier:
        def route(self, *args):
            raise AssertionError(
                "The fully covered rule must not invoke a remote classifier"
            )

    router = HybridIntentRouter(UnusedExternalClassifier(), UnusedExternalClassifier())
    graph = build_main_graph(intent_router=router, worldline_subgraph=rt.graph).compile(
        checkpointer=False
    )
    output = asyncio.run(graph.ainvoke(create_main_state("探索歌手《A》的音乐世界线")))
    results = output["task_results"]
    assert len(results) == 1 and results[0].route_key == "worldline"
    assert results[0].status == "success"
    assert all(len(items) == 1 for items in rt.calls.values())
    assert rt.transport.requests


@pytest.mark.parametrize(
    "field", ["source_url", "publisher", "content_sha256", "segments"]
)
def test_index_rejects_changed_document_capture(field):
    child, parent = native_inputs()[0]
    metadata = deepcopy(child.metadata)
    capture = metadata["document_metadata"]["source_context_captures"][0]
    if field == "segments":
        capture[field][0]["start"] += 1
    else:
        capture[field] = (
            "f" * 64 if field.endswith("sha256") else str(capture[field]) + "changed"
        )
    with pytest.raises(ValueError):
        IndexDocumentProjector(_config()).project(
            child.model_copy(update={"metadata": metadata}),
            parent,
            source_release_id="engineering-release-v4",
        )


def test_source_identity_ignores_rank_but_protects_verbatim_context():
    rt = runtime()
    result = asyncio.run(
        WorldLineSessionService(
            create_memory_checkpointer(), turn_graph=rt.graph
        ).create(QUERY)
    )
    source = next(s for s in result.sources if "relation_source_context" in s.metadata)
    reordered = source.model_copy(
        update={
            "metadata": {**source.metadata, "fusion_score": 0.2, "context_pack_rank": 3}
        }
    )
    assert (
        merge_worldline_graph(
            result.graph,
            result.graph,
            result.sources,
            [reordered],
            target_node_id=result.graph.root,
        )[1]
        == result.sources
    )
    changed = deepcopy(source.metadata)
    changed["relation_source_context"]["source_url"] += "/changed"
    with pytest.raises(WorldLineMergeError, match="source_conflict"):
        merge_worldline_graph(
            result.graph,
            result.graph,
            result.sources,
            [source.model_copy(update={"metadata": changed})],
            target_node_id=result.graph.root,
        )


def test_context_pack_missing_or_tampered_capture_fails_closed():
    rt = runtime()
    plan = QueryUnderstandingService(index_profile=profile())
    understanding = asyncio.run(plan.understand(QUERY))
    # Catalog context is normally supplied by Worldline; the standalone QU
    # still yields the same exploratory plan here.
    pool = asyncio.run(
        HybridRetrievalService(
            SimpleNamespace(
                profile=profile(), manifest=SimpleNamespace(projected_count=4)
            ),
            encoder=FakeEncoder(),
            backend=QdrantHybridSearchBackend(
                load_retrieval_config(), client=rt.transport
            ),
        ).retrieve(understanding.search_plan)
    )
    candidate = pool.candidates[0]
    assert candidate.metadata["relation_source_contexts"]
    metadata = deepcopy(candidate.metadata)
    metadata["relation_source_contexts"][0]["source_content_sha256"] = "f" * 64
    invalid = pool.model_copy(
        update={"candidates": [candidate.model_copy(update={"metadata": metadata})]}
    )
    pack = ContextPackBuilder().build(understanding.search_plan, invalid)
    assert pack.items[0].relation_source_contexts == []
    assert any(w.startswith("source_context_invalid:") for w in pack.warnings)
    missing = pool.model_copy(
        update={"candidates": [candidate.model_copy(update={"metadata": {}})]}
    )
    assert (
        ContextPackBuilder()
        .build(understanding.search_plan, missing)
        .items[0]
        .relation_source_contexts
        == []
    )


@pytest.mark.parametrize(
    "condition", ["reverse", "weak", "negation_only", "scene_query"]
)
def test_retrieved_context_does_not_override_runtime_rejection(condition):
    rt = runtime()
    for item in rt.transport.documents[:2]:
        claim = item.payload["supported_claims"][0]
        if condition == "reverse":
            claim["subject_entity_id"], claim["object_entity_id"] = (
                claim["object_entity_id"],
                claim["subject_entity_id"],
            )
        elif condition == "weak":
            claim["extraction_confidence"] = claim["verification_confidence"] = 0.2
    if condition == "negation_only":
        rt.transport.documents[0].payload["supported_claims"] = []
    query = "歌手《A》的音乐场景" if condition == "scene_query" else QUERY
    result = asyncio.run(
        WorldLineSessionService(
            create_memory_checkpointer(), turn_graph=rt.graph
        ).create(query)
    )
    assert result.influence_relations == []
    assert rt.calls["influence"][0].relations == []
    assert rt.adapter.observations[0]["retrieval"]["status"] == "success"


def test_repacked_context_respects_budget_and_duplicate_bindings():
    rt = runtime()
    result = asyncio.run(
        WorldLineSessionService(
            create_memory_checkpointer(), turn_graph=rt.graph
        ).create(QUERY)
    )
    observation = rt.adapter.observations[0]
    from app.rag.retrieval import RetrievalResult
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.context_packing.config import (
        ContextPackingConfig,
        EvidenceCompressionConfig,
    )

    pool = RetrievalResult.model_validate(observation["retrieval"])
    plan = SearchPlan.model_validate(observation["search_plan"])
    candidate = pool.candidates[0]
    supplement = candidate.metadata["relation_source_contexts"][0]
    metadata = deepcopy(candidate.metadata)
    metadata["relation_source_contexts"] = [supplement, supplement]
    duplicated = pool.model_copy(
        update={"candidates": [candidate.model_copy(update={"metadata": metadata})]}
    )
    assert (
        ContextPackBuilder().build(plan, duplicated).items[0].relation_source_contexts
        == []
    )
    config = ContextPackingConfig(
        context_version="v1",
        compression=EvidenceCompressionConfig(
            max_evidence_tokens=50, max_item_tokens=100
        ),
    )
    large = deepcopy(candidate.metadata)
    capture = large["relation_source_contexts"][0]
    capture["content"] += "\n\n" + "archival detail " * 300
    capture["content_sha256"] = sha256(capture["content"].encode()).hexdigest()
    oversized = pool.model_copy(
        update={"candidates": [candidate.model_copy(update={"metadata": large})]}
    )
    pack = ContextPackBuilder(config).build(plan, oversized)
    assert pack.token_count <= pack.token_budget
    assert all(item.token_count <= 100 for item in pack.items)
    assert any(
        w.startswith("source_context_budget_omitted:")
        or w.startswith("source_context_anchor_omitted:")
        for w in pack.warnings
    )
    # A re-retrieved conflict must update an earlier plain edge description.
    graph = result.graph.model_copy(
        update={
            "edges": [
                edge.model_copy(update={"description": "Earlier assertion"})
                for edge in result.graph.edges
            ]
        }
    )
    state = initial_state(QUERY)
    state.update(
        session_mode=True,
        trusted_target=result.root_entity,
        root_entity=result.root_entity,
        prior_graph=graph,
        prior_sources=result.sources,
        prior_era_relations=result.era_relations,
        prior_scene_relations=result.scene_relations,
        prior_influence_relations=result.influence_relations,
    )
    output = asyncio.run(rt.graph.ainvoke(state))
    assert output["task_result"].status == "success", output["task_result"]
    restored = output["task_result"].data["worldline_result"]
    conflict_edge = next(
        e for e in restored["graph"]["edges"] if e["relation"] == "influenced"
    )
    assert conflict_edge["description"] != "Earlier assertion"
    assert restored["influence_relations"][0]["metadata"]["conflict_flags"] == [
        "attribution_conflict"
    ]


def test_retrieval_detects_source_payload_tampering():
    rt = runtime()
    rt.transport.documents[0].payload["content"] += "changed"
    result = asyncio.run(
        WorldLineSessionService(
            create_memory_checkpointer(), turn_graph=rt.graph
        ).create(QUERY)
    )
    assert result.influence_relations == []
    assert (
        "invalid_retrieval_payloads:1"
        in rt.adapter.observations[0]["retrieval"]["warnings"]
    )


def test_ambiguous_catalog_peer_is_not_given_an_invented_knowledge_binding():
    ambiguous = EntityRegistry(
        EntityRegistryDocument(
            registry_version="v3",
            entities=[
                {
                    "entity_id": "artist:a",
                    "entity_type": "artist",
                    "canonical_name": "A",
                },
                {
                    "entity_id": "artist:b",
                    "entity_type": "artist",
                    "canonical_name": "B",
                },
                {
                    "entity_id": "artist:other_b",
                    "entity_type": "artist",
                    "canonical_name": "Other B",
                    "aliases": ["B"],
                },
                {
                    "entity_id": "song:b",
                    "entity_type": "song",
                    "canonical_name": "Song B",
                },
            ],
        )
    )
    rt = runtime(catalog_registry=ambiguous)
    result = asyncio.run(
        WorldLineSessionService(
            create_memory_checkpointer(), turn_graph=rt.graph
        ).create(QUERY)
    )
    assert len(result.era_relations) == 1
    assert result.scene_relations == result.influence_relations == []
    peer = next(
        e
        for e in rt.adapter.observations[0]["search_plan"]["entities"]
        if e["surface"] == "B"
    )
    assert peer["knowledge_entity_id"] is None


def test_parent_retrieved_with_different_children_keeps_both_native_sources():
    from app.agent.foundation.subgraphs.worldline.builder import build_worldline_graph
    from app.agent.foundation.subgraphs.worldline.models import MusicKnowledgeDocument

    catalog = CatalogTransport()
    facts = MusicEntityFacts(entity=catalog.entities[0])

    def doc(child):
        content = "Original indexed child " + child
        return MusicKnowledgeDocument(
            id="same-parent",
            title="Engineering document",
            source="engineering-source",
            url="https://example.com/engineering",
            content="Query view: " + content,
            metadata={
                "source_id": "native-source",
                "chunk_id": child,
                "index_input_hash": "sha256:" + sha256(child.encode()).hexdigest(),
                "source_content_sha256": sha256(content.encode()).hexdigest(),
                "source_evidence_content": content,
            },
        )

    first, second = doc("one"), doc("two")
    old, old_sources = build_worldline_graph(facts, [first])
    new, new_sources = build_worldline_graph(facts, [second])
    _, sources = merge_worldline_graph(
        old, new, old_sources, new_sources, target_node_id=old.root
    )
    assert len(sources) == 2 and sources[0].id != sources[1].id
    assert sources[0].excerpt == first.metadata["source_evidence_content"]
    assert sources[1].excerpt == second.metadata["source_evidence_content"]
