"""Audit the frozen baseline export without locking future runtime repairs out."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import subprocess
import sys

import pytest

from evals.semantic_relations_metrics import METRICS, aggregate
from evals.semantic_relations_models import load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "semantic_relations_v1_review_r4"
REPORT = ROOT / "reports" / "semantic_relations_v1_baseline_r4"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.fixture(scope="module")
def exported():
    return read_json(REPORT / "metrics.json"), read_rows(REPORT / "results.jsonl")


def test_export_cohorts_approval_and_failure_inventory(exported):
    payload, rows = exported
    cases, _, _, manifest = load_suite(SUITE)
    assert payload["manifest"] == manifest
    assert {row["case_id"] for row in rows} == {case.case_id for case in cases}
    assert Counter(row["cohort"] for row in rows) == {
        "approved_real_baseline": 22, "synthetic_regression": 28,
    }
    assert payload["metrics"] == aggregate(rows)
    assert read_rows(REPORT / "failures.jsonl") == [row for row in rows if not row["passed"]]
    for row in rows:
        case = next(case for case in cases if case.case_id == row["case_id"])
        assert row["trace"]["expected_gold"] == case.model_dump(mode="json")
        assert row["cohort"] == case.cohort
    assert read_rows(REPORT / "real_failures.jsonl") == [
        row for row in rows if row["cohort"] == "approved_real_baseline" and not row["passed"]
    ]


def test_all_metric_groups_keep_denominators_and_abstentions(exported):
    payload, rows = exported
    for cohort, groups in payload["metrics"].items():
        assert set(groups) == {"overall", "era", "scene", "influence"}
        for family, group in groups.items():
            selected = [row for row in rows if row["cohort"] == cohort
                        and (family == "overall" or row["relation_type"] == family)]
            assert set(group["metrics"]) == set(METRICS)
            for name, metric in group["metrics"].items():
                numerator = sum(row["metric_counts"][name]["numerator"] for row in selected)
                denominator = sum(row["metric_counts"][name]["denominator"] for row in selected)
                assert metric == {"numerator": numerator, "denominator": denominator,
                                  "value": numerator / denominator if denominator else None}
    for row in rows:
        expected = row["trace"]["expected_gold"]["expected_relations"]
        count = row["metric_counts"]["influence_direction_accuracy"]
        assert count["denominator"] == (len(expected) if row["relation_type"] == "influence" else 0)
        if not row["actual"]["relations"]:
            assert row["metric_counts"]["relation_precision"]["denominator"] == 0


def test_source_spans_candidate_filter_and_checkpoint_trace(exported):
    _, rows = exported
    _, inputs, snapshots, _ = load_suite(SUITE)
    for row in rows:
        trace = row["trace"]
        assert trace["query"] == row["query"] == inputs[row["case_id"]].query
        assert trace["retrieved_evidence"] == [doc.model_dump(mode="json")
                                               for doc in inputs[row["case_id"]].documents]
        assert set(trace["candidate_relations"]) == set(trace["filtering_scoring"]) == {
            "era", "scene", "influence",
        }
        assert trace["final_relations"] == row["actual"]["relations"]
        assert trace["failure_reasons"] == row["failure_reasons"]
        for span in trace["expected_gold"]["supporting_evidence"]:
            assert snapshots[span["source_id"]].content[span["span_start"]:span["span_end"]] == span["exact_span"]
        checkpoint = row["actual"]["checkpoint"]
        assert checkpoint["scope"] == "result_contract_and_query_replay"
        assert checkpoint["production_restart_verified"] is False


def test_diagnosis_preserves_real_failures_and_observed_only_categories(exported):
    _, rows = exported
    diagnosis = read_json(REPORT / "failure_diagnosis.json")
    real = [row for row in rows if row["cohort"] == "approved_real_baseline"]
    failed = [row for row in real if not row["passed"]]
    assert {item["case_id"] for item in diagnosis["cases"]} == {row["case_id"] for row in failed}
    categories = Counter(reason for row in failed for reason in row["failure_reasons"])
    assert {item["category"]: item["case_count"] for item in diagnosis["priorities"]} == categories
    assert all(item["case_count"] > 0 for item in diagnosis["priorities"])
    assert diagnosis["runtime_optimized"] is False
    for item in diagnosis["cases"]:
        row = next(row for row in failed if row["case_id"] == item["case_id"])
        assert item["failure_reasons"] == row["failure_reasons"]
        assert item["final_relations"] == row["actual"]["relations"]
        assert item["expected_gold"] == row["trace"]["expected_gold"]


def test_frozen_artifacts_and_export_hashes():
    protected = read_json(REPORT / "protected_artifacts.json")
    assert protected["file_count"] == len(protected["files"]) == 177
    eol_variants = protected["historical_report_eol_variants"]
    assert len(eol_variants) == 7
    assert all(Path(name).parent == Path("evals/reports/semantic_relations_v1") for name in eol_variants)
    for name, digest in protected["files"].items():
        allowed = {digest}
        if name in eol_variants:
            allowed.add(eol_variants[name])
        assert sha256((ROOT.parent / name).read_bytes()).hexdigest() in allowed
    manifest = read_json(REPORT / "artifact_manifest.json")
    assert manifest["suite_manifest_sha256"] == sha256((SUITE / "manifest.json").read_bytes()).hexdigest()
    for name, digest in manifest["files"].items():
        assert sha256((REPORT / name).read_bytes()).hexdigest() == digest


def test_readable_report_matches_metric_tables_and_gate(exported):
    payload, _ = exported
    report = (REPORT / "semantic_relations_evaluation_v1_report.md").read_text(encoding="utf-8")
    for cohort in ("approved_real_baseline", "synthetic_regression"):
        groups = payload["metrics"][cohort]
        for metric in METRICS:
            cells = []
            for family in ("overall", "era", "scene", "influence"):
                value = groups[family]["metrics"][metric]
                cells.append("N/A (0/0)" if value["value"] is None else
                             f"{value['numerator']}/{value['denominator']} ({value['value']:.1%})")
            assert "| " + metric + " | " + " | ".join(cells) + " |" in report
    assert "can_enter_targeted_repairs=False" in report
    assert "production_restart_verified=false" in report
    assert "1/9" in report  # Rejection-heavy precision is not presented as recall.


def test_current_coverage_gate_remains_visible_without_mutating_gold(tmp_path):
    before = {path: sha256(path.read_bytes()).hexdigest()
              for path in SUITE.rglob("*") if path.is_file()}
    output = tmp_path / "baseline"
    completed = subprocess.run(
        [sys.executable, str(ROOT.parent / "run_semantic_relations_eval.py"),
         "--suite", str(SUITE), "--output", str(output)],
        cwd=ROOT.parent, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 2, completed.stderr
    payload = read_json(output / "metrics.json")
    assert payload["baseline_ready"] is False
    assert payload["can_enter_targeted_repairs"] is False
    assert "influence: approved real coverage missing confusable" in payload["blockers"]
    assert payload["metrics"]["approved_real_baseline"]["overall"]["case_count"] == 22
    assert payload["metrics"]["pending_real_diagnostic"]["overall"]["case_count"] == 0
    assert {path: sha256(path.read_bytes()).hexdigest() for path in before} == before
