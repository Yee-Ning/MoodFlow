"""Frozen-population, production-path, readonly and attribution regressions."""

from copy import deepcopy
import json
from threading import RLock

import pytest

from evals import rag_h1_retrieval_v5 as run
from evals import rag_source_baseline as baseline
from evals.ragas_gate0 import Gate0ForbiddenOperation
from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit


@pytest.fixture(scope="module")
def inputs():
    return run.load_inputs()


@pytest.fixture
def prepared(tmp_path, monkeypatch, inputs):
    monkeypatch.setattr(run, "OUTPUT", tmp_path / "retrieval")
    monkeypatch.setattr(run, "load_inputs", lambda: inputs)
    run.prepare()
    return run.load_prepared()


def test_frozen_population_preparation_and_unchanged_protocol(prepared):
    queries, cases, control, _, config, rerank, _ = prepared
    assert len(queries) == len(cases) == len(control) == 25
    assert run.original.sha256(run.OUTPUT / "inputs.json") == run.FROZEN_SHA
    manifest = run.original.read_json(run.OUTPUT / "pre_call_manifest.json")
    assert manifest["judge_calls"] == manifest["new_deepseek_calls"] == 0
    assert not manifest["secret_reads_allowed"] and not manifest["baseline_rerun"]
    assert manifest["protocol"]["doc_cap"] == 4 and manifest["protocol"]["selection_input_max"] == 80
    assert config.fusion.maximum_legs == 6 and rerank.top_k == 12
    assert [p["standalone_query"] for p in run.original.read_json(run.OUTPUT / "plans.json")] == [q["completed_query"] for q in queries]


@pytest.mark.parametrize("event,args", [
    ("socket.connect", (None, ("8.8.8.8", 6333))),
    ("socket.connect", (None, ("127.0.0.1", 443))),
    ("socket.getaddrinfo", ("api.deepseek.com", 443)),
    ("open", (run.original.AI_ROOT / ".env",)),
    ("subprocess.Popen", ("unused",)),
])
def test_readonly_process_guard_blocks_external_network_secrets_and_subprocess(event, args):
    guard = run.ReadOnlyGuard()
    with pytest.raises(Gate0ForbiddenOperation):
        guard.audit(event, args)


def test_readonly_process_guard_allows_only_qdrant_loopback():
    guard = run.ReadOnlyGuard()
    guard.audit("socket.connect", (None, ("127.0.0.1", 6333)))
    guard.audit("socket.getaddrinfo", ("localhost", 6333))
    assert not guard.denied


def test_formal_source_observation_preserves_seven_legs_single_rrf_80_then_40_then_12(inputs, monkeypatch):
    queries, cases, _, projection, config, _, binding = inputs
    observed = {}
    hits, parents = [], set()
    for point, child in projection.points.items():
        payload = projection.children[child]
        if payload["parent_id"] not in parents:
            parents.add(payload["parent_id"])
            hits.append(RawSearchHit(point, 1.0, payload))
        if len(hits) == 80:
            break

    class Encoder:
        def encode_queries(self, texts):
            observed["texts"] = list(texts)
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, items):
            observed["rerank_queries"] = [i.query for i in items]
            return [1 - i / len(items) for i in range(len(items))]

    class Client:
        def __init__(self, **_kwargs):
            pass

        async def close(self):
            pass

    async def validate(_self, _binding):
        assert _binding == binding

    async def search(_self, _binding, legs, query_filter, *, limit):
        assert query_filter is None and limit == 80
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(baseline.rag_source, "_local_models", lambda: (Encoder(), RLock(), Scorer(), RLock()))
    monkeypatch.setattr(baseline, "AsyncQdrantClient", Client)
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "search", search)
    row = run.collect_case(cases[0], queries[0], projection, binding, config, [])
    assert row["healthy"] and row["search_calls"] == row["rrf_calls"] == 1
    assert len(row["selection_input_rrf"]) == 80 and len(row["rerank_input"]) == 40 and len(row["rerank_top12"]) == 12
    assert len(row["leg_results"]) == 7 and all(leg["hit_count"] == 80 for leg in row["leg_results"])
    assert all(leg["independent_leg_latency_ms"] is None for leg in row["leg_results"])
    assert observed["texts"] == [*queries[0]["queries"], queries[0]["hyde"]]
    assert set(observed["rerank_queries"]) == {queries[0]["completed_query"]}
    assert len({h["parent_id"] for h in row["selection"]}) == 40


def test_live_text_drift_is_rejected_before_attribution(inputs):
    _, _, _, projection, config, _, binding = inputs
    plan = run.bundle_plan("q", ["a", "b", "c"], "hyde", binding.profile)
    leg = run.seven_legs(plan, {i: [0.0] for i in range(4)}, config)[0]
    point, child = next(iter(projection.points.items()))
    payload = {**projection.children[child], "content": "changed after freeze"}
    with pytest.raises(run.original.H1Error, match="live_child_text_drift"):
        run.serialize_legs([LegSearchResult(leg=leg, hits=[RawSearchHit(point, 1.0, payload)])],
                           1.0, projection=projection, relevant=set(), limit=80)


def example(parent):
    rows = [{"parent_id": parent, "child_id": "child:" + parent}]
    row = {"case_id": "x", "question": "q", "question_group": "timeline", "answerability": "full",
           "relevant_parent_ids": ["gold"], "rrf_top40": rows, "selection_input_rrf": rows,
           "selection": rows, "rerank_top12": rows}
    row.update(baseline.score_case(row, rows, rows, rows))
    return row


def test_parent_gain_is_attributed_to_actual_legs_without_causal_ablation_claim():
    before, after = example("other"), example("gold")
    after["leg_results"] = [{"leg_id": "q3:semantic:dense", "text": "hyde", "channel": "dense", "variant_index": 3,
                             "hits": [{"parent_id": "gold", "child_id": "child:gold", "rank": 3}]}]
    result = run.compare_case(after, before)
    assert result["delta"]["recall_at_40"] == 1
    assert result["parent_changes"]["rrf_top40"]["gained_relevant_parents"] == ["gold"]
    gain = result["gained_gold_parent_attribution"][0]
    assert gain["retrieving_legs"][0]["variant_index"] == 3
    assert gain["retrieving_legs"][0]["rrf_top40_contributing_children"][0]["rank"] == 3
    assert "no marginal" in gain["causal_claim"]


@pytest.mark.parametrize("timeline,overview,expected", [(0.59, 0.33, "不成立"), (0.6, 1 / 3, "部分成立"), (0.6, 0.34, "成立")])
def test_preregistered_joint_hypothesis_is_evaluated_exactly(inputs, timeline, overview, expected):
    controls = inputs[2]
    rows = deepcopy(controls)
    for row in rows:
        if row["question_group"] == "timeline":
            row["metrics"]["recall_at_40"] = timeline
        if row["question_group"] == "artist_overview":
            row["metrics"]["recall_at_40"] = overview
    assert run.summarize_comparison(rows, controls)["hypothesis"]["conclusion"] == expected


def test_repeated_run_and_input_drift_block_before_model_execution(prepared, monkeypatch):
    run.original.write_json(run.OUTPUT / "run_manifest.json", {})

    def forbidden():
        raise AssertionError("must not load models or access Qdrant")
    monkeypatch.setattr(baseline.rag_source, "_local_models", forbidden)
    with pytest.raises(run.original.H1Error, match="already_attempted"):
        run.run(run.ReadOnlyGuard())
    (run.OUTPUT / "inputs.json").write_text(json.dumps([]), encoding="utf-8")
    with pytest.raises(run.original.H1Error, match="pre_call_drift"):
        run.load_prepared()
