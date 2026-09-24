from evals.worldline_eval_metrics import compute_worldline_metrics
from evals.worldline_eval_models import (
    WorldLineEvalResult,
    WorldLineEvalTask,
    WorldLineEvalTier,
)


def result(
    case_id: str,
    task: WorldLineEvalTask,
    *,
    expected=None,
    actual=None,
    trace=None,
    passed=True,
    status="completed",
    tier=WorldLineEvalTier.COMPONENT,
) -> WorldLineEvalResult:
    return WorldLineEvalResult(
        case_id=case_id,
        tier=tier,
        task=task,
        passed=passed,
        status=status,
        expected=expected or {"outcome": "accepted"},
        actual=actual or {},
        trace=trace or {},
        failures=[] if passed else ["expected mismatch"],
        duration_ms=1,
    )


def test_metrics_compute_quality_yield_identity_and_reliability() -> None:
    relation = {
        "source_id": "netease:artist:1",
        "target_id": "genre:hip_hop",
        "relation": "associated_with",
    }
    artist = {
        "knowledge_entity_id": "artist:new",
        "catalog_node_id": "netease:artist:2",
    }
    rows = [
        result(
            "semantic-positive",
            WorldLineEvalTask.SEMANTIC_EXPANSION,
            expected={"outcome": "accepted", "relations": [relation]},
            actual={
                "relations": [{**relation, "evidence_valid": True}],
                "evidence": [{"valid": True}],
            },
            trace={
                "candidate_count": 1,
                "accepted_count": 1,
                "rejected_count": 0,
            },
        ),
        result(
            "semantic-negative",
            WorldLineEvalTask.SEMANTIC_EXPANSION,
            expected={
                "outcome": "rejected",
                "rejection_reasons": ["insufficient_context"],
            },
            actual={
                "relations": [],
                "evidence": [],
                "rejection_reasons": ["insufficient_context"],
            },
            trace={
                "candidate_count": 1,
                "accepted_count": 0,
                "rejected_count": 1,
            },
        ),
        result(
            "genre-positive",
            WorldLineEvalTask.GENRE_EXPLORATION,
            expected={"outcome": "accepted", "artists": [artist]},
            actual={"artists": [artist], "action_enabled": True},
            trace={
                "candidate_count": 1,
                "evidence_accepted_count": 1,
                "catalog_resolved_count": 1,
                "projected_artist_count": 1,
            },
        ),
        result(
            "identity",
            WorldLineEvalTask.ENTITY_RESOLUTION,
            expected={
                "outcome": "resolved",
                "status": "resolved",
                "catalog_node_id": "netease:artist:2",
            },
            actual={
                "status": "resolved",
                "catalog_node_id": "netease:artist:2",
            },
        ),
        result(
            "merge",
            WorldLineEvalTask.GRAPH_MERGE,
            expected={"outcome": "accepted"},
        ),
        result(
            "checkpoint",
            WorldLineEvalTask.CHECKPOINT_RECOVERY,
            expected={"outcome": "recovered"},
            actual={"post_recovery_exploration": True},
            tier=WorldLineEvalTier.POSTGRES,
        ),
        result(
            "execution-error",
            WorldLineEvalTask.GROUNDING,
            status="error",
            passed=False,
        ),
    ]

    metrics = compute_worldline_metrics(rows)

    assert metrics["summary"]["execution_success_rate"]["value"] == 6 / 7
    assert metrics["semantic_quality"]["semantic_relation_precision"]["value"] == 1
    assert metrics["semantic_quality"]["accepted_relation_rate"]["value"] == 0.5
    assert metrics["semantic_quality"]["expected_rejection_accuracy"]["value"] == 1
    assert metrics["exploration_quality"]["genre_expansion_success_rate"]["value"] == 1
    assert metrics["identity_resolution"]["catalog_identity_accuracy"]["value"] == 1
    assert metrics["system_reliability"]["graph_merge_success_rate"]["value"] == 1
    assert metrics["system_reliability"]["checkpoint_recovery_success_rate"]["value"] == 1


def test_empty_denominators_are_na_not_zero() -> None:
    metrics = compute_worldline_metrics([])

    assert metrics["summary"]["execution_success_rate"]["value"] is None
    assert metrics["semantic_quality"]["semantic_relation_precision"]["value"] is None
    assert metrics["exploration_quality"]["catalog_resolution_yield"]["value"] is None
    assert metrics["identity_resolution"]["ambiguity_rate"]["value"] is None
    assert metrics["system_reliability"]["checkpoint_recovery_success_rate"]["value"] is None
