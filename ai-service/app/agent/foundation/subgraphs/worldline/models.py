from enum import StrEnum
from typing import Any

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)


class EntityType(StrEnum):
    SONG = "song"
    ARTIST = "artist"
    ALBUM = "album"


class WorldLineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=4_000)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("query must not be blank")
        return normalized


class EntityResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"
    AMBIGUOUS = "ambiguous"


class ResolvedEntity(BaseModel):
    """Canonical music catalog entity selected from trusted search results."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    entity_id: str = Field(min_length=1, max_length=128)
    entity_type: EntityType
    name: str = Field(min_length=1, max_length=300)
    artist: str | None = Field(default=None, max_length=300)
    album: str | None = Field(default=None, max_length=300)
    source: str = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)


class EntityResolution(BaseModel):
    """Resolution outcome kept inside WorldLineState until completion."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    status: EntityResolutionStatus
    query: str = Field(min_length=1, max_length=300)
    expected_entity_type: EntityType | None = None
    entity: ResolvedEntity | None = None
    candidates: list[ResolvedEntity] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def validate_outcome(self) -> "EntityResolution":
        if self.status is EntityResolutionStatus.RESOLVED:
            if self.entity is None:
                raise ValueError("resolved outcome requires an entity")
        elif self.entity is not None:
            raise ValueError("unresolved or ambiguous outcome cannot select an entity")
        if self.status is EntityResolutionStatus.AMBIGUOUS and len(self.candidates) < 2:
            raise ValueError("ambiguous outcome requires at least two candidates")
        return self


class EvidenceType(StrEnum):
    MUSIC_API = "music_api"
    KNOWLEDGE = "knowledge"


class EvidenceSource(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    source_type: EvidenceType
    title: str = Field(min_length=1, max_length=500)
    source: str = Field(min_length=1, max_length=200)
    url: HttpUrl | None = None
    excerpt: str | None = Field(default=None, max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class MusicKnowledgeDocument(BaseModel):
    """Retriever-neutral knowledge item for a future hybrid RAG backend."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1, max_length=8_000)
    source: str = Field(min_length=1, max_length=200)
    url: HttpUrl | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MusicFact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    subject_id: str = Field(min_length=1, max_length=128)
    predicate: str = Field(min_length=1, max_length=100)
    value: str = Field(min_length=1, max_length=2_000)
    object_entity_id: str | None = Field(default=None, max_length=128)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class MusicEntityFacts(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entity: ResolvedEntity
    related_entities: list[ResolvedEntity] = Field(default_factory=list, max_length=20)
    facts: list[MusicFact] = Field(default_factory=list, max_length=50)
    sources: list[EvidenceSource] = Field(default_factory=list, max_length=30)


class WorldLineNodeType(StrEnum):
    SONG = "song"
    ARTIST = "artist"
    ALBUM = "album"
    CREATOR = "creator"
    GENRE = "genre"
    ERA = "era"
    RELATED_WORK = "related_work"


class WorldLineRelation(StrEnum):
    PERFORMED_BY = "performed_by"
    BELONGS_TO_ALBUM = "belongs_to_album"
    LYRICS_BY = "lyrics_by"
    COMPOSED_BY = "composed_by"
    PRODUCED_BY = "produced_by"
    BELONGS_TO_GENRE = "belongs_to_genre"
    BELONGS_TO_ERA = "belongs_to_era"
    RELATED_TO = "related_to"
    INFLUENCED_BY = "influenced_by"
    INFLUENCED = "influenced"


class WorldLineNode(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: WorldLineNodeType
    name: str = Field(min_length=1, max_length=500)
    description: str = Field(default="", max_length=2_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldLineEdge(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: str = Field(min_length=1, max_length=128)
    target: str = Field(min_length=1, max_length=128)
    relation: WorldLineRelation
    description: str = Field(default="", max_length=2_000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)


class WorldLineGraph(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root: str = Field(min_length=1, max_length=128)
    nodes: list[WorldLineNode] = Field(min_length=1, max_length=15)
    edges: list[WorldLineEdge] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_graph(self) -> "WorldLineGraph":
        node_ids = [node.id for node in self.nodes]
        if len(node_ids) != len(set(node_ids)):
            raise ValueError("worldline node ids must be unique")
        if self.root not in node_ids:
            raise ValueError("worldline root must reference an existing node")
        edge_keys = [
            (edge.source, edge.target, edge.relation)
            for edge in self.edges
        ]
        if len(edge_keys) != len(set(edge_keys)):
            raise ValueError("worldline edges must be unique")
        for edge in self.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError("worldline edge must reference existing nodes")
            if edge.source == edge.target:
                raise ValueError("worldline edge cannot be a self-loop")
        return self


class WorldLineBranchType(StrEnum):
    ARTIST = "artist"
    ALBUM = "album"
    CREATOR = "creator"
    COLLABORATION = "collaboration"
    GENRE = "genre"
    ERA = "era"
    RELATED_WORK = "related_work"


class WorldLineBranchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    type: WorldLineNodeType
    name: str = Field(min_length=1, max_length=500)


class WorldLineBranch(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=1_000)
    branch_type: WorldLineBranchType
    target_entity: WorldLineBranchTarget


class WorldLineResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    root_entity: ResolvedEntity
    graph: WorldLineGraph
    narrative: str = Field(min_length=1, max_length=6_000)
    branches: list[WorldLineBranch] = Field(min_length=3, max_length=3)
    sources: list[EvidenceSource] = Field(default_factory=list, max_length=30)
    citations: list[EvidenceSource] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def validate_citations(self) -> "WorldLineResult":
        source_by_id = {item.id: item for item in self.sources}
        citation_ids = [item.id for item in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("worldline citations must be unique")
        if not set(citation_ids).issubset(source_by_id):
            raise ValueError("worldline citations must reference result sources")
        if any(source_by_id[item.id] != item for item in self.citations):
            raise ValueError("worldline citations must equal their result sources")
        if any(f"[{citation_id}]" not in self.narrative for citation_id in citation_ids):
            raise ValueError("worldline citations must have inline narrative anchors")
        return self


class NarrativeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    narrative: str = Field(min_length=1, max_length=6_000)
    citation_ids: list[str] = Field(max_length=30)

    @model_validator(mode="after")
    def unique_citation_ids(self) -> "NarrativeOutput":
        if len(self.citation_ids) != len(set(self.citation_ids)):
            raise ValueError("narrative citation ids must be unique")
        return self


class BranchesOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    branches: list[WorldLineBranch] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def unique_branch_ids(self) -> "BranchesOutput":
        branch_ids = [branch.id for branch in self.branches]
        if len(branch_ids) != len(set(branch_ids)):
            raise ValueError("worldline branch ids must be unique")
        return self
