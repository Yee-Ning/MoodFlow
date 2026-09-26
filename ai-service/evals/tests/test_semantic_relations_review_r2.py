"""Validate the second human-review registration without executing evaluation."""

from hashlib import sha256
import json
from pathlib import Path
import sys

import pytest

from evals.semantic_relations_models import SemanticGoldCase, file_hash, load_suite, read_jsonl
from evals.semantic_relations_review import HumanReviewBatch, label_hash, validate_review_revision


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "semantic_relations_v1"
PARENT = ROOT / "semantic_relations_v1_review_r1"
REVISION = ROOT / "semantic_relations_v1_review_r2"


def lines_by_id(path):
    return {json.loads(line)["case_id"]: line for line in path.read_bytes().splitlines(keepends=True)}


@pytest.fixture(scope="module")
def revision():
    return load_suite(REVISION)


def test_all_real_cases_are_approved_and_pending_view_is_empty(revision):
    cases, inputs, sources, _ = revision
    approved = read_jsonl(REVISION / "approved_gold.jsonl", SemanticGoldCase)
    assert len(cases) == len(inputs) == 40 and len(sources) == 39
    assert len(approved) == 12
    assert approved == [case for case in cases if case.dataset_kind == "real_source"]
    assert all(case.review.status == "approved" for case in approved)
    assert {family: sum(case.relation_type == family for case in approved)
            for family in ("era", "scene", "influence")} == {"era": 4, "scene": 4, "influence": 4}
    assert sum(case.label == "positive" for case in approved) == 4
    assert sum("weak_evidence" in case.categories for case in approved) == 4
    assert (REVISION / "pending_weak_evidence.jsonl").read_bytes() == b""


def test_manifest_and_all_review_artifacts_are_frozen(revision):
    manifest = revision[3]
    assert manifest["parent_manifest_sha256"] == file_hash(PARENT / "manifest.json")
    assert manifest["parent_files"] == load_suite(PARENT)[3]["files"]
    assert not manifest["evaluation_run"]
    expected = {path.relative_to(REVISION).as_posix() for path in REVISION.rglob("*")
                if path.is_file() and path.name not in {"README.md", "manifest.json"}}
    assert expected == set(manifest["files"]) | set(manifest["review_files"])
    for name, digest in manifest["review_files"].items():
        assert file_hash(REVISION / name) == digest


def test_only_review_changes_and_inputs_sources_schema_are_identical(revision):
    parent_cases = {case.case_id: case for case in load_suite(PARENT)[0]}
    current = {case.case_id: case for case in revision[0]}
    assert set(current) == set(parent_cases)
    parent_lines = lines_by_id(PARENT / "gold.jsonl")
    current_lines = lines_by_id(REVISION / "gold.jsonl")
    for case_id, case in current.items():
        assert case.model_dump(exclude={"review"}) == parent_cases[case_id].model_dump(exclude={"review"})
        if parent_cases[case_id].review.status != "pending":
            assert current_lines[case_id] == parent_lines[case_id]
    for name in ("inputs.jsonl", "sources.jsonl", "schema.json", "excluded_cases.jsonl"):
        assert (REVISION / name).read_bytes() == (PARENT / name).read_bytes()


def test_new_receipts_bind_all_four_explicit_decisions(revision):
    batch = HumanReviewBatch.model_validate_json((REVISION / "review_decisions.json").read_bytes())
    assert batch.reviewer == "human-review-WT" and batch.reviewed_at == "2026-09-26"
    assert len(batch.decisions) == 4 and all(d.status == "approved" for d in batch.decisions)
    parent_cases = {case.case_id: case for case in load_suite(PARENT)[0]}
    assert {d.case_id for d in batch.decisions} == {
        key for key, case in parent_cases.items() if case.review.status == "pending"
    }
    current = {case.case_id: case for case in revision[0]}
    parent_lines = lines_by_id(PARENT / "gold.jsonl")
    input_lines = lines_by_id(PARENT / "inputs.jsonl")
    for decision in batch.decisions:
        case = current[decision.case_id]
        receipt_path = REVISION / case.review.record_path
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert case.review.record_sha256 == file_hash(receipt_path)
        assert receipt == {
            "case_id": case.case_id, "status": "approved", "reviewer": batch.reviewer,
            "reviewed_at": batch.reviewed_at, "reason": decision.reason,
            "provenance": "explicit_user_review", "label_sha256": label_hash(parent_cases[case.case_id]),
            "parent_manifest_sha256": file_hash(PARENT / "manifest.json"),
            "parent_case_line_sha256": sha256(parent_lines[case.case_id]).hexdigest(),
            "replay_input_line_sha256": sha256(input_lines[case.case_id]).hexdigest(),
        }
        assert case.review.notes == decision.reason
        assert case.review.reviewer == batch.reviewer and case.review.reviewed_at == batch.reviewed_at
        assert case.label == "negative" and case.expected_relations == []


def test_historical_reviews_receipts_and_exclusions_are_preserved(revision):
    assert validate_review_revision(PARENT, ORIGINAL)["pending"] == 4
    for path in (PARENT / "receipts").glob("*.json"):
        assert path.read_bytes() == (REVISION / "receipts" / path.name).read_bytes()
    for name in ("review_decisions.json", "human_review_pack.md", "review_summary.json"):
        assert (PARENT / name).read_bytes() == (REVISION / "review_history" / f"r1_{name}").read_bytes()
    excluded = read_jsonl(REVISION / "excluded_cases.jsonl", SemanticGoldCase)
    assert len(excluded) == 4 and all(case.review.status == "rejected" for case in excluded)
    assert not {case.case_id for case in excluded} & {case.case_id for case in revision[0]}


def test_previous_real_failure_cases_keep_their_gold_and_input(revision):
    failures = [json.loads(line) for line in
                (ROOT / "reports/semantic_relations_v1/failures.jsonl").read_text(encoding="utf-8").splitlines()]
    real_failures = [row["case_id"] for row in failures if row["case_id"].startswith("real-")]
    assert len(real_failures) == 3
    original_cases = {case.case_id: case for case in load_suite(ORIGINAL)[0]}
    original_inputs = lines_by_id(ORIGINAL / "inputs.jsonl")
    current = {case.case_id: case for case in revision[0]}
    current_inputs = lines_by_id(REVISION / "inputs.jsonl")
    for case_id in real_failures:
        assert label_hash(current[case_id]) == label_hash(original_cases[case_id])
        assert current_inputs[case_id] == original_inputs[case_id]


def test_registration_loads_without_runtime_or_scoring(monkeypatch):
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    load_suite(REVISION)
    assert not any((REVISION / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))
