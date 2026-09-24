"""Deterministic, evidence-gated Genre actions for WorldLine sessions."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from app.agent.foundation.subgraphs.worldline.entity_bridge import (
    WorldLineEntityReference,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceSource,
    WorldLineBranch,
    WorldLineBranchTarget,
    WorldLineBranchType,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
    WorldLineSessionResult,
)


class GenreActionError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class GenreActionContext(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    genre_reference: WorldLineEntityReference
    source_artist_reference: WorldLineEntityReference
    trigger_evidence_ids: list[str]


class GenreActionPolicy:
    def __init__(self, registry: GenreVocabularyRegistry) -> None:
        self.registry = registry

    def resolve(
        self,
        graph: WorldLineGraph,
        sources: list[EvidenceSource],
        node: WorldLineNode,
    ) -> GenreActionContext:
        if node.type is not WorldLineNodeType.GENRE:
            raise GenreActionError("unsupported_target_type")
        reference = self._reference(node)
        genre = self.registry.get(node.id)
        if (
            reference is None
            or reference.node_type is not WorldLineNodeType.GENRE
            or reference.node_id != node.id
            or reference.knowledge_entity_id != node.id
            or reference.canonical_name != node.name
            or reference.catalog_reference is not None
            or genre is None
            or genre.canonical_name != node.name
            or not genre.executable
            or not genre.expandable
            or node.metadata.get("registry_version") != self.registry.version
            or node.metadata.get("executable") is not True
            or node.metadata.get("expandable") is not True
        ):
            raise GenreActionError("genre_not_executable")
        source_by_id = {source.id: source for source in sources}
        for edge in graph.edges:
            if (
                edge.target != node.id
                or edge.relation is not WorldLineRelation.ASSOCIATED_WITH
                or not edge.evidence_ids
            ):
                continue
            source_node = next(
                (item for item in graph.nodes if item.id == edge.source),
                None,
            )
            if (
                source_node is None
                or source_node.type is not WorldLineNodeType.ARTIST
            ):
                continue
            artist_reference = self._reference(source_node)
            if (
                artist_reference is None
                or artist_reference.node_type is not WorldLineNodeType.ARTIST
                or artist_reference.node_id != source_node.id
            ):
                continue
            expected_relation_key = (
                f"{edge.source}|{edge.target}|{edge.relation.value}"
            )
            evidence = [source_by_id.get(item) for item in edge.evidence_ids]
            if any(item is None for item in evidence):
                continue
            if all(
                item is not None
                and item.metadata.get("relation_key")
                == expected_relation_key
                and isinstance(item.metadata.get("claim_id"), str)
                and isinstance(item.metadata.get("parent_id"), str)
                and isinstance(item.metadata.get("child_id"), str)
                and isinstance(item.metadata.get("validator_version"), str)
                and bool(item.excerpt)
                for item in evidence
            ):
                return GenreActionContext(
                    genre_reference=reference,
                    source_artist_reference=artist_reference,
                    trigger_evidence_ids=edge.evidence_ids,
                )
        raise GenreActionError("genre_evidence_missing")

    def augment_branches(
        self,
        result: WorldLineSessionResult,
        *,
        visited_nodes: list[str],
    ) -> WorldLineSessionResult:
        visited = set(visited_nodes)
        genre_branch: WorldLineBranch | None = None
        for node in result.graph.nodes:
            if node.id in visited or node.type is not WorldLineNodeType.GENRE:
                continue
            try:
                self.resolve(result.graph, result.sources, node)
            except GenreActionError:
                continue
            genre_branch = WorldLineBranch(
                id=f"branch:genre:{node.id.removeprefix('genre:')}",
                title=f"探索 {node.name}",
                description=f"沿已有证据继续发现与 {node.name} 相关的音乐人。",
                branch_type=WorldLineBranchType.GENRE,
                target_entity=WorldLineBranchTarget(
                    id=node.id,
                    type=node.type,
                    name=node.name,
                ),
            )
            break
        existing = [
            branch
            for branch in result.branches
            if branch.target_entity.type is not WorldLineNodeType.GENRE
            and branch.target_entity.id not in visited
            and (
                genre_branch is None
                or branch.target_entity.id
                != genre_branch.target_entity.id
            )
        ]
        branches = (
            [genre_branch, *existing[:2]]
            if genre_branch is not None
            else existing[:3]
        )
        return result.model_copy(update={"branches": branches})

    @staticmethod
    def _reference(node: WorldLineNode) -> WorldLineEntityReference | None:
        payload = node.metadata.get("entity_reference")
        try:
            return WorldLineEntityReference.model_validate(payload)
        except (TypeError, ValueError):
            return None
