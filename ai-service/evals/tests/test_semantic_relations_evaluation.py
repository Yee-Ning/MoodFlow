"""Quality gates for the evaluator, independent of runtime quality scores."""

from copy import deepcopy
import json
import shutil

import pytest

from evals.semantic_relations_metrics import METRICS, aggregate, diagnose
from evals.semantic_relations_models import ReplayInput, SemanticGoldCase, file_hash, load_suite
from evals.semantic_relations_runner import (
    DEFAULT_SUITE, checkpoint_replay, execute_replay, main, normalize_result, render_report, run_suite,
)


@pytest.fixture(scope="module")
def suite():
    return load_suite(DEFAULT_SUITE)


def evaluated(suite, case_id):
    cases, inputs, snapshots, _ = suite
    case = next(item for item in cases if item.case_id == case_id)
    result, trace = execute_replay(inputs[case_id])
    actual = {"relations": normalize_result(result, inputs[case_id], snapshots),
              "checkpoint": {"passed": True}}
    return case, actual, trace


def refreeze_test_copy(directory):
    manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {name: file_hash(directory / name) for name in manifest["files"]}
    (directory / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_real_sources_pending_and_synthetic_never_promoted(suite):
    cases, inputs, snapshots, _ = suite
    assert len(cases) == len(inputs) == 40
    real = [case for case in cases if case.dataset_kind == "real_source"]
    assert len(real) == 12
    assert {case.relation_type for case in real} == {"era", "scene", "influence"}
    assert all(case.review.status == "pending" for case in real)
    assert len([source for source in snapshots.values() if source.origin == "real_source"]) == 4
    for family in ("era", "scene", "influence"):
        synthetic = [case for case in cases if case.relation_type == family and case.dataset_kind == "synthetic"]
        assert {"positive", "negative", "weak_evidence", "negation", "conflict", "confusable", "isolation"} <= {category for case in synthetic for category in case.categories}


def test_manifest_detects_gold_mutation(tmp_path):
    directory = tmp_path / "suite"
    shutil.copytree(DEFAULT_SUITE, directory)
    with (directory / "gold.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("\n")
    with pytest.raises(ValueError, match="freeze_hash_mismatch:gold"):
        load_suite(directory)


def test_span_integrity_even_with_changed_manifest(tmp_path):
    directory = tmp_path / "suite"
    shutil.copytree(DEFAULT_SUITE, directory)
    path = directory / "gold.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["supporting_evidence"][0]["exact_span"] = "Invented evidence"
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    refreeze_test_copy(directory)
    with pytest.raises(ValueError, match="gold_source_span_mismatch"):
        load_suite(directory)


def test_real_approval_requires_human_receipt(suite):
    case = next(case for case in suite[0] if case.dataset_kind == "real_source")
    raw = case.model_dump(mode="json")
    raw["review"].update(status="approved", method="human", reviewer="test human", reviewed_at="2026-09-26T01:00:00Z")
    with pytest.raises(ValueError, match="human_review_record_required"):
        SemanticGoldCase.model_validate(raw)


def test_receipt_binds_labels_not_just_reviewer_name(tmp_path):
    directory = tmp_path / "suite"
    shutil.copytree(DEFAULT_SUITE, directory)
    path = directory / "gold.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    real = next(row for row in rows if row["dataset_kind"] == "real_source")
    receipt = {"case_id": real["case_id"], "status": "approved", "label_sha256": "0" * 64,
               "reviewer": "test human", "reviewed_at": "2026-09-26T01:00:00Z"}
    (directory / "test_receipt.json").write_text(json.dumps(receipt), encoding="utf-8")
    real["review"].update(status="approved", method="human", reviewer=receipt["reviewer"],
                          reviewed_at=receipt["reviewed_at"], record_path="test_receipt.json",
                          record_sha256=file_hash(directory / "test_receipt.json"))
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    refreeze_test_copy(directory)
    with pytest.raises(ValueError, match="review_receipt_label_mismatch"):
        load_suite(directory)


def test_gold_never_visible_to_runtime_adapter(suite):
    raw = next(iter(suite[1].values())).model_dump(mode="json")
    raw["expected_relations"] = []
    with pytest.raises(ValueError, match="extra_forbidden"):
        ReplayInput.model_validate(raw)
    raw.pop("expected_relations")
    for name in ("label", "relation_type", "review"):
        assert name not in raw


def test_graph_only_output_cannot_hide_unsupported_relation(suite):
    replay = suite[1]["synthetic-era-positive"]
    result, trace = execute_replay(replay)
    result = result.model_copy(update={"era_relations": []})
    actual = {"relations": normalize_result(result, replay, suite[2]),
              "checkpoint": {"passed": True}}
    case = next(case for case in suite[0] if case.case_id == replay.case_id)
    assert len(actual["relations"]) == 1
    assert not actual["relations"][0]["evidence"]
    diagnosis = diagnose(case, actual, trace)
    assert diagnosis["metric_counts"]["unsupported_hallucination_rate"] == {"numerator": 1, "denominator": 1}
    assert "evidence mismatch" in diagnosis["failure_reasons"]


@pytest.mark.parametrize("family", ["era", "scene", "influence"])
def test_all_families_success_evidence_chain_and_checkpoint(suite, family):
    case, actual, trace = evaluated(suite, f"synthetic-{family}-positive")
    assert diagnose(case, actual, trace)["passed"]
    assert all(ev["traceable"] for relation in actual["relations"] for ev in relation["evidence"])
    assert set(trace["filtering_scoring"]) == {"era", "scene", "influence"}
    result, _ = execute_replay(suite[1][case.case_id])
    checkpoint = checkpoint_replay(result, suite[1][case.case_id])
    assert checkpoint["restore_equal"] and checkpoint["query_equal"] and checkpoint["continuation_equal"]
    assert not checkpoint["production_restart_verified"]


@pytest.mark.parametrize("family", ["era", "scene", "influence"])
def test_runtime_query_isolation_with_other_family_evidence_present(suite, family):
    replay = suite[1][f"synthetic-{family}-isolation"]
    distractors = [doc for other in ("era", "scene", "influence") if other != family
                   for doc in suite[1][f"synthetic-{other}-positive"].documents]
    mixed = replay.model_copy(update={"documents": [*replay.documents, *distractors]})
    result, trace = execute_replay(mixed)
    actual = normalize_result(result, mixed, suite[2])
    assert len(actual) == 1 and actual[0]["relation_type"] == family
    assert all(not trace["filtering_scoring"][other]["trace"]["requested"]
               for other in ("era", "scene", "influence") if other != family)


@pytest.mark.parametrize("family", ["era", "scene", "influence"])
def test_retrieval_miss_not_hidden_by_abstention(suite, family):
    case, actual, trace = evaluated(suite, f"synthetic-{family}-retrieval_missing")
    result = diagnose(case, actual, trace)
    assert result["failure_reasons"] == ["retrieval failure"]
    assert result["metric_counts"]["evidence_coverage"]["numerator"] == 0
    assert result["metric_counts"]["evidence_coverage"]["denominator"] == 1


def test_reverse_direction_is_a_failure_even_with_valid_span(suite):
    case, actual, trace = evaluated(suite, "synthetic-influence-positive")
    relation = actual["relations"][0]
    relation["source_entity"], relation["target_entity"] = relation["target_entity"], relation["source_entity"]
    result = diagnose(case, actual, trace)
    assert {"direction error", "unsupported relation"} <= set(result["failure_reasons"])
    assert result["metric_counts"]["influence_direction_accuracy"] == {"numerator": 0, "denominator": 1}


def test_relation_classification_distinguished_from_direction(suite):
    case, actual, trace = evaluated(suite, "synthetic-influence-positive")
    actual["relations"][0]["predicate"] = "inspired"
    result = diagnose(case, actual, trace)
    assert "relation classification" in result["failure_reasons"]
    assert "direction error" not in result["failure_reasons"]


def test_evidence_traceability_and_support_are_independent(suite):
    case, actual, trace = evaluated(suite, "synthetic-era-positive")
    actual["relations"][0]["evidence"][0]["exact_span"] = "Wrong but traceable to a different source"
    result = diagnose(case, actual, trace)
    assert "evidence mismatch" in result["failure_reasons"]
    assert result["metric_counts"]["evidence_traceability"] == {"numerator": 1, "denominator": 1}
    assert result["metric_counts"]["relation_precision"] == {"numerator": 0, "denominator": 1}


@pytest.mark.parametrize("family", ["era", "scene", "influence"])
def test_conflict_requires_all_evidence_flags_and_null_semantics(suite, family):
    case, actual, trace = evaluated(suite, f"synthetic-{family}-conflict")
    assert diagnose(case, actual, trace)["passed"]
    damaged = deepcopy(actual)
    damaged["relations"][0]["evidence"].pop()
    result = diagnose(case, damaged, trace)
    assert "conflict failure" in result["failure_reasons"]
    assert result["metric_counts"]["conflict_handling"] == {"numerator": 0, "denominator": 1}
    assert result["metric_counts"]["evidence_coverage"] == {"numerator": 1, "denominator": 2}
    damaged = deepcopy(actual)
    damaged["relations"][0]["raw"]["metadata"][case.expected_relations[0].conflict_flags[0]] = False
    assert "conflict failure" in diagnose(case, damaged, trace)["failure_reasons"]


@pytest.mark.parametrize("category,reason", [("weak_evidence", "weak evidence accepted"), ("negation", "negation failure")])
def test_failed_negative_classification(suite, category, reason):
    case, actual, trace = evaluated(suite, f"synthetic-influence-{category}")
    _, positive, _ = evaluated(suite, "synthetic-influence-positive")
    actual["relations"] = positive["relations"]
    result = diagnose(case, actual, trace)
    assert reason in result["failure_reasons"]
    assert result["metric_counts"][f"{'weak_evidence' if category == 'weak_evidence' else 'negation'}_rejection"] == {"numerator": 0, "denominator": 1}


def test_cross_relation_and_checkpoint_failures_not_masked(suite):
    case, actual, trace = evaluated(suite, "synthetic-era-positive")
    actual["relations"][0]["relation_type"] = "scene"
    actual["checkpoint"]["passed"] = False
    result = diagnose(case, actual, trace)
    assert {"cross-relation contamination", "checkpoint continuity failure"} <= set(result["failure_reasons"])
    assert result["metric_counts"]["query_isolation"]["numerator"] == 0


def test_real_diagnostics_are_not_approved_metrics(tmp_path, suite):
    payload = run_suite(DEFAULT_SUITE, tmp_path / "results")
    assert payload["blockers"]
    for cohort in payload["metrics"].values():
        assert set(cohort) == {"overall", "era", "scene", "influence"}
        for group in cohort.values():
            assert set(group["metrics"]) == set(METRICS)
    approved = payload["metrics"]["approved_real_baseline"]["overall"]
    assert approved["case_count"] == 0
    assert all(metric["value"] is None for metric in approved["metrics"].values())
    failures = [json.loads(line) for line in (tmp_path / "results/failures.jsonl").read_text(encoding="utf-8").splitlines()]
    assert failures
    assert all({"query", "retrieved_evidence", "candidate_relations", "filtering_scoring", "final_relations", "expected_gold", "failure_reasons"} <= set(row["trace"]) for row in failures)
    assert load_suite(DEFAULT_SUITE)[3] == suite[3]
    assert run_suite(DEFAULT_SUITE, tmp_path / "repeat")["metrics"] == payload["metrics"]


def test_cannot_write_outputs_into_gold_directory():
    with pytest.raises(ValueError, match="evaluation_output_cannot_overwrite_suite"):
        run_suite(DEFAULT_SUITE, DEFAULT_SUITE / "outputs")


def test_cli_reports_blocked_baseline_as_nonzero(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.argv", ["run_semantic_relations_eval.py", "--output", str(tmp_path / "results")])
    assert main() == 2


def test_empty_groups_not_perfect_scores():
    result = aggregate([])
    assert all(metric["value"] is None for cohort in result.values()
               for group in cohort.values() for metric in group["metrics"].values())


def test_confirmed_quality_failures_allow_targeted_repairs_after_gold_ready():
    payload = {"created_at": "test", "manifest": {"files": {}}, "code_revision": "test",
               "code_dirty": False, "metrics": aggregate([]), "baseline_ready": True,
               "blockers": ["reviewed real failures require diagnosis before Final Worldline Regression"]}
    report = render_report(payload, [])
    assert "可以进入针对真实失败点修复" in report
    assert "已确认的质量失败不会阻止建立 baseline" in report
