"""Curator-only discovery adapter over WorldLine's existing evidence services.

Knowledge identities are search hints, never executable music IDs. No LLM tool,
Journey consumption, transfer edge, or new knowledge source is involved.
"""

from dataclasses import dataclass, field
from hashlib import sha256

from app.agent.foundation.subgraphs.worldline.era_expansion import EraExpansionService
from app.agent.foundation.subgraphs.worldline.influence_expansion import (
    InfluenceExpansionService,
)
from app.agent.foundation.subgraphs.worldline.scene_expansion import (
    SceneExpansionService,
)
from app.agent.foundation.subgraphs.worldline.semantic_expansion import (
    SemanticExpansionService,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType as CatalogType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
)
from app.agent.foundation.subgraphs.worldline.music_service import (
    JavaMusicEntityService,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    HybridMusicKnowledgeRetriever,
    get_worldline_retrieval_runtime,
)
from app.rag.knowledge.entity_linking.models import EntityType
from app.rag.knowledge.entity_linking.registry import (
    DEFAULT_ENTITY_REGISTRY_PATH,
    EntityRegistry,
    normalize_alias,
)
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

MAX_HINTS = 24
MAX_ARTIST_READS = 3


@dataclass(frozen=True)
class DiscoveryHint:
    knowledge_id: str
    name: str
    entity_type: str
    aliases: tuple[str, ...]
    kind: str
    sources: tuple[dict, ...]
    start_year: int | None = None
    end_year: int | None = None

    def matches(self, song, songs):
        """Exact registry names only; ambiguous recordings never inherit dates."""
        names = {normalize_alias(name) for name in (self.name, *self.aliases)}
        if self.entity_type == "artist":
            return any(normalize_alias(name) in names for name in song.artists)
        if self.entity_type == "album":
            return normalize_alias(song.album) in names
        if self.entity_type != "song" or normalize_alias(song.name) not in names:
            return False
        matches = {row.song_id for row in songs if normalize_alias(row.name) in names}
        return matches == {song.song_id}


@dataclass
class Discovery:
    hints: list[DiscoveryHint] = field(default_factory=list)
    traces: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _plan(scene, profile=None):
    query = scene + " 音乐时代 场景 影响 风格"
    return SearchPlan(
        plan_id="query-plan:" + sha256(query.encode()).hexdigest()[:24],
        original_query=scene,
        standalone_query=query,
        query_types=[
            QueryType.EXPLORATORY,
            QueryType.TIMELINE,
            QueryType.INFLUENCE_STYLE,
        ],
        queries=[
            QueryVariant(
                text=query,
                kind=QueryVariantKind.ORIGINAL,
                channel=SearchChannel.BOTH,
                weight=1,
            )
        ],
        metadata_filter=MetadataFilter(),
        strategy=RetrievalStrategy.HYBRID,
        candidate_top_k=100,
        final_top_k=50,
        needs_rerank=True,
        analysis_version="v1",
        prompt_version="v1",
        index_profile=profile,
    )


def _graph(registry, ids):
    nodes = []
    for identity in dict.fromkeys(ids):
        entity = registry.get(identity)
        if entity and entity.entity_type.value in {"song", "artist", "album", "genre"}:
            nodes.append(
                WorldLineNode(
                    id=identity,
                    type=WorldLineNodeType(entity.entity_type.value),
                    name=entity.canonical_name,
                    metadata={"entity_reference": {"knowledge_entity_id": identity}},
                )
            )
    return WorldLineGraph(root=nodes[0].id, nodes=nodes) if nodes else None


def _evidence(result, relation):
    identifiers = {e.source_id for e in relation.evidence}
    return tuple(
        source.model_dump(mode="json")
        for source in result.sources
        if source.id in identifiers and source.excerpt
    )


def _clean(relation):
    return not any(
        value
        for key, value in relation.metadata.items()
        if key.endswith("_conflict") or key == "conflict_flags"
    )


def _fits(scene_name, scene, tags):
    name = normalize_alias(scene_name)
    # Explicit language aliases, not factual associations invented by a model.
    aliases = {
        "cantopop": ("港乐", "粤语"),
        "hong kong": ("香港", "港乐"),
        "香港": ("港乐",),
        "雨天夜跑": ("雨天", "夜跑"),
    }
    terms = (scene_name, *aliases.get(name, ()))
    context = normalize_alias(scene + " " + " ".join(tags))
    return any(normalize_alias(term) in context for term in terms)


class CuratorKnowledgeService:
    def __init__(self, *, runtime=None, registry=None, music=None):
        self.runtime = runtime
        self.registry = registry
        self.music = music

    async def discover(self, request, tags, catalog):
        runtime = self.runtime or get_worldline_retrieval_runtime()
        retriever = runtime.retriever
        # Empty/shadow modes remain empty; never turn an observation flag on.
        if not isinstance(retriever, HybridMusicKnowledgeRetriever):
            return Discovery(warnings=["知识扩展未启用，本次仅使用目录候选。"])
        profile = runtime.index_profile
        registry = self.registry or EntityRegistry.load(
            DEFAULT_ENTITY_REGISTRY_PATH.with_name(profile.registry_version + ".yaml")
            if profile
            else None
        )
        plan = _plan(request.scene, profile)
        pool = await retriever.candidate_provider.retrieve(plan)
        ranked = pool
        if retriever.rerank_mode != "off":
            reranked = await retriever.reranker.rerank(plan, pool)
            if retriever.rerank_mode == "on":
                ranked = reranked
        documents = retriever.context_adapter.adapt(ranked, plan)
        return await self.expand(request, tags, catalog, documents, registry, plan)

    async def expand(self, request, tags, catalog, documents, registry, plan=None):
        """Same validated Era/Scene/Influence/Semantic services as Explorer."""
        plan = plan or _plan(request.scene)
        result = Discovery()
        groups = []
        for document in documents:
            for claim in document.metadata.get("supported_claims", []):
                if not isinstance(claim, dict):
                    continue
                ids = (claim.get("subject_entity_id"), claim.get("object_entity_id"))
                if ids not in groups:
                    groups.append(ids)
        seeds = {normalize_alias(artist) for song in catalog for artist in song.artists}
        hints = {}

        def add(identity, kind, sources, start=None, end=None):
            entity = registry.get(identity) if identity else None
            if (
                not entity
                or not sources
                or entity.entity_type
                not in {
                    EntityType.SONG,
                    EntityType.ARTIST,
                    EntityType.ALBUM,
                }
            ):
                return
            # Ambiguous aliases cannot associate knowledge with a recording.
            names = (entity.canonical_name, *entity.aliases)
            names = tuple(
                name
                for name in names
                if registry.resolve(name, type_hint=entity.entity_type).entity == entity
            )
            if entity.canonical_name not in names:
                return
            key = (identity, kind, start, end)
            hints[key] = DiscoveryHint(
                identity,
                entity.canonical_name,
                entity.entity_type.value,
                names,
                kind,
                sources,
                start,
                end,
            )

        for ids in groups[:80]:
            graph = _graph(registry, ids)
            if graph is None:
                continue
            for service, kind in (
                (EraExpansionService(), "era"),
                (SceneExpansionService(), "scene"),
                (InfluenceExpansionService(), "influence"),
            ):
                expanded = service.expand(
                    plan.standalone_query, plan, graph, [], documents
                )
                result.traces.append(
                    {"service": kind, **expanded.trace.model_dump(mode="json")}
                )
                for relation in expanded.relations:
                    if not _clean(relation):
                        continue
                    source = relation.source_entity
                    target = relation.target_entity
                    evidence = _evidence(expanded, relation)
                    if kind == "era":
                        entity = (
                            target
                            if source.node_type is WorldLineNodeType.ERA
                            else source
                        )
                        # An artist's active years or an album's date do not date a recording.
                        if (
                            request.decade is not None
                            and entity
                            and entity.node_type is WorldLineNodeType.SONG
                            and relation.start_year is not None
                            and relation.end_year is not None
                            and request.decade
                            <= relation.start_year
                            <= relation.end_year
                            < request.decade + 10
                        ):
                            add(
                                entity.knowledge_entity_id,
                                kind,
                                evidence,
                                relation.start_year,
                                relation.end_year,
                            )
                    elif kind == "scene" and _fits(
                        relation.scene_name, request.scene, tags
                    ):
                        for entity in (source, target):
                            if entity:
                                add(entity.knowledge_entity_id, kind, evidence)
                    elif (
                        kind == "influence"
                        and target
                        and any(
                            normalize_alias(e.canonical_name) in seeds
                            for e in (source, target)
                        )
                    ):
                        for entity in (source, target):
                            if normalize_alias(entity.canonical_name) not in seeds:
                                add(entity.knowledge_entity_id, kind, evidence)

        # Semantic expansion needs a genuine catalog artist binding, not a fabricated ID.
        artist_reads = 0
        semantic_cache = {}

        async def semantic(artist):
            nonlocal artist_reads
            if artist.entity_id in semantic_cache:
                return semantic_cache[artist.entity_id]
            if artist_reads >= MAX_ARTIST_READS:
                return None
            artist_reads += 1
            music = self.music or JavaMusicEntityService()
            try:
                choices = await music.search_entities(
                    artist.canonical_name, CatalogType.ARTIST, limit=5
                )
            except Exception:
                result.warnings.append("风格线索的歌手目录核对失败，未使用该关联。")
                return None
            choices = [
                e
                for e in choices
                if e.entity_type is CatalogType.ARTIST
                and normalize_alias(e.name)
                in {
                    normalize_alias(n) for n in (artist.canonical_name, *artist.aliases)
                }
            ]
            if len(choices) != 1:
                return None
            focus = choices[0]
            provider, kind, raw_id = focus.entity_id.split(":")
            if (
                provider != "netease"
                or kind != "artist"
                or not raw_id.isdigit()
                or int(raw_id) <= 0
            ):
                return None
            bound = plan.model_copy(
                update={
                    "entities": [
                        ResolvedQueryEntity(
                            surface=focus.name,
                            canonical_name=artist.canonical_name,
                            entity_type=EntityType.ARTIST,
                            knowledge_entity_id=artist.entity_id,
                            catalog_reference=CatalogReference(
                                provider=provider,
                                entity_id=raw_id,
                                entity_type=EntityType.ARTIST,
                                name=focus.name,
                            ),
                            status=QueryEntityStatus.RESOLVED,
                            confidence=1,
                        )
                    ]
                }
            )
            graph = WorldLineGraph(
                root=focus.entity_id,
                nodes=[
                    WorldLineNode(
                        id=focus.entity_id,
                        type=WorldLineNodeType.ARTIST,
                        name=focus.name,
                    )
                ],
            )
            expanded = SemanticExpansionService(registry=registry).expand_artist_genres(
                focus, bound, graph, [], documents
            )
            result.traces.append(
                {"service": "semantic", **expanded.trace.model_dump(mode="json")}
            )
            semantic_cache[artist.entity_id] = expanded
            return expanded

        artists = [
            registry.resolve(seed, type_hint=EntityType.ARTIST).entity
            for seed in sorted(seeds)
        ]
        for artist in [a for a in artists if a]:
            expanded = await semantic(artist)
            if expanded is None:
                continue
            for relation in expanded.accepted:
                if not _fits(relation.target_name, request.scene, tags):
                    continue
                # Discover *other* registry artists with the same validated genre.
                peers = {
                    claim.get("subject_entity_id")
                    for doc in documents
                    for claim in doc.metadata.get("supported_claims", [])
                    if isinstance(claim, dict)
                    and claim.get("object_entity_id") == relation.target_id
                }
                for peer in sorted(
                    p for p in peers if isinstance(p, str) and p != artist.entity_id
                )[:3]:
                    entity = registry.get(peer)
                    if not entity or entity.entity_type is not EntityType.ARTIST:
                        continue
                    peer_result = await semantic(entity)
                    if peer_result is None:
                        continue
                    for peer_relation in peer_result.accepted:
                        if peer_relation.target_id != relation.target_id:
                            continue
                        evidence = tuple(
                            source.model_dump(mode="json")
                            for source in (*expanded.sources, *peer_result.sources)
                            if source.excerpt
                        )
                        add(peer, "semantic", evidence)
        result.hints = sorted(
            hints.values(),
            key=lambda h: (h.kind != "era", h.entity_type != "song", h.knowledge_id),
        )[:MAX_HINTS]
        if not result.hints:
            result.warnings.append("知识未提供可核对的扩展候选，本次不编造关联。")
        return result


def get_curator_knowledge_service():
    return CuratorKnowledgeService()
