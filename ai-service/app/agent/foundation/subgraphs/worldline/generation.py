import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from app.agent.foundation.subgraphs.worldline.models import (
    BranchesOutput,
    EvidenceSource,
    MusicEntityFacts,
    MusicKnowledgeDocument,
    NarrativeOutput,
    ResolvedEntity,
    WorldLineBranch,
    WorldLineBranchType,
    WorldLineGraph,
    WorldLineNodeType,
)
from app.agent.foundation.subgraphs.worldline.prompts import (
    BRANCH_SYSTEM_PROMPT,
    NARRATIVE_SYSTEM_PROMPT,
)
from app.llm.client import create_structured_response


class WorldLineGenerationError(RuntimeError):
    """Raised after structured generation or grounding validation is exhausted."""


class WorldLineStructuredRuntime(Protocol):
    async def generate(
        self,
        payload: dict[str, Any],
        *,
        instructions: str,
        schema: dict[str, Any],
        schema_name: str,
        retry: bool,
    ) -> str: ...


class ProviderWorldLineRuntime:
    async def generate(
        self,
        payload: dict[str, Any],
        *,
        instructions: str,
        schema: dict[str, Any],
        schema_name: str,
        retry: bool,
    ) -> str:
        if retry:
            instructions += (
                "\n上次输出未通过 JSON Schema 或事实落地校验。"
                "请只修正结构和依据，不要增加新事实。"
            )
        response = await create_structured_response(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                }
            ],
            instructions,
            schema,
            schema_name=schema_name,
        )
        if response.status != "completed" or not response.output_text:
            raise WorldLineGenerationError(
                "WorldLine LLM returned no completed structured response"
            )
        if any(
            item.type not in {"message", "reasoning"}
            for item in response.output
        ):
            raise WorldLineGenerationError(
                "WorldLine LLM returned an unexpected output type"
            )
        return response.output_text


class WorldLineGenerator(Protocol):
    async def generate_narrative(
        self,
        entity: ResolvedEntity,
        facts: MusicEntityFacts,
        documents: list[MusicKnowledgeDocument],
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> str: ...

    async def generate_branches(
        self,
        entity: ResolvedEntity,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> list[WorldLineBranch]: ...


SchemaModel = TypeVar("SchemaModel", bound=BaseModel)


class StructuredWorldLineGenerator:
    def __init__(
        self,
        runtime: WorldLineStructuredRuntime | None = None,
    ) -> None:
        self._runtime = runtime or ProviderWorldLineRuntime()

    async def generate_narrative(
        self,
        entity: ResolvedEntity,
        facts: MusicEntityFacts,
        documents: list[MusicKnowledgeDocument],
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> str:
        payload = {
            "root_entity": entity.model_dump(mode="json"),
            "entity_facts": facts.model_dump(mode="json"),
            "retrieved_knowledge": [
                item.model_dump(mode="json") for item in documents
            ],
            "knowledge_available": bool(documents),
            "worldline_graph": graph.model_dump(mode="json"),
            "sources": [item.model_dump(mode="json") for item in sources],
        }
        output = await self._validated_output(
            payload,
            instructions=NARRATIVE_SYSTEM_PROMPT,
            schema_model=NarrativeOutput,
            schema_name="worldline_narrative",
        )
        narrative = output.narrative.strip()
        if not documents:
            prefix = "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。"
            if not narrative.startswith(prefix):
                narrative = f"{prefix}{narrative}"
        return narrative

    async def generate_branches(
        self,
        entity: ResolvedEntity,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
    ) -> list[WorldLineBranch]:
        payload = {
            "root_entity": entity.model_dump(mode="json"),
            "worldline_graph": graph.model_dump(mode="json"),
            "sources": [item.model_dump(mode="json") for item in sources],
        }
        last_error: Exception | None = None
        for attempt in range(2):
            raw = await self._runtime.generate(
                payload,
                instructions=BRANCH_SYSTEM_PROMPT,
                schema=BranchesOutput.model_json_schema(),
                schema_name="worldline_branches",
                retry=bool(attempt),
            )
            try:
                output = BranchesOutput.model_validate_json(raw)
                _validate_grounded_branches(output.branches, graph)
                return output.branches
            except (ValidationError, ValueError) as exc:
                last_error = exc
        raise WorldLineGenerationError(
            "WorldLine branch output failed validation twice"
        ) from last_error

    async def _validated_output(
        self,
        payload: dict[str, Any],
        *,
        instructions: str,
        schema_model: type[SchemaModel],
        schema_name: str,
    ) -> SchemaModel:
        last_error: ValidationError | None = None
        for attempt in range(2):
            raw = await self._runtime.generate(
                payload,
                instructions=instructions,
                schema=schema_model.model_json_schema(),
                schema_name=schema_name,
                retry=bool(attempt),
            )
            try:
                return schema_model.model_validate_json(raw)
            except ValidationError as exc:
                last_error = exc
        raise WorldLineGenerationError(
            f"{schema_name} output failed validation twice"
        ) from last_error


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
