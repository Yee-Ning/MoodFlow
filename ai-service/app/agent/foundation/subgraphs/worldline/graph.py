import logging
from time import perf_counter
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.worldline.builder import build_worldline_graph
from app.agent.foundation.subgraphs.worldline.entity_resolver import (
    resolve_music_entity,
)
from app.agent.foundation.subgraphs.worldline.generation import (
    StructuredWorldLineGenerator,
    WorldLineGenerator,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityResolutionStatus,
    WorldLineResult,
)
from app.agent.foundation.subgraphs.worldline.music_service import (
    JavaMusicEntityService,
    WorldLineMusicService,
)
from app.agent.foundation.subgraphs.worldline.retrieval import (
    EmptyMusicKnowledgeRetriever,
    MusicKnowledgeRetriever,
)
from app.agent.foundation.subgraphs.worldline.state import WorldLineState

logger = logging.getLogger(__name__)

ENTITY_NOT_FOUND_MESSAGE = "没有找到能够确认的歌曲、歌手或专辑，请检查名称后再试。"
ENTITY_AMBIGUOUS_MESSAGE = "这个名称对应多个音乐实体，请明确是歌曲、歌手还是专辑。"
MUSIC_SERVICE_UNAVAILABLE_MESSAGE = "音乐实体资料暂时无法加载，请稍后再试。"
GRAPH_UNAVAILABLE_MESSAGE = "现有事实不足以构建音乐世界线，请换一个实体再试。"
NARRATIVE_UNAVAILABLE_MESSAGE = "音乐世界线叙事暂时无法生成，请稍后再试。"
BRANCHES_UNAVAILABLE_MESSAGE = "音乐世界线分支暂时无法生成，请稍后再试。"


def _thread_id(config: RunnableConfig) -> str | None:
    configurable = config.get("configurable", {})
    value = configurable.get("thread_id")
    return str(value) if value is not None else None


def _log_node(
    event: str,
    *,
    started_at: float,
    config: RunnableConfig,
    success: bool,
    entity_id: str | None = None,
    entity_type: str | None = None,
    error_code: str | None = None,
) -> None:
    logger.log(
        logging.INFO if success else logging.WARNING,
        event,
        extra={
            "thread_id": _thread_id(config),
            "node_name": event.removeprefix("worldline."),
            "entity_id": entity_id,
            "entity_type": entity_type,
            "latency_ms": round((perf_counter() - started_at) * 1_000, 2),
            "success": success,
            "error_code": error_code,
        },
    )


def _failed_result(
    state: WorldLineState,
    message: str,
    error_code: str,
) -> dict[str, Any]:
    return {
        "answer": message,
        "error_code": error_code,
        "error": message,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.FAILED,
            content=message,
            data={"reason": error_code},
            error=message,
        ),
    }


def _needs_input_result(
    state: WorldLineState,
    message: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "answer": message,
        "error_code": reason,
        "error": None,
        "task_result": TaskResult(
            task_id=state["task_id"],
            route_key=RouteKey.WORLDLINE,
            status=TaskStatus.NEEDS_INPUT,
            content=message,
            data={"reason": reason},
        ),
    }


def _route_or_end(next_node: str):
    def route(state: WorldLineState) -> str:
        return END if state.get("task_result") is not None else next_node

    return route


def _visible_content(result: WorldLineResult) -> str:
    branch_lines = "\n".join(
        f"{index}. {branch.title}：{branch.description}"
        for index, branch in enumerate(result.branches, start=1)
    )
    return f"{result.narrative}\n\n可继续探索：\n{branch_lines}"


def build_worldline_subgraph(
    *,
    music_service: WorldLineMusicService | None = None,
    retriever: MusicKnowledgeRetriever | None = None,
    generator: WorldLineGenerator | None = None,
) -> StateGraph:
    service = music_service or JavaMusicEntityService()
    knowledge_retriever = retriever or EmptyMusicKnowledgeRetriever()
    worldline_generator = generator or StructuredWorldLineGenerator()

    async def resolve_entity(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        try:
            resolution = await resolve_music_entity(
                service,
                state["current_input"],
            )
        except ValueError:
            update = _needs_input_result(
                state,
                ENTITY_NOT_FOUND_MESSAGE,
                "invalid_entity_query",
            )
            _log_node(
                "worldline.resolve_entity",
                started_at=started_at,
                config=config,
                success=False,
                error_code="invalid_entity_query",
            )
            return update
        except Exception:
            update = _failed_result(
                state,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
                "entity_search_failed",
            )
            _log_node(
                "worldline.resolve_entity",
                started_at=started_at,
                config=config,
                success=False,
                error_code="entity_search_failed",
            )
            return update

        update: dict[str, Any] = {
            "user_query": state["current_input"],
            "entity_query": resolution.query,
            "expected_entity_type": resolution.expected_entity_type,
            "candidate_entities": resolution.candidates,
            "entity_resolution": resolution,
            "resolved_entity": resolution.entity,
        }
        if resolution.status is EntityResolutionStatus.UNRESOLVED:
            update.update(
                _needs_input_result(
                    state,
                    ENTITY_NOT_FOUND_MESSAGE,
                    "entity_not_found",
                )
            )
        elif resolution.status is EntityResolutionStatus.AMBIGUOUS:
            labels = "、".join(
                f"{item.entity_type.value}《{item.name}》"
                for item in resolution.candidates[:5]
            )
            update.update(
                _needs_input_result(
                    state,
                    f"{ENTITY_AMBIGUOUS_MESSAGE}候选：{labels}",
                    "entity_ambiguous",
                )
            )

        resolved = resolution.entity
        success = resolved is not None
        _log_node(
            "worldline.resolve_entity",
            started_at=started_at,
            config=config,
            success=success,
            entity_id=resolved.entity_id if resolved is not None else None,
            entity_type=(
                resolved.entity_type.value if resolved is not None else None
            ),
            error_code=None if success else resolution.status.value,
        )
        return update

    async def load_entity_facts(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        entity = state.get("resolved_entity")
        if entity is None:
            raise RuntimeError("WorldLine entity was not resolved")
        try:
            facts = await service.load_entity_facts(entity)
        except Exception:
            _log_node(
                "worldline.load_facts",
                started_at=started_at,
                config=config,
                success=False,
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                error_code="entity_facts_failed",
            )
            return _failed_result(
                state,
                MUSIC_SERVICE_UNAVAILABLE_MESSAGE,
                "entity_facts_failed",
            )
        _log_node(
            "worldline.load_facts",
            started_at=started_at,
            config=config,
            success=True,
            entity_id=facts.entity.entity_id,
            entity_type=facts.entity.entity_type.value,
        )
        return {
            "resolved_entity": facts.entity,
            "entity_facts": facts,
            "sources": facts.sources,
        }

    async def retrieve_music_knowledge(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        entity = state.get("resolved_entity")
        if entity is None:
            raise RuntimeError("WorldLine entity facts were not loaded")
        try:
            documents = await knowledge_retriever.retrieve(
                entity,
                state["entity_query"] or state["current_input"],
            )
        except Exception:
            _log_node(
                "worldline.retrieve",
                started_at=started_at,
                config=config,
                success=False,
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                error_code="retriever_unavailable",
            )
            return {
                "retrieved_docs": [],
                "error_code": "retriever_unavailable",
                "error": None,
            }
        _log_node(
            "worldline.retrieve",
            started_at=started_at,
            config=config,
            success=True,
            entity_id=entity.entity_id,
            entity_type=entity.entity_type.value,
        )
        return {"retrieved_docs": documents}

    def build_graph(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        facts = state.get("entity_facts")
        entity = state.get("resolved_entity")
        if facts is None or entity is None:
            raise RuntimeError("WorldLine facts are unavailable")
        try:
            graph, sources = build_worldline_graph(
                facts,
                state.get("retrieved_docs", []),
            )
        except Exception:
            _log_node(
                "worldline.build_graph",
                started_at=started_at,
                config=config,
                success=False,
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                error_code="graph_empty_or_invalid",
            )
            return _failed_result(
                state,
                GRAPH_UNAVAILABLE_MESSAGE,
                "graph_empty_or_invalid",
            )
        _log_node(
            "worldline.build_graph",
            started_at=started_at,
            config=config,
            success=True,
            entity_id=entity.entity_id,
            entity_type=entity.entity_type.value,
        )
        return {"worldline_graph": graph, "sources": sources}

    async def generate_narrative(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        entity = state.get("resolved_entity")
        facts = state.get("entity_facts")
        graph = state.get("worldline_graph")
        if entity is None or facts is None or graph is None:
            raise RuntimeError("WorldLine narrative inputs are unavailable")
        try:
            narrative = await worldline_generator.generate_narrative(
                entity,
                facts,
                state.get("retrieved_docs", []),
                graph,
                state.get("sources", []),
            )
        except Exception:
            _log_node(
                "worldline.generate_narrative",
                started_at=started_at,
                config=config,
                success=False,
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                error_code="narrative_generation_failed",
            )
            return _failed_result(
                state,
                NARRATIVE_UNAVAILABLE_MESSAGE,
                "narrative_generation_failed",
            )
        _log_node(
            "worldline.generate_narrative",
            started_at=started_at,
            config=config,
            success=True,
            entity_id=entity.entity_id,
            entity_type=entity.entity_type.value,
        )
        return {"narrative": narrative}

    async def generate_branches(
        state: WorldLineState,
        config: RunnableConfig,
    ) -> dict[str, Any]:
        started_at = perf_counter()
        entity = state.get("resolved_entity")
        graph = state.get("worldline_graph")
        narrative = state.get("narrative")
        if entity is None or graph is None or narrative is None:
            raise RuntimeError("WorldLine branch inputs are unavailable")
        try:
            branches = await worldline_generator.generate_branches(
                entity,
                graph,
                state.get("sources", []),
            )
            result = WorldLineResult(
                root_entity=entity,
                graph=graph,
                narrative=narrative,
                branches=branches,
                sources=state.get("sources", []),
            )
        except Exception:
            _log_node(
                "worldline.generate_branches",
                started_at=started_at,
                config=config,
                success=False,
                entity_id=entity.entity_id,
                entity_type=entity.entity_type.value,
                error_code="branch_generation_failed",
            )
            return _failed_result(
                state,
                BRANCHES_UNAVAILABLE_MESSAGE,
                "branch_generation_failed",
            )
        content = _visible_content(result)
        _log_node(
            "worldline.generate_branches",
            started_at=started_at,
            config=config,
            success=True,
            entity_id=entity.entity_id,
            entity_type=entity.entity_type.value,
        )
        return {
            "branches": branches,
            "answer": content,
            "task_result": TaskResult(
                task_id=state["task_id"],
                route_key=RouteKey.WORLDLINE,
                status=TaskStatus.SUCCESS,
                content=content,
                data={
                    "worldline_result": result.model_dump(mode="json"),
                },
            ),
        }

    builder = StateGraph(WorldLineState, output_schema=SubGraphOutput)
    builder.add_node("resolve_entity", resolve_entity)
    builder.add_node("load_entity_facts", load_entity_facts)
    builder.add_node("retrieve_music_knowledge", retrieve_music_knowledge)
    builder.add_node("build_worldline_graph", build_graph)
    builder.add_node("generate_narrative", generate_narrative)
    builder.add_node("generate_branches", generate_branches)
    builder.add_edge(START, "resolve_entity")
    builder.add_conditional_edges(
        "resolve_entity",
        _route_or_end("load_entity_facts"),
        {"load_entity_facts": "load_entity_facts", END: END},
    )
    builder.add_conditional_edges(
        "load_entity_facts",
        _route_or_end("retrieve_music_knowledge"),
        {"retrieve_music_knowledge": "retrieve_music_knowledge", END: END},
    )
    builder.add_edge("retrieve_music_knowledge", "build_worldline_graph")
    builder.add_conditional_edges(
        "build_worldline_graph",
        _route_or_end("generate_narrative"),
        {"generate_narrative": "generate_narrative", END: END},
    )
    builder.add_conditional_edges(
        "generate_narrative",
        _route_or_end("generate_branches"),
        {"generate_branches": "generate_branches", END: END},
    )
    builder.add_edge("generate_branches", END)
    return builder


def create_worldline_subgraph(
    *,
    music_service: WorldLineMusicService | None = None,
    retriever: MusicKnowledgeRetriever | None = None,
    generator: WorldLineGenerator | None = None,
) -> CompiledStateGraph:
    return build_worldline_subgraph(
        music_service=music_service,
        retriever=retriever,
        generator=generator,
    ).compile()
