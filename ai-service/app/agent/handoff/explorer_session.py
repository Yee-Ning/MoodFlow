"""Explorer chat adapter over the existing WorldLine session lifecycle."""

from contextvars import ContextVar
from functools import lru_cache
from hashlib import sha256
import re
from typing import TypedDict

from langgraph.config import get_config
from langgraph.graph import START, END, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.agent.checkpointing import checkpoint_backend, create_runtime_checkpointer
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.models import (
    MusicEntityFacts,
    WorldLineSessionResult,
)
from app.agent.foundation.subgraphs.worldline.session import WorldLineSessionService
from app.agent.foundation.subgraphs.worldline.genre_actions import (
    GenreActionPolicy,
    GenreActionContext,
    GenreActionError,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceType,
    EntityType,
    ResolvedEntity,
)
from app.agent.foundation.subgraphs.worldline.service import WorldLineExecutionError


_turn = ContextVar("explorer_worldline_turn", default=None)
_CONTINUE = re.compile(
    r"继续|接着|沿.*分支|刚才.*(?:节点|分支)|(?:这个|那个|此|该).*(?:节点|分支)|展开讲|展开.*节点"
)


def is_branch_followup(text):
    return bool(_CONTINUE.search(text))


def owner_key(view, runtime):
    """Server conversation and identity only; never use a user-supplied session ID."""
    conversation = view.get("continuity_context", {}).get("conversation_id")
    if not conversation:
        try:
            conversation = get_config().get("configurable", {}).get("thread_id")
        except RuntimeError:
            conversation = None
    if not conversation:
        # Direct, unscoped calls remain v1; do not share anonymous global state.
        return None
    user = getattr(runtime.context, "user_id", None)
    return sha256(f"explorer:{user or 'anonymous'}:{conversation}".encode()).hexdigest()


class _Store(TypedDict, total=False):
    value: dict


def _save(state):
    return {"value": state["value"]}


class _TurnGraph:
    async def ainvoke(self, state, config):
        call = _turn.get()
        if call is None:
            raise RuntimeError("Explorer session turn has no request context")
        if call.get("prepared") is not None:
            result = call["prepared"].model_copy(update={"task_id": state["task_id"]})
            return {"task_result": result}
        from app.agent.handoff.explorer import journey_query

        state = dict(state)
        original_query = state["user_query"]
        state["user_query"] = journey_query(original_query)
        if "沿" in original_query:
            state["user_query"] += "；" + original_query
        state["current_input"] = state["user_query"]
        options = {"context": call["context"]}
        if isinstance(call["graph"], CompiledStateGraph):
            options["output_keys"] = ["task_result", "entity_facts"]
        output = await call["graph"].ainvoke(state, config, **options)
        result = TaskResult.model_validate(output.get("task_result"))
        if result.task_id != state["task_id"] or result.route_key != RouteKey.WORLDLINE:
            raise ValueError("Explorer session result changed its assigned task")
        if (
            result.status is TaskStatus.SUCCESS
            and output.get("entity_facts") is not None
        ):
            facts = MusicEntityFacts.model_validate(output["entity_facts"])
            if facts.entity.entity_id != state["trusted_target"].entity_id:
                raise ValueError("Explorer session catalog changed its selected target")
            call["facts"].append(facts)
        return {"task_result": result}


class _ExplorerFocusPolicy(GenreActionPolicy):
    """Non-executable vocabularies permit read-only focus, never Genre execution."""

    def resolve(self, graph, sources, node):
        genre = self.registry.get(node.id)
        if genre and genre.executable and genre.expandable:
            return super().resolve(graph, sources, node)
        reference = self._reference(node)
        if (
            not genre
            or not reference
            or reference.node_id != node.id
            or reference.canonical_name != node.name
            or reference.knowledge_entity_id != genre.canonical_id
            or reference.node_type.value != "genre"
            or reference.catalog_reference is not None
            or genre.canonical_name != node.name
            or node.metadata.get("registry_version") != self.registry.version
        ):
            raise GenreActionError("genre_focus_identity_missing")
        source_by_id = {item.id: item for item in sources}
        for edge in graph.edges:
            if (
                edge.target != node.id
                or edge.relation.value not in {"associated_with", "belongs_to_genre"}
                or not edge.evidence_ids
            ):
                continue
            artist = next(
                (
                    item
                    for item in graph.nodes
                    if item.id == edge.source and item.type.value == "artist"
                ),
                None,
            )
            artist_ref = self._reference(artist) if artist else None
            if (
                not artist_ref
                or artist_ref.catalog_reference is None
                or artist_ref.node_type.value != "artist"
                or artist_ref.node_id != artist.id
                or artist_ref.canonical_name != artist.name
                or artist_ref.catalog_reference.provider != "netease"
            ):
                continue
            if all(
                key in source_by_id
                and source_by_id[key].source_type is EvidenceType.KNOWLEDGE
                and source_by_id[key].excerpt
                and all(
                    isinstance(source_by_id[key].metadata.get(field), str)
                    for field in (
                        "claim_id",
                        "parent_id",
                        "child_id",
                        "validator_version",
                    )
                )
                and source_by_id[key].metadata.get("relation_key")
                == f"{edge.source}|{edge.target}|{edge.relation.value}"
                for key in edge.evidence_ids
            ):
                return GenreActionContext(
                    genre_reference=reference,
                    source_artist_reference=artist_ref,
                    trigger_evidence_ids=edge.evidence_ids,
                )
        raise GenreActionError("genre_focus_evidence_missing")


class _ExplorerWorldLineSessions(WorldLineSessionService):
    async def _run_genre_turn(self, old, node, context):
        genre = self._genre_policy.registry.get(node.id)
        if genre.executable and genre.expandable:
            return await super()._run_genre_turn(old, node, context)
        # Focus an existing catalog artist's evidence; do not call or enable the
        # disabled Genre executor, alter registry flags, or infer new artists.
        reference = context.source_artist_reference
        target = ResolvedEntity(
            entity_id=reference.node_id,
            entity_type=EntityType.ARTIST,
            name=reference.canonical_name,
            source="netease",
        )
        return await self._run_turn(
            f"继续探索歌手《{target.name}》的音乐世界线；沿 {node.name} 分支查找时代、场景、影响与风格证据",
            session_id=old.session_id,
            target=target,
            previous=old,
        )


class ExplorerSessions:
    """Persist the chat pointer/catalog receipts alongside unmodified WorldLine snapshots.

    Catalog receipts stay in Explorer-owned checkpoints, not in model summaries.
    WorldLine still owns merge, branch IDs, depth/duplicate checks and revisions.
    """

    def __init__(self, saver, *, genre_executor=None, distributed_lock=False):
        self.service = _ExplorerWorldLineSessions(
            saver,
            turn_graph=_TurnGraph(),
            genre_executor=genre_executor,
            distributed_lock=distributed_lock,
            genre_policy=_ExplorerFocusPolicy(GenreVocabularyRegistry.load()),
        )
        graph = StateGraph(_Store)
        graph.add_node("save", _save)
        graph.add_edge(START, "save")
        graph.add_edge("save", END)
        self.store = graph.compile(checkpointer=saver)

    @staticmethod
    def config(key):
        return {"configurable": {"thread_id": "explorer-chat:" + key}}

    async def read(self, key):
        state = await self.store.aget_state(self.config(key))
        return state.values.get("value") if state.values else None

    async def write(self, key, value):
        await self.store.ainvoke({"value": value}, self.config(key))

    async def adopt(self, result, facts, *, owner, graph, runtime):
        token = _turn.set(
            {
                "prepared": result,
                "graph": graph,
                "context": runtime.context,
                "facts": [],
            }
        )
        try:
            response = await self.service.create(result.content or "Explorer")
        finally:
            _turn.reset(token)
        await self.write(
            response.session_id,
            {
                "owner": owner,
                "catalog": [facts.model_dump(mode="json")] if facts else [],
            },
        )
        await self.write(owner, {"session_id": response.session_id})
        return response

    async def current(self, owner):
        pointer = await self.read(owner)
        if not pointer:
            raise WorldLineExecutionError(
                "explorer_session_missing",
                "当前对话没有可恢复的探索图，请先指定歌曲、歌手或专辑。",
                404,
            )
        session_id = pointer["session_id"]
        private = await self.read(session_id)
        if not private or private.get("owner") != owner:
            raise WorldLineExecutionError(
                "explorer_session_scope",
                "无法恢复当前账号与对话的探索图，请重新指定根实体。",
                409,
            )
        response = await self.service.recover(session_id)
        facts = [MusicEntityFacts.model_validate(item) for item in private["catalog"]]
        return response, facts

    async def follow(self, text, *, owner, graph, runtime):
        response, prior = await self.current(owner)
        if explains_selected_node(text, response):
            return response, prior, "explain_selected"
        branch = select_branch(text, response)
        action = next(
            item for item in response.available_actions if item.id == branch.id
        )
        if not action.enabled:
            raise WorldLineExecutionError(
                action.disabled_reason or "branch_disabled",
                "该分支暂不能继续扩展；可以选择其他可用分支。",
                409,
            )
        call = {
            "prepared": None,
            "graph": graph,
            "context": runtime.context,
            "facts": [],
        }
        token = _turn.set(call)
        try:
            expanded = await self.service.explore(response.session_id, branch.id)
        finally:
            _turn.reset(token)
        # Keep prior receipts even if a later catalog page returns fewer songs.
        # Depth is capped by WorldLine at three, so history is at most four reads.
        catalog = list(prior)
        for item in call["facts"]:
            if item not in catalog:
                catalog.append(item)
        await self.write(
            response.session_id,
            {
                "owner": owner,
                "catalog": [item.model_dump(mode="json") for item in catalog],
            },
        )
        return expanded, catalog, "expand_branch"


def explains_selected_node(text, response):
    """A resolved prior selection can be explained again without re-exploring it."""
    if len(response.visited_nodes) < 2 or not re.search(r"展开讲|讲讲|详细|解释", text):
        return False
    from app.rag.knowledge.entity_linking.registry import normalize_alias

    normalized = normalize_alias(text)
    registry = GenreVocabularyRegistry.load()
    mentioned = set()
    for node in response.graph.nodes:
        labels = [node.name]
        entry = registry.get(node.id)
        if entry:
            labels.extend(entry.aliases)
        if any(normalize_alias(label) in normalized for label in labels):
            mentioned.add(node.id)
    if mentioned:
        return mentioned == {response.current_node}
    return bool(re.search(r"(?:刚才|这个|那个|选中|此|该).*(?:节点|分支)", text))


def select_branch(text, response):
    """Resolve only real branch targets; never let prose invent a node/branch ID."""
    from app.agent.foundation.subgraphs.worldline.genre_registry import (
        GenreVocabularyRegistry,
    )
    from app.rag.knowledge.entity_linking.registry import normalize_alias

    normalized = normalize_alias(text)
    registry = GenreVocabularyRegistry.load()
    matches = []
    for branch in response.branches:
        labels = [branch.target_entity.name, branch.title]
        entry = registry.get(branch.target_entity.id)
        if entry:
            labels.extend([entry.canonical_name, *entry.aliases])
        if any(normalize_alias(label) in normalized for label in labels):
            matches.append(branch)
    if len(matches) == 1:
        return matches[0]
    if (
        not matches
        and len(response.branches) == 1
        and re.search(r"(?:这个|那个|此|该).*分支", text)
    ):
        return response.branches[0]
    options = (
        "、".join(branch.target_entity.name for branch in response.branches)
        or "无可用分支"
    )
    code = (
        "ambiguous_explorer_branch"
        if len(matches) > 1
        else "explorer_branch_unspecified"
    )
    raise WorldLineExecutionError(
        code, f"请明确要展开的节点：{options}。已保留当前图和选中节点。", 409
    )


@lru_cache
def get_explorer_sessions():
    return ExplorerSessions(
        create_runtime_checkpointer(),
        distributed_lock=checkpoint_backend() == "postgres",
    )


def session_world(response):
    return WorldLineSessionResult.model_validate(
        response.model_dump(include=set(WorldLineSessionResult.model_fields))
    )
