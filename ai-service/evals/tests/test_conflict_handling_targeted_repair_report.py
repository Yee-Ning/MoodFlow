"""Audit fixed repair exports without constraining future runtime repairs."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from evals.semantic_relations_metrics import aggregate
from evals.semantic_relations_models import file_hash, load_suite
from evals.tests.historical_scope import assert_historical_runtime_scope


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "semantic_relations_v1_review_r6"
BEFORE = ROOT / "reports/semantic_relations_v1_baseline_r6"
REPORT = ROOT / "reports/semantic_relations_v1_conflict_repair_r6"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def checked_export(directory):
    manifest = read_json(directory / "artifact_manifest.json")
    assert set(manifest["files"]) == {p.name for p in directory.iterdir() if p.is_file() and p.name != "artifact_manifest.json"}
    for name, digest in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or file_hash(path) != digest:
            raise ValueError("export_hash_mismatch")
    payload, results = read_json(directory / "metrics.json"), rows(directory / "results.jsonl")
    assert payload["metrics"] == aggregate(results)
    return payload, results


@pytest.fixture(scope="module")
def exported():
    return checked_export(REPORT)


def test_old_freezes_receipts_and_evaluator_scope_stay_frozen(exported):
    cases, _, _, manifest = load_suite(SUITE)
    payload, results = exported
    assert payload["manifest"] == manifest
    assert len(cases) == len(results) == 53
    protection = read_json(REPORT / "protected_artifacts.json")
    assert protection["file_count"] == len(protection["files"]) == 355
    for name, digest in protection["files"].items():
        assert file_hash(ROOT.parent / name) == digest
    scope = read_json(REPORT / "scope_validation.json")
    changed = {name for name in scope["runtime_after"] if scope["runtime_before"][name] != scope["runtime_after"][name]}
    assert changed == set(scope["changed_runtime_files"])
    assert len(changed) == 3 and all(name.endswith(("era_expansion.py", "scene_expansion.py", "influence_expansion.py")) for name in changed)
    assert scope["runtime_before"] == protection["runtime_before"]
    assert len(scope["runtime_before"]) == 28
    assert scope["evaluation_scoring_and_category_gate_unchanged"]


def test_metric_comparison_contains_every_cohort_family_and_metric(exported):
    after, results = exported
    before = read_json(BEFORE / "metrics.json")
    comparison = read_json(REPORT / "metrics_comparison.json")
    assert set(comparison["metrics"]) == set(after["metrics"]) == set(before["metrics"])
    for cohort in after["metrics"]:
        assert set(comparison["metrics"][cohort]) == {"overall", "era", "scene", "influence"}
        for family in comparison["metrics"][cohort]:
            expected = after["metrics"][cohort][family]["metrics"]
            assert set(comparison["metrics"][cohort][family]) == set(expected)
            for metric, value in expected.items():
                assert comparison["metrics"][cohort][family][metric] == {
                    "before": before["metrics"][cohort][family]["metrics"][metric], "after": value,
                }
    old = {r["case_id"]: r for r in rows(BEFORE / "results.jsonl")}
    assert comparison["improved_cases"] == [r["case_id"] for r in results if r["passed"] and not old[r["case_id"]]["passed"]]
    assert comparison["new_regressions"] == []
    assert after["metrics"]["synthetic_regression"] == before["metrics"]["synthetic_regression"]


def test_conservative_rejections_and_p0_incompleteness_are_explicit(exported):
    payload, results = exported
    real = payload["metrics"]["approved_real_baseline"]
    assert real["overall"]["passed"] == 19
    assert real["overall"]["metrics"]["conflict_handling"] == {"numerator": 2, "denominator": 3, "value": 2 / 3}
    assert [real[f]["metrics"]["conflict_handling"]["numerator"] for f in ("era", "scene", "influence")] == [1, 1, 0]
    for metric, count in [("weak_evidence_rejection", 4), ("negation_rejection", 3), ("query_isolation", 25), ("checkpoint_continuity", 25)]:
        assert real["overall"]["metrics"][metric] == {"numerator": count, "denominator": count, "value": 1}
    reverse = [r for r in results if r["cohort"] == "approved_real_baseline" and r["label"] == "negative" and "direction_sensitive" in r["categories"]]
    assert len(reverse) == 2 and all(r["passed"] and r["actual"]["relations"] == [] for r in reverse)
    gate = read_json(REPORT / "machine_gate.json")
    assert gate["baseline_ready"] is gate["can_enter_targeted_repairs"] is True
    assert gate["p0_conflict_target_met"] is gate["can_enter_p1_direction_repair"] is False
    assert gate["blocking_dependencies"] and gate["final_regression_ready"] is False
    assert gate["reverse_direction_negative"] == {"passed": 2, "count": 2}


def test_complete_conflict_traces_are_exact_slices_of_runtime_results(exported):
    _, results = exported
    traces = rows(REPORT / "conflict_case_traces.jsonl")
    conflicts = [r for r in results if r["cohort"] == "approved_real_baseline" and "conflict" in r["categories"]]
    assert {r["case_id"] for r in traces} == {r["case_id"] for r in conflicts}
    _, _, snapshots, _ = load_suite(SUITE)
    for trace in traces:
        row = next(r for r in conflicts if r["case_id"] == trace["case_id"])
        family = row["relation_type"]
        assert trace["query"] == row["query"]
        assert trace["retrieved_evidence"] == row["trace"]["retrieved_evidence"]
        assert trace["candidate_relations"] == row["trace"]["candidate_relations"][family]
        assert trace["validator"] == row["trace"]["filtering_scoring"][family]["decisions"]
        assert trace["conflict_aggregation"] == row["trace"]["filtering_scoring"][family]["trace"]["aggregation_trace"]
        assert trace["final_relations"] == row["actual"]["relations"]
        assert trace["expected_gold"] == row["trace"]["expected_gold"]
        for span in trace["expected_gold"]["supporting_evidence"]:
            source = snapshots[span["source_id"]]
            assert source.model_dump(mode="json") in trace["source_metadata"]
            assert source.content[span["span_start"]:span["span_end"]] == span["exact_span"]
            assert sha256(source.content.encode()).hexdigest() == source.content_hash
        if family == "influence":
            assert trace["final_relations"] == [] and not trace["passed"]
            assert all(item["aggregation_role"] == "excluded" for item in trace["conflict_aggregation"][0]["inputs"])
        else:
            claims = {s["claim_id"] for s in trace["expected_gold"]["supporting_evidence"]}
            for relation in trace["final_relations"]:
                assert {e["claim_id"] for e in relation["evidence"]} == claims
                assert relation["raw"]["metadata"]["conflict_flags"]
                assert all(e["traceable"] for e in relation["evidence"])


def test_real_failure_exports_and_diagnosis_never_omit_a_failure(exported):
    _, results = exported
    real_failures = [r for r in results if r["cohort"] == "approved_real_baseline" and not r["passed"]]
    assert len(real_failures) == 6
    assert rows(REPORT / "real_failures.jsonl") == real_failures
    assert rows(REPORT / "failures.jsonl") == [r for r in results if not r["passed"]]
    diagnosis = read_json(REPORT / "failure_diagnosis.json")
    assert {c["case_id"] for c in diagnosis["cases"]} == {r["case_id"] for r in real_failures}
    assert diagnosis["reviewed_real_failure_count"] == 6


def test_report_artifact_tampering_is_detected(tmp_path):
    directory = tmp_path / "export"
    shutil.copytree(REPORT, directory)
    with (directory / "conflict_case_traces.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="export_hash_mismatch"):
        checked_export(directory)


def test_historical_runtime_protection_rejects_a_tampered_scope():
    path = ROOT / "semantic_relations_v1_review_r6/scope_lock.json"
    scope = deepcopy(read_json(path))
    scope["runtime_and_evaluation_file_sha256"]["run_semantic_relations_eval.py"] = "0" * 64
    with pytest.raises(AssertionError, match="historical scope lock changed"):
        assert_historical_runtime_scope(path, scope)
