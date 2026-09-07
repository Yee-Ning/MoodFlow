from app.agent.foundation.models import TaskResult
from app.agent.foundation.state.base import BaseAgentState
from app.agent.foundation.subgraphs.worldline.models import (
    EntityResolution,
    EntityType,
    EvidenceSource,
    MusicEntityFacts,
    MusicKnowledgeDocument,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineGraph,
)


class WorldLineState(BaseAgentState):
    """Private state for one grounded WorldLine workflow."""

    task_id: str
    user_query: str
    entity_query: str | None
    expected_entity_type: EntityType | None
    candidate_entities: list[ResolvedEntity]
    entity_resolution: EntityResolution | None
    resolved_entity: ResolvedEntity | None
    entity_facts: MusicEntityFacts | None
    retrieved_docs: list[MusicKnowledgeDocument]
    worldline_graph: WorldLineGraph | None
    narrative: str | None
    branches: list[WorldLineBranch]
    sources: list[EvidenceSource]
    error_code: str | None
    error: str | None
    answer: str | None
    task_result: TaskResult | None
