"""Audit frozen r6 exports; do not rerun or constrain future runtime repairs."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from evals.semantic_relations_metrics import METRICS, aggregate
from evals.semantic_relations_models import load_suite


ROOT = Path(__file__).resolve().parents[1]
SUITE = ROOT / "semantic_relations_v1_review_r6"
REPORT = ROOT / "reports/semantic_relations_v1_baseline_r6"
PREVIOUS = ROOT / "reports/semantic_relations_v1_baseline_r4"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def check_export(directory):
    manifest = read_json(directory / "artifact_manifest.json")
    for name, digest in manifest["files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or sha256(path.read_bytes()).hexdigest() != digest:
            raise ValueError("export_hash_mismatch")
    payload, rows = read_json(directory / "metrics.json"), read_rows(directory / "results.jsonl")
    if payload["metrics"] != aggregate(rows):
        raise ValueError("export_metric_counts_mismatch")
    return payload, rows


@pytest.fixture(scope="module")
def exported():
    return check_export(REPORT)


def test_cohort_receipts_and_complete_real_failure_exports(exported):
    payload, rows = exported
    cases, _, _, manifest = load_suite(SUITE)
    assert payload["manifest"] == manifest
    assert {r["case_id"] for r in rows} == {c.case_id for c in cases}
    assert Counter(r["cohort"] for r in rows) == {"approved_real_baseline": 25, "synthetic_regression": 28}
    assert read_rows(REPORT / "failures.jsonl") == [r for r in rows if not r["passed"]]
    real = [r for r in rows if r["cohort"] == "approved_real_baseline"]
    assert len(real) == 25 and sum(r["passed"] for r in real) == 17
    assert read_rows(REPORT / "real_failures.jsonl") == [r for r in real if not r["passed"]]
    assert len(read_rows(REPORT / "real_failures.jsonl")) == 8
    for row in rows:
        case = next(c for c in cases if c.case_id == row["case_id"])
        assert row["trace"]["expected_gold"] == case.model_dump(mode="json")
        assert row["cohort"] == case.cohort


def test_metrics_keep_every_denominator_group_and_zero_output_abstention(exported):
    payload, rows = exported
    for cohort, groups in payload["metrics"].items():
        assert set(groups) == {"overall", "era", "scene", "influence"}
        for family, group in groups.items():
            selected = [r for r in rows if r["cohort"] == cohort and (family == "overall" or r["relation_type"] == family)]
            assert set(group["metrics"]) == set(METRICS)
            for name, value in group["metrics"].items():
                numerator = sum(r["metric_counts"][name]["numerator"] for r in selected)
                denominator = sum(r["metric_counts"][name]["denominator"] for r in selected)
                assert value == {"numerator": numerator, "denominator": denominator,
                                 "value": numerator / denominator if denominator else None}
    for row in rows:
        expected = row["trace"]["expected_gold"]["expected_relations"]
        assert row["metric_counts"]["influence_direction_accuracy"]["denominator"] == (
            len(expected) if row["relation_type"] == "influence" else 0
        )
        if not row["actual"]["relations"]:
            assert row["metric_counts"]["relation_precision"]["denominator"] == 0
    real = payload["metrics"]["approved_real_baseline"]
    assert real["overall"]["metrics"]["evidence_coverage"] == {"numerator": 1, "denominator": 12, "value": 1 / 12}
    assert real["influence"]["metrics"]["relation_precision"]["value"] is None


def test_every_trace_retains_retrieved_source_spans_candidates_filters_and_checkpoint(exported):
    _, rows = exported
    _, inputs, sources, _ = load_suite(SUITE)
    for row in rows:
        trace = row["trace"]
        assert trace["query"] == row["query"] == inputs[row["case_id"]].query
        assert trace["retrieved_evidence"] == [d.model_dump(mode="json") for d in inputs[row["case_id"]].documents]
        assert set(trace["candidate_relations"]) == set(trace["filtering_scoring"]) == {"era", "scene", "influence"}
        assert trace["final_relations"] == row["actual"]["relations"]
        assert trace["failure_reasons"] == row["failure_reasons"]
        for span in trace["expected_gold"]["supporting_evidence"]:
            assert sources[span["source_id"]].content[span["span_start"]:span["span_end"]] == span["exact_span"]
        checkpoint = row["actual"]["checkpoint"]
        assert checkpoint["scope"] == "result_contract_and_query_replay"
        assert checkpoint["backend"] == "application_in_memory_saver"
        assert checkpoint["production_restart_verified"] is False


def test_machine_gate_true_is_distinct_from_cli_final_regression_blocker(exported):
    payload, rows = exported
    assert payload["baseline_ready"] is payload["can_enter_targeted_repairs"] is True
    assert payload["blockers"] == ["reviewed real failures require diagnosis before Final Worldline Regression"]
    inventory = read_json(REPORT / "coverage_inventory.json")
    real = [r for r in rows if r["cohort"] == "approved_real_baseline"]
    for family, group in inventory.items():
        selected = [r for r in real if r["relation_type"] == family]
        assert group["approved_real"] == len(selected) and group["pending_real"] == 0
        needed = {"positive", "negative", "weak_evidence", "negation", "conflict", "confusable", "isolation"}
        if family == "influence":
            needed.add("direction_sensitive")
        assert all(group["approved_category_counts"][c] > 0 for c in needed)
    assert inventory["influence"]["approved_category_counts"]["confusable"] == 3
    gate = read_json(REPORT / "machine_gate.json")
    assert gate["baseline_ready"] == payload["baseline_ready"]
    assert gate["can_enter_targeted_repairs"] == payload["can_enter_targeted_repairs"]
    assert gate["cli_exit_code"] == 2 and gate["final_worldline_regression_ready"] is False
    assert gate["blockers"] == payload["blockers"] and gate["coverage_blockers"] == []
    assert gate["targeted_repairs_executed"] is False


def test_prior_50_results_unchanged_new_3_are_real_negative_rejections(exported):
    _, rows = exported
    previous = read_rows(PREVIOUS / "results.jsonl")
    old_map, current = {r["case_id"]: r for r in previous}, {r["case_id"]: r for r in rows}
    assert len(old_map) == 50 and all(current[k] == v for k, v in old_map.items())
    new = [r for r in rows if r["case_id"] not in old_map]
    assert len(new) == 3
    for row in new:
        assert row["cohort"] == "approved_real_baseline" and row["relation_type"] == "influence"
        assert row["label"] == "negative" and "confusable" in row["categories"]
        assert row["passed"] is True and row["actual"]["relations"] == []
        assert row["trace"]["candidate_relations"]["influence"]
        assert row["trace"]["filtering_scoring"]["influence"]["trace"]["accepted_count"] == 0


def test_current_failure_diagnosis_is_observed_only_and_bound_to_trace(exported):
    _, rows = exported
    diagnosis = read_json(REPORT / "failure_diagnosis.json")
    failed = [r for r in rows if r["cohort"] == "approved_real_baseline" and not r["passed"]]
    reasons = Counter(reason for r in failed for reason in r["failure_reasons"])
    assert {p["category"]: p["case_count"] for p in diagnosis["priorities"]} == reasons
    assert reasons == {"conflict failure": 3, "direction error": 5, "evidence mismatch": 3}
    assert {c["case_id"] for c in diagnosis["cases"]} == {r["case_id"] for r in failed}
    assert diagnosis["reviewed_real_failures"] == 8 and diagnosis["unique_evidence_failures"] == 6
    assert diagnosis["runtime_optimized"] is False and diagnosis["coverage_blocker"] is None
    assert diagnosis["baseline_gate_ready"] is diagnosis["can_enter_targeted_repairs"] is True
    _, _, sources, _ = load_suite(SUITE)
    for item in diagnosis["cases"]:
        row = next(r for r in failed if r["case_id"] == item["case_id"])
        trace = row["trace"]
        for name in ("query", "retrieved_evidence", "candidate_relations", "filtering_scoring", "expected_gold", "failure_reasons"):
            assert item[name] == trace[name]
        assert item["final_relations"] == row["actual"]["relations"]
        assert item["source_metadata"] == [sources[s["source_id"]].model_dump(mode="json") for s in trace["expected_gold"]["supporting_evidence"]]
    for family in ("era", "scene", "influence"):
        assert diagnosis["failure_category_counts_by_relation_type"][family] == Counter(
            reason for r in failed if r["relation_type"] == family for reason in r["failure_reasons"]
        )


def test_old_341_freezes_and_new_artifacts_remain_hash_bound():
    protected = read_json(REPORT / "protected_artifacts.json")
    assert protected["file_count"] == len(protected["files"]) == 341
    assert len(protected["runtime_and_evaluation_files"]) == 28
    variants = protected["historical_report_eol_variants"]
    assert len(variants) == 7 and all(n.startswith("evals/reports/semantic_relations_v1/") for n in variants)
    for name, digest in protected["files"].items():
        allowed = {digest, variants[name]} if name in variants else {digest}
        assert sha256((ROOT.parent / name).read_bytes()).hexdigest() in allowed
    manifest = read_json(REPORT / "artifact_manifest.json")
    assert manifest["suite_manifest_sha256"] == sha256((SUITE / "manifest.json").read_bytes()).hexdigest()
    assert set(manifest["files"]) == {p.name for p in REPORT.iterdir() if p.is_file() and p.name not in manifest["excluded_files"]}
    assert manifest["code_revision"] == read_json(REPORT / "metrics.json")["code_revision"]


def test_report_tables_gate_and_known_evaluation_limits_match_export(exported):
    payload, _ = exported
    report = (REPORT / "semantic_relations_evaluation_v1_report.md").read_text(encoding="utf-8")
    for groups in payload["metrics"].values():
        for metric in METRICS:
            cells = []
            for family in ("overall", "era", "scene", "influence"):
                value = groups[family]["metrics"][metric]
                cells.append("N/A (0/0)" if value["value"] is None else
                             f"{value['numerator']}/{value['denominator']} ({value['value']:.1%})")
            assert "| " + metric + " | " + " | ".join(cells) + " |" in report
    assert "baseline_ready=true；can_enter_targeted_repairs=true" in report
    assert "production_restart_verified=false" in report
    assert "1/9" in report and "17/25" in report and "CLI=2" in report
    assert "## 最终判断" in report


def test_export_metric_tamper_rejected_even_if_artifact_hash_refreshed(tmp_path):
    target = tmp_path / "tampered"
    shutil.copytree(REPORT, target)
    path = target / "metrics.json"
    payload = read_json(path)
    payload["metrics"]["approved_real_baseline"]["overall"]["metrics"]["evidence_coverage"]["numerator"] = 12
    path.write_bytes(json.dumps(payload).encode())
    manifest = read_json(target / "artifact_manifest.json")
    manifest["files"][path.name] = sha256(path.read_bytes()).hexdigest()
    (target / "artifact_manifest.json").write_bytes(json.dumps(manifest).encode())
    with pytest.raises(ValueError, match="export_metric_counts_mismatch"):
        check_export(target)
