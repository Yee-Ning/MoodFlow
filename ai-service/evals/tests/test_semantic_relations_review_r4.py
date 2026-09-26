"""Register explicit r3 human approvals without running evaluation or changing labels."""

from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

import pytest

from evals.semantic_relations_models import SemanticGoldCase, file_hash, load_suite, read_jsonl
from evals.semantic_relations_review import HumanReviewBatch, label_hash


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "semantic_relations_v1_review_r3"
REVISION = ROOT / "semantic_relations_v1_review_r4"


def lines_by_id(path):
    return {json.loads(line)["case_id"]: line for line in path.read_bytes().splitlines(keepends=True)}


def read_json(name):
    return json.loads((REVISION / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def revision():
    return load_suite(REVISION)


def test_all_real_cases_are_approved_and_no_pending_remains(revision):
    cases, inputs, sources, _ = revision
    assert (len(cases), len(inputs), len(sources)) == (50, 50, 48)
    approved = read_jsonl(REVISION / "approved_gold.jsonl", SemanticGoldCase)
    assert len(approved) == 22
    assert approved == [case for case in cases if case.dataset_kind == "real_source"]
    assert all(case.review.status == "approved" and case.review.method == "human" for case in approved)
    assert Counter(case.relation_type for case in approved) == {"era": 6, "scene": 6, "influence": 10}
    assert Counter(case.label for case in approved) == {"positive": 9, "negative": 13}
    assert sum(case.dataset_kind == "synthetic" for case in cases) == 28
    assert not any(case.review.status == "pending" for case in cases)
    assert (REVISION / "pending_cases.jsonl").read_bytes() == b""
    assert (REVISION / "approved_gold.jsonl").read_bytes().startswith(
        (PARENT / "approved_gold.jsonl").read_bytes()
    )


def test_only_ten_review_records_change_and_all_label_payloads_are_preserved(revision):
    parents = {case.case_id: case for case in load_suite(PARENT)[0]}
    current = {case.case_id: case for case in revision[0]}
    assert set(current) == set(parents)
    parent_lines = lines_by_id(PARENT / "gold.jsonl")
    current_lines = lines_by_id(REVISION / "gold.jsonl")
    changed = set()
    for case_id, case in current.items():
        assert case.model_dump(exclude={"review"}) == parents[case_id].model_dump(exclude={"review"})
        assert label_hash(case) == label_hash(parents[case_id])
        if parents[case_id].review.status == "pending":
            assert case.review.status == "approved"
            changed.add(case_id)
        else:
            assert current_lines[case_id] == parent_lines[case_id]
    assert len(changed) == 10
    for name in ("inputs.jsonl", "sources.jsonl", "schema.json", "source_audit.json",
                 "research_exclusions.json", "excluded_cases.jsonl"):
        assert (REVISION / name).read_bytes() == (PARENT / name).read_bytes()


def test_ten_new_receipts_bind_exact_decisions_labels_and_replay_lines(revision):
    batch = HumanReviewBatch.model_validate_json((REVISION / "review_decisions.json").read_bytes())
    assert (batch.reviewer, batch.reviewed_at, batch.provenance) == (
        "human-review-WT", "2026-09-26", "explicit_user_review",
    )
    parents = {case.case_id: case for case in load_suite(PARENT)[0]}
    current = {case.case_id: case for case in revision[0]}
    assert len(batch.decisions) == 10 and all(d.status == "approved" for d in batch.decisions)
    assert {decision.case_id for decision in batch.decisions} == {
        case_id for case_id, case in parents.items() if case.review.status == "pending"
    }
    parent_lines = lines_by_id(PARENT / "gold.jsonl")
    input_lines = lines_by_id(PARENT / "inputs.jsonl")
    jsonl_receipts = [json.loads(line) for line in
                     (REVISION / "new_review_receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    expected_receipts = []
    registration = (REVISION / "human_review_registration.md").read_text(encoding="utf-8")
    for decision in batch.decisions:
        case = current[decision.case_id]
        receipt_path = REVISION / case.review.record_path
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        assert receipt == {
            "case_id": case.case_id, "status": "approved", "reviewer": batch.reviewer,
            "reviewed_at": batch.reviewed_at, "reason": decision.reason,
            "provenance": batch.provenance, "label_sha256": label_hash(parents[case.case_id]),
            "parent_manifest_sha256": file_hash(PARENT / "manifest.json"),
            "parent_case_line_sha256": sha256(parent_lines[case.case_id]).hexdigest(),
            "replay_input_line_sha256": sha256(input_lines[case.case_id]).hexdigest(),
        }
        assert file_hash(receipt_path) == case.review.record_sha256
        assert (case.review.reviewer, case.review.reviewed_at, case.review.notes) == (
            batch.reviewer, batch.reviewed_at, decision.reason,
        )
        assert decision.reason in registration and case.case_id in registration
        expected_receipts.append(receipt)
    assert jsonl_receipts == expected_receipts


def test_prior_receipts_review_history_pending_pack_and_exclusions_are_preserved(revision):
    for path in (PARENT / "receipts").glob("*.json"):
        assert path.read_bytes() == (REVISION / "receipts" / path.name).read_bytes()
    assert len(list((REVISION / "receipts").glob("*.json"))) == 26
    for path in (PARENT / "review_history").rglob("*"):
        if path.is_file():
            assert path.read_bytes() == (REVISION / path.relative_to(PARENT)).read_bytes()
    for name in ("human_review_pack.md", "review_checklist.json", "pending_cases.jsonl",
                 "review_summary.json", "protected_artifacts.json"):
        assert (PARENT / name).read_bytes() == (REVISION / "review_history" / f"r3_{name}").read_bytes()
    excluded = read_jsonl(REVISION / "excluded_cases.jsonl", SemanticGoldCase)
    assert len(excluded) == 4 and all(case.review.status == "rejected" for case in excluded)
    assert not {case.case_id for case in excluded} & {case.case_id for case in revision[0]}


def test_approved_coverage_keeps_conflict_negation_and_reverse_semantics(revision):
    cases = [case for case in revision[0] if case.dataset_kind == "real_source"]
    summary = read_json("review_summary.json")
    assert summary["evaluation_run"] is False
    assert (summary["new_approved_real"], summary["approved_real"], summary["pending_real"]) == (10, 22, 0)
    assert summary["approved_by_relation_type"] == Counter(case.relation_type for case in cases)
    assert summary["approved_label_counts"] == Counter(case.label for case in cases)
    assert summary["approved_category_counts"] == Counter(cat for c in cases for cat in c.categories)
    for family in ("era", "scene", "influence"):
        rows = [case for case in cases if case.relation_type == family]
        assert summary["approved_category_counts_by_relation_type"][family] == Counter(
            cat for case in rows for cat in case.categories
        )
        assert sum("negation" in case.categories for case in rows) == 1
        conflicts = [case for case in rows if "conflict" in case.categories]
        assert len(conflicts) == 1
        conflict = conflicts[0]
        claims = {span.claim_id for span in conflict.supporting_evidence}
        assert {span.stance for span in conflict.supporting_evidence} == {"support", "contradiction"}
        for relation in conflict.expected_relations:
            assert set(relation.evidence_claim_ids) == claims
            assert relation.conflict_flags == [
                "temporal_conflict" if family == "era" else "attribution_conflict"
            ]
            if family == "era":
                assert relation.fields == {
                    "start_year": None, "end_year": None, "temporal_precision": "unknown",
                }
    assert sum("weak_evidence" in case.categories for case in cases) == 4
    reverse = [case for case in cases if case.case_id.endswith("-reverse")]
    assert len(reverse) == 2
    for case in reverse:
        assert case.label == "negative" and case.expected_relations == []
        forward = next(c for c in cases if c.case_id == case.case_id.replace("-reverse", "-forward"))
        assert forward.source_entity == case.target_entity and forward.target_entity == case.source_entity
        assert revision[1][case.case_id].documents == revision[1][forward.case_id].documents


def test_all_prior_freezes_and_reports_remain_protected():
    record = read_json("protected_artifacts.json")
    directories = (
        "semantic_relations_v1", "semantic_relations_v1_review_r1", "semantic_relations_v1_review_r2",
        "semantic_relations_v1_review_r3", "reports/semantic_relations_v1",
        "reports/semantic_relations_v1_review_r1", "reports/semantic_relations_v1_review_r2",
        "reports/semantic_relations_v1_review_r3",
    )
    existing = {path.relative_to(ROOT).as_posix() for directory in directories
                for path in (ROOT / directory).rglob("*") if path.is_file()}
    assert len(existing) == 119
    assert set(record["sha256"]) == set(record["git_blob_sha256"]) == existing
    eol_variants = set(record["historical_report_eol_variants"])
    assert len(eol_variants) == 7
    assert all(name.startswith("reports/semantic_relations_v1/") for name in eol_variants)
    for name, digest in record["sha256"].items():
        allowed = {digest}
        if name in eol_variants:
            allowed.add(record["git_blob_sha256"][name])
        else:
            assert record["git_blob_sha256"][name] == digest
        assert file_hash(ROOT / name) in allowed


def test_parent_lineage_and_entire_review_revision_are_frozen(revision):
    manifest = revision[3]
    assert manifest["parent_manifest_sha256"] == file_hash(PARENT / "manifest.json")
    assert manifest["parent_files"] == load_suite(PARENT)[3]["files"]
    assert manifest["parent_directory"] == PARENT.name and manifest["evaluation_run"] is False
    names = {path.relative_to(REVISION).as_posix() for path in REVISION.rglob("*")
             if path.is_file() and path.name not in {"README.md", "manifest.json"}}
    assert names == set(manifest["files"]) | set(manifest["review_files"])
    for name, digest in manifest["review_files"].items():
        assert file_hash(REVISION / name) == digest


def test_registration_loads_without_scoring_or_new_evaluation_outputs(monkeypatch):
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    load_suite(REVISION)
    for directory in (REVISION, ROOT / "reports/semantic_relations_v1_review_r4"):
        assert not any((directory / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))


@pytest.mark.parametrize("kind", ["label_payload", "receipt_identity", "missing_receipt"])
def test_approval_binding_rejects_tampering_even_when_hashes_are_refreshed(tmp_path, kind):
    target = tmp_path / "tampered"
    shutil.copytree(REVISION, target)
    path = target / "gold.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    row = rows[-1]
    receipt_path = target / row["review"]["record_path"]
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    if kind == "label_payload":
        row["rationale"] = "Tampered after human review."
    elif kind == "receipt_identity":
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        receipt["reviewer"] = "agent-inferred-approval"
        receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
        row["review"]["record_sha256"] = file_hash(receipt_path)
        manifest["review_files"][row["review"]["record_path"]] = file_hash(receipt_path)
    else:
        receipt_path.unlink()
    path.write_text("".join(json.dumps(item) + "\n" for item in rows), encoding="utf-8")
    manifest["files"]["gold.jsonl"] = file_hash(path)
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, FileNotFoundError)):
        load_suite(target)
