"""Deterministic metrics for WorldLine evaluation results."""

from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import Any, Iterable

from evals.worldline_eval_models import (
    WorldLineEvalResult,
    WorldLineEvalTask,
)


def _ratio(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _metric(numerator: int, denominator: int) -> dict[str, int | float | None]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "value": _ratio(numerator, denominator),
    }


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


def _percentile(values: list[int], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * quantile)
    return float(ordered[index])


def _completed(
    results: Iterable[WorldLineEvalResult],
    task: WorldLineEvalTask,
) -> list[WorldLineEvalResult]:
    return [
        result
        for result in results
        if result.task is task and result.status == "completed"
    ]


def semantic_metrics(results: list[WorldLineEvalResult]) -> dict[str, Any]:
    rows = _completed(results, WorldLineEvalTask.SEMANTIC_EXPANSION)
    predicted_total = gold_total = true_positive = 0
    evidence_total = evidence_valid = 0
    accepted = candidates = rejected = 0
    expected_rejections = matched_rejections = unsupported = 0
    for row in rows:
        predicted = {
            _relation_key(item)
            for item in row.actual.get("relations", [])
        }
        gold = {
            _relation_key(item)
            for item in row.expected.get("relations", [])
        }
        predicted_total += len(predicted)
        gold_total += len(gold)
        true_positive += len(predicted & gold)
        unsupported += sum(
            relation[2] != "associated_with" for relation in predicted
        )
        evidence = row.actual.get("evidence", [])
        evidence_total += len(evidence)
        evidence_valid += sum(bool(item.get("valid")) for item in evidence)
        trace = row.trace
        candidates += int(trace.get("candidate_count", 0))
        accepted += int(trace.get("accepted_count", 0))
        rejected += int(trace.get("rejected_count", 0))
        if row.expected.get("outcome") == "rejected":
            expected_rejections += 1
            expected_reasons = set(row.expected.get("rejection_reasons", []))
            actual_reasons = set(row.actual.get("rejection_reasons", []))
            matched_rejections += expected_reasons == actual_reasons
    grounded_true_positive = sum(
        len(
            {
                _relation_key(item)
                for item in row.actual.get("relations", [])
                if item.get("evidence_valid")
            }
            & {
                _relation_key(item)
                for item in row.expected.get("relations", [])
            }
        )
        for row in rows
    )
    return {
        "cases": len(rows),
        "semantic_relation_precision": _metric(
            true_positive,
            predicted_total,
        ),
        "semantic_relation_recall": _metric(true_positive, gold_total),
        "grounded_relation_precision": _metric(
            grounded_true_positive,
            predicted_total,
        ),
        "accepted_relation_rate": _metric(accepted, candidates),
        "rejection_rate": _metric(rejected, candidates),
        "expected_rejection_accuracy": _metric(
            matched_rejections,
            expected_rejections,
        ),
        "evidence_provenance_validity": _metric(
            evidence_valid,
            evidence_total,
        ),
        "unsupported_relation_count": unsupported,
    }


def exploration_metrics(results: list[WorldLineEvalResult]) -> dict[str, Any]:
    rows = _completed(results, WorldLineEvalTask.GENRE_EXPLORATION)
    positive = [row for row in rows if row.expected.get("outcome") == "accepted"]
    projected_total = gold_total = correct = 0
    successes = 0
    yields: list[int] = []
    candidate_count = evidence_accepted = catalog_resolved = projected = 0
    gate_cases = gate_correct = 0
    for row in rows:
        predicted = {
            _artist_key(item) for item in row.actual.get("artists", [])
        }
        gold = {
            _artist_key(item) for item in row.expected.get("artists", [])
        }
        projected_total += len(predicted)
        gold_total += len(gold)
        correct += len(predicted & gold)
        if row in positive:
            success = bool(predicted & gold) and row.passed
            successes += success
            if success:
                yields.append(len(predicted))
        if "action_gate_correct" in row.actual:
            gate_cases += 1
            gate_correct += bool(row.actual["action_gate_correct"])
        trace = row.trace
        candidate_count += int(trace.get("candidate_count", 0))
        evidence_accepted += int(trace.get("evidence_accepted_count", 0))
        catalog_resolved += int(trace.get("catalog_resolved_count", 0))
        projected += int(trace.get("projected_artist_count", 0))
    return {
        "cases": len(rows),
        "positive_cases": len(positive),
        "genre_expansion_success_rate": _metric(successes, len(positive)),
        "genre_artist_precision": _metric(correct, projected_total),
        "genre_artist_recall": _metric(correct, gold_total),
        "valid_artist_yield": {
            "sample_count": len(yields),
            "mean": mean(yields) if yields else None,
            "p50": _percentile(yields, 0.5),
            "p95": _percentile(yields, 0.95),
        },
        "evidence_acceptance_rate": _metric(
            evidence_accepted,
            candidate_count,
        ),
        "catalog_resolution_yield": _metric(
            catalog_resolved,
            evidence_accepted,
        ),
        "projection_yield": _metric(projected, candidate_count),
        "action_gate_accuracy": _metric(gate_correct, gate_cases),
    }


def identity_metrics(results: list[WorldLineEvalResult]) -> dict[str, Any]:
    rows = _completed(results, WorldLineEvalTask.ENTITY_RESOLUTION)
    resolvable = [row for row in rows if row.expected.get("status") == "resolved"]
    ambiguous = [row for row in rows if row.expected.get("status") == "ambiguous"]
    unresolved = [row for row in rows if row.expected.get("status") == "unresolved"]
    resolved_correct = sum(
        row.actual.get("status") == "resolved"
        and row.actual.get("catalog_node_id")
        == row.expected.get("catalog_node_id")
        for row in resolvable
    )
    actual_resolved = [row for row in rows if row.actual.get("status") == "resolved"]
    catalog_correct = sum(
        row.actual.get("catalog_node_id") == row.expected.get("catalog_node_id")
        for row in actual_resolved
    )
    ambiguity_correct = sum(
        row.actual.get("status") == "ambiguous" for row in ambiguous
    )
    reason_correct = sum(
        row.actual.get("status") == "unresolved"
        and row.actual.get("reason") == row.expected.get("reason")
        for row in unresolved
    )
    false_executable = sum(
        bool(row.actual.get("projected"))
        for row in rows
        if row.expected.get("status") != "resolved"
    )
    non_resolvable = [
        row for row in rows if row.expected.get("status") != "resolved"
    ]
    return {
        "cases": len(rows),
        "knowledge_catalog_resolution_success_rate": _metric(
            resolved_correct,
            len(resolvable),
        ),
        "catalog_identity_accuracy": _metric(
            catalog_correct,
            len(actual_resolved),
        ),
        "ambiguity_detection_rate": _metric(
            ambiguity_correct,
            len(ambiguous),
        ),
        "ambiguity_rate": _metric(
            sum(row.actual.get("status") == "ambiguous" for row in rows),
            len(rows),
        ),
        "unresolved_reason_accuracy": _metric(
            reason_correct,
            len(unresolved),
        ),
        "false_executable_identity_count": false_executable,
        "false_executable_identity_rate": _metric(
            false_executable,
            len(non_resolvable),
        ),
    }


def reliability_metrics(results: list[WorldLineEvalResult]) -> dict[str, Any]:
    merge_rows = _completed(results, WorldLineEvalTask.GRAPH_MERGE)
    valid_merge = [row for row in merge_rows if row.expected.get("outcome") == "accepted"]
    invalid_merge = [row for row in merge_rows if row.expected.get("outcome") == "rejected"]
    session_rows = [
        row
        for row in results
        if row.status == "completed"
        and row.tier.value == "session_replay"
        and row.task is WorldLineEvalTask.GENRE_EXPLORATION
    ]
    checkpoint_rows = _completed(results, WorldLineEvalTask.CHECKPOINT_RECOVERY)
    recovery = [row for row in checkpoint_rows if row.expected.get("outcome") == "recovered"]
    failure_atomicity = [
        row
        for row in results
        if row.expected.get("old_snapshot_unchanged") is True
        and row.status == "completed"
    ]
    return {
        "graph_merge_success_rate": _metric(
            sum(row.passed for row in valid_merge),
            len(valid_merge),
        ),
        "invalid_merge_rejection_rate": _metric(
            sum(row.passed for row in invalid_merge),
            len(invalid_merge),
        ),
        "session_continuity_rate": _metric(
            sum(row.passed for row in session_rows),
            len(session_rows),
        ),
        "checkpoint_recovery_success_rate": _metric(
            sum(row.passed for row in recovery),
            len(recovery),
        ),
        "post_recovery_exploration_success": _metric(
            sum(bool(row.actual.get("post_recovery_exploration")) for row in recovery),
            len(recovery),
        ),
        "failure_atomicity_rate": _metric(
            sum(bool(row.actual.get("old_snapshot_unchanged")) for row in failure_atomicity),
            len(failure_atomicity),
        ),
    }


def grounding_metrics(results: list[WorldLineEvalResult]) -> dict[str, Any]:
    rows = _completed(results, WorldLineEvalTask.GROUNDING)
    generated = [
        result
        for result in results
        if result.status == "completed" and "citation_valid" in result.actual
    ]
    return {
        "cases": len(rows),
        "grounding_validator_accuracy": _metric(
            sum(row.passed for row in rows),
            len(rows),
        ),
        "citation_validity": _metric(
            sum(bool(row.actual["citation_valid"]) for row in generated),
            len(generated),
        ),
    }


def compute_worldline_metrics(
    results: list[WorldLineEvalResult],
) -> dict[str, Any]:
    statuses = Counter(result.status for result in results)
    return {
        "summary": {
            "cases": len(results),
            "passed": sum(result.passed for result in results),
            "failed": sum(
                not result.passed and result.status == "completed"
                for result in results
            ),
            "skipped": statuses["skipped"],
            "errors": statuses["error"],
            "execution_success_rate": _metric(
                statuses["completed"],
                len(results),
            ),
        },
        "semantic_quality": semantic_metrics(results),
        "evidence_grounding": {
            **grounding_metrics(results),
            "semantic_evidence_provenance_validity": semantic_metrics(results)[
                "evidence_provenance_validity"
            ],
        },
        "exploration_quality": exploration_metrics(results),
        "identity_resolution": identity_metrics(results),
        "system_reliability": reliability_metrics(results),
    }
