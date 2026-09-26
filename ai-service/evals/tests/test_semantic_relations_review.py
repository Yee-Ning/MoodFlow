"""Review preparation/integrity tests only: no runtime replay or metric evaluation."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil
import sys

import pytest

from evals.semantic_relations_models import SemanticGoldCase, file_hash, load_suite, read_jsonl
from evals.semantic_relations_review import (
    HumanReviewBatch, label_hash, prepare_review_revision, validate_review_revision,
)


BASE = Path(__file__).resolve().parents[1] / "semantic_relations_v1"
REVISION = BASE.with_name("semantic_relations_v1_review_r1")


@pytest.fixture
def proposal():
    cases, inputs, sources, _ = load_suite(REVISION)
    pending = [case for case in cases if case.review.status == "pending"]
    source_ids = {span.source_id for case in pending for span in case.supporting_evidence}
    batch = HumanReviewBatch.model_validate_json((REVISION / "review_decisions.json").read_bytes())
    return (batch, pending, [inputs[case.case_id] for case in pending],
            [sources[key] for key in source_ids])


def file_tree_hashes(directory):
    return {str(path.relative_to(directory)): file_hash(path)
            for path in directory.rglob("*") if path.is_file()}


def test_committed_revision_receipts_views_and_provenance():
    assert validate_review_revision(REVISION, BASE) == {
        "integrity": "passed", "evaluation_run": False, "approved": 8, "excluded": 4, "pending": 4,
    }
    original_cases, _, _, manifest = load_suite(BASE)
    assert len(original_cases) == 40
    assert all(case.review.status == "pending" for case in original_cases if case.dataset_kind == "real_source")
    assert manifest["files"] == {
        "gold.jsonl": "e0ecf98e1048d6dd81dacc217302e822bbf2b536e3ed458f3f057b13f918a1ba",
        "inputs.jsonl": "9a40e8384ed049bed500ce4428e7aab706e011660c83ebe2a11ebf4ebcc48477",
        "sources.jsonl": "3edfae785cb01f355c90a0599d4e64cb0f227f310d44e0d98ef4abbed8ee5c6e",
        "schema.json": "019033ce34e5ad97bb6c2f1703dd97f4b300ba871bda46d8ad23b0dfa66fb193",
    }
    approved = read_jsonl(REVISION / "approved_gold.jsonl", SemanticGoldCase)
    assert {case.review.reviewer for case in approved} == {"human-review-WT"}
    assert {case.review.reviewed_at for case in approved} == {"2026-09-26"}
    excluded = read_jsonl(REVISION / "excluded_cases.jsonl", SemanticGoldCase)
    assert all(case.case_id.endswith("weak_evidence") for case in excluded)
    assert not {case.case_id for case in excluded} & {case.case_id for case in load_suite(REVISION)[0]}


def test_new_pending_pairs_match_candidates_and_source_context(proposal):
    _, cases, inputs, sources = proposal
    assert len(cases) == 4
    assert sorted(case.relation_type for case in cases) == ["era", "influence", "influence", "scene"]
    source_map = {source.source_id: source for source in sources}
    for case, replay in zip(cases, inputs, strict=True):
        assert case.review.status == "pending" and case.review.reviewer is None
        assert case.label == "negative" and not case.expected_relations
        span = case.supporting_evidence[0]
        assert span.stance == "context"
        source = source_map[span.source_id]
        assert source.content[span.span_start:span.span_end] == span.exact_span
        assert source.content_hash == sha256(source.content.encode("utf-8")).hexdigest()
        assert source.url and source.capture_method == "verbatim_web_excerpt"
        assert replay.query == case.query == replay.search_plan.original_query
        raw = replay.model_dump(mode="json")
        assert not {"label", "expected_relations", "review", "rationale"} & raw.keys()
        claim = replay.documents[0].metadata["supported_claims"][0]
        assert f"eval:{claim['subject_entity_id']}" == case.source_entity
        if case.relation_type == "influence":
            assert case.source_entity != case.target_entity
            assert f"eval:{claim['object_entity_id']}" == case.target_entity
            names = {node.name for node in replay.graph.nodes}
            assert len(names) == 2 and all(name in case.query for name in names)


def test_preparation_preserves_original_data_and_reports_without_evaluation(tmp_path, proposal, monkeypatch):
    # A blocked import makes accidental runner/scoring use fail this preparation test.
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_runner", None)
    monkeypatch.setitem(sys.modules, "evals.semantic_relations_metrics", None)
    reports = BASE.parent / "reports" / "semantic_relations_v1"
    before = file_tree_hashes(BASE), file_tree_hashes(reports)
    output = tmp_path / "new-review"
    summary = prepare_review_revision(BASE, output, *proposal)
    assert not summary["evaluation_run"]
    assert summary["approved_real_gold"] == 8 and summary["excluded_rejected"] == 4
    assert validate_review_revision(output, BASE)["pending"] == 4
    assert before == (file_tree_hashes(BASE), file_tree_hashes(reports))
    assert not any((output / name).exists() for name in ("metrics.json", "results.jsonl", "failures.jsonl"))
    for case in read_jsonl(output / "approved_gold.jsonl", SemanticGoldCase):
        original = next(item for item in load_suite(BASE)[0] if item.case_id == case.case_id)
        assert label_hash(case) == label_hash(original)


@pytest.mark.parametrize("target", ["existing", "parent"])
def test_preparation_refuses_overwrite_or_parent_mutation(tmp_path, proposal, target):
    output = tmp_path / "existing" if target == "existing" else BASE / "new-child"
    if target == "existing":
        output.mkdir()
    with pytest.raises(ValueError, match="new_revision_directory_required"):
        prepare_review_revision(BASE, output, *proposal)


@pytest.mark.parametrize("change", ["missing_decision", "approve_pending", "self_loop", "same_query", "input_mismatch"])
def test_invalid_review_or_pending_inputs_fail_before_writing(tmp_path, proposal, change):
    batch, cases, inputs, sources = deepcopy(proposal)
    if change == "missing_decision":
        batch = batch.model_copy(update={"decisions": batch.decisions[:-1]})
    elif change == "approve_pending":
        cases[0] = cases[0].model_copy(update={"review": cases[0].review.model_copy(update={"status": "approved"})})
    elif change == "self_loop":
        idx = next(i for i, case in enumerate(cases) if case.relation_type == "influence")
        cases[idx] = cases[idx].model_copy(update={"source_entity": cases[idx].target_entity})
    elif change == "same_query":
        cases[0] = cases[0].model_copy(update={"query": load_suite(BASE)[0][-1].query})
    else:
        inputs = inputs[:-1]
    output = tmp_path / "new-review"
    with pytest.raises(ValueError):
        prepare_review_revision(BASE, output, batch, cases, inputs, sources)
    assert not output.exists()


def test_duplicate_decisions_rejected(proposal):
    raw = proposal[0].model_dump(mode="json")
    raw["decisions"].append(raw["decisions"][0])
    with pytest.raises(ValueError, match="duplicate_review_decision"):
        HumanReviewBatch.model_validate(raw)


@pytest.mark.parametrize("tamper", ["receipt", "excluded_reason", "input", "span"])
def test_revision_validator_rejects_tampering_even_after_core_refreeze(tmp_path, tamper):
    output = tmp_path / "copy"
    shutil.copytree(REVISION, output)
    manifest_path = output / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if tamper in {"receipt", "excluded_reason"}:
        relative = "receipts/real-era-0-weak_evidence.json"
        path = output / relative
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["reason"] = "Changed human reason"
        path.write_text(json.dumps(receipt), encoding="utf-8")
        if tamper == "excluded_reason":
            manifest["review_files"][relative] = file_hash(path)
    else:
        filename = "inputs.jsonl" if tamper == "input" else "gold.jsonl"
        path = output / filename
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        if tamper == "input":
            rows[0]["search_plan"]["final_top_k"] += 1
        else:
            rows[-1]["supporting_evidence"][0]["exact_span"] = "Invented quote"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
        manifest["files"][filename] = file_hash(path)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError):
        validate_review_revision(output, BASE)
