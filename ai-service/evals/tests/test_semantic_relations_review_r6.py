"""Bind explicit r5 approvals to a new frozen revision without evaluation."""

import ast
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

import pytest

from evals.semantic_relations_models import SemanticGoldCase, file_hash, load_suite, read_jsonl
from evals.semantic_relations_review import HumanReviewBatch, label_hash
from evals.tests.historical_scope import assert_historical_runtime_scope


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "semantic_relations_v1_review_r5"
REVISION = ROOT / "semantic_relations_v1_review_r6"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def lines_by_id(path):
    return {json.loads(line)["case_id"]: line for line in path.read_bytes().splitlines(keepends=True)}


def gate_categories():
    tree = ast.parse((ROOT / "semantic_relations_runner.py").read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_suite")
    required = next(ast.literal_eval(n.value) for n in ast.walk(function) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "required" for t in n.targets))
    extra = next(ast.literal_eval(n.args[0]) for n in ast.walk(function) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "required" and n.func.attr == "add")
    return {family: required | ({extra} if family == "influence" else set())
            for family in ("era", "scene", "influence")}


def checked_revision(directory):
    cases, inputs, sources, manifest = load_suite(directory)
    parents, _, _, parent_manifest = load_suite(PARENT)
    if (manifest["parent_directory"] != PARENT.name
            or manifest["parent_manifest_sha256"] != file_hash(PARENT / "manifest.json")
            or manifest["parent_files"] != parent_manifest["files"]):
        raise ValueError("parent_freeze_mismatch")
    for name, digest in manifest["review_files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or file_hash(path) != digest:
            raise ValueError("review_file_hash_mismatch")
    batch = HumanReviewBatch.model_validate_json((directory / "review_decisions.json").read_bytes())
    decisions = {d.case_id: d for d in batch.decisions}
    parent_map = {c.case_id: c for c in parents}
    pending = {c.case_id for c in parents if c.review.status == "pending"}
    if set(decisions) != pending or {c.case_id for c in cases} != set(parent_map):
        raise ValueError("review_case_inventory_mismatch")
    parent_lines, current_lines = lines_by_id(PARENT / "gold.jsonl"), lines_by_id(directory / "gold.jsonl")
    input_lines = lines_by_id(PARENT / "inputs.jsonl")
    for case in cases:
        if label_hash(case) != label_hash(parent_map[case.case_id]):
            raise ValueError("review_changed_label_payload")
        if case.case_id not in pending:
            if current_lines[case.case_id] != parent_lines[case.case_id]:
                raise ValueError("review_changed_existing_case")
            continue
        decision = decisions[case.case_id]
        if (decision.status != "approved" or case.review.status != "approved"
                or case.review.method != "human" or case.review.reviewer != batch.reviewer
                or case.review.reviewed_at != batch.reviewed_at or case.review.notes != decision.reason):
            raise ValueError("review_metadata_mismatch")
        receipt = read_json(directory / case.review.record_path)
        if receipt != {
            "case_id": case.case_id, "status": "approved", "reviewer": batch.reviewer,
            "reviewed_at": batch.reviewed_at, "reason": decision.reason, "provenance": batch.provenance,
            "label_sha256": label_hash(parent_map[case.case_id]),
            "parent_manifest_sha256": manifest["parent_manifest_sha256"],
            "parent_case_line_sha256": sha256(parent_lines[case.case_id]).hexdigest(),
            "replay_input_line_sha256": sha256(input_lines[case.case_id]).hexdigest(),
        }:
            raise ValueError("receipt_lineage_or_decision_mismatch")
    for name in ("inputs.jsonl", "sources.jsonl", "schema.json", "source_audit.json", "excluded_cases.jsonl"):
        if (directory / name).read_bytes() != (PARENT / name).read_bytes():
            raise ValueError("review_changed_evidence_or_replay")
    if read_jsonl(directory / "approved_gold.jsonl", SemanticGoldCase) != [c for c in cases if c.review.status == "approved"]:
        raise ValueError("approved_view_mismatch")
    if (directory / "pending_cases.jsonl").read_bytes():
        raise ValueError("pending_view_not_empty")
    return cases, inputs, sources, manifest


@pytest.fixture(scope="module")
def revision():
    return checked_revision(REVISION)


def test_all_three_approved_gold_is_25_without_new_data(revision):
    cases, inputs, sources, _ = revision
    assert (len(cases), len(inputs), len(sources)) == (53, 53, 52)
    approved = [c for c in cases if c.review.status == "approved"]
    assert len(approved) == 25 and all(c.dataset_kind == "real_source" for c in approved)
    assert Counter(c.relation_type for c in approved) == {"era": 6, "scene": 6, "influence": 13}
    assert Counter(c.label for c in approved) == {"positive": 9, "negative": 16}
    assert sum(c.dataset_kind == "synthetic" for c in cases) == 28
    assert not any(c.review.status == "pending" for c in cases)
    assert (REVISION / "approved_gold.jsonl").read_bytes().startswith((PARENT / "approved_gold.jsonl").read_bytes())
    assert not (REVISION / "pending_review_items.jsonl").exists()
    assert not (REVISION / "review_checklist.json").exists()


def test_only_three_review_metadata_change_all_labels_evidence_and_input_bytes_preserved(revision):
    parent_cases = {c.case_id: c for c in load_suite(PARENT)[0]}
    original, current = lines_by_id(PARENT / "gold.jsonl"), lines_by_id(REVISION / "gold.jsonl")
    changed = {cid for cid in current if original[cid] != current[cid]}
    assert changed == {c.case_id for c in parent_cases.values() if c.review.status == "pending"}
    assert len(changed) == 3
    for case in revision[0]:
        assert case.model_dump(exclude={"review"}) == parent_cases[case.case_id].model_dump(exclude={"review"})
        if case.case_id in changed:
            assert case.relation_type == "influence" and case.label == "negative"
            assert set(case.categories) == {"negative", "confusable", "isolation"}
            assert case.expected_relations == [] and all(s.stance == "context" for s in case.supporting_evidence)
            assert "不表示现实中不存在该影响" in case.rationale


def test_receipts_bind_supplied_identity_date_reason_parent_labels_and_replay(revision):
    batch = HumanReviewBatch.model_validate_json((REVISION / "review_decisions.json").read_bytes())
    assert (batch.reviewer, batch.reviewed_at, batch.provenance) == ("human-review-WT", "2026-09-26", "explicit_user_review")
    assert len(batch.decisions) == 3 and all(d.status == "approved" for d in batch.decisions)
    receipts = [json.loads(line) for line in (REVISION / "new_review_receipts.jsonl").read_text(encoding="utf-8").splitlines()]
    cases = {c.case_id: c for c in revision[0]}
    expected = []
    registration = (REVISION / "human_review_registration.md").read_text(encoding="utf-8")
    items = {i["case_id"]: i for i in read_json(PARENT / "review_checklist.json")["items"]}
    for decision in batch.decisions:
        case = cases[decision.case_id]
        path = REVISION / case.review.record_path
        assert file_hash(path) == case.review.record_sha256
        receipt = read_json(path)
        assert receipt["reason"] == decision.reason == case.review.notes
        assert receipt["label_sha256"] == items[case.case_id]["label_sha256"]
        assert receipt["replay_input_line_sha256"] == items[case.case_id]["replay_input_line_sha256"]
        for text in (case.case_id, decision.reason, case.query, case.source_entity, case.target_entity,
                     items[case.case_id]["candidate_predicate"], case.review.record_sha256):
            assert text in registration
        expected.append(receipt)
    assert receipts == expected


def test_all_previous_receipts_archives_pending_materials_and_rejections_preserved(revision):
    for folder in ("receipts", "review_history"):
        for path in (PARENT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (REVISION / path.relative_to(PARENT)).read_bytes()
    assert len(list((REVISION / "receipts").glob("*.json"))) == 29
    kept = {"gold.jsonl", "approved_gold.jsonl", "inputs.jsonl", "sources.jsonl", "schema.json",
            "excluded_cases.jsonl", "review_decisions.schema.json", "source_audit.json", ".gitattributes"}
    for path in PARENT.iterdir():
        if path.is_file() and path.name not in kept:
            assert path.read_bytes() == (REVISION / "review_history" / ("r5_" + path.name)).read_bytes()
    excluded = read_jsonl(REVISION / "excluded_cases.jsonl", SemanticGoldCase)
    assert len(excluded) == 4 and all(c.review.status == "rejected" for c in excluded)
    assert not {c.case_id for c in excluded} & {c.case_id for c in revision[0]}
    assert read_json(REVISION / "source_audit.json")["inspection_is_human_review"] is False
    assert all(e["human_review_status"] == "pending" for e in read_json(REVISION / "source_audit.json")["entries"])


def test_static_approved_coverage_complete_does_not_claim_new_machine_gate(revision):
    inventory = read_json(REVISION / "coverage_inventory.json")
    summary = read_json(REVISION / "review_summary.json")
    assert inventory["evaluation_run"] is summary["evaluation_run"] is False
    assert inventory["required_real_category_coverage_complete"] is True
    assert inventory["influence_approved_real_confusable"] == 3
    assert inventory["coverage_blockers"] == [] and inventory["overall"] == {"approved_real": 25, "pending_real": 0}
    assert inventory["gate_source_sha256"] == file_hash(ROOT.parent / inventory["gate_source_path"])
    assert inventory["new_baseline_required"] is True
    assert inventory["new_baseline_ready"] is inventory["new_can_enter_targeted_repairs"] is None
    assert inventory["last_formal_machine_gate"] == {"version": "r4", "baseline_ready": False, "can_enter_targeted_repairs": False}
    assert inventory["quality_failures_not_cleared_by_review_approval"] is True
    assert (summary["new_approved_real"], summary["approved_real"], summary["pending_real"]) == (3, 25, 0)
    approved = [c for c in revision[0] if c.review.status == "approved"]
    assert summary["approved_label_counts"] == Counter(c.label for c in approved)
    assert summary["approved_category_counts"] == Counter(cat for c in approved for cat in c.categories)
    for family, needed in gate_categories().items():
        selected = [c for c in approved if c.relation_type == family]
        counts = Counter(cat for c in selected for cat in c.categories)
        assert inventory["by_relation_type"][family] == {
            "approved_real": len(selected), "pending_real": 0, "approved_category_counts": dict(counts),
            "required_categories": sorted(needed), "missing_required_categories": [],
        }
        assert needed <= counts.keys()
        assert summary["approved_category_counts_by_relation_type"][family] == counts


def test_prior_259_freezes_reports_and_28_runtime_files_are_protected():
    protected = read_json(REVISION / "protected_artifacts.json")
    parent = read_json(PARENT / "protected_artifacts.json")
    additions = {p.relative_to(ROOT).as_posix() for folder in (PARENT, ROOT / "reports/semantic_relations_v1_review_r5")
                 for p in folder.rglob("*") if p.is_file()}
    assert protected["file_count"] == 259
    assert set(protected["sha256"]) == set(protected["git_blob_sha256"]) == set(parent["sha256"]) | additions
    assert protected["historical_report_eol_variants"] == parent["historical_report_eol_variants"]
    variants = set(protected["historical_report_eol_variants"])
    assert len(variants) == 7 and all(n.startswith("reports/semantic_relations_v1/") for n in variants)
    for name, digest in protected["sha256"].items():
        allowed = {digest, protected["git_blob_sha256"][name]} if name in variants else {digest}
        if name not in variants:
            assert protected["git_blob_sha256"][name] == digest
        assert file_hash(ROOT / name) in allowed
    scope = read_json(REVISION / "scope_lock.json")
    assert scope["parent_directory"] == PARENT.name
    assert scope["runtime_and_evaluation_file_sha256"] == read_json(PARENT / "scope_lock.json")["runtime_and_evaluation_file_sha256"]
    assert len(scope["runtime_and_evaluation_file_sha256"]) == 28
    assert_historical_runtime_scope(REVISION / "scope_lock.json", scope)
    assert scope["evaluation_run"] is scope["targeted_repairs_run"] is scope["production_data_written"] is False


def test_entire_review_bundle_is_hash_bound_to_parent_and_r4_baseline(revision):
    manifest = revision[3]
    names = {p.relative_to(REVISION).as_posix() for p in REVISION.rglob("*")
             if p.is_file() and p.name not in {"README.md", "manifest.json"}}
    assert names == set(manifest["files"]) | set(manifest["review_files"])
    assert manifest["evaluation_run"] is False
    assert manifest["parent_baseline_artifact_manifest_sha256"] == file_hash(
        ROOT / "reports/semantic_relations_v1_baseline_r4/artifact_manifest.json",
    )


def test_registration_does_not_import_scoring_or_generate_baseline(monkeypatch):
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    checked_revision(REVISION)
    for directory in (REVISION, ROOT / "reports/semantic_relations_v1_review_r6"):
        assert not any((directory / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))


@pytest.mark.parametrize("kind", ["label", "reason", "parent_binding", "replay_input", "missing_receipt", "registration_pack"])
def test_tampering_rejected_after_core_and_receipt_hash_refresh(tmp_path, kind):
    target = tmp_path / "tampered"
    shutil.copytree(REVISION, target)
    manifest = read_json(target / "manifest.json")
    rows = [json.loads(line) for line in (target / "gold.jsonl").read_text(encoding="utf-8").splitlines()]
    row = rows[-1]
    receipt_path = target / row["review"]["record_path"]
    if kind == "label":
        row["rationale"] = "Changed after explicit human approval."
    elif kind in {"reason", "parent_binding"}:
        receipt = read_json(receipt_path)
        if kind == "reason":
            receipt["reason"] = row["review"]["notes"] = "An agent substituted the review reason."
        else:
            receipt["parent_manifest_sha256"] = "0" * 64
        receipt_path.write_bytes(json.dumps(receipt, ensure_ascii=False).encode())
        row["review"]["record_sha256"] = file_hash(receipt_path)
        manifest["review_files"][row["review"]["record_path"]] = file_hash(receipt_path)
    elif kind == "replay_input":
        path = target / "inputs.jsonl"
        inputs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        inputs[-1]["documents"][0]["metadata"]["supported_claims"][-1]["verification_confidence"] = 0.5
        path.write_bytes("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in inputs).encode())
        manifest["files"]["inputs.jsonl"] = file_hash(path)
    elif kind == "missing_receipt":
        receipt_path.unlink()
    else:
        path = target / "human_review_registration.md"
        path.write_bytes(path.read_bytes() + b"\nUnreviewed replacement.\n")
    path = target / "gold.jsonl"
    path.write_bytes("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows).encode())
    manifest["files"]["gold.jsonl"] = file_hash(path)
    (target / "manifest.json").write_bytes(json.dumps(manifest).encode())
    with pytest.raises((ValueError, FileNotFoundError)):
        checked_revision(target)
