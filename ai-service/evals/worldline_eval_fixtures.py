"""Frozen, synthetic fixtures for WorldLine contract evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.entity_bridge import (
    EntityBridge,
    WorldLineEntityReference,
)
from app.agent.foundation.subgraphs.worldline.genre_executor import GenreExecutor
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
    WorldLineSessionResult,
)
from app.agent.foundation.subgraphs.worldline.semantic_expansion import (
    SemanticExpansionService,
)
from app.rag.knowledge.entity_linking.models import (
    EntityRegistryDocument,
    EntityType as KnowledgeEntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from app.rag.query_understanding.models import (
    CatalogReference,
    MetadataFilter,
    QueryEntityStatus,
    QueryType,
    QueryVariant,
    QueryVariantKind,
    ResolvedQueryEntity,
    RetrievalStrategy,
    SearchChannel,
    SearchPlan,
)
from evals.worldline_eval_models import WorldLineEvalCase


GENRE_NAMES = {
    "genre:hip_hop": "Hip-Hop",
    "genre:jazz": "Jazz",
    "genre:jazz_hip_hop": "Jazz Hip-Hop",
    "genre:r_and_b": "R&B",
    "genre:c_pop": "C-pop",
    "genre:not_registered": "Unknown Genre",
}


def _catalog_reference(entity: ResolvedEntity) -> CatalogReference:
    return CatalogReference(
        provider=entity.source,
        entity_id=entity.entity_id.rsplit(":", 1)[-1],
        entity_type=KnowledgeEntityType.ARTIST,
        name=entity.name,
    )


def _search_plan(
    entity: ResolvedEntity,
    knowledge_id: str,
    *,
    include_mapping: bool = True,
) -> SearchPlan:
    entities = []
    if include_mapping:
        entities.append(
            ResolvedQueryEntity(
                surface=entity.name,
                canonical_name=entity.name,
                entity_type=KnowledgeEntityType.ARTIST,
                knowledge_entity_id=knowledge_id,
                catalog_reference=_catalog_reference(entity),
                status=QueryEntityStatus.RESOLVED,
                confidence=1,
            )
        )
    return SearchPlan(
        plan_id="query-plan:0123456789abcdef01234567",
        original_query=f"探索 {entity.name}",
        standalone_query=f"探索 {entity.name}",
        query_types=[QueryType.EXPLORATORY],
        entities=entities,
        queries=[
            QueryVariant(
                text=f"探索 {entity.name}",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
    )


def _registry(entries: list[RegistryEntity]) -> EntityRegistry:
    return EntityRegistry(
        EntityRegistryDocument(registry_version="v99", entities=entries)
    )


@dataclass(frozen=True)
class SemanticFixture:
    service: SemanticExpansionService
    artist: ResolvedEntity
    search_plan: SearchPlan
    graph: WorldLineGraph
    sources: list[EvidenceSource]
    documents: list[MusicKnowledgeDocument]


def semantic_fixture(case: WorldLineEvalCase) -> SemanticFixture:
    artist = ResolvedEntity(
        entity_id=case.input["artist_catalog_id"],
        entity_type=EntityType.ARTIST,
        name=case.input["artist_name"],
        artist=case.input["artist_name"],
        source=case.input.get("catalog_provider", "netease"),
    )
    aliases = [case.input["artist_alias"]] if case.input.get("artist_alias") else []
    registry = _registry(
        [
            RegistryEntity(
                entity_id=case.input["artist_knowledge_id"],
                entity_type=KnowledgeEntityType.ARTIST,
                canonical_name=case.input["artist_name"],
                aliases=aliases,
            )
        ]
    )
    source = EvidenceSource(
        id=f"music-api:{artist.entity_id}",
        source_type=EvidenceType.MUSIC_API,
        title=artist.name,
        source="eval-catalog",
    )
    scenario = case.fixtures["scenario"]
    documents: list[MusicKnowledgeDocument] = []
    if scenario != "semantic_no_evidence":
        expected = (case.expected.get("relations") or [{}])[0]
        target_id = expected.get("target_id", "genre:hip_hop")
        if scenario == "semantic_unknown_genre":
            target_id = "genre:not_registered"
        span = expected.get(
            "exact_span",
            f"{artist.name} is associated with {GENRE_NAMES[target_id]}.",
        )
        claim_id = expected.get(
            "claim_id",
            f"claim:{int(artist.entity_id.rsplit(':', 1)[-1]):024d}",
        )
        claim = {
            "claim_id": claim_id,
            "statement": span,
            "subject_entity_id": case.input["artist_knowledge_id"],
            "predicate": (
                "mentions_genre"
                if scenario == "semantic_wrong_predicate"
                else "has_genre"
            ),
            "object_entity_id": target_id,
            "source_chunk_id": f"child:{case.case_id}",
            "evidence_span": span,
            "extraction_confidence": (
                0.8 if scenario == "semantic_low_confidence" else 0.94
            ),
            "verification_confidence": (
                0.8 if scenario == "semantic_low_confidence" else 0.94
            ),
        }
        claims = [claim, claim] if scenario == "semantic_duplicate_evidence" else [claim]
        documents = [
            MusicKnowledgeDocument(
                id=f"parent:{case.case_id}",
                title=f"Evidence for {artist.name}",
                content=case.fixtures["documents"][0]["content"],
                source="eval-knowledge",
                url=f"https://example.com/{case.case_id}",
                metadata={
                    "parent_id": f"parent:{case.case_id}",
                    "chunk_id": f"child:{case.case_id}",
                    "context_version": "eval-context-v0",
                    "retrieval_version": "eval-retrieval-v0",
                    "sufficiency_status": "sufficient",
                    "supported_claims": claims,
                },
            )
        ]
    return SemanticFixture(
        service=SemanticExpansionService(registry),
        artist=artist,
        search_plan=_search_plan(
            artist,
            case.input["artist_knowledge_id"],
            include_mapping=scenario != "semantic_identity_unmapped",
        ),
        graph=WorldLineGraph(
            root=artist.entity_id,
            nodes=[
                WorldLineNode(
                    id=artist.entity_id,
                    type=WorldLineNodeType.ARTIST,
                    name=artist.name,
                )
            ],
        ),
        sources=[source],
        documents=documents,
    )


class FixedGenrePlanner:
    def __init__(self, plan: SearchPlan) -> None:
        self.plan = plan

    async def build(self, genre: WorldLineEntityReference) -> SearchPlan:
        return self.plan


class FixedRetriever:
    def __init__(self, documents: list[MusicKnowledgeDocument]) -> None:
        self.documents = documents

    async def retrieve(
        self,
        entity: ResolvedEntity,
        plan: SearchPlan,
    ) -> list[MusicKnowledgeDocument]:
        return self.documents


class FixedMusicService:
    def __init__(self, candidates: list[ResolvedEntity]) -> None:
        self.candidates = candidates

    async def search_entities(
        self,
        query: str,
        expected_entity_type: EntityType | None,
        *,
        limit: int = 5,
    ) -> list[ResolvedEntity]:
        return self.candidates[:limit]

    async def load_entity_facts(self, entity: ResolvedEntity):
        raise AssertionError("Genre evaluation must not load catalog facts")


@dataclass(frozen=True)
class GenreFixture:
    executor: GenreExecutor
    graph: WorldLineGraph
    sources: list[EvidenceSource]
    source_reference: WorldLineEntityReference
    genre_reference: WorldLineEntityReference
    initial_result: WorldLineSessionResult


def genre_fixture(case: WorldLineEvalCase) -> GenreFixture:
    genre_registry = GenreVocabularyRegistry.load()
    genre_id = case.input["target_genre_id"]
    genre = genre_registry.get(genre_id)
    if genre is None:
        raise ValueError(f"unknown eval genre:{genre_id}")
    root_id = case.input["root_artist_id"]
    root_name = case.input["root_artist_name"]
    root_knowledge_id = case.input["root_artist_knowledge_id"]
    source_reference = WorldLineEntityReference(
        node_id=root_id,
        node_type=WorldLineNodeType.ARTIST,
        canonical_name=root_name,
        knowledge_entity_id=root_knowledge_id,
        catalog_reference=CatalogReference(
            provider=case.input.get("root_catalog_provider", "netease"),
            entity_id=root_id.rsplit(":", 1)[-1],
            entity_type=KnowledgeEntityType.ARTIST,
            name=root_name,
        ),
    )
    genre_reference = WorldLineEntityReference(
        node_id=genre_id,
        node_type=WorldLineNodeType.GENRE,
        canonical_name=genre.canonical_name,
        knowledge_entity_id=genre_id,
        aliases=genre.aliases,
    )
    trigger = EvidenceSource(
        id=f"knowledge:trigger:{case.case_id}",
        source_type=EvidenceType.KNOWLEDGE,
        title=f"{root_name} and {genre.canonical_name}",
        source="eval-knowledge",
        excerpt=f"{root_name} is associated with {genre.canonical_name}.",
        metadata={
            "claim_id": f"claim:trigger:{case.case_id}",
            "parent_id": f"parent:trigger:{case.case_id}",
            "child_id": f"child:trigger:{case.case_id}",
            "validator_version": "v1",
            "relation_key": f"{root_id}|{genre_id}|associated_with",
        },
    )
    graph = WorldLineGraph(
        root=root_id,
        nodes=[
            WorldLineNode(
                id=root_id,
                type=WorldLineNodeType.ARTIST,
                name=root_name,
                metadata={
                    "entity_reference": source_reference.model_dump(mode="json")
                },
            ),
            WorldLineNode(
                id=genre_id,
                type=WorldLineNodeType.GENRE,
                name=genre.canonical_name,
                metadata={
                    "entity_reference": genre_reference.model_dump(mode="json"),
                    "registry_version": genre_registry.version,
                    "executable": genre.executable,
                    "expandable": genre.expandable,
                },
            ),
        ],
        edges=[
            WorldLineEdge(
                source=root_id,
                target=genre_id,
                relation=WorldLineRelation.ASSOCIATED_WITH,
                evidence_ids=[trigger.id],
            )
        ],
    )
    scenario = case.fixtures["scenario"]
    expected_artists = case.expected.get("artists", [])
    try:
        case_number = int(case.case_id.rsplit("-", 1)[-1])
    except ValueError:
        case_number = int(sha256(case.case_id.encode("utf-8")).hexdigest()[:8], 16)
    target = expected_artists[0] if expected_artists else {
        "knowledge_entity_id": f"artist:eval_{case_number}",
        "catalog_node_id": "netease:artist:999",
        "artist_name": f"Eval Artist {case_number}",
        "claim_id": f"claim:{999:024d}",
        "exact_span": (
            f"Eval Artist {case_number} is associated "
            f"with {genre.canonical_name}."
        ),
    }
    entries = [
        RegistryEntity(
            entity_id=root_knowledge_id,
            entity_type=KnowledgeEntityType.ARTIST,
            canonical_name=root_name,
        )
    ]
    if scenario != "genre_no_evidence":
        entries.append(
            RegistryEntity(
                entity_id=target["knowledge_entity_id"],
                entity_type=KnowledgeEntityType.ARTIST,
                canonical_name=target["artist_name"],
                aliases=[f"Alias {target['artist_name']}"]
                if scenario == "genre_alias"
                else [],
            )
        )
    knowledge_registry = _registry(entries)
    bridge = EntityBridge(knowledge_registry, genre_registry)
    documents: list[MusicKnowledgeDocument] = []
    if scenario != "genre_no_evidence":
        claim = {
            "claim_id": target["claim_id"],
            "statement": target["exact_span"],
            "subject_entity_id": target["knowledge_entity_id"],
            "predicate": "has_genre",
            "object_entity_id": genre_id,
            "source_chunk_id": f"child:{case.case_id}",
            "evidence_span": target["exact_span"],
            "extraction_confidence": 0.94,
            "verification_confidence": 0.94,
        }
        claims = [claim, claim] if scenario == "genre_duplicate_evidence" else [claim]
        documents = [
            MusicKnowledgeDocument(
                id=f"parent:{case.case_id}",
                title=f"Artists for {genre.canonical_name}",
                content=case.fixtures["documents"][0]["content"],
                source="eval-knowledge",
                url=f"https://example.com/{case.case_id}",
                metadata={
                    "parent_id": f"parent:{case.case_id}",
                    "chunk_id": f"child:{case.case_id}",
                    "context_version": "eval-context-v0",
                    "retrieval_version": "eval-retrieval-v0",
                    "sufficiency_status": "sufficient",
                    "supported_claims": claims,
                },
            )
        ]
    candidates: list[ResolvedEntity] = []
    if scenario not in {"genre_no_evidence", "genre_catalog_unresolved"}:
        candidate_name = (
            f"Alias {target['artist_name']}"
            if scenario == "genre_alias"
            else target["artist_name"]
        )
        candidates.append(
            ResolvedEntity(
                entity_id=target["catalog_node_id"],
                entity_type=EntityType.ARTIST,
                name=candidate_name,
                artist=candidate_name,
                source=target.get("catalog_provider", "netease"),
            )
        )
        if scenario == "genre_catalog_ambiguous":
            candidates.append(
                ResolvedEntity(
                    entity_id="netease:artist:998",
                    entity_type=EntityType.ARTIST,
                    name=candidate_name,
                    artist=candidate_name,
                    source="netease",
                )
            )
    genre_entity = ResolvedQueryEntity(
        surface=genre.canonical_name,
        canonical_name=genre.canonical_name,
        entity_type=KnowledgeEntityType.GENRE,
        knowledge_entity_id=genre_id,
        status=QueryEntityStatus.RESOLVED,
        confidence=1,
    )
    plan = SearchPlan(
        plan_id="query-plan:abcdef0123456789abcdef01",
        original_query=f"查找 {genre.canonical_name} 歌手",
        standalone_query=f"查找 {genre.canonical_name} 歌手",
        query_types=[QueryType.RELATIONSHIP],
        entities=[genre_entity],
        queries=[
            QueryVariant(
                text=f"查找 {genre.canonical_name} 歌手",
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=40,
        final_top_k=10,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
    )
    executor = GenreExecutor(
        registry=genre_registry,
        entity_bridge=bridge,
        planner=FixedGenrePlanner(plan),
        retriever=FixedRetriever(documents),
        music_service=FixedMusicService(candidates),
    )
    root = ResolvedEntity(
        entity_id=root_id,
        entity_type=EntityType.ARTIST,
        name=root_name,
        artist=root_name,
        source=case.input.get("root_catalog_provider", "netease"),
    )
    initial_result = WorldLineSessionResult(
        root_entity=root,
        graph=graph,
        narrative=f"{root_name} is associated with {genre.canonical_name}.[{trigger.id}]",
        branches=[],
        sources=[trigger],
        citations=[trigger],
    )
    return GenreFixture(
        executor=executor,
        graph=graph,
        sources=[trigger],
        source_reference=source_reference,
        genre_reference=genre_reference,
        initial_result=initial_result,
    )


class FixedTurnGraph:
    def __init__(self, result: WorldLineSessionResult) -> None:
        self.result = result

    async def ainvoke(self, state: dict[str, Any], config: dict[str, Any]):
        return {
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.WORLDLINE,
                status=TaskStatus.SUCCESS,
                content=self.result.narrative,
                data={
                    "worldline_result": self.result.model_dump(mode="json")
                },
            )
        }
