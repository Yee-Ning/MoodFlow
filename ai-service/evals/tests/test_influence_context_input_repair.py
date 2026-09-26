"""Audit the input-only freeze and P0 acceptance without changing Gold/scoring."""

from collections import Counter
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import shutil

import pytest

from app.agent.foundation.subgraphs.worldline.source_context import RelationSourceContext
from evals.semantic_relations_metrics import aggregate
from evals.semantic_relations_models import file_hash, load_suite


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "semantic_relations_v1_review_r6"
SUITE = ROOT / "semantic_relations_context_inputs_v1"
BEFORE = ROOT / "reports/semantic_relations_v1_conflict_repair_r6"
REPORT = ROOT / "reports/semantic_relations_v1_influence_context_repair"


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_json(path, value):
    path.write_bytes((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def copied_suite(tmp_path):
    destination = tmp_path / "context"
    shutil.copytree(SUITE, destination)
    manifest = read_json(destination / "manifest.json")
    manifest["base_directory"] = str(BASE)
    write_json(destination / "manifest.json", manifest)
    return destination


def rehash(directory, name):
    manifest = read_json(directory / "manifest.json")
    manifest["files"][name] = file_hash(directory / name)
    write_json(directory / "manifest.json", manifest)


def test_overlay_changes_only_source_context_and_retains_original_review_and_spans():
    cases, inputs, sources, manifest = load_suite(SUITE)
    old_cases, old_inputs, old_sources, base_manifest = load_suite(BASE)
    assert cases == old_cases and sources == old_sources
    assert manifest["base_manifest_sha256"] == file_hash(BASE / "manifest.json")
    assert manifest["base_files"] == base_manifest["files"]
    assert manifest["base_review_files"] == base_manifest["review_files"]
    changed = []
    for case_id, replay in inputs.items():
        stripped = deepcopy(replay.model_dump(mode="json"))
        for doc in stripped["documents"]:
            payload = doc["metadata"].pop("relation_source_context", None)
            if payload:
                context = RelationSourceContext.model_validate(payload)
                original = next(d for d in old_inputs[case_id].documents if d.id == doc["id"])
                claim = original.metadata["supported_claims"][0]
                assert context.bound_to(original, claim["claim_id"], claim["evidence_span"])
                assert context.content[context.anchor.start:context.anchor.end] == claim["evidence_span"]
                changed.append((case_id, doc["id"]))
        assert stripped == old_inputs[case_id].model_dump(mode="json")
    assert len(changed) == 2 and len({c for c, _ in changed}) == 1
    assert len(cases) == 53 and not (SUITE / "gold.jsonl").exists()


def test_context_freeze_rejects_raw_tamper(tmp_path):
    copied = copied_suite(tmp_path)
    path = copied / "context_sources.jsonl"
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError, match="context_freeze_hash_mismatch"):
        load_suite(copied)


@pytest.mark.parametrize("field", ["query", "claim", "content", "source_id", "context"])
def test_rehashed_input_cannot_change_query_claim_snapshot_or_capture(tmp_path, field):
    copied = copied_suite(tmp_path)
    data = rows(copied / "inputs.jsonl")
    replay = next(r for r in data if any("relation_source_context" in d["metadata"] for d in r["documents"]))
    doc = replay["documents"][0]
    if field == "query":
        replay["query"] += " changed"
    elif field == "claim":
        doc["metadata"]["supported_claims"][0]["subject_entity_id"] = "other:artist"
    elif field == "content":
        doc["content"] += " changed"
    elif field == "source_id":
        doc["metadata"]["snapshot_source_id"] = "other:source"
    else:
        doc["metadata"]["relation_source_context"]["locator"] += " changed"
    (copied / "inputs.jsonl").write_bytes("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in data).encode())
    rehash(copied, "inputs.jsonl")
    with pytest.raises(ValueError, match="context_(overlay_changed_base_input|input_binding_mismatch|input_capture_mismatch)"):
        load_suite(copied)


def test_frozen_export_hashes_metrics_scope_and_old_artifacts():
    manifest = read_json(REPORT / "artifact_manifest.json")
    assert set(manifest["files"]) == {p.name for p in REPORT.iterdir() if p.is_file() and p.name != "artifact_manifest.json"}
    for name, digest in manifest["files"].items():
        assert file_hash(REPORT / name) == digest
    after = read_json(REPORT / "metrics.json")
    result = rows(REPORT / "results.jsonl")
    assert after["metrics"] == aggregate(result)
    protection = read_json(REPORT / "protected_artifacts.json")
    assert protection["file_count"] == len(protection["files"]) == 373
    for name, digest in protection["files"].items():
        assert file_hash(ROOT.parent / name) == digest
    scope = read_json(REPORT / "scope_validation.json")
    changed = {name for name, h in scope["runtime_after"].items() if h != scope["runtime_before"][name]}
    assert changed == set(scope["changed_runtime_files"]) == {
        "evals/semantic_relations_models.py", "app/agent/foundation/subgraphs/worldline/influence_expansion.py",
    }
    for name in ("evals/semantic_relations_metrics.py", "evals/semantic_relations_runner.py"):
        assert file_hash(ROOT.parent / name) == scope["runtime_before"][name]


def test_p0_completed_with_all_conservative_guards_and_synthetic_results_unchanged():
    after, before = read_json(REPORT / "metrics.json"), read_json(BEFORE / "metrics.json")
    result = rows(REPORT / "results.jsonl")
    previous = {r["case_id"]: r for r in rows(BEFORE / "results.jsonl")}
    comparison = read_json(REPORT / "metrics_comparison.json")
    assert comparison["new_regressions"] == []
    assert len(comparison["improved_cases"]) == 1
    assert after["metrics"]["synthetic_regression"] == before["metrics"]["synthetic_regression"]
    for r in result:
        if r["case_id"] not in comparison["improved_cases"]:
            assert r["actual"] == previous[r["case_id"]]["actual"]
    real = after["metrics"]["approved_real_baseline"]
    assert real["overall"]["passed"] == 20
    assert real["overall"]["metrics"]["conflict_handling"] == {"numerator": 3, "denominator": 3, "value": 1}
    for family in ("era", "scene", "influence"):
        assert real[family]["metrics"]["conflict_handling"] == {"numerator": 1, "denominator": 1, "value": 1}
        for metric in ("weak_evidence_rejection", "negation_rejection", "query_isolation", "checkpoint_continuity"):
            assert real[family]["metrics"][metric] == before["metrics"]["approved_real_baseline"][family]["metrics"][metric]
    gate = read_json(REPORT / "machine_gate.json")
    assert gate["baseline_ready"] is gate["can_enter_targeted_repairs"] is True
    assert gate["p0_conflict_target_met"] is gate["can_enter_p1_direction_repair"] is True
    assert gate["reverse_direction_negative"] == {"passed": 2, "count": 2}
    assert gate["p1_started"] is gate["final_regression_ready"] is False
    control = read_json(REPORT / "r6_control_comparison.json")
    assert control["metrics_identical"] and control["actual_results_identical"]
    assert control["real_conflict_handling"]["numerator"] == 2


def test_full_conflict_trace_preserves_contradiction_and_both_source_contexts():
    results = {r["case_id"]: r for r in rows(REPORT / "results.jsonl")}
    traces = rows(REPORT / "conflict_case_traces.jsonl")
    assert len(traces) == 3 and all(t["passed"] for t in traces)
    for trace in traces:
        row = results[trace["case_id"]]
        assert trace["final_relations"] == row["actual"]["relations"]
        assert trace["retrieved_evidence"] == row["trace"]["retrieved_evidence"]
        assert trace["validator"] == row["trace"]["filtering_scoring"][trace["relation_type"]]["decisions"]
        if trace["relation_type"] != "influence":
            continue
        aggregation = trace["conflict_aggregation"][0]
        assert {r["aggregation_role"] for r in aggregation["inputs"]} == {"support", "contradiction"}
        opposition = next(r for r in aggregation["inputs"] if r["aggregation_role"] == "contradiction")
        assert opposition["rejection_reasons"] == ["negated_influence"]
        assert aggregation["outputs"][0]["conflict_flags"] == ["attribution_conflict"]
        assert {r["claim_id"] for r in aggregation["inputs"]} == set(aggregation["outputs"][0]["evidence_claim_ids"])
        for decision in trace["validator"]:
            source = decision["candidate"]["evidence_source"]
            context = RelationSourceContext.model_validate(source["metadata"]["relation_source_context"])
            assert source["excerpt"] == context.anchor.exact_span
            assert context.source_content_sha256 == sha256(source["excerpt"].encode()).hexdigest()


def test_remaining_failures_are_original_unmodified_scope():
    failed = rows(REPORT / "real_failures.jsonl")
    original = {r["case_id"]: r for r in rows(BEFORE / "real_failures.jsonl")}
    assert len(failed) == 5
    assert Counter(r["relation_type"] for r in failed) == {"scene": 1, "influence": 4}
    for row in failed:
        assert row["actual"] == original[row["case_id"]]["actual"]
        assert row["failure_reasons"] == original[row["case_id"]]["failure_reasons"]
