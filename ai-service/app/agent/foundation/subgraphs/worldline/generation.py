import json
import logging
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any, Protocol

from app.agent.foundation.model_input import prepare_model_input
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingError,
    WorldLineGroundingValidator,
    inline_citation_ids,
)

from app.agent.foundation.subgraphs.worldline.models import (
    BranchDraftOutput,
    ExplorationBranchDraftOutput,
    EvidenceSource,
    MusicEntityFacts,
    MusicKnowledgeDocument,
    NarrativeDraft,
    NarrativeOutput,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineGraph,
    WorldLineNodeType,
)
from app.agent.foundation.subgraphs.worldline.prompts import (
    BRANCH_SYSTEM_PROMPT,
    EXPLORATION_BRANCH_BASE_V1,
    EXPLORATION_BRANCH_RULE_V1,
    NARRATIVE_SYSTEM_PROMPT,
    WORLDLINE_RETRY_RULE_V1,
)
from app.agent.structured_output import (
    ModelOutput,
    SemanticValidationError,
    StructuredOutputError,
    StructuredOutputService,
)
from app.agent.structured_output.parser import model_output_from_response
from app.llm.client import create_generation_response


class WorldLineGenerationError(RuntimeError):
    """Raised after structured generation or grounding validation is exhausted."""


logger = logging.getLogger(__name__)


class WorldLineStructuredRuntime(Protocol):
    async def generate(
        self,
        payload: dict[str, Any],
        *,
        instructions: str,
        schema: dict[str, Any],
        schema_name: str,
        retry: bool,
    ) -> str | ModelOutput: ...


class ProviderWorldLineRuntime:
    async def generate(
        self,
        payload: dict[str, Any],
        *,
        instructions: str,
        schema: dict[str, Any],
        schema_name: str,
        retry: bool,
    ) -> ModelOutput:
        if retry:
            instructions += "\n" + WORLDLINE_RETRY_RULE_V1
        stage = {"worldline_narrative": "narrative", "worldline_branches": "branches"}.get(schema_name)
        from .stage_pack import evidence_row, prepare_explorer_input, record_selected_evidence
        # The graph and facts are structured constraints; existing RAG sources
        # are selected independently before the provider sees this one call.
        source_rows = payload.get("sources", [])
        required_source_ids = {
            evidence_id
            for edge in payload.get("worldline_graph", {}).get("edges", [])
            for evidence_id in edge.get("evidence_ids", [])
        } | {
            evidence_id
            for fact in payload.get("catalog_facts", [])
            for evidence_id in fact.get("evidence_ids", [])
        }
        source_metadata = {
            source["id"]: {key: str(source[key])[:512 if key == "url" else 160]
                           for key in ("source_type", "source", "url") if source.get(key)}
            for source in source_rows
        }

        def render(pack):
            projected, sources, context = {}, [], []
            system = ""
            for item in pack.items:
                if item.type == "system":
                    system = item.content
                elif item.source == "retry_feedback":
                    continue
                elif item.type == "structured_output":
                    projected.update(json.loads(item.content))
                elif item.type == "user_query":
                    projected["user_query"] = item.content
                elif item.type == "search_plan":
                    projected["search_plan"] = json.loads(item.content)
                elif item.type == "rag_evidence":
                    sources.append(evidence_row(item, source_metadata))
                else:
                    context.append({"type": item.type, "content": item.content})
            projected["sources"] = sources
            if stage == "narrative" and any(item.source == "rag" for item in pack.items):
                projected["knowledge_available"] = True
            if context:
                projected["context"] = context
            return [{"role": "user", "content": json.dumps(projected, ensure_ascii=False, separators=(",", ":"))}], system

        source_inputs = dict(structured_output={key: value for key, value in payload.items()
                               if key not in {"sources", "search_plan", "user_query", "continuity_context"}},
            search_plan=payload.get("search_plan"),
            rag_pack=SimpleNamespace(items=source_rows),
            rag_required_ids=required_source_ids,
            continuity_context=payload.get("continuity_context"))
        trace_context = {
                key: payload["continuity_context"][key]
                for key in ("task_id", "session_id", "request_id")
                if payload.get("continuity_context") and key in payload["continuity_context"]
            }
        query = str(payload.get("user_query") or payload.get("root_entity", {}).get("name") or "音乐世界线")
        from app.llm.runtime import agent_scope
        with agent_scope("explorer") if stage else nullcontext():
            if stage:
                from app.agent.foundation.agent_pack import AgentPackInputs
                model_input = await prepare_explorer_input(stage, query=query, instructions=instructions,
                    schema=schema, schema_name=schema_name, renderer=render, inputs=AgentPackInputs(**source_inputs),
                    retry=retry, exploration=stage == "branches" and "eligible_target_node_ids" in payload,
                    trace_context=trace_context)
                if stage == "narrative":
                    sent = json.loads(model_input.input_items[0]["content"])
                    record_selected_evidence(sent["sources"], knowledge_available=sent.get("knowledge_available", False))
            else:
                model_input = await prepare_model_input(RouteKey.WORLDLINE, schema=schema, schema_name=schema_name,
                    renderer=render, query=query, system=instructions, trace_context=trace_context, **source_inputs)
            response = await create_generation_response(model_input.input_items, model_input.instructions, schema,
                schema_name=schema_name, request_budget=model_input.budget)
        return model_output_from_response(response)


class WorldLineGenerator(Protocol):
    async def generate_narrative(
        self,
        entity: ResolvedEntity,
        facts: MusicEntityFacts,
        documents: list[MusicKnowledgeDocument],
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> NarrativeOutput: ...

    async def generate_branches(
        self,
        entity: ResolvedEntity,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> list[WorldLineBranch]: ...


class StructuredWorldLineGenerator:
    def __init__(
        self,
        runtime: WorldLineStructuredRuntime | None = None,
        *,
        grounding_validator: WorldLineGroundingValidator | None = None,
    ) -> None:
        self._runtime = runtime or ProviderWorldLineRuntime()
        self._grounding_validator = grounding_validator or WorldLineGroundingValidator()

    async def generate_narrative(
        self,
        entity: ResolvedEntity,
        facts: MusicEntityFacts,
        documents: list[MusicKnowledgeDocument],
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        *,
        user_query: str | None = None,
        search_plan: Any = None,
        continuity_context: dict[str, Any] | None = None,
        original_root: ResolvedEntity | None = None,
    ) -> NarrativeOutput:
        payload = {
            "root_entity": entity.model_dump(
                mode="json", include={"entity_id", "entity_type", "name", "artist", "album"}
            ),
            "catalog_facts": [
                item.model_dump(
                    mode="json",
                    include={"subject_id", "predicate", "value", "object_entity_id", "evidence_ids"},
                )
                for item in facts.facts
            ],
            "knowledge_available": bool(documents),
            "worldline_graph": _graph_payload(graph),
            "sources": [
                item.model_dump(
                    mode="json", include={"id", "source_type", "title", "source", "url", "excerpt"},
                    exclude_none=True,
                )
                for item in sources
            ],
        }
        for source_row, source in zip(payload["sources"], sources):
            if source.metadata.get("rag_item_id"):
                source_row["_prior_formal_source"] = True
            span = source.metadata.get("evidence_span")
            if isinstance(span, str) and span:
                source_row["evidence_span"] = span
        if user_query:
            payload["user_query"] = user_query
        if search_plan is not None:
            payload["search_plan"] = search_plan.model_dump(mode="json", exclude_none=True)
        if continuity_context:
            payload["continuity_context"] = continuity_context
        if original_root is not None:
            payload["original_root_entity"] = original_root.model_dump(
                mode="json", include={"entity_id", "entity_type", "name"}
            )
            payload["focus_entity"] = payload["root_entity"]
        disclosure = "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。"
        from .stage_pack import active_evidence, raise_hard_source_failure, selected_evidence_scope

        def knowledge_available():
            selected = active_evidence.get()
            if selected is not None and selected.knowledge_available is not None:
                return selected.knowledge_available
            return bool(documents)

        def validate(draft: NarrativeDraft) -> None:
            narrative = draft.narrative.strip()
            if not knowledge_available() and not narrative.startswith(disclosure):
                raise SemanticValidationError("missing_no_knowledge_disclosure")
            output = NarrativeOutput(
                narrative=narrative,
                citation_ids=inline_citation_ids(narrative),
            )
            try:
                selected = active_evidence.get()
                actual_sources = selected.sources if selected is not None and selected.sources is not None else sources
                self._grounding_validator.validate(output, actual_sources)
            except WorldLineGroundingError as exc:
                raise SemanticValidationError(exc.code) from exc

        def repair(raw: dict[str, Any], _: Any) -> dict[str, Any] | None:
            repaired = dict(raw)
            # Previous LLM schema exposed a duplicate citation list. Prose is authoritative.
            repaired.pop("citation_ids", None)
            narrative = repaired.get("narrative")
            if isinstance(narrative, str) and not knowledge_available() and not narrative.strip().startswith(disclosure):
                repaired["narrative"] = f"{disclosure}\n{narrative.strip()}"
            return repaired if repaired != raw else None

        service = StructuredOutputService(
            "worldline_narrative", NarrativeDraft,
            semantic_validator=validate, repair=repair,
        )

        async def generate(attempt: int, feedback: str | None) -> str | ModelOutput:
            instructions = NARRATIVE_SYSTEM_PROMPT
            if feedback:
                instructions += f"\n{feedback}"
            return await self._runtime.generate(
                payload,
                instructions=instructions,
                schema=NarrativeDraft.model_json_schema(),
                schema_name="worldline_narrative",
                retry=attempt > 1,
            )

        try:
            with selected_evidence_scope() if active_evidence.get() is None else nullcontext():
                draft = await service.run(generate)
        except StructuredOutputError as exc:
            raise_hard_source_failure(exc)
            raise WorldLineGenerationError("worldline_narrative_invalid") from exc
        narrative = draft.narrative.strip()
        return NarrativeOutput(
            narrative=narrative,
            citation_ids=inline_citation_ids(narrative),
        )

    async def generate_branches(
        self,
        entity: ResolvedEntity,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        *,
        user_query: str | None = None,
        search_plan: Any = None,
        continuity_context: dict[str, Any] | None = None,
        visited_nodes: list[str] | None = None,
    ) -> list[WorldLineBranch]:
        exploration = visited_nodes is not None
        eligible = {
            node.id for node in graph.nodes
            if node.id not in (visited_nodes or [])
            and node.type in {WorldLineNodeType.SONG, WorldLineNodeType.ARTIST, WorldLineNodeType.ALBUM}
            and node.id.startswith(f"netease:{node.type.value}:")
            and node.id.rsplit(":", 1)[-1].isdigit()
            and int(node.id.rsplit(":", 1)[-1]) > 0
        }
        if exploration and not eligible:
            return []
        payload = {
            "root_entity": entity.model_dump(mode="json", include={"entity_id", "name"}),
            "worldline_graph": _graph_payload(graph),
            "sources": [
                {**item.model_dump(mode="json", include={"id", "title"}),
                 **({"_prior_formal_source": True} if item.metadata.get("rag_item_id") else {})}
                for item in sources
            ],
        }
        if user_query:
            payload["user_query"] = user_query
        if search_plan is not None:
            payload["search_plan"] = search_plan.model_dump(mode="json", exclude_none=True)
        if continuity_context:
            payload["continuity_context"] = continuity_context
        if exploration:
            payload["eligible_target_node_ids"] = sorted(eligible)

        def validate(draft: BranchDraftOutput | ExplorationBranchDraftOutput) -> None:
            branches = _materialize_branches(draft, graph)
            if exploration and any(branch.target_entity.id not in eligible for branch in branches):
                raise SemanticValidationError("ineligible_branch_target")

        def repair(raw: dict[str, Any], _: Any) -> dict[str, Any] | None:
            # Accept only the known V0 target shape; the graph supplies type/name/id.
            items = raw.get("branches")
            if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
                return None
            changed = False
            repaired_items = []
            for item in items:
                branch = dict(item)
                target = branch.pop("target_entity", None)
                if target is not None:
                    changed = True
                if "id" in branch:
                    branch.pop("id")
                    changed = True
                if isinstance(target, dict) and "target_node_id" not in branch:
                    branch["target_node_id"] = target.get("id")
                    changed = True
                repaired_items.append(branch)
            return {**raw, "branches": repaired_items} if changed else None

        output_model = ExplorationBranchDraftOutput if exploration else BranchDraftOutput
        service = StructuredOutputService(
            "worldline_branches", output_model,
            semantic_validator=validate, repair=repair,
        )

        async def generate(attempt: int, feedback: str | None) -> str | ModelOutput:
            instructions = BRANCH_SYSTEM_PROMPT
            if exploration:
                instructions = EXPLORATION_BRANCH_BASE_V1 + "\n" + EXPLORATION_BRANCH_RULE_V1
            if feedback:
                instructions += f"\n{feedback}"
            return await self._runtime.generate(
                payload,
                instructions=instructions,
                schema=output_model.model_json_schema(),
                schema_name="worldline_branches",
                retry=attempt > 1,
            )

        try:
            draft = await service.run(generate)
        except StructuredOutputError as exc:
            from .stage_pack import raise_hard_source_failure
            raise_hard_source_failure(exc)
            if exploration:
                logger.warning("worldline.exploration_branch_fallback")
                return _grounded_exploration_branches(graph, sources, eligible)
            raise WorldLineGenerationError("worldline_branches_invalid") from exc
        return _materialize_branches(draft, graph)

    async def generate_exploration_branches(
        self,
        entity: ResolvedEntity,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        *,
        visited_nodes: list[str],
        user_query: str | None = None,
        search_plan: Any = None,
        continuity_context: dict[str, Any] | None = None,
    ) -> list[WorldLineBranch]:
        return await self.generate_branches(
            entity, graph, sources, visited_nodes=visited_nodes,
            user_query=user_query, search_plan=search_plan,
            continuity_context=continuity_context,
        )

_BRANCH_TARGET_TYPES = {
    WorldLineBranchType.ARTIST: {WorldLineNodeType.ARTIST},
    WorldLineBranchType.ALBUM: {WorldLineNodeType.ALBUM},
    WorldLineBranchType.CREATOR: {WorldLineNodeType.CREATOR},
    WorldLineBranchType.COLLABORATION: {
        WorldLineNodeType.ARTIST,
        WorldLineNodeType.CREATOR,
    },
    WorldLineBranchType.GENRE: {WorldLineNodeType.GENRE},
    WorldLineBranchType.ERA: {WorldLineNodeType.ERA},
    WorldLineBranchType.RELATED_WORK: {
        WorldLineNodeType.SONG,
        WorldLineNodeType.ALBUM,
        WorldLineNodeType.RELATED_WORK,
    },
}


def _grounded_exploration_branches(
    graph: WorldLineGraph,
    sources: list[EvidenceSource],
    eligible: set[str],
) -> list[WorldLineBranch]:
    """Keep continuation available using only evidenced, unvisited graph nodes."""
    source_ids = {source.id for source in sources}
    evidenced_nodes = {
        endpoint
        for edge in graph.edges
        if edge.evidence_ids and set(edge.evidence_ids).issubset(source_ids)
        for endpoint in (edge.source, edge.target)
    }
    branch_types = {
        WorldLineNodeType.SONG: WorldLineBranchType.RELATED_WORK,
        WorldLineNodeType.ARTIST: WorldLineBranchType.ARTIST,
        WorldLineNodeType.ALBUM: WorldLineBranchType.ALBUM,
    }
    branches = [
        WorldLineBranch(
            id=f"branch:{index}",
            title=f"继续探索《{node.name}》",
            description="沿当前图谱中已有来源的关系继续探索。",
            branch_type=branch_types[node.type],
            target_entity=WorldLineBranchTarget(
                id=node.id, type=node.type, name=node.name,
            ),
        )
        for index, node in enumerate(
            (node for node in graph.nodes if node.id in eligible and node.id in evidenced_nodes),
            start=1,
        )
    ][:3]
    _validate_grounded_branches(branches, graph)
    return branches


def _graph_payload(graph: WorldLineGraph) -> dict[str, Any]:
    """Project once from validated graph data; omit repeated metadata and prose."""
    return {
        "root": graph.root,
        "nodes": [
            node.model_dump(mode="json", include={"id", "type", "name"})
            for node in graph.nodes
        ],
        "edges": [
            edge.model_dump(
                mode="json", include={"source", "target", "relation", "evidence_ids"}
            )
            for edge in graph.edges
        ],
    }


def _materialize_branches(
    draft: BranchDraftOutput | ExplorationBranchDraftOutput,
    graph: WorldLineGraph,
) -> list[WorldLineBranch]:
    nodes = {node.id: node for node in graph.nodes}
    selected: set[str] = set()
    branches: list[WorldLineBranch] = []
    for index, item in enumerate(draft.branches, start=1):
        node = nodes.get(item.target_node_id)
        if node is None:
            raise SemanticValidationError("unknown_branch_target")
        if node.id in selected:
            raise SemanticValidationError("duplicate_branch_target")
        if node.type not in _BRANCH_TARGET_TYPES[item.branch_type]:
            raise SemanticValidationError("branch_type_target_mismatch")
        selected.add(node.id)
        branches.append(
            WorldLineBranch(
                id=f"branch:{index}",
                title=item.title,
                description=item.description,
                branch_type=item.branch_type,
                target_entity=WorldLineBranchTarget(
                    id=node.id, type=node.type, name=node.name,
                ),
            )
        )
    _validate_grounded_branches(branches, graph)
    return branches


def _validate_grounded_branches(
    branches: list[WorldLineBranch],
    graph: WorldLineGraph,
) -> None:
    nodes = {node.id: node for node in graph.nodes}
    for branch in branches:
        target = branch.target_entity
        node = nodes.get(target.id)
        if node is None or node.type is not target.type or node.name != target.name:
            raise ValueError("branch target must exactly match a graph node")
        if target.type not in _BRANCH_TARGET_TYPES[branch.branch_type]:
            raise ValueError("branch type is incompatible with its graph target")
