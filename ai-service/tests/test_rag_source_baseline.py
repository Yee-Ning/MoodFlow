"""Deterministic baseline metrics and read-only boundary regression tests."""

import httpx
import pytest

from evals.rag_source_baseline import Projection, guard_request, preflight, score_case, summarize


def row(parent):
    return {"parent_id": parent}


def test_metrics_cutoff_dedup_and_original_child_rank():
    case = {"answerability": "full", "relevant_parent_ids": ["p1", "p2"]}
    rrf = [row("p1")] * 40 + [row("p2")]
    ranked = [row("x"), row("x"), row("p2"), row("p2")]
    result = score_case(case, rrf, [row("p1"), row("p2")], ranked)
    assert result["metrics"] == {"recall_at_40": 0.5, "recall_at_12": 0.5,
                                 "precision_at_12": 1 / 12, "mrr": 1 / 3,
                                 "first_hit_child_rank": 3, "unique_rerank_parent_count": 2}
    assert result["stage_missing"]["rrf_top40"] == ["p2"]


def test_rank_twelve_counts_and_rank_thirteen_does_not():
    case = {"answerability": "partial", "relevant_parent_ids": ["p"]}
    assert score_case(case, [], [], [row("x")] * 11 + [row("p")])["metrics"]["mrr"] == 1 / 12
    result = score_case(case, [], [], [row("x")] * 12 + [row("p")])
    assert all(result["metrics"][key] == 0 for key in ("mrr", "recall_at_12", "precision_at_12"))


@pytest.mark.parametrize("label", ["insufficient", "needs_input"])
def test_unscored_empty_gold_does_not_get_a_zero_score(label):
    result = score_case({"answerability": label, "relevant_parent_ids": []}, [row("p")], [], [row("p")])
    assert result["metrics"] is None
    assert result["stage_hits"]["rrf_top40"] == []


@pytest.mark.parametrize("parents", [[], ["p", "p"]])
def test_scored_invalid_gold_is_rejected(parents):
    with pytest.raises(ValueError, match="scored_gold_parents"):
        score_case({"answerability": "full", "relevant_parent_ids": parents}, [], [], [])


def test_macro_mean_uses_only_scored_cases():
    rows = [{"case_id": str(i), "question_group": "example", "answerability": label,
             "scored": label in {"full", "partial"}, "metrics": metrics}
            for i, (label, metrics) in enumerate([
                ("full", dict.fromkeys(("recall_at_40", "recall_at_12", "precision_at_12", "mrr"), 1.0)),
                ("partial", dict.fromkeys(("recall_at_40", "recall_at_12", "precision_at_12", "mrr"), 0.0)),
                ("needs_input", None)])]
    result = summarize(rows)
    assert result["all_scored"]["case_count"] == 2
    assert result["all_scored"]["mrr"] == 0.5
    assert result["by_answerability"]["full"]["mrr"] == 1
    assert result["by_question_group"]["example"]["recall_at_40"] == 0.5


@pytest.mark.parametrize("method,path", [("PUT", "/collections/x"), ("POST", "/collections/x/points"),
                                         ("DELETE", "/collections/x"), ("GET", "/collections/other")])
def test_qdrant_guard_rejects_writes_and_wrong_collection(method, path):
    with pytest.raises(ValueError, match="not_read_only"):
        guard_request(httpx.Request(method, "http://localhost:6333" + path), [])


def test_qdrant_guard_allows_only_local_metadata_and_query():
    from evals.rag_source_baseline import COLLECTION
    log = []
    guard_request(httpx.Request("GET", "http://localhost:6333/"), log)
    guard_request(httpx.Request("POST", f"http://localhost:6333/collections/{COLLECTION}/points/query/batch",
                               json={"searches": [{"using": "bm25", "limit": 80}]}), log)
    assert len(log) == 2 and log[1]["queries"][0]["limit"] == 80
    with pytest.raises(ValueError, match="not_read_only"):
        guard_request(httpx.Request("GET", "http://example.com:6333/"), log)
    assert len(log) == 2


def test_projection_mismatch_fails_instead_of_silently_scoring():
    projection = Projection([{"point_id": "point", "chunk_id": "child", "parent_id": "parent", "doc_id": "doc",
                              "payload": {"chunk_id": "child", "parent_id": "parent", "doc_id": "doc", "source_id": "s"}}])
    assert projection.item(child="child", parent="parent", point="point", relevant={"parent"})["hit"]
    with pytest.raises(ValueError, match="projection_mismatch"):
        projection.item(child="child", parent="other", point="point", relevant=set())


def test_frozen_inputs_and_actual_production_config_preflight():
    cases, projection, _, _, binding = preflight()
    assert len(cases) == 50 and len(projection.parents) == 413
    assert binding.manifest.projected_count == 691


def test_stage_diagnosis_separates_overfetch_selection_and_rerank():
    from evals.rag_source_baseline_report import stage_diagnosis
    record = {"relevant_parent_ids": ["a", "b", "c", "d"],
              "rrf_top40": [row("a"), row("c")],
              "selection_input_rrf": [row("a"), row("b"), row("c")],
              "selection": [row("a"), row("b")], "rerank_top12": [row("b")]}
    result = stage_diagnosis(record)
    assert result == {"rrf_top40_missed": ["b", "d"], "outside_rrf_selection_top80": ["d"],
                      "overfetch_recovered_beyond40": ["b"], "selection_dropped": ["c"],
                      "rerank_dropped": ["a"], "final_missed": ["a", "c", "d"]}


def test_replay_rejects_missing_cases():
    from evals.rag_source_baseline_report import validate_records
    with pytest.raises(ValueError, match="population_mismatch"):
        validate_records([{"case_id": "example"}], [], None)
