"""Evidence-first execution from a controlled genre to catalog artists."""

from __future__ import annotations

from collections import Counter
from hashlib import sha256
import json
import logging
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.agent.foundation.subgraphs.worldline.entity_bridge import (
    CatalogResolutionStatus,
    EntityBridge,
    WorldLineEntityReference,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
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
from app.agent.foundation.subgraphs.worldline.music_service import (
    JavaMusicEntityService,
    WorldLineMusicService,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    MusicKnowledgeRetriever,
    get_worldline_retrieval_runtime,
)
from app.agent.foundation.subgraphs.worldline.semantic_expansion import (
    SEMANTIC_ACCEPTANCE_THRESHOLD,
    SemanticRelationEvidence,
    SemanticRelationStatus,
)
from app.rag.knowledge.entity_linking.models import (
    EntityType as KnowledgeEntityType,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import (
    ContextResolutionStatus,
    QueryEntityStatus,
    QueryType,
    ResolvedQueryEntity,
    SearchPlan,
)
from app.rag.query_understanding.service import QueryUnderstandingService


GENRE_EXECUTOR_VERSION = "v1"
GENRE_EXECUTOR_VALIDATOR_VERSION = "v1"
MAX_PROJECTED_ARTISTS = 3
MAX_RELATION_EVIDENCE = 8
LOGGER = logging.getLogger(__name__)


class GenreExecutorError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GenreSearchPlanner(Protocol):
    async def build(self, genre: WorldLineEntityReference) -> SearchPlan: ...


class GenreSearchPlanFactory:
    """Build a normal SearchPlan around one trusted registry genre."""

    def __init__(self, service: QueryUnderstandingService) -> None:
        self.service = service

    async def build(self, genre: WorldLineEntityReference) -> SearchPlan:
        if genre.node_type is not WorldLineNodeType.GENRE:
            raise GenreExecutorError("invalid_genre_reference")
        query = f"查找有证据与 {genre.canonical_name} 音乐类型相关联的歌手"
        context = await self.service.resolve_context(query)
        if context.status is not ContextResolutionStatus.RESOLVED:
            raise GenreExecutorError("genre_query_context_unresolved")
        query_entity = ResolvedQueryEntity(
            surface=genre.canonical_name,
            canonical_name=genre.canonical_name,
            entity_type=KnowledgeEntityType.GENRE,
            knowledge_entity_id=genre.knowledge_entity_id,
            catalog_reference=None,
            aliases=genre.aliases,
            status=QueryEntityStatus.RESOLVED,
            confidence=1,
        )
        analysis = await self.service.analyze(context, [query_entity])
        plan = self.service.plan(analysis)
        if genre.knowledge_entity_id not in analysis.constraints.entity_ids:
            raise GenreExecutorError("genre_search_plan_unconstrained")
        if not any(
            item.knowledge_entity_id == genre.knowledge_entity_id
            for item in plan.entities
        ):
            raise GenreExecutorError("genre_search_plan_identity_missing")
        if not {
            QueryType.RELATIONSHIP,
            QueryType.EXPLORATORY,
        }.intersection(plan.query_types):
            raise GenreExecutorError("genre_search_plan_type_invalid")
        return plan


class GenreExecutorInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str = Field(min_length=1, max_length=64)
    genre_id: str = Field(pattern=r"^genre:[a-z0-9][a-z0-9_]*$")
    genre_name: str = Field(min_length=1, max_length=500)
    genre_reference: WorldLineEntityReference
    source_artist_reference: WorldLineEntityReference
    trigger_evidence_ids: list[str] = Field(min_length=1, max_length=8)
    current_graph: WorldLineGraph
    current_sources: list[EvidenceSource] = Field(max_length=30)
    visited_nodes: list[str] = Field(default_factory=list, max_length=15)
    exploration_depth: int = Field(ge=0)


class GenreArtistCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    genre_id: str = Field(pattern=r"^genre:[a-z0-9][a-z0-9_]*$")
    artist_knowledge_id: str = Field(
        pattern=r"^artist:[a-z0-9][a-z0-9_]*$"
    )
    artist_canonical_name: str = Field(min_length=1, max_length=500)
    evidence: SemanticRelationEvidence
    confidence: float = Field(ge=0, le=1)


class GenreArtistDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    candidate: GenreArtistCandidate
    status: SemanticRelationStatus
    artist_reference: WorldLineEntityReference | None = None
    rejection_reasons: list[str] = Field(default_factory=list, max_length=20)


class AcceptedGenreArtist(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    reference: WorldLineEntityReference
    evidence: list[SemanticRelationEvidence] = Field(
        min_length=1,
        max_length=MAX_RELATION_EVIDENCE,
    )
    confidence: float = Field(ge=0, le=1)


class GenreExecutorTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    genre_id: str
    registry_version: str
    validator_version: str
    trigger_evidence_ids: list[str]
    retrieved_document_count: int = Field(ge=0)
    candidate_count: int = Field(ge=0)
    evidence_accepted_count: int = Field(ge=0)
    catalog_resolved_count: int = Field(ge=0)
    projected_artist_count: int = Field(ge=0)
    rejection_reason: dict[str, int]
    projected_evidence_ids: list[str]


class GenreExecutorResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    genre_id: str
    genre_name: str
    evidence_ids: list[str]
    candidate_artists: list[GenreArtistCandidate]
    accepted_artists: list[AcceptedGenreArtist]
    rejected_reason: dict[str, int]
    decisions: list[GenreArtistDecision]
    search_plan: SearchPlan
    delta_graph: WorldLineGraph
    delta_sources: list[EvidenceSource]
    documents: list[MusicKnowledgeDocument]
    trace: GenreExecutorTrace


class _SupportedGenreClaim(BaseModel):
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


def _resolved_artist(reference: WorldLineEntityReference) -> ResolvedEntity:
    catalog = reference.catalog_reference
    if (
        reference.node_type is not WorldLineNodeType.ARTIST
        or catalog is None
        or catalog.entity_type is not KnowledgeEntityType.ARTIST
    ):
        raise GenreExecutorError("source_artist_identity_invalid")
    return ResolvedEntity(
        entity_id=reference.node_id,
        entity_type=EntityType.ARTIST,
        name=reference.canonical_name,
        artist=reference.canonical_name,
        source=catalog.provider,
    )


class GenreExecutor:
    def __init__(
        self,
        *,
        registry: GenreVocabularyRegistry,
        entity_bridge: EntityBridge,
        planner: GenreSearchPlanner,
        retriever: MusicKnowledgeRetriever,
        music_service: WorldLineMusicService,
        threshold: float = SEMANTIC_ACCEPTANCE_THRESHOLD,
    ) -> None:
        self.registry = registry
        self.entity_bridge = entity_bridge
        self.planner = planner
        self.retriever = retriever
        self.music_service = music_service
        self.threshold = threshold

    async def execute(self, value: GenreExecutorInput) -> GenreExecutorResult:
        genre = self._validate_input(value)
        plan = await self.planner.build(value.genre_reference)
        anchor = _resolved_artist(value.source_artist_reference)
        try:
            documents = await self.retriever.retrieve(anchor, plan)
        except Exception as exc:
            raise GenreExecutorError("genre_retrieval_failed") from exc
        candidates = self._extract_candidates(
            value.genre_id,
            documents,
        )
        evidence_valid, decisions = self._validate_evidence(
            value,
            documents,
            candidates,
        )
        accepted, catalog_decisions = await self._resolve_catalog_artists(
            value,
            evidence_valid,
        )
        decisions.extend(catalog_decisions)
        graph, sources = self._project(value, genre.canonical_name, accepted)
        rejected = [
            item
            for item in decisions
            if item.status is SemanticRelationStatus.REJECTED
        ]
        reasons = Counter(
            reason for item in rejected for reason in item.rejection_reasons
        )
        evidence_ids = list(
            dict.fromkeys(
                evidence_id
                for edge in graph.edges
                for evidence_id in edge.evidence_ids
            )
        )
        trace = GenreExecutorTrace(
            genre_id=value.genre_id,
            registry_version=self.registry.version,
            validator_version=GENRE_EXECUTOR_VALIDATOR_VERSION,
            trigger_evidence_ids=value.trigger_evidence_ids,
            retrieved_document_count=len(documents),
            candidate_count=len(candidates),
            evidence_accepted_count=len(evidence_valid),
            catalog_resolved_count=len(accepted),
            projected_artist_count=len(accepted),
            rejection_reason=dict(reasons),
            projected_evidence_ids=evidence_ids,
        )
        LOGGER.info(
            "worldline.genre_executor",
            extra={"genre_executor_trace": trace.model_dump(mode="json")},
        )
        return GenreExecutorResult(
            genre_id=value.genre_id,
            genre_name=genre.canonical_name,
            evidence_ids=evidence_ids,
            candidate_artists=candidates,
            accepted_artists=accepted,
            rejected_reason=dict(reasons),
            decisions=decisions,
            search_plan=plan,
            delta_graph=graph,
            delta_sources=sources,
            documents=documents,
            trace=trace,
        )

    def _validate_input(self, value: GenreExecutorInput):
        genre = self.registry.get(value.genre_id)
        reference = value.genre_reference
        if (
            genre is None
            or not genre.executable
            or not genre.expandable
            or genre.canonical_name != value.genre_name
            or reference.node_type is not WorldLineNodeType.GENRE
            or reference.node_id != value.genre_id
            or reference.knowledge_entity_id != value.genre_id
            or reference.canonical_name != value.genre_name
            or reference.catalog_reference is not None
        ):
            raise GenreExecutorError("genre_not_executable")
        source_ids = {source.id for source in value.current_sources}
        if not set(value.trigger_evidence_ids).issubset(source_ids):
            raise GenreExecutorError("genre_trigger_evidence_missing")
        return genre

    def _extract_candidates(
        self,
        genre_id: str,
        documents: list[MusicKnowledgeDocument],
    ) -> list[GenreArtistCandidate]:
        candidates: list[GenreArtistCandidate] = []
        for document in documents:
            claims = document.metadata.get("supported_claims")
            if not isinstance(claims, list):
                continue
            parent_id = _metadata_string(document, "parent_id")
            child_id = _metadata_string(document, "chunk_id")
            context_version = _metadata_string(document, "context_version")
            if parent_id is None or child_id is None or context_version is None:
                continue
            for raw_claim in claims:
                if not isinstance(raw_claim, dict):
                    continue
                try:
                    claim = _SupportedGenreClaim.model_validate(raw_claim)
                except ValidationError:
                    continue
                if (
                    claim.predicate != "has_genre"
                    or claim.object_entity_id != genre_id
                    or not claim.subject_entity_id.startswith("artist:")
                ):
                    continue
                artist = self.entity_bridge.knowledge_registry.get(
                    claim.subject_entity_id
                )
                if (
                    artist is None
                    or artist.entity_type is not KnowledgeEntityType.ARTIST
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
                    GenreArtistCandidate(
                        genre_id=genre_id,
                        artist_knowledge_id=artist.entity_id,
                        artist_canonical_name=artist.canonical_name,
                        evidence=evidence,
                        confidence=min(
                            claim.extraction_confidence,
                            claim.verification_confidence,
                        ),
                    )
                )
        return candidates

    def _validate_evidence(
        self,
        value: GenreExecutorInput,
        documents: list[MusicKnowledgeDocument],
        candidates: list[GenreArtistCandidate],
    ) -> tuple[list[GenreArtistCandidate], list[GenreArtistDecision]]:
        counts = Counter(item.id for item in documents)
        by_id = {item.id: item for item in documents}
        current_knowledge_ids = {
            reference.knowledge_entity_id
            for node in value.current_graph.nodes
            if isinstance(node.metadata.get("entity_reference"), dict)
            for reference in [
                self._parse_reference(node.metadata["entity_reference"])
            ]
            if reference is not None
            and reference.node_type is WorldLineNodeType.ARTIST
        }
        valid: list[GenreArtistCandidate] = []
        decisions: list[GenreArtistDecision] = []
        for candidate in candidates:
            reasons: list[str] = []
            if candidate.artist_knowledge_id in current_knowledge_ids:
                reasons.append("artist_already_in_graph")
            evidence = candidate.evidence
            document = by_id.get(evidence.document_id)
            if counts[evidence.document_id] != 1 or document is None:
                reasons.append("evidence_document_missing")
            else:
                if document.metadata.get("sufficiency_status") != "sufficient":
                    reasons.append("insufficient_context")
                if (
                    document.id != evidence.parent_id
                    or document.metadata.get("parent_id")
                    != evidence.parent_id
                ):
                    reasons.append("evidence_parent_mismatch")
                if (
                    document.metadata.get("chunk_id") != evidence.child_id
                    or evidence.source_chunk_id != evidence.child_id
                ):
                    reasons.append("evidence_chunk_mismatch")
                if evidence.exact_span not in document.content:
                    reasons.append("evidence_span_missing")
                if not self._claim_present(document, candidate):
                    reasons.append("claim_not_in_document")
            effective = min(
                evidence.extraction_confidence,
                evidence.verification_confidence,
            )
            if candidate.confidence != effective:
                reasons.append("candidate_confidence_mismatch")
            if effective < self.threshold:
                reasons.append("confidence_below_threshold")
            if reasons:
                decisions.append(
                    GenreArtistDecision(
                        candidate=candidate,
                        status=SemanticRelationStatus.REJECTED,
                        rejection_reasons=list(dict.fromkeys(reasons)),
                    )
                )
            else:
                valid.append(candidate)
        return valid, decisions

    async def _resolve_catalog_artists(
        self,
        value: GenreExecutorInput,
        candidates: list[GenreArtistCandidate],
    ) -> tuple[list[AcceptedGenreArtist], list[GenreArtistDecision]]:
        grouped: dict[str, list[GenreArtistCandidate]] = {}
        for candidate in candidates:
            grouped.setdefault(candidate.artist_knowledge_id, []).append(candidate)
        accepted: list[AcceptedGenreArtist] = []
        decisions: list[GenreArtistDecision] = []
        ordered = sorted(
            grouped.items(),
            key=lambda item: (
                -max(candidate.confidence for candidate in item[1]),
                item[0],
            ),
        )
        for knowledge_id, group in ordered:
            if len(accepted) >= MAX_PROJECTED_ARTISTS:
                decisions.extend(
                    GenreArtistDecision(
                        candidate=candidate,
                        status=SemanticRelationStatus.REJECTED,
                        rejection_reasons=["projection_limit_reached"],
                    )
                    for candidate in group
                )
                continue
            canonical_name = group[0].artist_canonical_name
            try:
                catalog_candidates = await self.music_service.search_entities(
                    canonical_name,
                    EntityType.ARTIST,
                    limit=5,
                )
            except Exception as exc:
                raise GenreExecutorError("catalog_artist_search_failed") from exc
            resolution = self.entity_bridge.resolve_catalog_artist_candidates(
                knowledge_id,
                catalog_candidates,
            )
            if (
                resolution.status is not CatalogResolutionStatus.RESOLVED
                or resolution.reference is None
            ):
                for candidate in group:
                    decisions.append(
                        GenreArtistDecision(
                            candidate=candidate,
                            status=SemanticRelationStatus.REJECTED,
                            rejection_reasons=[resolution.reason],
                        )
                    )
                continue
            evidence_by_key: dict[
                tuple[str, str, str],
                SemanticRelationEvidence,
            ] = {}
            for candidate in group:
                evidence = candidate.evidence
                key = (
                    evidence.claim_id,
                    evidence.source_chunk_id,
                    sha256(evidence.exact_span.encode("utf-8")).hexdigest(),
                )
                evidence_by_key.setdefault(key, evidence)
                decisions.append(
                    GenreArtistDecision(
                        candidate=candidate,
                        status=SemanticRelationStatus.ACCEPTED,
                        artist_reference=resolution.reference,
                    )
                )
            evidence = list(evidence_by_key.values())
            if len(evidence) > MAX_RELATION_EVIDENCE:
                raise GenreExecutorError("capacity_reached")
            accepted.append(
                AcceptedGenreArtist(
                    reference=resolution.reference,
                    evidence=evidence,
                    confidence=min(item.confidence for item in group),
                )
            )
        return accepted, decisions

    def _project(
        self,
        value: GenreExecutorInput,
        genre_name: str,
        accepted: list[AcceptedGenreArtist],
    ) -> tuple[WorldLineGraph, list[EvidenceSource]]:
        genre_node = next(
            (
                node
                for node in value.current_graph.nodes
                if node.id == value.genre_id
            ),
            None,
        )
        if genre_node is None or genre_node.type is not WorldLineNodeType.GENRE:
            raise GenreExecutorError("invalid_genre_node")
        nodes = [genre_node]
        edges: list[WorldLineEdge] = []
        sources: list[EvidenceSource] = []
        for artist in accepted:
            reference = artist.reference
            nodes.append(
                WorldLineNode(
                    id=reference.node_id,
                    type=WorldLineNodeType.ARTIST,
                    name=reference.canonical_name,
                    description=f"由证据验证的 {genre_name} 关联音乐人",
                    metadata={
                        "entity_reference": reference.model_dump(mode="json"),
                        "genre_executor_version": GENRE_EXECUTOR_VERSION,
                    },
                )
            )
            evidence_ids = []
            for evidence in artist.evidence:
                source = self._evidence_source(
                    value.genre_id,
                    genre_name,
                    artist,
                    evidence,
                )
                sources.append(source)
                evidence_ids.append(source.id)
            edges.append(
                WorldLineEdge(
                    source=value.genre_id,
                    target=reference.node_id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    description=(
                        f"{genre_name} 与 {reference.canonical_name} 相关联"
                    ),
                    evidence_ids=evidence_ids,
                )
            )
        return (
            WorldLineGraph(
                root=value.genre_id,
                nodes=nodes,
                edges=edges,
            ),
            sources,
        )

    @staticmethod
    def _evidence_source(
        genre_id: str,
        genre_name: str,
        artist: AcceptedGenreArtist,
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
        reference = artist.reference
        return EvidenceSource(
            id=f"knowledge:rel:{digest}",
            source_type=EvidenceType.KNOWLEDGE,
            title=f"{genre_name} 与 {reference.canonical_name} 的风格关系"[
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
                    f"{genre_id}|{reference.node_id}|"
                    f"{WorldLineRelation.ASSOCIATED_WITH.value}"
                ),
                "claim_subject_entity_id": reference.knowledge_entity_id,
                "claim_predicate": "has_genre",
                "claim_object_entity_id": genre_id,
                "genre_executor_version": GENRE_EXECUTOR_VERSION,
                "validator_version": GENRE_EXECUTOR_VALIDATOR_VERSION,
                "confidence": artist.confidence,
            },
        )

    @staticmethod
    def _claim_present(
        document: MusicKnowledgeDocument,
        candidate: GenreArtistCandidate,
    ) -> bool:
        claims = document.metadata.get("supported_claims")
        if not isinstance(claims, list):
            return False
        evidence = candidate.evidence
        for raw_claim in claims:
            if not isinstance(raw_claim, dict):
                continue
            try:
                claim = _SupportedGenreClaim.model_validate(raw_claim)
            except ValidationError:
                continue
            if (
                claim.claim_id == evidence.claim_id
                and claim.subject_entity_id == candidate.artist_knowledge_id
                and claim.predicate == "has_genre"
                and claim.object_entity_id == candidate.genre_id
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
    def _parse_reference(
        payload: object,
    ) -> WorldLineEntityReference | None:
        try:
            return WorldLineEntityReference.model_validate(payload)
        except (TypeError, ValueError):
            return None


def create_genre_executor() -> GenreExecutor:
    registry = GenreVocabularyRegistry.load()
    entity_bridge = EntityBridge(EntityRegistry.load(), registry)
    runtime = get_worldline_retrieval_runtime()
    query_understanding = QueryUnderstandingService(
        index_profile=runtime.index_profile
    )
    return GenreExecutor(
        registry=registry,
        entity_bridge=entity_bridge,
        planner=GenreSearchPlanFactory(query_understanding),
        retriever=runtime.retriever,
        music_service=JavaMusicEntityService(),
    )
