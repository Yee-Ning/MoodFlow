import asyncio
import json
from pathlib import Path

from evals.worldline_eval_runner import run_worldline_evaluation


SERVICE_ROOT = Path(__file__).parents[2]


def test_non_postgres_worldline_evaluation_passes(tmp_path: Path) -> None:
    config = {
        "schema_version": "worldline-eval-v0",
        "dataset": str(
            SERVICE_ROOT / "evals" / "datasets" / "worldline_eval_v0.jsonl"
        ),
        "results": str(tmp_path / "results.jsonl"),
        "metrics": str(tmp_path / "metrics.json"),
        "failures": str(tmp_path / "failures.jsonl"),
        "report": str(tmp_path / "report.md"),
        "tiers": ["component_replay", "session_replay"],
        "postgres_env_flag": "RUN_WORLDLINE_EVAL_POSTGRES",
    }
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")

    run, metrics, paths = asyncio.run(
        run_worldline_evaluation(config_path)
    )

    assert len(run.results) == 36
    assert all(result.passed for result in run.results)
    assert metrics["summary"] == {
        "cases": 36,
        "passed": 36,
        "failed": 0,
        "skipped": 0,
        "errors": 0,
        "execution_success_rate": {
            "numerator": 36,
            "denominator": 36,
            "value": 1,
        },
    }
    assert metrics["semantic_quality"]["semantic_relation_precision"]["value"] == 1
    assert metrics["evidence_grounding"]["citation_validity"]["value"] == 1
    assert metrics["exploration_quality"]["action_gate_accuracy"]["value"] == 1
    assert metrics["identity_resolution"]["false_executable_identity_count"] == 0
    assert metrics["system_reliability"]["failure_atomicity_rate"]["value"] == 1
    assert metrics["system_reliability"]["checkpoint_recovery_success_rate"]["value"] is None
    assert paths["results"].exists()
    assert paths["metrics"].exists()
    assert paths["failures"].read_text(encoding="utf-8") == ""
    report = paths["report"].read_text(encoding="utf-8")
    assert "WorldLine Evaluation V0 Report" in report
    assert "synthetic contract fixtures" in report
