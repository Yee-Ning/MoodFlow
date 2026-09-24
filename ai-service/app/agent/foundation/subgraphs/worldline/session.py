"""Persistent, structured WorldLine exploration sessions."""

import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, TypedDict
from uuid import uuid4

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, ConfigDict, Field, model_validator
from psycopg import connect

from app.agent.checkpointing import checkpoint_backend, create_runtime_checkpointer
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.genre_actions import (
    GenreActionContext,
    GenreActionError,
    GenreActionPolicy,
)
from app.agent.foundation.subgraphs.worldline.genre_executor import (
    GenreExecutor,
    GenreExecutorError,
    GenreExecutorInput,
    GenreExecutorResult,
    create_genre_executor,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingValidator,
)
from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    ResolvedEntity,
    WorldLineAction,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    NarrativeOutput,
    WorldLineSessionResponse,
    WorldLineSessionResult,
)
from app.agent.foundation.subgraphs.worldline.service import WorldLineExecutionError
from app.agent.foundation.subgraphs.worldline.state import create_worldline_state
from app.agent.foundation.models import TaskResult, TaskStatus

MAX_EXPLORATION_DEPTH = 3
_SUPPORTED = {WorldLineNodeType.SONG, WorldLineNodeType.ARTIST, WorldLineNodeType.ALBUM}


class WorldLineSessionState(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    root_entity: ResolvedEntity
    current_graph: WorldLineGraph
    current_node: str
    visited_nodes: list[str]
    available_branches: list[WorldLineBranch]
    consumed_branch_ids: list[str] = Field(default_factory=list)
    exploration_depth: int = Field(ge=0, le=MAX_EXPLORATION_DEPTH)
    evidence_history: list[EvidenceSource]
    revision: int = Field(ge=1)
    last_result: WorldLineSessionResult
    created_at: datetime
    updated_at: datetime

    @model_validator(mode="after")
    def validate_consistency(self) -> "WorldLineSessionState":
        node_ids = {node.id for node in self.current_graph.nodes}
        if self.root_entity.entity_id != self.current_graph.root:
            raise ValueError("session root must match graph root")
        if self.current_node not in node_ids or not set(self.visited_nodes).issubset(node_ids):
            raise ValueError("session node must exist in graph")
        if len(self.visited_nodes) != len(set(self.visited_nodes)):
            raise ValueError("visited nodes must be unique")
        if len(self.consumed_branch_ids) != len(set(self.consumed_branch_ids)):
            raise ValueError("consumed branches must be unique")
        if self.current_node not in self.visited_nodes:
            raise ValueError("current node must be visited")
        if self.last_result.graph != self.current_graph or self.last_result.sources != self.evidence_history:
            raise ValueError("session result must match persistent graph and evidence")
        if self.last_result.branches != self.available_branches:
            raise ValueError("session branches must match result")
        return self


class _SnapshotState(TypedDict, total=False):
    candidate: dict[str, Any]
    snapshot: dict[str, Any]


def _save_candidate(state: _SnapshotState) -> dict[str, Any]:
    return {"snapshot": state["candidate"]}


def _snapshot_graph(saver: BaseCheckpointSaver):
    builder = StateGraph(_SnapshotState)
    builder.add_node("save", _save_candidate)
    builder.add_edge(START, "save")
    builder.add_edge("save", END)
    return builder.compile(checkpointer=saver)


def _config(session_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": f"worldline:{session_id}"}}


def _canonical_target(node_id: str, node_type: WorldLineNodeType, name: str) -> ResolvedEntity:
    if node_type not in _SUPPORTED:
        raise WorldLineExecutionError("unsupported_target_type", "当前版本暂不支持该类型扩展。", 422)
    parts = node_id.split(":")
    if len(parts) != 3 or parts[0] != "netease" or parts[1] != node_type.value or not parts[2].isdigit() or int(parts[2]) <= 0:
        raise WorldLineExecutionError("unsupported_target_type", "当前版本暂不支持该类型扩展。", 422)
    return ResolvedEntity(
        entity_id=node_id, entity_type=EntityType(node_type.value),
        name=name, source="netease",
    )


def _actions(
    state: WorldLineSessionState,
    genre_policy: GenreActionPolicy,
) -> list[WorldLineAction]:
    actions = []
    for branch in state.available_branches:
        target = branch.target_entity
        disabled = None
        if target.type is WorldLineNodeType.GENRE:
            node = next(
                (item for item in state.current_graph.nodes if item.id == target.id),
                None,
            )
            if node is None:
                disabled = "invalid_branch_target"
            else:
                try:
                    genre_policy.resolve(
                        state.current_graph,
                        state.evidence_history,
                        node,
                    )
                except GenreActionError as exc:
                    disabled = exc.code
        elif target.type not in _SUPPORTED:
            disabled = "unsupported_target_type"
        if disabled is None:
            if target.id in state.visited_nodes:
                disabled = "already_visited"
            elif state.exploration_depth >= MAX_EXPLORATION_DEPTH:
                disabled = "depth_limit_reached"
            elif target.type is not WorldLineNodeType.GENRE:
                try:
                    _canonical_target(target.id, target.type, target.name)
                except WorldLineExecutionError:
                    disabled = "unsupported_target_type"
        actions.append(WorldLineAction(
            id=branch.id, target_node_id=target.id,
            enabled=disabled is None, disabled_reason=disabled,
        ))
    return actions


def _response(
    state: WorldLineSessionState,
    genre_policy: GenreActionPolicy,
    *,
    outcome: str | None = None,
) -> WorldLineSessionResponse:
    return WorldLineSessionResponse.model_validate({
        **state.last_result.model_dump(mode="json"),
        "session_id": state.session_id,
        "current_node": state.current_node,
        "available_actions": [
            action.model_dump(mode="json")
            for action in _actions(state, genre_policy)
        ],
        "exploration_depth": state.exploration_depth,
        "revision": state.revision,
        "outcome": outcome,
    })


def _assign_branch_ids(result: WorldLineSessionResult, revision: int) -> WorldLineSessionResult:
    branches = [branch.model_copy(update={"id": f"wlb:{revision}:{uuid4().hex}"})
                for branch in result.branches]
    return result.model_copy(update={"branches": branches})


class WorldLineSessionService:
    def __init__(
        self,
        saver: BaseCheckpointSaver,
        *,
        turn_graph: Any | None = None,
        genre_executor: GenreExecutor | None = None,
        genre_policy: GenreActionPolicy | None = None,
        distributed_lock: bool = False,
    ) -> None:
        self._snapshots = _snapshot_graph(saver)
        self._turn_graph = turn_graph or create_worldline_subgraph()
        self._genre_executor = genre_executor
        self._genre_policy = genre_policy or GenreActionPolicy(
            GenreVocabularyRegistry.load()
        )
        self._grounding_validator = WorldLineGroundingValidator()
        self._locks: dict[str, asyncio.Lock] = {}
        self._distributed_lock = distributed_lock

    @asynccontextmanager
    async def _session_lock(self, session_id: str):
        local = self._locks.setdefault(session_id, asyncio.Lock())
        async with local:
            if not self._distributed_lock:
                yield
                return
            database_url = os.getenv("DATABASE_URL", "").strip()
            if not database_url:
                raise WorldLineExecutionError("checkpoint_unavailable", "PostgreSQL checkpoint 尚未就绪。", 503)
            connection = None
            try:
                connection = await asyncio.to_thread(connect, database_url)
                await asyncio.to_thread(
                    connection.execute,
                    "SELECT pg_advisory_xact_lock(41721, hashtext(%s))",
                    (session_id,),
                )
                yield
            except WorldLineExecutionError:
                raise
            except Exception as exc:
                raise WorldLineExecutionError("checkpoint_unavailable", "WorldLine 会话暂时无法保存。", 503) from exc
            finally:
                if connection is not None:
                    await asyncio.to_thread(connection.close)

    async def _read(self, session_id: str) -> WorldLineSessionState:
        try:
            snapshot = await self._snapshots.aget_state(_config(session_id))
            raw = snapshot.values.get("snapshot") if snapshot.values else None
            if raw is None:
                raise WorldLineExecutionError("session_not_found", "WorldLine 会话不存在。", 404)
            state = WorldLineSessionState.model_validate(raw)
            if state.session_id != session_id:
                raise ValueError("checkpoint session id mismatch")
            return state
        except WorldLineExecutionError:
            raise
        except Exception as exc:
            raise WorldLineExecutionError("checkpoint_unavailable", "WorldLine 会话暂时无法恢复。", 503) from exc

    async def load(self, session_id: str) -> WorldLineSessionState:
        return await self._read(session_id)

    async def _commit(self, state: WorldLineSessionState) -> None:
        try:
            await self._snapshots.ainvoke(
                {"candidate": state.model_dump(mode="json")}, _config(state.session_id)
            )
        except Exception as exc:
            raise WorldLineExecutionError("checkpoint_write_failed", "WorldLine 会话暂时无法保存。", 503) from exc

    async def _run_turn(
        self, query: str, *, session_id: str,
        target: ResolvedEntity | None = None,
        previous: WorldLineSessionState | None = None,
    ) -> WorldLineSessionResult:
        state = create_worldline_state(
            query, task_id=f"worldline-session:{session_id}:{uuid4().hex}",
            messages=[HumanMessage(content=query)],
        )
        state.update({
            "session_mode": True,
            "trusted_target": target,
            "prior_graph": previous.current_graph if previous else None,
            "prior_sources": previous.evidence_history if previous else [],
            "root_entity": previous.root_entity if previous else None,
            "visited_nodes": [*previous.visited_nodes, target.entity_id] if previous and target else [],
        })
        try:
            output = await self._turn_graph.ainvoke(state, _config(session_id))
        except Exception as exc:
            raise WorldLineExecutionError("worldline_failed", "音乐世界线暂时无法生成。", 502) from exc
        raw = output.get("task_result")
        if raw is None:
            raise WorldLineExecutionError("missing_worldline_result", "音乐世界线没有返回结果。", 502)
        task = raw if isinstance(raw, TaskResult) else TaskResult.model_validate(raw)
        if task.status is not TaskStatus.SUCCESS:
            code = str(task.data.get("reason") or "worldline_failed")
            status = 503 if code in {"entity_search_failed", "entity_facts_failed"} else 409 if code == "capacity_reached" else 502
            raise WorldLineExecutionError(code, task.content or "音乐世界线暂时无法生成。", status)
        try:
            return WorldLineSessionResult.model_validate(task.data["worldline_result"])
        except (KeyError, ValueError) as exc:
            raise WorldLineExecutionError("invalid_worldline_result", "音乐世界线返回了无法识别的数据。", 502) from exc

    def _get_genre_executor(self) -> GenreExecutor:
        if self._genre_executor is None:
            self._genre_executor = create_genre_executor()
        return self._genre_executor

    async def _run_genre_turn(
        self,
        old: WorldLineSessionState,
        node: WorldLineNode,
        context: GenreActionContext,
    ) -> WorldLineSessionResult:
        try:
            execution = await self._get_genre_executor().execute(
                GenreExecutorInput(
                    session_id=old.session_id,
                    genre_id=node.id,
                    genre_name=node.name,
                    genre_reference=context.genre_reference,
                    source_artist_reference=context.source_artist_reference,
                    trigger_evidence_ids=context.trigger_evidence_ids,
                    current_graph=old.current_graph,
                    current_sources=old.evidence_history,
                    visited_nodes=old.visited_nodes,
                    exploration_depth=old.exploration_depth,
                )
            )
        except GenreExecutorError as exc:
            status = (
                503
                if exc.code
                in {"genre_retrieval_failed", "catalog_artist_search_failed"}
                else 422
            )
            raise WorldLineExecutionError(
                exc.code,
                "当前没有足够的可验证证据继续探索该音乐类型。",
                status,
            ) from exc
        if not execution.accepted_artists:
            raise WorldLineExecutionError(
                "no_grounded_genre_connections",
                "当前没有足够的可验证证据继续探索该音乐类型。",
                422,
            )
        try:
            graph, sources = merge_worldline_graph(
                old.current_graph,
                execution.delta_graph,
                old.evidence_history,
                execution.delta_sources,
                target_node_id=node.id,
            )
            narrative, citations = self._genre_narrative(execution)
            self._grounding_validator.validate(narrative, sources)
            branches = [
                WorldLineBranch(
                    id=f"branch:artist:{index}",
                    title=f"探索 {artist.reference.canonical_name}",
                    description=(
                        f"沿 {node.name} 与 {artist.reference.canonical_name} "
                        "的证据关系继续探索。"
                    ),
                    branch_type=WorldLineBranchType.ARTIST,
                    target_entity=WorldLineBranchTarget(
                        id=artist.reference.node_id,
                        type=WorldLineNodeType.ARTIST,
                        name=artist.reference.canonical_name,
                    ),
                )
                for index, artist in enumerate(
                    execution.accepted_artists,
                    start=1,
                )
            ]
            return WorldLineSessionResult(
                root_entity=old.root_entity,
                graph=graph,
                narrative=narrative.narrative,
                branches=branches,
                sources=sources,
                citations=citations,
            )
        except WorldLineMergeError as exc:
            raise WorldLineExecutionError(
                exc.code,
                "世界线扩展无法合并。",
                409,
            ) from exc
        except ValueError as exc:
            raise WorldLineExecutionError(
                "genre_result_invalid",
                "音乐类型探索结果无法验证。",
                502,
            ) from exc

    @staticmethod
    def _genre_narrative(
        execution: GenreExecutorResult,
    ) -> tuple[NarrativeOutput, list[EvidenceSource]]:
        source_by_subject = {
            source.metadata.get("claim_subject_entity_id"): source
            for source in execution.delta_sources
            if isinstance(
                source.metadata.get("claim_subject_entity_id"),
                str,
            )
        }
        lines: list[str] = []
        citations: list[EvidenceSource] = []
        for artist in execution.accepted_artists:
            reference = artist.reference
            source = source_by_subject.get(reference.knowledge_entity_id)
            if source is None:
                raise ValueError("accepted artist has no projected evidence")
            lines.append(
                f"{execution.genre_name} 与 {reference.canonical_name} "
                f"存在有证据支持的音乐关联。[{source.id}]"
            )
            citations.append(source)
        narrative = NarrativeOutput(
            narrative="\n".join(lines),
            citation_ids=[source.id for source in citations],
        )
        return narrative, citations

    async def create(self, query: str) -> WorldLineSessionResponse:
        session_id = uuid4().hex
        result = await self._run_turn(query, session_id=session_id)
        result = self._genre_policy.augment_branches(
            result,
            visited_nodes=[result.graph.root],
        )
        result = _assign_branch_ids(result, 1)
        now = datetime.now(timezone.utc)
        state = WorldLineSessionState(
            session_id=session_id, root_entity=result.root_entity,
            current_graph=result.graph, current_node=result.graph.root,
            visited_nodes=[result.graph.root], available_branches=result.branches,
            exploration_depth=0, evidence_history=result.sources,
            revision=1, last_result=result, created_at=now, updated_at=now,
        )
        await self._commit(state)
        return _response(state, self._genre_policy)

    async def explore(self, session_id: str, branch_id: str) -> WorldLineSessionResponse:
        async with self._session_lock(session_id):
            old = await self._read(session_id)
            branch = next((item for item in old.available_branches if item.id == branch_id), None)
            if branch is None:
                if branch_id in old.consumed_branch_ids:
                    raise WorldLineExecutionError("duplicate_exploration", "该分支已经探索过。", 409)
                raise WorldLineExecutionError("invalid_branch_id", "分支不存在或已过期。", 404)
            target_ref = branch.target_entity
            if target_ref.id in old.visited_nodes:
                raise WorldLineExecutionError("duplicate_exploration", "该节点已经探索过。", 409)
            if old.exploration_depth >= MAX_EXPLORATION_DEPTH:
                raise WorldLineExecutionError("depth_limit_reached", "已达到当前版本的探索深度上限。", 409)
            node = next((item for item in old.current_graph.nodes if item.id == target_ref.id), None)
            if node is None or node.type != target_ref.type or node.name != target_ref.name:
                raise WorldLineExecutionError("invalid_branch_target", "分支目标不属于当前世界线。", 409)
            if target_ref.type is WorldLineNodeType.GENRE:
                try:
                    context = self._genre_policy.resolve(
                        old.current_graph,
                        old.evidence_history,
                        node,
                    )
                except GenreActionError as exc:
                    raise WorldLineExecutionError(
                        exc.code,
                        "当前音乐类型暂不支持继续探索。",
                        422,
                    ) from exc
                result = await self._run_genre_turn(old, node, context)
            else:
                target = _canonical_target(
                    target_ref.id,
                    target_ref.type,
                    target_ref.name,
                )
                label = {
                    EntityType.SONG: "歌曲",
                    EntityType.ARTIST: "歌手",
                    EntityType.ALBUM: "专辑",
                }[target.entity_type]
                query = f"继续探索{label}《{target.name}》的音乐世界线"
                result = await self._run_turn(
                    query,
                    session_id=session_id,
                    target=target,
                    previous=old,
                )
            if result.root_entity != old.root_entity or result.graph.root != old.current_graph.root:
                raise WorldLineExecutionError("invalid_expansion_root", "世界线根实体发生变化。", 502)
            outcome = None
            if len(result.graph.nodes) == len(old.current_graph.nodes) and len(result.graph.edges) == len(old.current_graph.edges):
                outcome = "no_new_connections"
            next_visited = [*old.visited_nodes, target_ref.id]
            result = self._genre_policy.augment_branches(
                result,
                visited_nodes=next_visited,
            )
            result = _assign_branch_ids(result, old.revision + 1)
            new = WorldLineSessionState(
                session_id=old.session_id, root_entity=old.root_entity,
                current_graph=result.graph, current_node=target_ref.id,
                visited_nodes=next_visited,
                available_branches=result.branches,
                consumed_branch_ids=[*old.consumed_branch_ids, branch_id],
                exploration_depth=old.exploration_depth + 1,
                evidence_history=result.sources, revision=old.revision + 1,
                last_result=result, created_at=old.created_at,
                updated_at=datetime.now(timezone.utc),
            )
            # Re-read before writing to reject a stale turn in this process.
            if (await self._read(session_id)).revision != old.revision:
                raise WorldLineExecutionError("session_conflict", "会话已被更新，请重试。", 409)
            await self._commit(new)
            return _response(
                new,
                self._genre_policy,
                outcome=outcome,
            )


@lru_cache
def get_worldline_session_service() -> WorldLineSessionService:
    if checkpoint_backend() != "postgres":
        raise WorldLineExecutionError("persistent_checkpoint_required", "WorldLine 会话需要 PostgreSQL checkpoint。", 503)
    try:
        saver = create_runtime_checkpointer()
    except Exception as exc:
        raise WorldLineExecutionError("checkpoint_unavailable", "PostgreSQL checkpoint 尚未就绪。", 503) from exc
    return WorldLineSessionService(saver, distributed_lock=True)
