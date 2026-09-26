"""Pending Influence confusable review bundle; no scoring or runtime replay."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import ast
import json
from pathlib import Path
import shutil
import sys

import pytest

from evals.semantic_relations_models import (
    PREDICATES, SemanticGoldCase, file_hash, load_suite, read_jsonl,
)
from evals.semantic_relations_review import label_hash
from evals.tests.historical_scope import assert_historical_runtime_scope


ROOT = Path(__file__).resolve().parents[1]
PARENT = ROOT / "semantic_relations_v1_review_r4"
REVISION = ROOT / "semantic_relations_v1_review_r5"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def checked_bundle(directory):
    cases, inputs, sources, manifest = load_suite(directory)
    for name, digest in manifest["review_files"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or file_hash(path) != digest:
            raise ValueError("review_material_hash_mismatch")
    items = read_json(directory / "review_checklist.json")["items"]
    lines = {json.loads(line)["case_id"]: line for line in
             (directory / "inputs.jsonl").read_bytes().splitlines(keepends=True)}
    pending = {c.case_id: c for c in cases if c.review.status == "pending"}
    if {item["case_id"] for item in items} != set(pending):
        raise ValueError("pending_inventory_mismatch")
    for item in items:
        case = pending[item["case_id"]]
        if item["label_sha256"] != label_hash(case):
            raise ValueError("pending_label_binding_mismatch")
        if item["replay_input_line_sha256"] != sha256(lines[case.case_id]).hexdigest():
            raise ValueError("pending_input_binding_mismatch")
    return cases, inputs, sources, manifest


@pytest.fixture(scope="module")
def revision():
    return checked_bundle(REVISION)


def test_pending_cases_are_new_real_influence_confusable_only(revision):
    cases, inputs, sources, _ = revision
    assert (len(cases), len(inputs), len(sources)) == (53, 53, 52)
    pending = [c for c in cases if c.review.status == "pending"]
    assert pending == read_jsonl(REVISION / "pending_cases.jsonl", SemanticGoldCase)
    assert len(pending) == 3
    for case in pending:
        assert case.dataset_kind == "real_source" and case.relation_type == "influence"
        assert case.label == "negative" and case.expected_relations == []
        assert set(case.categories) == {"negative", "confusable", "isolation"}
        assert case.review.method == "pending_human"
        assert case.review.reviewer is case.review.reviewed_at is None
        assert case.review.record_path is case.review.record_sha256 is None
        assert case.cohort == "pending_real_diagnostic"
        assert all(s.stance == "context" for s in case.supporting_evidence)
    assert len([c for c in cases if c.review.status == "approved"]) == 22
    assert sum(c.dataset_kind == "synthetic" for c in cases) == 28


def test_parent_data_approved_receipts_and_history_remain_append_only(revision):
    cases, _, _, manifest = revision
    for name in ("gold.jsonl", "inputs.jsonl", "sources.jsonl"):
        assert (REVISION / name).read_bytes().startswith((PARENT / name).read_bytes())
    for name in ("schema.json", "approved_gold.jsonl", "excluded_cases.jsonl", "review_decisions.schema.json"):
        assert (REVISION / name).read_bytes() == (PARENT / name).read_bytes()
    assert [c for c in cases if c.review.status == "approved"] == read_jsonl(
        PARENT / "approved_gold.jsonl", SemanticGoldCase,
    )
    assert len(list((REVISION / "receipts").glob("*.json"))) == 26
    for folder in ("receipts", "review_history"):
        for path in (PARENT / folder).rglob("*"):
            if path.is_file():
                assert path.read_bytes() == (REVISION / path.relative_to(PARENT)).read_bytes()
    kept = {"gold.jsonl", "inputs.jsonl", "sources.jsonl", "schema.json", "approved_gold.jsonl",
            "excluded_cases.jsonl", "review_decisions.schema.json", ".gitattributes"}
    for path in PARENT.iterdir():
        if path.is_file() and path.name not in kept:
            assert path.read_bytes() == (REVISION / "review_history" / ("r4_" + path.name)).read_bytes()
    assert manifest["parent_manifest_sha256"] == file_hash(PARENT / "manifest.json")
    assert manifest["parent_files"] == load_suite(PARENT)[3]["files"]
    assert manifest["parent_baseline_artifact_manifest_sha256"] == file_hash(
        ROOT / "reports/semantic_relations_v1_baseline_r4/artifact_manifest.json",
    )


def test_bundle_inventory_and_review_sidecars_are_bound(revision):
    _, _, _, manifest = revision
    names = {p.relative_to(REVISION).as_posix() for p in REVISION.rglob("*")
             if p.is_file() and p.name not in {"README.md", "manifest.json"}}
    assert names == set(manifest["files"]) | set(manifest["review_files"])
    checklist = read_json(REVISION / "review_checklist.json")
    assert checklist["reviewer"] is checklist["reviewed_at"] is None
    assert checklist["evaluation_run"] is False
    assert checklist["items"] == [json.loads(line) for line in
                                  (REVISION / "pending_review_items.jsonl").read_text(encoding="utf-8").splitlines()]
    for item in checklist["items"]:
        assert item["status"] == "pending"
        assert item["human_decision"] is item["human_reason"] is None
        assert "evidence-relative" in item["negative_scope"]


def test_source_facts_and_candidate_influence_probes_are_separate(revision):
    cases, inputs, sources, _ = revision
    case_map = {c.case_id: c for c in cases}
    for item in read_json(REVISION / "review_checklist.json")["items"]:
        case = case_map[item["case_id"]]
        replay = inputs[case.case_id]
        assert (item["query"], item["source_entity"], item["target_entity"]) == (
            case.query, case.source_entity, case.target_entity,
        )
        assert item["candidate_predicate"] in PREDICATES["influence"]
        assert item["candidate_predicate"] in case.query
        assert [kind.value for kind in replay.search_plan.query_types] == ["relationship", "influence_style"]
        nodes = {node.id for node in replay.graph.nodes}
        assert case.source_entity != case.target_entity
        assert {case.source_entity, case.target_entity} <= nodes
        facts, probes = item["non_influence_fact_claims"], item["influence_candidate_hypotheses"]
        assert len(probes) == 1
        assert all(f["predicate"] not in PREDICATES["influence"] for f in facts)
        assert all(f["predicate"] == ("released" if item["non_influence_evidence_family"] == "era"
                                     else "scene_association") for f in facts)
        probe = probes[0]
        assert probe["predicate"] == item["candidate_predicate"]
        assert ("eval:" + probe["subject_entity_id"], "eval:" + probe["object_entity_id"]) == (
            case.source_entity, case.target_entity,
        )
        actual_claims = {f["claim_id"]: f for doc in replay.documents
                         for f in doc.metadata["supported_claims"]}
        assert actual_claims == {f["claim_id"]: f for f in facts + probes}
        assert {s.claim_id for s in case.supporting_evidence} == {f["claim_id"] for f in facts}
        for joined in item["supporting_evidence"]:
            span, source = joined["span"], sources[joined["span"]["source_id"]]
            assert joined["source_metadata"] == source.model_dump(mode="json")
            assert source.content[span["span_start"]:span["span_end"]] == span["exact_span"]
            assert joined["exact_span_sha256"] == sha256(span["exact_span"].encode()).hexdigest()
        raw = replay.model_dump(mode="json")
        assert not {"label", "expected_relations", "review", "categories", "rationale"} & set(raw)
        for doc in raw["documents"]:
            assert not {"label", "expected_relations", "review", "gold"} & set(doc["metadata"])


def test_original_publisher_captures_do_not_approve_gold(revision):
    audit = read_json(REVISION / "source_audit.json")
    assert audit["inspection_is_human_review"] is False and len(audit["entries"]) == 4
    for entry in audit["entries"]:
        source = revision[2][entry["source_id"]]
        assert source.origin == "real_source" and source.capture_method == "verbatim_web_excerpt"
        assert entry["http_status"] == 200 and entry["resolved_url"] == str(source.url)
        assert source.content_hash == entry["source_content_sha256"]
        assert entry["human_review_status"] == "pending" and entry["context_is_gold_evidence"] is False
        assert entry["publication_date"] is None and entry["publication_date_basis"]
        assert entry["locator"] and entry["claim_scope"] and entry["review_caution"]
        for fragment in entry["fragments"]:
            text = source.content[fragment["snapshot_start"]:fragment["snapshot_end"]]
            assert text == fragment["text"]
            assert sha256(text.encode()).hexdigest() == fragment["fragment_sha256"]
    pearljam = next(e for e in audit["entries"] if e["key"] == "pearljam")
    assert pearljam["verified_album_identity"] == {
        "id": "18592", "name": "Ten", "slug": "ten", "released": "Aug. 27, 1991",
    }


def test_pack_contains_all_requested_fields_and_scope(revision):
    cases = {c.case_id: c for c in revision[0]}
    pack = (REVISION / "human_review_pack.md").read_text(encoding="utf-8")
    items = read_json(REVISION / "review_checklist.json")["items"]
    assert Counter(item["non_influence_evidence_family"] for item in items) == {"scene": 2, "era": 1}
    for item in items:
        case = cases[item["case_id"]]
        for text in (case.case_id, case.query, case.source_entity, case.target_entity,
                     item["candidate_predicate"], item["label_sha256"], item["replay_input_line_sha256"]):
            assert text in pack
        for span in case.supporting_evidence:
            source = revision[2][span.source_id]
            for text in (span.claim_id, span.chunk_id, span.document_id, source.source_id,
                         source.content_hash, source.locator, str(source.url), span.exact_span):
                assert text in pack
            assert f"[{span.span_start}, {span.span_end})" in pack
    assert "不表示现实世界绝无该影响" in pack
    assert "没有回复即保持 pending" in pack


def test_static_coverage_projection_matches_existing_gate_without_execution(revision):
    report = read_json(REVISION / "coverage_projection.json")
    assert report["evaluation_run"] is False
    path = ROOT.parent / report["gate_source_path"]
    assert file_hash(path) == report["gate_source_sha256"]
    tree = ast.parse(path.read_text(encoding="utf-8"))
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run_suite")
    required = next(ast.literal_eval(n.value) for n in ast.walk(function) if isinstance(n, ast.Assign)
                    and any(isinstance(t, ast.Name) and t.id == "required" for t in n.targets))
    extra = next(ast.literal_eval(n.args[0]) for n in ast.walk(function) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "required" and n.func.attr == "add")
    for family in ("era", "scene", "influence"):
        approved = [c for c in revision[0] if c.review.status == "approved" and c.relation_type == family]
        pending = [c for c in revision[0] if c.review.status == "pending" and c.relation_type == family]
        needed = required | ({extra} if family == "influence" else set())
        missing = sorted(needed - {cat for c in approved for cat in c.categories})
        assert report["current"][family]["missing_required_categories"] == missing
        assert report["conditional_after_explicit_human_approval"][family][
            "missing_required_categories_if_all_three_approved"
        ] == sorted(needed - {cat for c in approved + pending for cat in c.categories}) == []
    assert report["current"]["influence"]["missing_required_categories"] == ["confusable"]
    assert report["minimum_new_approvals_to_close_this_coverage_gap"] == 1
    assert report["baseline_ready_now"] is report["can_enter_targeted_repairs_now"] is False
    assert report["quality_failures_are_not_cleared_by_coverage_projection"] is True


def test_old_freezes_baseline_and_runtime_are_protected():
    record = read_json(REVISION / "protected_artifacts.json")
    assert record["file_count"] == len(record["sha256"]) == 190
    assert set(record["sha256"]) == set(record["git_blob_sha256"])
    variants = set(record["historical_report_eol_variants"])
    assert len(variants) == 7 and all(p.startswith("reports/semantic_relations_v1/") for p in variants)
    assert "reports/semantic_relations_v1_baseline_r4/metrics.json" in record["sha256"]
    for name, digest in record["sha256"].items():
        allowed = {digest}
        if name in variants:
            allowed.add(record["git_blob_sha256"][name])
        else:
            assert digest == record["git_blob_sha256"][name]
        assert file_hash(ROOT / name) in allowed
    scope = read_json(REVISION / "scope_lock.json")
    assert scope["evaluation_run"] is scope["targeted_repairs_run"] is False
    assert_historical_runtime_scope(REVISION / "scope_lock.json", scope)


def test_preparation_does_not_import_or_run_evaluation(monkeypatch):
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    checked_bundle(REVISION)
    assert not any((REVISION / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))
    assert not any((ROOT / "reports/semantic_relations_v1_review_r5" / name).exists()
                   for name in ("metrics.json", "results.jsonl", "failures.jsonl"))


@pytest.mark.parametrize("kind", ["gold_label", "input_probe", "gold_span", "source_hash", "review_pack"])
def test_tampering_is_rejected_even_if_core_hash_is_refreshed(tmp_path, kind):
    target = tmp_path / "tampered"
    shutil.copytree(REVISION, target)
    name = {"gold_label": "gold.jsonl", "input_probe": "inputs.jsonl", "gold_span": "gold.jsonl",
            "source_hash": "sources.jsonl", "review_pack": "human_review_pack.md"}[kind]
    path = target / name
    if kind == "review_pack":
        path.write_bytes(path.read_bytes() + b"\nChanged after freeze.\n")
    else:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if kind == "gold_label":
            rows[-1]["target_entity"] = "eval:album:unreviewed"
        elif kind == "input_probe":
            rows[-1]["documents"][0]["metadata"]["supported_claims"][-1]["predicate"] = "influenced"
        elif kind == "gold_span":
            rows[-1]["supporting_evidence"][0]["exact_span"] = "Invented evidence."
        else:
            rows[-1]["content_hash"] = "0" * 64
        path.write_bytes("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows).encode("utf-8"))
        manifest = read_json(target / "manifest.json")
        manifest["files"][name] = file_hash(path)
        (target / "manifest.json").write_bytes(json.dumps(manifest).encode())
    with pytest.raises(ValueError):
        checked_bundle(target)


def test_pending_approval_cannot_be_fabricated():
    pending = read_jsonl(REVISION / "pending_cases.jsonl", SemanticGoldCase)
    raw = deepcopy(pending[0].model_dump(mode="json"))
    raw["review"].update(status="approved", method="human", reviewer="agent")
    with pytest.raises(ValueError, match="human_review_record_required"):
        SemanticGoldCase.model_validate(raw)
