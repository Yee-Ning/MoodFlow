"""Evidence-gated artist-to-genre projection for WorldLine V2.2."""

from __future__ import annotations

from collections import Counter
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, ValidationError

from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.rag.knowledge.entity_linking.models import (
    EntityType as KnowledgeEntityType,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import SearchPlan


SEMANTIC_EXPANSION_VERSION = "v1"
SEMANTIC_VALIDATOR_VERSION = "v1"
SEMANTIC_ACCEPTANCE_THRESHOLD = 0.85
MAX_RELATION_EVIDENCE = 8


class SemanticRelationStatus(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class SemanticRelationEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1, max_length=128)
    parent_id: str = Field(min_length=1, max_length=200)
    child_id: str = Field(min_length=1, max_length=200)
    claim_id: str = Field(min_length=1, max_length=128)
    source_chunk_id: str = Field(min_length=1, max_length=200)
    exact_span: str = Field(min_length=1, max_length=2_000)
    source_name: str = Field(min_length=1, max_length=200)
    source_url: HttpUrl | None = None
    context_version: str = Field(min_length=1, max_length=100)
    retrieval_version: str | None = Field(default=None, max_length=100)
    extraction_confidence: float = Field(ge=0, le=1)
    verification_confidence: float = Field(ge=0, le=1)


class SemanticRelationCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_catalog_id: str = Field(min_length=1, max_length=128)
    source_knowledge_id: str = Field(min_length=1, max_length=128)
    target_knowledge_id: str = Field(min_length=1, max_length=128)
    target_name: str = Field(min_length=1, max_length=500)
    relation: Literal[WorldLineRelation.ASSOCIATED_WITH] = (
        WorldLineRelation.ASSOCIATED_WITH
    )
    evidence: SemanticRelationEvidence
    confidence: float = Field(ge=0, le=1)


class SemanticRelationDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: SemanticRelationCandidate
    status: SemanticRelationStatus
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class ValidatedSemanticRelation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=128)
    target_name: str = Field(min_length=1, max_length=500)
    relation: Literal[WorldLineRelation.ASSOCIATED_WITH] = (
        WorldLineRelation.ASSOCIATED_WITH
    )
    evidence: list[SemanticRelationEvidence] = Field(
        min_length=1,
        max_length=MAX_RELATION_EVIDENCE,
    )
    confidence: float = Field(ge=0, le=1)


class SemanticExpansionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    graph: WorldLineGraph
    sources: list[EvidenceSource] = Field(max_length=30)
    accepted: list[ValidatedSemanticRelation] = Field(default_factory=list)
    rejected: list[SemanticRelationDecision] = Field(default_factory=list)


class _SupportedClaim(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    claim_id: str = Field(min_length=1, max_length=128)
    subject_entity_id: str = Field(min_length=1, max_length=128)
    predicate: str = Field(min_length=1, max_length=100)
    object_entity_id: str | None = Field(default=None, max_length=128)
    source_chunk_id: str = Field(min_length=1, max_length=200)
    evidence_span: str = Field(min_length=1, max_length=5_000)
    extraction_confidence: float = Field(ge=0, le=1)
    verification_confidence: float = Field(ge=0, le=1)


def _metadata_string(document: MusicKnowledgeDocument, key: str) -> str | None:
    value = document.metadata.get(key)
    return value if isinstance(value, str) and value else None


def _artist_knowledge_id(
    focus_artist: ResolvedEntity,
    search_plan: SearchPlan,
) -> str | None:
    if focus_artist.entity_type is not EntityType.ARTIST:
        return None
    parts = focus_artist.entity_id.split(":")
    if (
        len(parts) != 3
        or parts[0] != focus_artist.source
        or parts[1] != EntityType.ARTIST.value
        or not parts[2]
    ):
        return None
    catalog_id = parts[2]
    matches = []
    for entity in search_plan.entities:
        reference = entity.catalog_reference
        if (
            entity.entity_type is KnowledgeEntityType.ARTIST
            and entity.knowledge_entity_id is not None
            and reference is not None
            and reference.provider == focus_artist.source
            and reference.entity_id == catalog_id
            and reference.entity_type is KnowledgeEntityType.ARTIST
        ):
            matches.append(entity.knowledge_entity_id)
    unique = list(dict.fromkeys(matches))
    return unique[0] if len(unique) == 1 else None


class RelationCandidateExtractor:
    def __init__(self, registry: EntityRegistry) -> None:
        self.registry = registry

    def extract(
        self,
        focus_artist: ResolvedEntity,
        search_plan: SearchPlan,
        documents: list[MusicKnowledgeDocument],
    ) -> list[SemanticRelationCandidate]:
        artist_knowledge_id = _artist_knowledge_id(focus_artist, search_plan)
        if artist_knowledge_id is None:
            return []
        candidates: list[SemanticRelationCandidate] = []
        for document in documents:
            raw_claims = document.metadata.get("supported_claims")
            if not isinstance(raw_claims, list):
                continue
            parent_id = _metadata_string(document, "parent_id")
            child_id = _metadata_string(document, "chunk_id")
            context_version = _metadata_string(document, "context_version")
            if parent_id is None or child_id is None or context_version is None:
                continue
            for raw_claim in raw_claims:
                if not isinstance(raw_claim, dict):
                    continue
                try:
                    claim = _SupportedClaim.model_validate(raw_claim)
                except ValidationError:
                    continue
                if (
                    claim.predicate != "has_genre"
                    or claim.subject_entity_id != artist_knowledge_id
                    or claim.object_entity_id is None
                    or not claim.object_entity_id.startswith("genre:")
                ):
                    continue
                genre = self.registry.get(claim.object_entity_id)
                if (
                    genre is None
                    or genre.entity_type is not KnowledgeEntityType.GENRE
                ):
                    continue
                try:
                    evidence = SemanticRelationEvidence(
                        document_id=document.id,
                        parent_id=parent_id,
                        child_id=child_id,
                        claim_id=claim.claim_id,
                        source_chunk_id=claim.source_chunk_id,
                        exact_span=claim.evidence_span,
                        source_name=document.source,
                        source_url=document.url,
                        context_version=context_version,
                        retrieval_version=_metadata_string(
                            document,
                            "retrieval_version",
                        ),
                        extraction_confidence=claim.extraction_confidence,
                        verification_confidence=claim.verification_confidence,
                    )
                except ValidationError:
                    continue
                candidates.append(
                    SemanticRelationCandidate(
                        source_catalog_id=focus_artist.entity_id,
                        source_knowledge_id=artist_knowledge_id,
                        target_knowledge_id=genre.entity_id,
                        target_name=genre.canonical_name,
                        evidence=evidence,
                        confidence=min(
                            claim.extraction_confidence,
                            claim.verification_confidence,
                        ),
                    )
                )
        return candidates


class RelationValidator:
    def __init__(
        self,
        registry: EntityRegistry,
        *,
        threshold: float = SEMANTIC_ACCEPTANCE_THRESHOLD,
    ) -> None:
        self.registry = registry
        self.threshold = threshold

    def validate(
        self,
        focus_artist: ResolvedEntity,
        search_plan: SearchPlan,
        documents: list[MusicKnowledgeDocument],
        candidates: list[SemanticRelationCandidate],
    ) -> tuple[list[ValidatedSemanticRelation], list[SemanticRelationDecision]]:
        artist_knowledge_id = _artist_knowledge_id(focus_artist, search_plan)
        document_counts = Counter(item.id for item in documents)
        document_by_id = {item.id: item for item in documents}
        decisions: list[SemanticRelationDecision] = []
        accepted_candidates: list[SemanticRelationCandidate] = []
        for candidate in candidates:
            reasons = self._rejection_reasons(
                focus_artist,
                artist_knowledge_id,
                document_counts,
                document_by_id,
                candidate,
            )
            status = (
                SemanticRelationStatus.REJECTED
                if reasons
                else SemanticRelationStatus.ACCEPTED
            )
            decisions.append(
                SemanticRelationDecision(
                    candidate=candidate,
                    status=status,
                    rejection_reasons=reasons,
                )
            )
            if not reasons:
                accepted_candidates.append(candidate)
        return self._deduplicate(accepted_candidates), decisions

    def _rejection_reasons(
        self,
        focus_artist: ResolvedEntity,
        artist_knowledge_id: str | None,
        document_counts: Counter[str],
        document_by_id: dict[str, MusicKnowledgeDocument],
        candidate: SemanticRelationCandidate,
    ) -> list[str]:
        reasons: list[str] = []
        if focus_artist.entity_type is not EntityType.ARTIST:
            reasons.append("unsupported_focus_type")
        if (
            artist_knowledge_id is None
            or candidate.source_catalog_id != focus_artist.entity_id
            or candidate.source_knowledge_id != artist_knowledge_id
        ):
            reasons.append("identity_mapping_missing")
        if candidate.relation is not WorldLineRelation.ASSOCIATED_WITH:
            reasons.append("predicate_not_allowed")
        genre = self.registry.get(candidate.target_knowledge_id)
        if (
            genre is None
            or genre.entity_type is not KnowledgeEntityType.GENRE
            or genre.canonical_name != candidate.target_name
        ):
            reasons.append("unknown_genre_entity")
        evidence = candidate.evidence
        document = document_by_id.get(evidence.document_id)
        if document_counts[evidence.document_id] != 1 or document is None:
            reasons.append("evidence_document_missing")
        else:
            if document.metadata.get("sufficiency_status") != "sufficient":
                reasons.append("insufficient_context")
            if (
                document.id != evidence.parent_id
                or document.metadata.get("parent_id") != evidence.parent_id
            ):
                reasons.append("evidence_parent_mismatch")
            if (
                document.metadata.get("chunk_id") != evidence.child_id
                or evidence.source_chunk_id != evidence.child_id
            ):
                reasons.append("evidence_chunk_mismatch")
            if evidence.exact_span not in document.content:
                reasons.append("evidence_span_missing")
            if not self._claim_is_present(document, candidate):
                reasons.append("claim_not_in_document")
        if candidate.confidence < self.threshold:
            reasons.append("confidence_below_threshold")
        return list(dict.fromkeys(reasons))

    @staticmethod
    def _claim_is_present(
        document: MusicKnowledgeDocument,
        candidate: SemanticRelationCandidate,
    ) -> bool:
        raw_claims = document.metadata.get("supported_claims")
        if not isinstance(raw_claims, list):
            return False
        evidence = candidate.evidence
        for raw_claim in raw_claims:
            if not isinstance(raw_claim, dict):
                continue
            try:
                claim = _SupportedClaim.model_validate(raw_claim)
            except ValidationError:
                continue
            if (
                claim.claim_id == evidence.claim_id
                and claim.subject_entity_id == candidate.source_knowledge_id
                and claim.predicate == "has_genre"
                and claim.object_entity_id == candidate.target_knowledge_id
                and claim.source_chunk_id == evidence.source_chunk_id
                and claim.evidence_span == evidence.exact_span
                and claim.extraction_confidence
                == evidence.extraction_confidence
                and claim.verification_confidence
                == evidence.verification_confidence
            ):
                return True
        return False

    @staticmethod
    def _deduplicate(
        candidates: list[SemanticRelationCandidate],
    ) -> list[ValidatedSemanticRelation]:
        grouped: dict[
            tuple[str, str, WorldLineRelation],
            dict[tuple[str, str, str], SemanticRelationEvidence],
        ] = {}
        names: dict[tuple[str, str, WorldLineRelation], str] = {}
        confidences: dict[tuple[str, str, WorldLineRelation], float] = {}
        for candidate in candidates:
            key = (
                candidate.source_catalog_id,
                candidate.target_knowledge_id,
                candidate.relation,
            )
            evidence_key = (
                candidate.evidence.claim_id,
                candidate.evidence.source_chunk_id,
                sha256(candidate.evidence.exact_span.encode("utf-8")).hexdigest(),
            )
            grouped.setdefault(key, {}).setdefault(
                evidence_key,
                candidate.evidence,
            )
            names[key] = candidate.target_name
            confidences[key] = min(
                confidences.get(key, candidate.confidence),
                candidate.confidence,
            )
        relations = []
        for key, evidence_by_key in grouped.items():
            evidence = list(evidence_by_key.values())
            if len(evidence) > MAX_RELATION_EVIDENCE:
                raise WorldLineMergeError("capacity_reached")
            relations.append(
                ValidatedSemanticRelation(
                    source_id=key[0],
                    target_id=key[1],
                    target_name=names[key],
                    evidence=evidence,
                    confidence=confidences[key],
                )
            )
        return relations


class GraphProjector:
    def __init__(self, registry_version: str) -> None:
        self.registry_version = registry_version

    def project(
        self,
        focus_artist: ResolvedEntity,
        directory_graph: WorldLineGraph,
        directory_sources: list[EvidenceSource],
        relations: list[ValidatedSemanticRelation],
    ) -> tuple[WorldLineGraph, list[EvidenceSource]]:
        if not relations:
            return directory_graph, directory_sources
        artist_node = next(
            (
                node
                for node in directory_graph.nodes
                if node.id == focus_artist.entity_id
            ),
            None,
        )
        if artist_node is None or artist_node.type is not WorldLineNodeType.ARTIST:
            raise WorldLineMergeError("invalid_expansion_root")
        delta_nodes = [artist_node]
        delta_edges: list[WorldLineEdge] = []
        delta_sources: list[EvidenceSource] = []
        seen_nodes = {artist_node.id}
        for relation in relations:
            if relation.source_id != artist_node.id:
                raise WorldLineMergeError("invalid_expansion_root")
            if relation.target_id not in seen_nodes:
                existing = next(
                    (
                        node
                        for node in directory_graph.nodes
                        if node.id == relation.target_id
                    ),
                    None,
                )
                if existing is not None and (
                    existing.type is not WorldLineNodeType.GENRE
                    or existing.name != relation.target_name
                ):
                    raise WorldLineMergeError("node_identity_conflict")
                delta_nodes.append(
                    existing
                    or WorldLineNode(
                        id=relation.target_id,
                        type=WorldLineNodeType.GENRE,
                        name=relation.target_name,
                        description=(
                            f"由证据验证的 {focus_artist.name} 关联音乐类型"
                        ),
                        metadata={
                            "registry_version": self.registry_version,
                            "semantic_expansion_version": (
                                SEMANTIC_EXPANSION_VERSION
                            ),
                        },
                    )
                )
                seen_nodes.add(relation.target_id)
            evidence_ids = []
            for evidence in relation.evidence:
                source = self._evidence_source(
                    focus_artist,
                    relation,
                    evidence,
                )
                delta_sources.append(source)
                evidence_ids.append(source.id)
            delta_edges.append(
                WorldLineEdge(
                    source=relation.source_id,
                    target=relation.target_id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    description=(
                        f"{focus_artist.name} 与 {relation.target_name} 风格相关联"
                    ),
                    evidence_ids=evidence_ids,
                )
            )
        delta = WorldLineGraph(
            root=artist_node.id,
            nodes=delta_nodes,
            edges=delta_edges,
        )
        return merge_worldline_graph(
            directory_graph,
            delta,
            directory_sources,
            delta_sources,
            target_node_id=artist_node.id,
        )

    def _evidence_source(
        self,
        focus_artist: ResolvedEntity,
        relation: ValidatedSemanticRelation,
        evidence: SemanticRelationEvidence,
    ) -> EvidenceSource:
        material = {
            "parent_id": evidence.parent_id,
            "child_id": evidence.child_id,
            "claim_id": evidence.claim_id,
            "exact_span": evidence.exact_span,
        }
        digest = sha256(
            json.dumps(
                material,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()[:24]
        return EvidenceSource(
            id=f"knowledge:rel:{digest}",
            source_type=EvidenceType.KNOWLEDGE,
            title=f"{focus_artist.name} 与 {relation.target_name} 的风格关系"[
                :500
            ],
            source=evidence.source_name,
            url=evidence.source_url,
            excerpt=evidence.exact_span,
            metadata={
                "document_id": evidence.document_id,
                "parent_id": evidence.parent_id,
                "child_id": evidence.child_id,
                "claim_id": evidence.claim_id,
                "source_chunk_id": evidence.source_chunk_id,
                "context_version": evidence.context_version,
                "retrieval_version": evidence.retrieval_version,
                "relation_key": (
                    f"{relation.source_id}|{relation.target_id}|"
                    f"{WorldLineRelation.ASSOCIATED_WITH.value}"
                ),
                "semantic_expansion_version": SEMANTIC_EXPANSION_VERSION,
                "validator_version": SEMANTIC_VALIDATOR_VERSION,
                "confidence": relation.confidence,
            },
        )


class SemanticExpansionService:
    def __init__(self, registry: EntityRegistry | None = None) -> None:
        self.registry = registry or EntityRegistry.load()
        self.extractor = RelationCandidateExtractor(self.registry)
        self.validator = RelationValidator(self.registry)
        self.projector = GraphProjector(self.registry.version)

    def expand_artist_genres(
        self,
        focus_artist: ResolvedEntity,
        search_plan: SearchPlan,
        directory_graph: WorldLineGraph,
        directory_sources: list[EvidenceSource],
        documents: list[MusicKnowledgeDocument],
    ) -> SemanticExpansionResult:
        candidates = self.extractor.extract(
            focus_artist,
            search_plan,
            documents,
        )
        accepted, decisions = self.validator.validate(
            focus_artist,
            search_plan,
            documents,
            candidates,
        )
        graph, sources = self.projector.project(
            focus_artist,
            directory_graph,
            directory_sources,
            accepted,
        )
        return SemanticExpansionResult(
            graph=graph,
            sources=sources,
            accepted=accepted,
            rejected=[
                item
                for item in decisions
                if item.status is SemanticRelationStatus.REJECTED
            ],
        )
