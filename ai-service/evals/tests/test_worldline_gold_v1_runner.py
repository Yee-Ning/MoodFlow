import asyncio
import json
from pathlib import Path

import pytest

from evals import worldline_eval_runner
from evals.tests.worldline_gold_v1_fixtures import (
    case_payload,
    source_payload,
    write_dataset,
)
from evals.worldline_eval_runner import (
    run_worldline_evaluation_suite,
    run_worldline_gold_evaluation,
)


def gold_config(
    tmp_path: Path,
    dataset_path: Path,
    ledger_path: Path,
) -> Path:
    config = {
        "schema_version": "worldline-gold-v1",
        "dataset_kind": "real_gold_fixture",
        "dataset": str(dataset_path),
        "source_ledger": str(ledger_path),
        "results": str(tmp_path / "gold-results.jsonl"),
        "metrics": str(tmp_path / "gold-metrics.json"),
        "failures": str(tmp_path / "gold-failures.jsonl"),
        "report": str(tmp_path / "gold-report.md"),
        "tiers": ["component_replay"],
        "postgres_env_flag": "RUN_WORLDLINE_EVAL_POSTGRES",
    }
    config_path = tmp_path / "gold-config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config_path


def test_real_gold_runner_isolated_outputs(tmp_path: Path) -> None:
    first_content = "Nujabes is associated with Hip-Hop."
    second_content = "Second Artist is associated with Hip-Hop."
    dataset_path, ledger_path = write_dataset(
        tmp_path,
        [
            case_payload(first_content),
            case_payload(
                second_content,
                suffix="second-artist",
                artist_name="Second Artist",
                artist_id="artist:second_artist",
                catalog_id="456",
            ),
        ],
        [
            source_payload(first_content),
            source_payload(second_content, suffix="second-artist"),
        ],
    )

    run, metrics, paths = asyncio.run(
        run_worldline_gold_evaluation(
            gold_config(tmp_path, dataset_path, ledger_path)
        )
    )

    assert run.dataset_kind == "real_gold_fixture"
    assert run.dataset_version == "v1"
    assert run.source_ledger_sha256 is not None
    assert len(run.results) == 5
    assert metrics["summary"]["passed"] == 5
    assert metrics["summary"]["failed"] == 0
    metric_payload = json.loads(paths["metrics"].read_text(encoding="utf-8"))
    assert metric_payload["dataset_kind"] == "real_gold_fixture"
    assert metric_payload["dataset_version"] == "v1"
    assert paths["failures"].read_text(encoding="utf-8") == ""
    report = paths["report"].read_text(encoding="utf-8")
    assert "WorldLine Real Gold V1 Evaluation Report" in report
    assert "reviewed real-gold fixtures" in report


def test_all_suite_keeps_synthetic_and_gold_results_separate(monkeypatch) -> None:
    synthetic_result = (object(), {"summary": {"cases": 36}}, {})
    gold_result = (object(), {"summary": {"cases": 5}}, {})

    async def fake_synthetic(*args, **kwargs):
        return synthetic_result

    async def fake_gold(*args, **kwargs):
        return gold_result

    monkeypatch.setattr(
        worldline_eval_runner,
        "run_worldline_evaluation",
        fake_synthetic,
    )
    monkeypatch.setattr(
        worldline_eval_runner,
        "run_worldline_gold_evaluation",
        fake_gold,
    )

    suites = asyncio.run(run_worldline_evaluation_suite("all"))

    assert suites == {
        "synthetic_baseline": synthetic_result,
        "real_gold_baseline": gold_result,
    }
    assert suites["synthetic_baseline"][1] is not suites["real_gold_baseline"][1]


def test_real_gold_runner_rejects_invalid_dataset_before_evaluation(
    tmp_path: Path,
) -> None:
    dataset_path, ledger_path = write_dataset(
        tmp_path,
        [case_payload()],
        [source_payload()],
    )
    payload = json.loads(dataset_path.read_text(encoding="utf-8"))
    payload["evidence"][0]["exact_span"] = "not in source"
    dataset_path.write_text(json.dumps(payload) + "\n", encoding="utf-8")

    with pytest.raises(ValueError, match="worldline_gold_dataset_invalid"):
        asyncio.run(
            run_worldline_gold_evaluation(
                gold_config(tmp_path, dataset_path, ledger_path)
            )
        )
