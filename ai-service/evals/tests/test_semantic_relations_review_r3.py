"""Pending coverage and provenance checks only; no evaluation or metric replay."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys

import pytest

from evals.semantic_relations_models import SemanticGoldCase, file_hash, load_suite, read_jsonl
from evals.semantic_relations_review import label_hash


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "semantic_relations_v1_review_r2"
REVISION = ROOT / "semantic_relations_v1_review_r3"


def read_json(name):
    return json.loads((REVISION / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def revision():
    return load_suite(REVISION)


@pytest.fixture(scope="module")
def pending():
    return read_jsonl(REVISION / "pending_cases.jsonl", SemanticGoldCase)


def test_new_cases_stay_pending_and_formal_gold_is_unchanged(revision, pending):
    cases, inputs, sources, _ = revision
    assert (len(cases), len(inputs), len(sources)) == (50, 50, 48)
    assert len(pending) == 10
    assert pending == [case for case in cases if case.review.status == "pending"]
    assert all(case.dataset_kind == "real_source" for case in pending)
    assert all(case.review.method == "pending_human" for case in pending)
    for case in pending:
        assert case.review.reviewer is None and case.review.reviewed_at is None
        assert case.review.record_path is None and case.review.record_sha256 is None
    approved = [case for case in cases if case.review.status == "approved"]
    assert len(approved) == 12
    assert approved == read_jsonl(PARENT / "approved_gold.jsonl", SemanticGoldCase)
    assert sum(case.dataset_kind == "synthetic" for case in cases) == 28
    assert (REVISION / "approved_gold.jsonl").read_bytes() == (PARENT / "approved_gold.jsonl").read_bytes()
    assert not {case.case_id for case in pending} & {case.case_id for case in approved}


def test_pending_coverage_in_each_family(pending):
    for family, total in (("era", 2), ("scene", 2), ("influence", 6)):
        rows = [case for case in pending if case.relation_type == family]
        assert len(rows) == total
        assert sum("negation" in case.categories for case in rows) == 1
        assert sum("conflict" in case.categories for case in rows) == 1
    directional = [case for case in pending if "direction_sensitive" in case.categories]
    assert len(directional) == 4
    assert sum(case.label == "negative" for case in directional) == 2
    assert sum(case.label == "positive" for case in directional) == 2


def test_append_only_parent_data_receipts_and_rejections(revision):
    manifest = revision[3]
    assert manifest["parent_manifest_sha256"] == file_hash(PARENT / "manifest.json")
    assert manifest["parent_files"] == load_suite(PARENT)[3]["files"]
    for name in ("gold.jsonl", "inputs.jsonl", "sources.jsonl"):
        assert (REVISION / name).read_bytes().startswith((PARENT / name).read_bytes())
    for name in ("schema.json", "approved_gold.jsonl", "excluded_cases.jsonl"):
        assert (REVISION / name).read_bytes() == (PARENT / name).read_bytes()
    for folder in ("receipts", "review_history"):
        for path in (PARENT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (REVISION / path.relative_to(PARENT)).read_bytes()
    for name in ("review_decisions.json", "review_decisions.schema.json", "review_summary.json"):
        assert (PARENT / name).read_bytes() == (REVISION / "review_history" / f"r2_{name}").read_bytes()
    excluded = read_jsonl(REVISION / "excluded_cases.jsonl", SemanticGoldCase)
    assert len(excluded) == 4 and all(case.review.status == "rejected" for case in excluded)
    assert not {case.case_id for case in excluded} & {case.case_id for case in revision[0]}


def test_all_previous_freezes_and_failure_reports_remain_byte_identical():
    record = read_json("protected_artifacts.json")
    protection = record["sha256"]
    directories = (
        "semantic_relations_v1", "semantic_relations_v1_review_r1", "semantic_relations_v1_review_r2",
        "reports/semantic_relations_v1", "reports/semantic_relations_v1_review_r1",
        "reports/semantic_relations_v1_review_r2",
    )
    existing = {path.relative_to(ROOT).as_posix() for directory in directories
                for path in (ROOT / directory).rglob("*") if path.is_file()}
    assert set(protection) == existing
    assert set(record["git_blob_sha256"]) == existing
    # Historical reports lack an LF checkout rule. Accept only their two
    # inspected Git/worktree byte hashes, never normalize source or Gold data.
    eol_paths = set(record["historical_report_eol_variants"])
    assert len(eol_paths) == 7
    assert all(name.startswith("reports/semantic_relations_v1/") for name in eol_paths)
    for name, digest in protection.items():
        allowed = {digest}
        if name in eol_paths:
            allowed.add(record["git_blob_sha256"][name])
        else:
            assert digest == record["git_blob_sha256"][name]
        assert file_hash(ROOT / name) in allowed


def test_all_new_review_materials_are_hashed(revision):
    manifest = revision[3]
    assert manifest["evaluation_run"] is False
    names = {path.relative_to(REVISION).as_posix() for path in REVISION.rglob("*")
             if path.is_file() and path.name not in {"README.md", "manifest.json"}}
    assert names == set(manifest["files"]) | set(manifest["review_files"])
    for name, digest in manifest["review_files"].items():
        assert file_hash(REVISION / name) == digest


def test_source_inspection_does_not_infer_human_approval(revision, pending):
    source_map = revision[2]
    audit = read_json("source_audit.json")
    assert audit["inspection_is_human_review"] is False
    assert len(audit["entries"]) == 9 and len(audit["reused_sources"]) == 2
    used = {span.source_id for case in pending for span in case.supporting_evidence}
    assert used == {row["source_id"] for row in audit["entries"] + audit["reused_sources"]}
    for entry in audit["entries"]:
        source = source_map[entry["source_id"]]
        assert source.origin == "real_source" and source.url
        assert source.capture_method == "verbatim_web_excerpt"
        assert source.content_hash == entry["source_content_sha256"]
        assert entry["human_review_status"] == "pending"
        assert entry["context_is_gold_evidence"] is False
        assert entry["claim_scope"] and entry["review_caution"] and entry["locator"]
    assert {source_map[key].language for key in used} == {"en", "it"}
    for case in pending:
        for span in case.supporting_evidence:
            assert source_map[span.source_id].content[span.span_start:span.span_end] == span.exact_span


def test_checklist_and_pack_bind_every_pending_label(pending):
    checklist = read_json("review_checklist.json")
    assert checklist["evaluation_run"] is False
    assert checklist["reviewer"] is None and checklist["reviewed_at"] is None
    items = {item["case_id"]: item for item in checklist["items"]}
    assert set(items) == {case.case_id for case in pending}
    pack = (REVISION / "human_review_pack.md").read_text(encoding="utf-8")
    for case in pending:
        item = items[case.case_id]
        assert item["status"] == "pending"
        assert item["human_decision"] is None and item["human_reason"] is None
        assert item["label_sha256"] == label_hash(case)
        assert item["review_questions"]
        assert case.case_id in pack and item["label_sha256"] in pack
        assert case.query in pack
        for span in case.supporting_evidence:
            assert span.exact_span in pack


def test_conflict_proposals_require_both_sides_and_explicit_semantics(pending):
    conflicts = [case for case in pending if "conflict" in case.categories]
    assert len(conflicts) == 3
    for case in conflicts:
        assert case.label == "positive"
        assert len({span.source_id for span in case.supporting_evidence}) == 2
        assert {span.stance for span in case.supporting_evidence} == {"support", "contradiction"}
        claims = {span.claim_id for span in case.supporting_evidence}
        for relation in case.expected_relations:
            assert set(relation.evidence_claim_ids) == claims
            assert relation.conflict_flags == [
                "temporal_conflict" if case.relation_type == "era" else "attribution_conflict"
            ]
            if case.relation_type == "era":
                assert relation.fields == {
                    "start_year": None, "end_year": None, "temporal_precision": "unknown",
                }
    era = next(case for case in conflicts if case.relation_type == "era")
    assert {relation.target_entity for relation in era.expected_relations} == {"era:1950s", "era:1960s"}


def test_reverse_queries_never_reverse_the_supplied_claims(revision, pending):
    cases = {case.case_id: case for case in pending}
    inputs = revision[1]
    items = read_json("review_checklist.json")["items"]
    pair_ids = {item["pair_id"] for item in items if item["pair_id"]}
    assert len(pair_ids) == 2
    for pair_id in pair_ids:
        ids = [item["case_id"] for item in items if item["pair_id"] == pair_id]
        assert len(ids) == 2
        forward = next(cases[key] for key in ids if cases[key].label == "positive")
        reverse = next(cases[key] for key in ids if cases[key].label == "negative")
        assert (forward.source_entity, forward.target_entity) == (reverse.target_entity, reverse.source_entity)
        assert forward.source_entity != forward.target_entity
        assert reverse.expected_relations == []
        assert inputs[forward.case_id].documents == inputs[reverse.case_id].documents
        assert forward.query != reverse.query
        for document in inputs[reverse.case_id].documents:
            for claim in document.metadata["supported_claims"]:
                assert "eval:" + claim["subject_entity_id"] == forward.source_entity
                assert "eval:" + claim["object_entity_id"] == forward.target_entity
        assert all(span.stance == "context" for span in reverse.supporting_evidence)


def test_negations_keep_empty_expected_relations_and_actual_scope(revision, pending):
    inputs = revision[1]
    for case in pending:
        assert case.source_entity in {node.id for node in inputs[case.case_id].graph.nodes}
        if "negation" in case.categories:
            assert case.label == "negative" and case.expected_relations == []
            assert all(span.stance == "contradiction" for span in case.supporting_evidence)
    era = next(case for case in pending if case.relation_type == "era" and case.label == "negative")
    claim = inputs[era.case_id].documents[0].metadata["supported_claims"][0]
    assert era.target_entity == "era:1960s"
    assert claim["time"]["year"] == 1970 and claim["object_value"] == "1970s"
    influence = next(case for case in pending if case.relation_type == "influence" and "negation" in case.categories)
    claim = inputs[influence.case_id].documents[0].metadata["supported_claims"][0]
    assert claim["predicate"] == "influenced_by"
    assert "eval:" + claim["subject_entity_id"] == influence.target_entity
    assert "eval:" + claim["object_entity_id"] == influence.source_entity


def test_replay_contains_no_gold_decisions_or_expectations(revision, pending):
    for case in pending:
        raw = revision[1][case.case_id].model_dump(mode="json")
        assert not {"label", "expected_relations", "categories", "review", "rationale"} & set(raw)
        for document in raw["documents"]:
            assert not {"label", "expected_relations", "review", "gold"} & set(document["metadata"])


def test_loading_uses_no_scoring_runtime_or_final_outputs(monkeypatch):
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    load_suite(REVISION)
    assert not any((REVISION / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))


@pytest.mark.parametrize("kind", ["gold_span", "input_content", "source_hash"])
def test_loader_rejects_tampering_even_after_core_manifest_rehash(tmp_path, kind):
    target = tmp_path / "tampered"
    shutil.copytree(REVISION, target)
    name = {"gold_span": "gold.jsonl", "input_content": "inputs.jsonl", "source_hash": "sources.jsonl"}[kind]
    rows = [json.loads(line) for line in (target / name).read_text(encoding="utf-8").splitlines()]
    row = rows[-1]  # Only a newly proposed record; never touch an existing freeze.
    if kind == "gold_span":
        row["supporting_evidence"][0]["exact_span"] = "Invented evidence."
    elif kind == "input_content":
        row["documents"][0]["content"] = "Invented evidence."
    else:
        row["content_hash"] = "0" * 64
    (target / name).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    manifest = json.loads((target / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"][name] = file_hash(target / name)
    (target / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        load_suite(target)


def test_pending_cannot_be_promoted_without_a_human_receipt(pending):
    raw = deepcopy(pending[0].model_dump(mode="json"))
    raw["review"].update(status="approved", method="human", reviewer="agent")
    with pytest.raises(ValueError, match="human_review_record_required"):
        SemanticGoldCase.model_validate(raw)
