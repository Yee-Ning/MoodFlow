"""Materialize registry endpoints only from the selected approved native index.

These are knowledge identities, never invented executable catalog identities.
Relation decisions remain exclusively with the existing validators/projectors.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from app.rag.indexing.models import PreparedIndexDocument
from app.rag.indexing.release import KnowledgeReleaseValidator
from app.rag.knowledge.entity_linking.registry import EntityRegistry, DEFAULT_ENTITY_REGISTRY_PATH
from app.rag.knowledge.qa import sha256_file
from app.rag.retrieval.binding import RetrievalIndexBinding
from app.rag.source_context import relation_provenance, source_context
from .builder import MAX_WORLDLINE_NODES
from .era_expansion import _knowledge_entity_map
from .influence_expansion import _PREDICATES
from .scene_expansion import _PREDICATE_TO_RELATION
from .merge import WorldLineMergeError
from .models import EraEntityReference, WorldLineNode, WorldLineNodeType


_IDENTITY_FIELDS = (
    "point_id", "chunk_id", "parent_id", "doc_id", "source_id",
    "source_locator", "source_content_sha256", "source_response_sha256",
    "source_release_id", "knowledge_input_hash", "index_input_hash",
    "source_published_at", "document_metadata",
)
_LEGAL_TYPES = {"artist", "song", "album", "genre"}


class VerifiedEndpointMaterializer:
    def __init__(self, *, registry, profile, payloads):
        self.registry = registry
        self.profile = profile
        self.payloads = payloads

    @classmethod
    def from_binding(cls, root: Path, binding: RetrievalIndexBinding):
        root = root.resolve()
        manifest = binding.manifest

        def within(relative):
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("runtime_binding_path_outside_project")
            return path

        projection = within(manifest.projection_path)
        if sha256_file(projection) != manifest.projection_fingerprint:
            raise ValueError("runtime_binding_projection_hash_mismatch")
        release = KnowledgeReleaseValidator(
            root, knowledge_path=within(manifest.source_path),
            manifest_path=within(manifest.source_release_manifest_path),
        ).validate()
        if (release.release_id != manifest.source_release_id
                or release.manifest_fingerprint != manifest.source_release_manifest_fingerprint
                or release.knowledge_fingerprint != manifest.source_fingerprint):
            raise ValueError("runtime_binding_release_mismatch")
        approval = json.loads(binding.manifest_path.with_name(
            "ready_index_approval.json").read_text(encoding="utf-8"))
        expected = {
            "status": "approved", "manifest_sha256": sha256_file(binding.manifest_path),
            "collection_name": manifest.collection_name,
            "source_release_id": manifest.source_release_id,
            "projection_fingerprint": manifest.projection_fingerprint,
        }
        if any(approval.get(key) != value for key, value in expected.items()):
            raise ValueError("runtime_binding_index_not_approved")
        registry = EntityRegistry.load(DEFAULT_ENTITY_REGISTRY_PATH.with_name(
            binding.profile.registry_version + ".yaml"))
        rows = [PreparedIndexDocument.model_validate_json(line)
                for line in projection.read_text(encoding="utf-8").splitlines() if line.strip()]
        payloads = {row.chunk_id: row.payload for row in rows}
        if len(payloads) != len(rows):
            raise ValueError("runtime_binding_duplicate_chunk")
        return cls(registry=registry, profile=binding.profile, payloads=payloads)

    def _trusted(self, document, claim):
        metadata = document.metadata
        payload = self.payloads.get(metadata.get("chunk_id"))
        if not payload or metadata.get("index_profile") != self.profile.model_dump(mode="json"):
            return None
        # Only release-gated SUPPORTED records enter the immutable projection list.
        # No confidence threshold or caller-supplied 'verified' flag grants trust.
        if claim not in payload.get("supported_claims", []):
            return None
        if any(metadata.get(key) != payload.get(key) for key in _IDENTITY_FIELDS):
            return None
        original = metadata.get("source_evidence_content")
        span = claim.get("evidence_span")
        title = payload.get("source_title") or payload.get("breadcrumb") or payload.get("source_name")
        if (document.id != payload.get("parent_id")
                or str(document.url or "") != payload.get("source_url")
                or document.source != payload.get("source_name") or document.title != title
                or claim.get("source_chunk_id") != payload.get("chunk_id")
                or not isinstance(original, str) or original != payload.get("content")
                or sha256(original.encode()).hexdigest() != payload.get("source_content_sha256")
                or not isinstance(span, str) or not span
                or original.count(span) != 1 or document.content.count(span) != 1):
            return None
        contexts = metadata.get("relation_source_contexts", [])
        if contexts != payload.get("relation_source_contexts", []):
            return None
        if any(item.get("claim_id") == claim.get("claim_id") for item in contexts):
            if source_context(document, claim["claim_id"], span) is None:
                return None
        # Source identity fields are required, not merely equal when both missing.
        if any(not payload.get(key) for key in (
            "source_id", "source_url", "source_name", "source_locator",
            "source_content_sha256", "source_response_sha256", "source_release_id",
            "knowledge_input_hash", "index_input_hash",
        )):
            return None
        return payload

    def _endpoint(self, entity_id, payload, plan):
        entity = self.registry.get(entity_id)
        if entity is None or entity.entity_type.value not in _LEGAL_TYPES:
            return None
        # Reject canonical collisions even when a type hint would hide ambiguity.
        resolution = self.registry.resolve(entity.canonical_name)
        if resolution.entity is None or resolution.entity.entity_id != entity_id:
            return None
        linked = [item for item in payload.get("entities", [])
                  if item.get("entity_id") == entity_id]
        if (len(linked) != 1 or linked[0].get("entity_type") != entity.entity_type.value
                or linked[0].get("canonical_name") != entity.canonical_name):
            return None
        for query_entity in plan.entities:
            if (query_entity.knowledge_entity_id == entity_id
                    and query_entity.status.value != "resolved"):
                return None
        return entity

    def materialize(self, plan, graph, documents):
        if plan.index_profile != self.profile:
            return graph
        nodes = {node.id: node for node in graph.nodes}
        mapping = _knowledge_entity_map(graph, plan)
        bindings = {}
        for node in graph.nodes:
            identity = node.metadata.get("entity_reference", {}).get("knowledge_entity_id")
            if identity:
                bindings.setdefault(identity, set()).add(node.id)
        for entity in plan.entities:
            catalog = entity.catalog_reference
            if catalog and entity.knowledge_entity_id:
                node_id = f"{catalog.provider}:{catalog.entity_type.value}:{catalog.entity_id}"
                if node_id in nodes:
                    bindings.setdefault(entity.knowledge_entity_id, set()).add(node_id)
        for document in documents:
            for claim in document.metadata.get("supported_claims", []):
                if not isinstance(claim, dict):
                    continue
                predicate = claim.get("predicate")
                if predicate not in _PREDICATES and predicate not in _PREDICATE_TO_RELATION:
                    continue
                payload = self._trusted(document, claim)
                if payload is None:
                    continue
                ids = [claim.get("subject_entity_id")]
                if predicate in _PREDICATES or predicate == "scene_association":
                    ids.append(claim.get("object_entity_id"))
                endpoints = [self._endpoint(identity, payload, plan) for identity in ids]
                if any(endpoint is None for endpoint in endpoints):
                    continue
                if any(len(bindings.get(identity, set())) > 1 for identity in ids):
                    continue
                if predicate in _PREDICATE_TO_RELATION and predicate != "style_scene_association":
                    if any(endpoint.entity_type.value == "genre" for endpoint in endpoints):
                        continue
                pending = {}
                for endpoint in endpoints:
                    identity = endpoint.entity_id
                    mapped = mapping.get(identity)
                    kind = WorldLineNodeType(endpoint.entity_type.value)
                    if mapped is not None:
                        if mapped.node_type != kind:
                            pending = None
                            break
                        continue
                    if identity in nodes:
                        pending = None  # Unbound preexisting node cannot acquire an identity by name.
                        break
                    reference = EraEntityReference(
                        node_id=identity, node_type=kind, canonical_name=endpoint.canonical_name,
                        knowledge_entity_id=identity,
                    )
                    pending[identity] = WorldLineNode(
                        id=identity, type=kind, name=endpoint.canonical_name,
                        metadata={
                            "canonical_id": identity, "entity_type": kind.value,
                            "knowledge_entity_id": identity,
                            "entity_reference": reference.model_dump(mode="json"),
                            "identity_kind": "verified_registry_endpoint",
                            "registry_version": self.registry.version,
                            "executable": False, "expandable": False,
                            "claim_binding": {
                                "claim_id": claim["claim_id"], "document_id": document.id,
                                "chunk_id": claim["source_chunk_id"], "exact_span": claim["evidence_span"],
                                **relation_provenance(document, claim["claim_id"], claim["evidence_span"]),
                                "source_url": str(document.url), "publisher": document.source,
                                "title": document.title,
                                "source_response_sha256": payload["source_response_sha256"],
                            },
                        },
                    )
                if pending is None:
                    continue
                if len(nodes) + len(pending) > MAX_WORLDLINE_NODES:
                    raise WorldLineMergeError("capacity_reached")
                nodes.update(pending)
                for node in pending.values():
                    mapping[node.id] = EraEntityReference.model_validate(node.metadata["entity_reference"])
        return graph.model_copy(update={"nodes": list(nodes.values())})

    @staticmethod
    def finalize(original, graph, sources, scene_relations, influence_relations):
        original_ids = {node.id for node in original.nodes}
        native_ids = set()
        scene_bindings = {}
        source_map = {source.id: source for source in sources}
        for relation in [*scene_relations, *influence_relations]:
            native_ids.add(relation.source_entity.node_id)
            target = relation.target_entity or getattr(relation, "scene", None)
            if target is not None:
                native_ids.add(target.node_id)
                if getattr(relation, "scene", None) is not None:
                    scene_bindings[target.node_id] = [
                        source_map[item.source_id].model_dump(mode="json")
                        for item in relation.evidence if item.source_id in source_map
                    ]
        nodes = []
        for node in graph.nodes:
            if (node.id not in original_ids
                    and node.metadata.get("identity_kind") == "verified_registry_endpoint"
                    and node.id not in native_ids):
                continue
            if node.id not in original_ids and node.id in scene_bindings:
                node = node.model_copy(update={"metadata": {
                    **node.metadata, "canonical_id": node.id, "entity_type": node.type.value,
                    "knowledge_entity_id": node.id,
                    "identity_kind": "validated_native_scene_descriptor",
                    "claim_evidence_bindings": scene_bindings[node.id],
                }})
            nodes.append(node)
        kept = {node.id for node in nodes}
        return graph.model_copy(update={"nodes": nodes, "edges": [
            edge for edge in graph.edges if edge.source in kept and edge.target in kept
        ]})
