from langchain_core.messages import AnyMessage

from app.agent.foundation.models import TaskResult
from app.agent.foundation.routing import RoutingContext
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
from app.rag.query_understanding.models import (
    ContextResolution,
    QueryAnalysis,
    SearchPlan,
)


class WorldLineState(BaseAgentState):
    """Private state for one grounded WorldLine workflow."""

    task_id: str
    routing_context: RoutingContext
    user_query: str
    query_context: ContextResolution | None
    query_analysis: QueryAnalysis | None
    search_plan: SearchPlan | None
    entity_query: str | None
    expected_entity_type: EntityType | None
    candidate_entities: list[ResolvedEntity]
    entity_resolution: EntityResolution | None
    resolved_entity: ResolvedEntity | None
    entity_facts: MusicEntityFacts | None
    retrieved_docs: list[MusicKnowledgeDocument]
    worldline_graph: WorldLineGraph | None
    narrative: str | None
    citation_ids: list[str]
    branches: list[WorldLineBranch]
    sources: list[EvidenceSource]
    error_code: str | None
    error: str | None
    answer: str | None
    task_result: TaskResult | None
    session_mode: bool
    trusted_target: ResolvedEntity | None
    prior_graph: WorldLineGraph | None
    prior_sources: list[EvidenceSource]
    root_entity: ResolvedEntity | None
    visited_nodes: list[str]


def create_worldline_state(
    query: str,
    task_id: str,
    *,
    messages: list[AnyMessage] | None = None,
    routing_context: RoutingContext | None = None,
) -> WorldLineState:
    return WorldLineState(
        messages=list(messages or []),
        current_input=query,
        task_id=task_id,
        routing_context=routing_context or RoutingContext(),
        user_query=query,
        query_context=None,
        query_analysis=None,
        search_plan=None,
        entity_query=None,
        expected_entity_type=None,
        candidate_entities=[],
        entity_resolution=None,
        resolved_entity=None,
        entity_facts=None,
        retrieved_docs=[],
        worldline_graph=None,
        narrative=None,
        citation_ids=[],
        branches=[],
        sources=[],
        error_code=None,
        error=None,
        answer=None,
        task_result=None,
        session_mode=False,
        trusted_target=None,
        prior_graph=None,
        prior_sources=[],
        root_entity=None,
        visited_nodes=[],
    )
