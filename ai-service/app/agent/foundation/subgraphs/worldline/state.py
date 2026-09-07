from langchain_core.messages import AnyMessage

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


def create_worldline_state(
    query: str,
    task_id: str,
    *,
    messages: list[AnyMessage] | None = None,
) -> WorldLineState:
    return WorldLineState(
        messages=list(messages or []),
        current_input=query,
        task_id=task_id,
        user_query=query,
        entity_query=None,
        expected_entity_type=None,
        candidate_entities=[],
        entity_resolution=None,
        resolved_entity=None,
        entity_facts=None,
        retrieved_docs=[],
        worldline_graph=None,
        narrative=None,
        branches=[],
        sources=[],
        error_code=None,
        error=None,
        answer=None,
        task_result=None,
    )
