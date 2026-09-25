import asyncio
import json

from evals.worldline_eval_metrics import compute_worldline_metrics
from evals.worldline_eval_models import WorldLineEvalTask
from evals.worldline_eval_runner import evaluate_case, run_worldline_gold_evaluation
from evals.worldline_gold_v1_adapter import adapt_worldline_gold_dataset
from evals.worldline_gold_v1_loader import load_worldline_gold_dataset
from evals.worldline_gold_v11_builder import build_worldline_gold_v11


def _build(tmp_path):
    dataset_path = tmp_path / "worldline_gold_v11.jsonl"
    source_path = tmp_path / "worldline_gold_v11_sources.jsonl"
    result = build_worldline_gold_v11(
        dataset_path=dataset_path,
        source_ledger_path=source_path,
    )
    loaded = load_worldline_gold_dataset(dataset_path, source_path)
    return result, loaded


def test_v11_builder_adds_reviewed_boundary_cases(tmp_path) -> None:
    result, loaded = _build(tmp_path)

    assert result.base_case_count == 8
    assert result.added_case_count == 10
    assert result.total_case_count == 18
    assert result.accepted_count == 12
    assert result.rejected_count == 6
    assert result.source_count == 13
    assert loaded.dataset_version == "v1.1"
    assert len(loaded.approved_cases) == 18
    assert all(
        evidence.evidence_text[evidence.span_start : evidence.span_end]
        == evidence.exact_span
        for case in loaded.cases
        for evidence in case.evidence
    )

    explicit = [case for case in loaded.cases if case.evaluation is not None]
    assert {case.evaluation.scenario for case in explicit} == {
        "semantic_no_evidence",
        "semantic_wrong_predicate",
        "semantic_duplicate_evidence",
        "identity_ambiguous",
        "identity_missing",
        "genre_disabled",
        "merge_source_conflict",
    }
    no_evidence = next(
        case for case in explicit if case.evaluation.scenario == "semantic_no_evidence"
    )
    assert no_evidence.evidence == []
    assert "does not assert the real-world relation is false" in (
        no_evidence.review.notes
    )


def test_v11_adapter_covers_requested_blind_spots(tmp_path) -> None:
    _, loaded = _build(tmp_path)
    adapted = adapt_worldline_gold_dataset(loaded)

    assert adapted.approved_gold_case_count == 18
    assert adapted.semantic_case_count == 13
    assert adapted.identity_case_count == 10
    assert adapted.genre_case_count == 4
    assert len(adapted.cases) == 28
    assert sum(
        case.task is WorldLineEvalTask.GRAPH_MERGE for case in adapted.cases
    ) == 1
    assert sum(
        case.expected.get("outcome") == "rejected"
        and case.task is WorldLineEvalTask.SEMANTIC_EXPANSION
        for case in adapted.cases
    ) == 4
    assert sum(
        case.expected.get("status") == "ambiguous" for case in adapted.cases
    ) == 1
    assert sum(
        case.expected.get("status") == "unresolved" for case in adapted.cases
    ) == 1
    assert sum(
        case.expected.get("error_code") == "genre_not_executable"
        for case in adapted.cases
    ) == 2


def test_v11_real_gold_metrics_have_nonzero_boundary_denominators(tmp_path) -> None:
    _, loaded = _build(tmp_path)
    adapted = adapt_worldline_gold_dataset(loaded)
    results = asyncio.run(_evaluate(adapted.cases))
    metrics = compute_worldline_metrics(results)

    assert all(result.passed for result in results), [
        (result.case_id, result.failures, result.actual) for result in results
    ]
    assert metrics["semantic_quality"]["expected_rejection_accuracy"] == {
        "numerator": 4,
        "denominator": 4,
        "value": 1,
    }
    assert metrics["identity_resolution"]["ambiguity_detection_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }
    assert metrics["identity_resolution"]["unresolved_reason_accuracy"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }
    assert metrics["identity_resolution"]["false_executable_identity_rate"] == {
        "numerator": 0,
        "denominator": 2,
        "value": 0,
    }
    assert metrics["exploration_quality"]["action_gate_accuracy"] == {
        "numerator": 4,
        "denominator": 4,
        "value": 1,
    }
    assert metrics["system_reliability"]["failure_atomicity_rate"] == {
        "numerator": 1,
        "denominator": 1,
        "value": 1,
    }


def test_v11_runner_preserves_version_and_isolated_outputs(tmp_path) -> None:
    dataset_path = tmp_path / "worldline_gold_v11.jsonl"
    source_path = tmp_path / "worldline_gold_v11_sources.jsonl"
    build_worldline_gold_v11(
        dataset_path=dataset_path,
        source_ledger_path=source_path,
    )
    config_path = tmp_path / "worldline_gold_v11_config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": "worldline-gold-v1",
                "dataset_kind": "real_gold_fixture",
                "dataset": str(dataset_path),
                "source_ledger": str(source_path),
                "results": str(tmp_path / "results.jsonl"),
                "metrics": str(tmp_path / "metrics.json"),
                "failures": str(tmp_path / "failures.jsonl"),
                "report": str(tmp_path / "report.md"),
                "tiers": ["component_replay", "session_replay"],
                "postgres_env_flag": "RUN_WORLDLINE_EVAL_POSTGRES",
            }
        ),
        encoding="utf-8",
    )

    run, metrics, paths = asyncio.run(
        run_worldline_gold_evaluation(config_path)
    )

    assert run.dataset_version == "v1.1"
    assert len(run.results) == 28
    assert metrics["summary"]["passed"] == 28
    assert paths["failures"].read_text(encoding="utf-8") == ""
    assert "WorldLine Real Gold V1.1 Evaluation Report" in paths[
        "report"
    ].read_text(encoding="utf-8")


async def _evaluate(cases):
    return [
        await evaluate_case(
            case,
            postgres_env_flag="RUN_WORLDLINE_EVAL_POSTGRES",
        )
        for case in cases
    ]
