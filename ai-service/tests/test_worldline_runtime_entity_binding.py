"""Controlled native endpoints still pass the real relation validators."""
from copy import deepcopy
from hashlib import sha256
from types import SimpleNamespace
import json

import pytest

from app.agent.foundation.subgraphs.worldline.runtime_entity_binding import VerifiedEndpointMaterializer
from app.agent.foundation.subgraphs.worldline.influence_expansion import InfluenceExpansionService
from app.agent.foundation.subgraphs.worldline.scene_expansion import SceneExpansionService
from app.agent.foundation.subgraphs.worldline.models import WorldLineNode
from app.agent.foundation.subgraphs.worldline.merge import WorldLineMergeError
from app.rag.knowledge.entity_linking.models import EntityRegistryDocument
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import IndexProfile
from tests.test_worldline_influence_expansion import graph, plan, document
from tests.test_query_registry_binding import bound_profile, copy_registry_snapshot, REGISTRY, SNAPSHOT_REGISTRY


def fixture(*, scene=False, missing=False, ambiguous=False):
    profile = IndexProfile(collection_name="production-test", source_release_id="knowledge-test",
                           index_schema_version="v2", projection_version="v4", registry_version="v5")
    records = [dict(entity_id="artist:a", entity_type="artist", canonical_name="A")]
    if not missing:
        records.append(dict(entity_id="artist:b", entity_type="artist", canonical_name="B"))
    if ambiguous:
        records.append(dict(entity_id="artist:b_other", entity_type="artist", canonical_name="B"))
    registry = EntityRegistry(EntityRegistryDocument(registry_version="v5", entities=records))
    p = plan().model_copy(update={"entities": plan().entities[:1], "index_profile": profile})
    g = graph().model_copy(update={"nodes": graph().nodes[:1]})
    d = document(1, "B emerged from the Seattle scene." if scene else "A influenced B.",
                 predicate="emerged_from_scene" if scene else "influenced",
                 subject="artist:b" if scene else "artist:a")
    claim = deepcopy(d.metadata["supported_claims"][0])
    if scene:
        claim["object_entity_id"] = None
        claim["object_value"] = "Seattle scene"
    payload = {
        "point_id": "test-point", "chunk_id": "child-1", "parent_id": d.id, "doc_id": "doc-1",
        "source_id": "source-1", "source_locator": "article > paragraph 1",
        "source_content_sha256": sha256(d.content.encode()).hexdigest(),
        "source_response_sha256": "a" * 64, "source_release_id": profile.source_release_id,
        "knowledge_input_hash": "sha256:" + "b" * 64, "index_input_hash": "sha256:" + "c" * 64,
        "source_published_at": None, "document_metadata": {},
        "source_url": str(d.url), "source_name": d.source, "source_title": d.title,
        "content": d.content, "supported_claims": [claim], "relation_source_contexts": [],
        "entities": [dict(entity_id="artist:a", entity_type="artist", canonical_name="A"),
                     dict(entity_id="artist:b", entity_type="artist", canonical_name="B")],
    }
    metadata = {**d.metadata, **{k: payload[k] for k in (
        "point_id", "chunk_id", "parent_id", "doc_id", "source_id", "source_locator",
        "source_content_sha256", "source_response_sha256", "source_release_id",
        "knowledge_input_hash", "index_input_hash", "source_published_at", "document_metadata")},
        "index_profile": profile.model_dump(mode="json"), "source_evidence_content": d.content,
        "supported_claims": [claim], "relation_source_contexts": []}
    d = d.model_copy(update={"metadata": metadata})
    return VerifiedEndpointMaterializer(registry=registry, profile=profile,
                                        payloads={"child-1": payload}), p, g, d


@pytest.mark.parametrize("scene", [False, True])
def test_missing_verified_endpoint_enters_existing_validator_and_projector(scene):
    binder, p, g, d = fixture(scene=scene)
    materialized = binder.materialize(p, g, [d])
    b = next(node for node in materialized.nodes if node.id == "artist:b")
    assert b.metadata["knowledge_entity_id"] == "artist:b"
    assert b.metadata["executable"] is False
    assert "catalog_reference" not in b.metadata["entity_reference"]
    assert b.metadata["claim_binding"]["exact_span"] == d.metadata["supported_claims"][0]["evidence_span"]
    service = SceneExpansionService() if scene else InfluenceExpansionService()
    result = service.expand("A 的 scene 场景和音乐影响", p, materialized, [], [d])
    assert len(result.relations) == 1
    final = binder.finalize(g, result.graph, result.sources,
                            result.relations if scene else [], [] if scene else result.relations)
    assert "artist:b" in {node.id for node in final.nodes}
    if scene:
        scene_node = next(node for node in final.nodes if node.type.value == "scene")
        assert scene_node.metadata["claim_evidence_bindings"]
        assert scene_node.metadata["knowledge_entity_id"] == scene_node.id
    else:
        relation = result.relations[0]
        assert (relation.source_entity.node_id, relation.target_entity.node_id) == (g.root, "artist:b")
        assert relation.direction.value == "influencer_to_influenced"


@pytest.mark.parametrize("scene", [False, True])
@pytest.mark.parametrize("reason", ["unresolved", "ambiguous", "unsupported"])
def test_untrusted_or_nonunique_endpoint_never_materializes(scene, reason):
    binder, p, g, d = fixture(scene=scene, missing=reason == "unresolved", ambiguous=reason == "ambiguous")
    if reason == "unsupported":
        binder.payloads["child-1"]["supported_claims"] = []
    assert binder.materialize(p, g, [d]) == g


@pytest.mark.parametrize("field", ["source_content_sha256", "source_response_sha256", "source_locator",
                                   "chunk_id", "source_id", "source_release_id", "index_input_hash"])
def test_source_drift_rejects_endpoint(field):
    binder, p, g, d = fixture()
    d = d.model_copy(update={"metadata": {**d.metadata, field: "tampered"}})
    assert binder.materialize(p, g, [d]) == g


@pytest.mark.parametrize("change", ["url", "span", "context", "type", "mention"])
def test_other_tamper_and_plain_mentions_do_not_materialize(change):
    binder, p, g, d = fixture()
    if change == "url":
        d = d.model_copy(update={"url": "https://changed.example/source"})
    else:
        metadata = deepcopy(d.metadata)
        if change == "span":
            metadata["supported_claims"][0]["evidence_span"] = "B influenced A."
        elif change == "context":
            metadata["relation_source_contexts"] = [{"claim_id": metadata["supported_claims"][0]["claim_id"]}]
        elif change == "type":
            binder.payloads["child-1"]["entities"][1]["entity_type"] = "person"
        else:
            metadata["supported_claims"] = []
            metadata["graph_nodes"] = [{"id": "artist:b", "type": "artist", "name": "B"}]
        d = d.model_copy(update={"metadata": metadata})
    assert binder.materialize(p, g, [d]) == g


def test_capacity_fails_atomically_and_validator_rejection_prunes_temporary_node():
    binder, p, g, d = fixture()
    full = g.model_copy(update={"nodes": [*g.nodes, *[
        WorldLineNode(id=f"netease:song:{i}", type="song", name=f"Song {i}") for i in range(1, 15)]]})
    with pytest.raises(WorldLineMergeError, match="capacity_reached"):
        binder.materialize(p, full, [d])
    assert len(full.nodes) == 15
    materialized = binder.materialize(p, g, [d])
    assert binder.finalize(g, materialized, [], [], []) == g


def test_projection_tamper_blocks_materializer_initialization(tmp_path):
    path = tmp_path / "projection.jsonl"
    path.write_text("changed", encoding="utf-8")
    binding = SimpleNamespace(manifest=SimpleNamespace(
        projection_path="projection.jsonl", projection_fingerprint="sha256:" + "0" * 64))
    with pytest.raises(ValueError, match="projection_hash_mismatch"):
        VerifiedEndpointMaterializer.from_binding(tmp_path, binding)


@pytest.mark.parametrize("tampered", [False, True])
@pytest.mark.parametrize("registry_directory", [REGISTRY, SNAPSHOT_REGISTRY])
def test_materializer_verifies_registry_bytes_before_loading(bound_profile, monkeypatch, tampered, registry_directory):
    root, selected = bound_profile
    if registry_directory == SNAPSHOT_REGISTRY:
        selected = copy_registry_snapshot(root)
    projection = root / "projection.jsonl"
    projection.write_text("", encoding="utf-8")
    release_path = root / "release.json"
    release_path.write_text(json.dumps({"source": {"entity_registry_files": [
        {"path": path, "sha256": digest} for path, digest in selected.registry_file_hashes.items()
    ]}}), encoding="utf-8")
    manifest_path = root / "index_build_manifest.json"
    manifest_path.write_text("{}", encoding="utf-8")
    manifest = SimpleNamespace(
        projection_path="projection.jsonl", projection_fingerprint="sha256:" + sha256(b"").hexdigest(),
        source_path="knowledge.jsonl", source_release_manifest_path="release.json",
        source_release_id=selected.source_release_id, collection_name=selected.collection_name,
        source_release_manifest_fingerprint="sha256:" + sha256(release_path.read_bytes()).hexdigest(),
        source_fingerprint="sha256:" + "a" * 64,
    )
    release = SimpleNamespace(
        release_id=manifest.source_release_id,
        manifest_fingerprint=manifest.source_release_manifest_fingerprint,
        knowledge_fingerprint=manifest.source_fingerprint,
    )
    monkeypatch.setattr(
        "app.agent.foundation.subgraphs.worldline.runtime_entity_binding.KnowledgeReleaseValidator",
        lambda *args, **kwargs: SimpleNamespace(validate=lambda: release),
    )
    (root / "ready_index_approval.json").write_text(json.dumps({
        "status": "approved", "manifest_sha256": "sha256:" + sha256(manifest_path.read_bytes()).hexdigest(),
        "collection_name": manifest.collection_name, "source_release_id": manifest.source_release_id,
        "projection_fingerprint": manifest.projection_fingerprint,
    }), encoding="utf-8")
    binding = SimpleNamespace(manifest=manifest, manifest_path=manifest_path, profile=selected)
    if tampered:
        target = root / registry_directory / "v2.yaml"
        target.write_bytes(target.read_bytes() + b"\n# changed\n")
        with pytest.raises(ValueError, match="registry_hash_mismatch"):
            VerifiedEndpointMaterializer.from_binding(root, binding)
    else:
        materializer = VerifiedEndpointMaterializer.from_binding(root, binding)
        assert materializer.registry.get("artist:jay_chou") is not None


def test_conflicting_existing_identity_rejects_claim_atomically():
    binder, p, g, d = fixture()
    metadata = {"entity_reference": {"knowledge_entity_id": "artist:a"}}
    g = g.model_copy(update={"nodes": [g.nodes[0].model_copy(update={"metadata": metadata}),
                                      WorldLineNode(id="netease:artist:99", type="artist", name="A", metadata=metadata)]})
    assert binder.materialize(p, g, [d]) == g


def test_cleanup_preserves_endpoint_used_by_an_existing_typed_projector():
    from app.agent.foundation.subgraphs.worldline.models import WorldLineEdge
    binder, p, g, d = fixture()
    materialized = binder.materialize(p, g, [d])
    projected = materialized.model_copy(update={
        "nodes": [*materialized.nodes, WorldLineNode(id="era:1990s", type="era", name="1990s")],
        "edges": [WorldLineEdge(source="artist:b", target="era:1990s", relation="career_phase")],
    })
    assert binder.finalize(g, projected, [], [], []) == projected


def test_production_build_graph_hook_materializes_before_relation_validation(monkeypatch):
    from app.agent.foundation.subgraphs.worldline import graph as graph_module
    from app.agent.foundation.subgraphs.worldline.models import MusicEntityFacts, ResolvedEntity
    binder, p, g, d = fixture()
    retriever = SimpleNamespace(candidate_provider=SimpleNamespace(binding=object()))
    monkeypatch.setattr(graph_module, "get_worldline_retrieval_runtime",
                        lambda: SimpleNamespace(retriever=retriever, index_profile=None))
    monkeypatch.setattr(VerifiedEndpointMaterializer, "from_binding", lambda *args: binder)
    compiled = graph_module.create_worldline_subgraph()
    root = ResolvedEntity(entity_id=g.root, entity_type="artist", name="A", source="netease")
    result = compiled.nodes["build_worldline_graph"].bound.func({
        "entity_facts": MusicEntityFacts(entity=root), "resolved_entity": root,
        "retrieved_docs": [d], "search_plan": p, "user_query": "A 的音乐影响", "task_id": "binding-test",
    }, {})
    assert len(result["influence_relations"]) == 1
    assert "artist:b" in {node.id for node in result["worldline_graph"].nodes}
