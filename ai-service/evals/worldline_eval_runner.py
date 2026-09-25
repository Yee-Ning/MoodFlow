"""Offline runner for isolated WorldLine synthetic and Real Gold suites."""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import subprocess
import sys
from time import perf_counter
from typing import Any
from unittest.mock import AsyncMock, patch

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.subgraphs.worldline.entity_bridge import EntityBridge
from app.agent.foundation.subgraphs.worldline.genre_actions import (
    GenreActionError,
    GenreActionPolicy,
)
from app.agent.foundation.subgraphs.worldline.genre_executor import (
    GenreExecutorInput,
    GenreExecutorResult,
)
from app.agent.foundation.subgraphs.worldline.genre_registry import (
    GenreVocabularyRegistry,
)
from app.agent.foundation.subgraphs.worldline.grounding import (
    WorldLineGroundingError,
    WorldLineGroundingValidator,
)
from app.agent.foundation.subgraphs.worldline.merge import (
    WorldLineMergeError,
    merge_worldline_graph,
)
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceSource,
    EvidenceType,
    NarrativeOutput,
    ResolvedEntity,
    WorldLineEdge,
    WorldLineGraph,
    WorldLineNode,
    WorldLineNodeType,
    WorldLineRelation,
)
from app.agent.foundation.subgraphs.worldline.session import (
    WorldLineExecutionError,
    WorldLineSessionService,
)
from app.rag.knowledge.entity_linking.models import (
    EntityRegistryDocument,
    EntityType as KnowledgeEntityType,
    RegistryEntity,
)
from app.rag.knowledge.entity_linking.registry import EntityRegistry
from evals.worldline_eval_fixtures import (
    FixedTurnGraph,
    genre_fixture,
    semantic_fixture,
)
from evals.worldline_eval_metrics import compute_worldline_metrics
from evals.worldline_eval_models import (
    SCHEMA_VERSION,
    WorldLineEvalCase,
    WorldLineEvalResult,
    WorldLineEvalRun,
    WorldLineEvalTask,
    WorldLineEvalTier,
    approved_cases,
    dataset_sha256,
    load_worldline_dataset,
)
from evals.worldline_eval_report import render_worldline_report
from evals.worldline_gold_v1_adapter import adapt_worldline_gold_dataset
from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v1_models import (
    SCHEMA_VERSION as GOLD_SCHEMA_VERSION,
)


SERVICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = SERVICE_ROOT / "evals" / "config" / "worldline_eval_v0.json"
DEFAULT_GOLD_CONFIG = (
    SERVICE_ROOT / "evals" / "config" / "worldline_gold_v11.json"
)


def _relation_key(value: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(value.get("source_id", "")),
        str(value.get("target_id", "")),
        str(value.get("relation", "")),
    )


def _artist_key(value: dict[str, Any]) -> tuple[str, str]:
    return (
        str(value.get("knowledge_entity_id", "")),
        str(value.get("catalog_node_id", "")),
    )


def _source(source_id: str, *, title: str | None = None) -> EvidenceSource:
    return EvidenceSource(
        id=source_id,
        source_type=EvidenceType.MUSIC_API,
        title=title or source_id,
        source="eval",
    )


def _node(node_id: str, node_type: WorldLineNodeType) -> WorldLineNode:
    return WorldLineNode(id=node_id, type=node_type, name=node_id)


def _expected_match(
    expected: list[dict[str, Any]],
    actual: list[dict[str, Any]],
    key,
) -> bool:
    return {key(item) for item in expected} == {key(item) for item in actual}


async def _semantic_case(case: WorldLineEvalCase) -> tuple[dict, dict, list[str]]:
    fixture = semantic_fixture(case)
    result = fixture.service.expand_artist_genres(
        fixture.artist,
        fixture.search_plan,
        fixture.graph,
        fixture.sources,
        fixture.documents,
    )
    expected_relations = case.expected.get("relations", [])
    expected_by_key = {
        _relation_key(item): item for item in expected_relations
    }
    source_by_id = {source.id: source for source in result.sources}
    relations = []
    evidence = []
    for edge in result.graph.edges:
        if edge.relation is not WorldLineRelation.ASSOCIATED_WITH:
            continue
        key = (edge.source, edge.target, edge.relation.value)
        expected = expected_by_key.get(key)
        valid = bool(expected) and bool(edge.evidence_ids)
        for evidence_id in edge.evidence_ids:
            source = source_by_id.get(evidence_id)
            item_valid = bool(
                source is not None
                and expected is not None
                and source.metadata.get("claim_id") == expected.get("claim_id")
                and source.excerpt == expected.get("exact_span")
                and any(
                    source.excerpt in document.content
                    for document in fixture.documents
                    if source.excerpt
                )
            )
            evidence.append({"id": evidence_id, "valid": item_valid})
            valid = valid and item_valid
        relations.append(
            {
                "source_id": edge.source,
                "target_id": edge.target,
                "relation": edge.relation.value,
                "evidence_valid": valid,
            }
        )
    rejection_reasons = sorted(
        {
            reason
            for decision in result.rejected
            for reason in decision.rejection_reasons
        }
    )
    actual = {
        "relations": relations,
        "evidence": evidence,
        "rejection_reasons": rejection_reasons,
    }
    failures: list[str] = []
    if case.expected["outcome"] == "accepted":
        if not _expected_match(expected_relations, relations, _relation_key):
            failures.append("semantic_relation_mismatch")
        if any(not item["valid"] for item in evidence) or not evidence:
            failures.append("semantic_evidence_invalid")
    else:
        if relations:
            failures.append("unexpected_semantic_relation")
        if result.trace.candidate_count != case.expected.get("candidate_count"):
            failures.append("semantic_candidate_count_mismatch")
        if rejection_reasons != sorted(case.expected.get("rejection_reasons", [])):
            failures.append("semantic_rejection_reason_mismatch")
    return actual, result.trace.model_dump(mode="json"), failures


class _RecordingExecutor:
    def __init__(self, executor) -> None:
        self.executor = executor
        self.last_result: GenreExecutorResult | None = None

    async def execute(self, value: GenreExecutorInput) -> GenreExecutorResult:
        self.last_result = await self.executor.execute(value)
        return self.last_result


async def _genre_component_case(
    case: WorldLineEvalCase,
) -> tuple[dict, dict, list[str]]:
    fixture = genre_fixture(case)
    policy = GenreActionPolicy(GenreVocabularyRegistry.load())
    genre_node = next(
        node
        for node in fixture.graph.nodes
        if node.id == case.input["target_genre_id"]
    )
    try:
        context = policy.resolve(fixture.graph, fixture.sources, genre_node)
        action_enabled = True
    except GenreActionError as exc:
        actual = {
            "artists": [],
            "action_enabled": False,
            "action_gate_correct": case.expected.get("error_code") == exc.code,
            "error_code": exc.code,
        }
        failures = []
        if case.expected.get("error_code") != exc.code:
            failures.append("genre_action_error_mismatch")
        return actual, {}, failures
    execution = await fixture.executor.execute(
        GenreExecutorInput(
            session_id=f"eval-{case.case_id}",
            genre_id=genre_node.id,
            genre_name=genre_node.name,
            genre_reference=context.genre_reference,
            source_artist_reference=context.source_artist_reference,
            trigger_evidence_ids=context.trigger_evidence_ids,
            current_graph=fixture.graph,
            current_sources=fixture.sources,
            visited_nodes=[fixture.graph.root],
            exploration_depth=0,
        )
    )
    artists = [
        {
            "knowledge_entity_id": artist.reference.knowledge_entity_id,
            "catalog_node_id": artist.reference.node_id,
        }
        for artist in execution.accepted_artists
    ]
    actual = {
        "artists": artists,
        "action_enabled": action_enabled,
        "action_gate_correct": True,
        "rejection_reasons": sorted(execution.rejected_reason),
        "evidence_valid": all(
            source.excerpt
            and source.metadata.get("claim_id")
            and source.id in execution.evidence_ids
            for source in execution.delta_sources
        ),
    }
    failures: list[str] = []
    if case.expected["outcome"] == "accepted":
        if not _expected_match(
            case.expected["artists"],
            artists,
            _artist_key,
        ):
            failures.append("genre_artist_mismatch")
        if not actual["evidence_valid"] or not execution.delta_sources:
            failures.append("genre_evidence_invalid")
    else:
        if artists:
            failures.append("unexpected_genre_artist")
        if actual["rejection_reasons"] != sorted(
            case.expected.get("rejection_reasons", [])
        ):
            failures.append("genre_rejection_reason_mismatch")
    return actual, execution.trace.model_dump(mode="json"), failures


async def _genre_session_case(
    case: WorldLineEvalCase,
) -> tuple[dict, dict, list[str]]:
    fixture = genre_fixture(case)
    recorder = _RecordingExecutor(fixture.executor)
    service = WorldLineSessionService(
        create_memory_checkpointer(),
        turn_graph=FixedTurnGraph(fixture.initial_result),
        genre_executor=recorder,  # type: ignore[arg-type]
    )
    created = await service.create(f"Evaluate {case.case_id}")
    actions = [
        action
        for action in created.available_actions
        if action.target_node_id == case.input["target_genre_id"]
    ]
    enabled = next((action for action in actions if action.enabled), None)
    if case.expected["outcome"] == "rejected":
        actual = {
            "artists": [],
            "action_enabled": enabled is not None,
            "action_gate_correct": enabled is None,
            "error_code": (
                "genre_not_executable" if enabled is None else None
            ),
        }
        failures = []
        if enabled is not None:
            failures.append("disabled_genre_action_enabled")
        if actual["error_code"] != case.expected.get("error_code"):
            failures.append("genre_action_error_mismatch")
        return actual, {}, failures
    if enabled is None:
        return (
            {"artists": [], "action_enabled": False},
            {},
            ["genre_action_missing"],
        )
    expanded = await service.explore(created.session_id, enabled.id)
    stored = await service.load(created.session_id)
    expected_ids = {
        artist["catalog_node_id"] for artist in case.expected["artists"]
    }
    artists = [
        {
            "knowledge_entity_id": node.metadata.get(
                "entity_reference", {}
            ).get("knowledge_entity_id", ""),
            "catalog_node_id": node.id,
        }
        for node in expanded.graph.nodes
        if node.id in expected_ids
    ]
    old_nodes = {node.id for node in created.graph.nodes}
    old_sources = {source.id for source in created.sources}
    actual = {
        "artists": artists,
        "action_enabled": True,
        "old_graph_preserved": old_nodes.issubset(
            {node.id for node in expanded.graph.nodes}
        ),
        "old_sources_preserved": old_sources.issubset(
            {source.id for source in expanded.sources}
        ),
        "revision_delta": expanded.revision - created.revision,
        "depth_delta": expanded.exploration_depth - created.exploration_depth,
        "checkpoint_matches_response": (
            stored.current_graph == expanded.graph
            and stored.evidence_history == expanded.sources
            and stored.revision == expanded.revision
        ),
        "citation_valid": True,
        "action_gate_correct": True,
    }
    failures = []
    if not _expected_match(case.expected["artists"], artists, _artist_key):
        failures.append("genre_artist_mismatch")
    if not actual["old_graph_preserved"] or not actual["old_sources_preserved"]:
        failures.append("session_history_lost")
    if actual["revision_delta"] != case.expected["expected_revision_delta"]:
        failures.append("session_revision_mismatch")
    if actual["depth_delta"] != case.expected["expected_depth_delta"]:
        failures.append("session_depth_mismatch")
    if not actual["checkpoint_matches_response"]:
        failures.append("session_checkpoint_mismatch")
    trace = (
        recorder.last_result.trace.model_dump(mode="json")
        if recorder.last_result is not None
        else {}
    )
    return actual, trace, failures


async def _identity_case(case: WorldLineEvalCase) -> tuple[dict, dict, list[str]]:
    registered = case.fixtures["scenario"] != "identity_unregistered"
    entries = []
    if registered:
        entries.append(
            RegistryEntity(
                entity_id=case.input["knowledge_entity_id"],
                entity_type=KnowledgeEntityType.ARTIST,
                canonical_name=case.input["canonical_name"],
                aliases=case.input.get("aliases", []),
            )
        )
    bridge = EntityBridge(
        EntityRegistry(
            EntityRegistryDocument(
                registry_version="v99",
                entities=entries
                or [
                    RegistryEntity(
                        entity_id="artist:placeholder",
                        entity_type=KnowledgeEntityType.ARTIST,
                        canonical_name="Placeholder",
                    )
                ],
            )
        ),
        GenreVocabularyRegistry.load(),
    )
    candidates = [
        ResolvedEntity(
            entity_id=item["id"],
            entity_type=EntityType.ARTIST,
            name=item["name"],
            artist=item["name"],
            source=item.get("provider", "netease"),
        )
        for item in case.input["catalog_candidates"]
    ]
    resolution = bridge.resolve_catalog_artist_candidates(
        case.input["knowledge_entity_id"],
        candidates,
    )
    actual = {
        "status": resolution.status.value,
        "reason": resolution.reason,
        "catalog_node_id": (
            resolution.reference.node_id if resolution.reference else None
        ),
        "projected": resolution.reference is not None,
    }
    failures = []
    for field in ("status", "reason"):
        if actual[field] != case.expected.get(field):
            failures.append(f"identity_{field}_mismatch")
    if case.expected.get("catalog_node_id") != actual["catalog_node_id"]:
        failures.append("identity_catalog_mismatch")
    return actual, {}, failures


async def _merge_case(case: WorldLineEvalCase) -> tuple[dict, dict, list[str]]:
    scenario = case.fixtures["scenario"]
    root = _node("netease:artist:1", WorldLineNodeType.ARTIST)
    genre = _node("genre:hip_hop", WorldLineNodeType.GENRE)
    album = _node("netease:album:3", WorldLineNodeType.ALBUM)
    if scenario == "merge_append":
        old = WorldLineGraph(
            root=root.id,
            nodes=[root, genre],
            edges=[
                WorldLineEdge(
                    source=root.id,
                    target=genre.id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    evidence_ids=["source:1"],
                )
            ],
        )
        increment = WorldLineGraph(
            root=genre.id,
            nodes=[genre, album],
            edges=[
                WorldLineEdge(
                    source=genre.id,
                    target=album.id,
                    relation=WorldLineRelation.RELATED_TO,
                    evidence_ids=["source:2"],
                )
            ],
        )
        old_sources = [_source("source:1")]
        new_sources = [_source("source:2")]
    else:
        old = WorldLineGraph(
            root=root.id,
            nodes=[root, genre],
            edges=[
                WorldLineEdge(
                    source=root.id,
                    target=genre.id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    evidence_ids=["source:1"],
                )
            ],
        )
        increment = WorldLineGraph(
            root=root.id,
            nodes=[root, genre],
            edges=[
                WorldLineEdge(
                    source=root.id,
                    target=genre.id,
                    relation=WorldLineRelation.ASSOCIATED_WITH,
                    evidence_ids=["source:2"],
                )
            ],
        )
        old_sources = [_source("source:1")]
        new_sources = [_source("source:2")]
        if scenario == "merge_source_conflict":
            new_sources = [_source("source:1", title="conflict")]
            increment = WorldLineGraph(root=root.id, nodes=[root, genre])
    old_dump = old.model_dump(mode="json")
    source_dump = [source.model_dump(mode="json") for source in old_sources]
    try:
        merged, sources = merge_worldline_graph(
            old,
            increment,
            old_sources,
            new_sources,
            target_node_id=increment.root,
        )
        actual = {
            "node_count": len(merged.nodes),
            "edge_count": len(merged.edges),
            "evidence_ids": (
                merged.edges[0].evidence_ids if merged.edges else []
            ),
            "old_snapshot_unchanged": (
                old.model_dump(mode="json") == old_dump
                and [source.model_dump(mode="json") for source in old_sources]
                == source_dump
            ),
            "source_count": len(sources),
        }
        failures = []
        for field in ("node_count", "edge_count", "evidence_ids"):
            if field in case.expected and actual[field] != case.expected[field]:
                failures.append(f"merge_{field}_mismatch")
        if not actual["old_snapshot_unchanged"]:
            failures.append("merge_mutated_old_snapshot")
        return actual, {}, failures
    except WorldLineMergeError as exc:
        actual = {
            "error_code": exc.code,
            "old_snapshot_unchanged": (
                old.model_dump(mode="json") == old_dump
                and [source.model_dump(mode="json") for source in old_sources]
                == source_dump
            ),
        }
        failures = []
        if exc.code != case.expected.get("error_code"):
            failures.append("merge_error_mismatch")
        if not actual["old_snapshot_unchanged"]:
            failures.append("merge_mutated_old_snapshot")
        return actual, {}, failures


async def _grounding_case(case: WorldLineEvalCase) -> tuple[dict, dict, list[str]]:
    source = EvidenceSource(
        id="knowledge:source:1",
        source_type=EvidenceType.KNOWLEDGE,
        title="Evidence",
        source="eval",
        excerpt="Grounded fact.",
    )
    output = NarrativeOutput(
        narrative="Unsupported claim.[knowledge:missing]",
        citation_ids=["knowledge:missing"],
    )
    try:
        WorldLineGroundingValidator().validate(output, [source])
        return {"valid": True}, {}, ["grounding_error_missing"]
    except WorldLineGroundingError as exc:
        failures = []
        if exc.code != case.expected.get("error_code"):
            failures.append("grounding_error_mismatch")
        return {"valid": False, "error_code": exc.code}, {}, failures


async def _checkpoint_session_case(
    case: WorldLineEvalCase,
) -> tuple[dict, dict, list[str]]:
    if case.input["scenario"] == "missing_session":
        service = WorldLineSessionService(
            create_memory_checkpointer(),
            turn_graph=object(),
        )
        try:
            await service.load("missing-eval-session")
            return {}, {}, ["checkpoint_missing_session_recovered"]
        except WorldLineExecutionError as exc:
            actual = {
                "error_code": exc.code,
                "old_snapshot_unchanged": True,
            }
            failures = [] if exc.code == case.expected["error_code"] else [
                "checkpoint_error_mismatch"
            ]
            return actual, {}, failures
    source_case = load_worldline_dataset(
        SERVICE_ROOT / "evals" / "datasets" / "worldline_eval_v0.jsonl"
    )[18]
    fixture = genre_fixture(source_case)
    service = WorldLineSessionService(
        create_memory_checkpointer(),
        turn_graph=FixedTurnGraph(fixture.initial_result),
        genre_executor=fixture.executor,
    )
    created = await service.create("Evaluate checkpoint write failure")
    action = next(item for item in created.available_actions if item.enabled)
    with patch.object(
        service._snapshots,
        "ainvoke",
        new=AsyncMock(side_effect=RuntimeError("eval write failure")),
    ):
        try:
            await service.explore(created.session_id, action.id)
            return {}, {}, ["checkpoint_write_failure_missing"]
        except WorldLineExecutionError as exc:
            restored = await service.load(created.session_id)
            actual = {
                "error_code": exc.code,
                "old_snapshot_unchanged": (
                    restored.revision == created.revision
                    and restored.current_graph == created.graph
                    and restored.evidence_history == created.sources
                ),
            }
            failures = []
            if exc.code != case.expected["error_code"]:
                failures.append("checkpoint_error_mismatch")
            if not actual["old_snapshot_unchanged"]:
                failures.append("checkpoint_failure_mutated_state")
            return actual, {}, failures


def _worker_process(mode: str, *args: str) -> dict[str, Any]:
    environment = {
        **os.environ,
        "CHECKPOINT_BACKEND": "postgres",
        "PYTHONPATH": str(SERVICE_ROOT),
    }
    completed = subprocess.run(
        [
            sys.executable,
            "tests/fixtures/worldline_postgres_restart_worker.py",
            mode,
            *args,
        ],
        cwd=SERVICE_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=90,
    )
    output = completed.stdout.strip().splitlines()
    payload = json.loads(output[-1]) if output else {}
    if completed.returncode != 0:
        raise RuntimeError(f"worldline_eval_worker_failed:{mode}:{payload}")
    return payload


async def _postgres_case(
    case: WorldLineEvalCase,
    env_flag: str,
) -> tuple[dict, dict, list[str]] | None:
    if os.getenv(env_flag) != "1":
        return None
    prefix = "genre_" if case.fixtures["worker_mode"] == "genre" else ""
    created = await asyncio.to_thread(_worker_process, f"{prefix}create")
    expanded = await asyncio.to_thread(
        _worker_process,
        f"{prefix}explore",
        created["session_id"],
        created["branch_id"],
    )
    recovered = await asyncio.to_thread(
        _worker_process,
        f"{prefix}verify",
        created["session_id"],
    )
    actual = {
        "revision": recovered["revision"],
        "graph_preserved": set(created["nodes"]).issubset(expanded["nodes"]),
        "sources_preserved": set(created["sources"]).issubset(
            expanded["sources"]
        ),
        "recovered_graph_matches": recovered["nodes"] == expanded["nodes"],
        "recovered_sources_match": recovered["sources"] == expanded["sources"],
        "post_recovery_exploration": (
            expanded["revision"] == created["revision"] + 1
            and recovered["revision"] == expanded["revision"]
        ),
    }
    failures = []
    if actual["revision"] != case.expected["revision"]:
        failures.append("checkpoint_revision_mismatch")
    for field in (
        "graph_preserved",
        "sources_preserved",
        "recovered_graph_matches",
        "recovered_sources_match",
        "post_recovery_exploration",
    ):
        if not actual[field]:
            failures.append(f"checkpoint_{field}_failed")
    return actual, {}, failures


async def evaluate_case(
    case: WorldLineEvalCase,
    *,
    postgres_env_flag: str,
) -> WorldLineEvalResult:
    started = perf_counter()
    try:
        if case.tier is WorldLineEvalTier.POSTGRES:
            outcome = await _postgres_case(case, postgres_env_flag)
            if outcome is None:
                return WorldLineEvalResult(
                    case_id=case.case_id,
                    tier=case.tier,
                    task=case.task,
                    passed=False,
                    status="skipped",
                    expected=case.expected,
                    failures=[f"set {postgres_env_flag}=1"],
                    duration_ms=(perf_counter() - started) * 1000,
                )
        elif case.task is WorldLineEvalTask.SEMANTIC_EXPANSION:
            outcome = await _semantic_case(case)
        elif case.task is WorldLineEvalTask.GENRE_EXPLORATION:
            outcome = (
                await _genre_session_case(case)
                if case.tier is WorldLineEvalTier.SESSION
                else await _genre_component_case(case)
            )
        elif case.task is WorldLineEvalTask.ENTITY_RESOLUTION:
            outcome = await _identity_case(case)
        elif case.task is WorldLineEvalTask.GRAPH_MERGE:
            outcome = await _merge_case(case)
        elif case.task is WorldLineEvalTask.GROUNDING:
            outcome = await _grounding_case(case)
        elif case.task is WorldLineEvalTask.CHECKPOINT_RECOVERY:
            outcome = await _checkpoint_session_case(case)
        else:
            raise ValueError(f"unsupported evaluation task:{case.task}")
        actual, trace, failures = outcome
        return WorldLineEvalResult(
            case_id=case.case_id,
            tier=case.tier,
            task=case.task,
            passed=not failures,
            status="completed",
            expected=case.expected,
            actual=actual,
            trace=trace,
            failures=failures,
            duration_ms=(perf_counter() - started) * 1000,
        )
    except Exception as exc:
        return WorldLineEvalResult(
            case_id=case.case_id,
            tier=case.tier,
            task=case.task,
            passed=False,
            status="error",
            expected=case.expected,
            actual={"error_type": type(exc).__name__},
            failures=[str(exc) or type(exc).__name__],
            duration_ms=(perf_counter() - started) * 1000,
        )


def _read_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "dataset",
        "results",
        "metrics",
        "failures",
        "report",
        "tiers",
        "postgres_env_flag",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(f"worldline_eval_config_invalid:{path}")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError(f"worldline_eval_config_invalid:{path}")
    return payload


def _read_gold_config(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "schema_version",
        "dataset_kind",
        "dataset",
        "source_ledger",
        "results",
        "metrics",
        "failures",
        "report",
        "tiers",
        "postgres_env_flag",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(f"worldline_gold_config_invalid:{path}")
    if (
        payload["schema_version"] != GOLD_SCHEMA_VERSION
        or payload["dataset_kind"] != "real_gold_fixture"
    ):
        raise ValueError(f"worldline_gold_config_invalid:{path}")
    return payload


def _git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=SERVICE_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(
            json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
            for row in rows
        ),
        encoding="utf-8",
    )


def _write_evaluation_outputs(
    run: WorldLineEvalRun,
    metrics: dict[str, Any],
    paths: dict[str, Path],
) -> None:
    _write_jsonl(
        paths["results"],
        [result.model_dump(mode="json") for result in run.results],
    )
    _write_jsonl(
        paths["failures"],
        [
            {
                "case_id": result.case_id,
                "tier": result.tier.value,
                "task": result.task.value,
                "status": result.status,
                "expected": result.expected,
                "actual": result.actual,
                "failures": result.failures,
            }
            for result in run.results
            if not result.passed
        ],
    )
    paths["metrics"].parent.mkdir(parents=True, exist_ok=True)
    paths["metrics"].write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "dataset_version": run.dataset_version,
                "dataset_kind": run.dataset_kind,
                "dataset_sha256": run.dataset_sha256,
                "source_ledger_sha256": run.source_ledger_sha256,
                "git_commit": run.git_commit,
                "evaluation_timestamp": run.evaluation_timestamp,
                "metrics": metrics,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    render_worldline_report(run, metrics, paths["report"])


async def run_worldline_evaluation(
    config_path: Path = DEFAULT_CONFIG,
    *,
    include_postgres: bool = False,
) -> tuple[WorldLineEvalRun, dict[str, Any], dict[str, Path]]:
    config = _read_config(config_path)
    dataset_path = SERVICE_ROOT / config["dataset"]
    cases = approved_cases(load_worldline_dataset(dataset_path))
    tiers = {WorldLineEvalTier(value) for value in config["tiers"]}
    if include_postgres:
        tiers.add(WorldLineEvalTier.POSTGRES)
    selected = [case for case in cases if case.tier in tiers]
    results = []
    for case in selected:
        results.append(
            await evaluate_case(
                case,
                postgres_env_flag=config["postgres_env_flag"],
            )
        )
    run = WorldLineEvalRun(
        schema_version=SCHEMA_VERSION,
        dataset_version="v0",
        dataset_kind="synthetic_contract_fixture",
        dataset_sha256=dataset_sha256(dataset_path),
        git_commit=_git_commit(),
        evaluation_timestamp=datetime.now(UTC).isoformat(),
        results=results,
    )
    metrics = compute_worldline_metrics(results)
    paths = {
        key: SERVICE_ROOT / config[key]
        for key in ("results", "metrics", "failures", "report")
    }
    _write_evaluation_outputs(run, metrics, paths)
    return run, metrics, paths


async def run_worldline_gold_evaluation(
    config_path: Path = DEFAULT_GOLD_CONFIG,
) -> tuple[WorldLineEvalRun, dict[str, Any], dict[str, Path]]:
    config = _read_gold_config(config_path)
    dataset = load_worldline_gold_dataset(
        SERVICE_ROOT / config["dataset"],
        SERVICE_ROOT / config["source_ledger"],
    )
    adapted = adapt_worldline_gold_dataset(dataset)
    tiers = {WorldLineEvalTier(value) for value in config["tiers"]}
    selected = [case for case in adapted.cases if case.tier in tiers]
    results = [
        await evaluate_case(
            case,
            postgres_env_flag=config["postgres_env_flag"],
        )
        for case in selected
    ]
    run = WorldLineEvalRun(
        schema_version=SCHEMA_VERSION,
        dataset_version=dataset.dataset_version,
        dataset_kind="real_gold_fixture",
        dataset_sha256=dataset.dataset_sha256,
        source_ledger_sha256=dataset.source_ledger_sha256,
        git_commit=_git_commit(),
        evaluation_timestamp=datetime.now(UTC).isoformat(),
        results=results,
    )
    metrics = compute_worldline_metrics(results)
    paths = {
        key: SERVICE_ROOT / config[key]
        for key in ("results", "metrics", "failures", "report")
    }
    _write_evaluation_outputs(run, metrics, paths)
    return run, metrics, paths


async def run_worldline_evaluation_suite(
    suite: str,
    *,
    synthetic_config: Path = DEFAULT_CONFIG,
    gold_config: Path = DEFAULT_GOLD_CONFIG,
    include_postgres: bool = False,
) -> dict[
    str,
    tuple[WorldLineEvalRun, dict[str, Any], dict[str, Path]],
]:
    if suite not in {"synthetic", "real-gold", "all"}:
        raise ValueError(f"worldline_eval_suite_invalid:{suite}")
    runs = {}
    if suite in {"synthetic", "all"}:
        runs["synthetic_baseline"] = await run_worldline_evaluation(
            synthetic_config,
            include_postgres=include_postgres,
        )
    if suite in {"real-gold", "all"}:
        runs["real_gold_baseline"] = await run_worldline_gold_evaluation(
            gold_config,
        )
    return runs


async def _main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--suite",
        choices=("synthetic", "real-gold", "all"),
        default="synthetic",
    )
    parser.add_argument("--config", type=Path)
    parser.add_argument("--synthetic-config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--gold-config", type=Path, default=DEFAULT_GOLD_CONFIG)
    parser.add_argument("--include-postgres", action="store_true")
    args = parser.parse_args()
    if args.config is not None and args.suite == "all":
        parser.error("--config cannot be used with --suite all")
    synthetic_config = (
        args.config if args.config is not None and args.suite == "synthetic"
        else args.synthetic_config
    )
    gold_config = (
        args.config if args.config is not None and args.suite == "real-gold"
        else args.gold_config
    )
    suites = await run_worldline_evaluation_suite(
        args.suite,
        synthetic_config=synthetic_config,
        gold_config=gold_config,
        include_postgres=args.include_postgres,
    )
    summaries = {
        name: {
            "dataset_kind": run.dataset_kind,
            "cases": len(run.results),
            **metrics["summary"],
        }
        for name, (run, metrics, _) in suites.items()
    }
    print(json.dumps(summaries, ensure_ascii=False))
    return 0 if all(
        summary["failed"] == 0 and summary["errors"] == 0
        for summary in summaries.values()
    ) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
