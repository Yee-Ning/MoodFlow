"""H3 contracts: actual pipeline mocks, real client wire format, no network."""

import asyncio
from copy import deepcopy
from threading import RLock

import httpx
import pytest

from evals import rag_h3_traditional_v5 as run
from evals import rag_h3_traditional_report_v5 as report


@pytest.fixture(scope="module")
def inputs():
    return run.load_inputs()


def test_opencc_keywords_and_all_75_frozen_conversions(inputs):
    rows = run.conversions(inputs[0])
    assert len(rows) == 75 and len({(row["case_id"], row["subquery_index"]) for row in rows}) == 75
    assert run.converter().convert("周杰伦 东风破 专辑 音乐 风格 演变") == "周杰倫 東風破 專輯 音樂 風格 演變"
    assert run.converter().convert("魔杰座") == "魔傑座"
    assert all(row["original"].strip() and row["traditional"].strip() for row in rows)


def test_ten_legs_preserve_original_seven_dense_vectors_and_weight(inputs):
    from app.rag.query_understanding.models import SearchPlan

    h1, _ = run.stack()
    plan = SearchPlan.model_validate(inputs[3][0])
    vectors = {i: [float(i)] * 1024 for i in range(4)}
    expected = h1.seven_legs(plan, vectors, inputs[6])
    before = plan.model_dump(mode="json")
    actual = run.ten_legs(plan, vectors, inputs[6])
    assert actual[:7] == expected and len(actual) == 10
    assert [leg.variant_index for leg in actual[7:]] == [4, 5, 6]
    assert all(leg.weight == 1 and leg.channel.value == "lexical" and leg.dense_vector is None for leg in actual[7:])
    assert len({leg.retrieval_leg for leg in actual}) == 10
    assert [leg.text for leg in actual[7:]] == [run.converter().convert(v.text) for v in plan.queries[:3]]
    assert plan.model_dump(mode="json") == before


def wire_fixture(inputs):
    audit = run.RequestAudit()
    audit.active = inputs[1][0]["case_id"]
    audit.expected_filter = inputs[4][0]["effective_qdrant_filter"]
    config = inputs[6]
    texts = [*inputs[0][0]["queries"], *[run.converter().convert(q) for q in inputs[0][0]["queries"]]]
    audit.expected_documents = [{"text": text, "model": config.bm25.model_name, "options": config.bm25.query_options()} for text in texts]
    searches = []
    for text in texts[:3]:
        searches += [{"using": "dense", "query": [0.0], "params": {"hnsw_ef": 128},
                      "filter": audit.expected_filter, "limit": 80, "with_vector": False},
                     {"using": "bm25", "query": {"nearest": audit.expected_documents[len(searches)//2]},
                      "filter": audit.expected_filter, "limit": 80, "with_vector": False}]
    searches += [{"using": "dense", "query": [0.0], "params": {"hnsw_ef": 128},
                  "filter": audit.expected_filter, "limit": 80, "with_vector": False}]
    searches += [{"using": "bm25", "query": {"nearest": document}, "filter": audit.expected_filter,
                  "limit": 80, "with_vector": False} for document in audit.expected_documents[3:]]
    path = f"http://127.0.0.1:6333/collections/{run.h2.COLLECTION}/points/query/batch"
    return audit, searches, path


@pytest.mark.parametrize("mutation", ["nine_legs", "extra_dense", "wrong_text", "weight_options", "filter", "limit", "vector_read", "ef"])
def test_http_guard_rejects_contract_changes_before_send(inputs, mutation):
    audit, searches, path = wire_fixture(inputs)
    audit.validate(httpx.Request("POST", path, json={"searches": searches}))
    searches = deepcopy(searches)
    if mutation == "nine_legs":
        searches.pop()
    elif mutation == "extra_dense":
        searches[-1]["using"] = "dense"
    elif mutation == "wrong_text":
        searches[-1]["query"]["nearest"]["text"] = "unapproved query"
    elif mutation == "weight_options":
        searches[-1]["query"]["nearest"]["options"]["k"] = 9.0
    elif mutation == "filter":
        searches[-1]["filter"] = {"must": []}
    elif mutation == "limit":
        searches[-1]["limit"] = 40
    elif mutation == "vector_read":
        searches[-1]["with_vector"] = True
    elif mutation == "ef":
        searches[0]["params"]["hnsw_ef"] = 64
    with pytest.raises(run.SafetyStop):
        audit.entry(httpx.Request("POST", path, json={"searches": searches}))
    assert audit.safety_events[-1]["request_sent"] is False


def test_read_only_boundary_rejects_writes_external_hosts_and_unfiltered_fallback(inputs):
    audit, searches, path = wire_fixture(inputs)
    for request in [httpx.Request("PUT", path.replace("/query/batch", "/payload")),
                    httpx.Request("DELETE", path), httpx.Request("POST", path.replace("127.0.0.1", "example.com"), json={"searches": searches}),
                    httpx.Request("POST", path.replace("/query/batch", "/query"), json={})]:
        with pytest.raises(run.SafetyStop):
            audit.entry(request)


def test_real_qdrant_client_ten_leg_wire_is_accepted_offline(inputs):
    from qdrant_client import AsyncQdrantClient, models
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.retrieval.qdrant import QdrantHybridSearchBackend

    audit, _, path = wire_fixture(inputs)
    calls = []

    async def handler(request):
        audit.validate(request)
        calls.append(request.url.path)
        return httpx.Response(200, json={"result": [{"points": []} for _ in range(10)], "status": "ok", "time": 0}, request=request)

    async def exercise():
        client = AsyncQdrantClient(url="http://127.0.0.1:6333", api_key=None, check_compatibility=False,
                                  trust_env=False, transport=httpx.MockTransport(handler))
        backend = object.__new__(QdrantHybridSearchBackend)
        backend.client, backend.config = client, inputs[6]
        plan = SearchPlan.model_validate(inputs[3][0])
        legs = run.ten_legs(plan, {i: [0.0] * 1024 for i in range(4)}, inputs[6])
        try:
            results = await client.query_batch_points(collection_name=run.h2.COLLECTION,
                requests=[backend._request(leg, models.Filter.model_validate(audit.expected_filter), 80) for leg in legs], timeout=5)
            assert len(results) == 10
        finally:
            await client.close()

    asyncio.run(exercise())
    assert len(calls) == 1 and calls[0] == httpx.URL(path).path


def mock_pipeline(inputs, monkeypatch, index, *, only_one=False):
    _, baseline = run.stack()
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    queries, cases, controls, plans, filters, projection, config, _, binding = inputs
    observed = {"searches": 0, "rerank_queries": []}
    hits = []
    for point, child in projection.points.items():
        payload = projection.children[child]
        if run.matches(payload, filters[index]["effective_qdrant_filter"]):
            hits.append(RawSearchHit(point, 1.0, payload))
        if len(hits) == (1 if only_one else 80):
            break

    class Encoder:
        def encode_queries(self, texts):
            observed["embedded_texts"] = list(texts)
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, items):
            observed["rerank_queries"].extend(item.query for item in items)
            return [1 - i / len(items) for i in range(len(items))]

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] is None

        async def close(self):
            pass

    async def validate(_self, value):
        assert value == binding

    async def search(_self, value, legs, query_filter, *, limit):
        assert value == binding and limit == 80 and len(legs) == 10
        assert run.dumped_filter(query_filter) == filters[index]["effective_qdrant_filter"]
        observed["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(baseline, "AsyncQdrantClient", Client)
    monkeypatch.setattr(baseline.rag_source, "_local_models", lambda: (Encoder(), RLock(), Scorer(), RLock()))
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "search", search)
    extra = [{"text": run.converter().convert(text), "channel": "lexical"} for text in queries[index]["queries"]]
    audit = run.RequestAudit()
    audit.active = cases[index]["case_id"]

    def collect():
        return run.collect_case({**cases[index], "control_legs": [*controls[index]["leg_results"], *extra]},
            queries[index], plans[index], filters[index], projection, config, binding, audit)

    return collect, observed, audit


def test_actual_pipeline_embeds_four_original_texts_only_and_reranks_original_question(inputs, monkeypatch):
    collect, observed, audit = mock_pipeline(inputs, monkeypatch, 0)
    row = collect()
    assert row["healthy"] and row["search_calls"] == row["rrf_calls"] == 1
    assert len(row["leg_results"]) == 10 and len(row["rrf_top40"]) == 40
    assert len(row["selection_input_rrf"]) == 80 and len(row["rerank_top12"]) == 12
    assert observed["embedded_texts"] == [*inputs[0][0]["queries"], inputs[0][0]["hyde"]]
    assert set(observed["rerank_queries"]) == {inputs[0][0]["completed_query"]}
    assert observed["searches"] == 1 and not audit.safety_events


def test_sparse_candidates_are_not_padded(inputs, monkeypatch):
    collect, observed, _ = mock_pipeline(inputs, monkeypatch, 4)
    row = collect()
    assert row["healthy"] and len(row["rrf_top40"]) < 40 and observed["searches"] == 1


def test_existing_fallback_valve_stops_before_any_unfiltered_request(inputs, monkeypatch):
    collect, observed, audit = mock_pipeline(inputs, monkeypatch, 4, only_one=True)
    with pytest.raises(run.SafetyStop, match="exploratory_entity_fallback_intercepted"):
        collect()
    assert observed["searches"] == 1 and not observed["rerank_queries"]
    assert audit.safety_events[0]["unfiltered_request_sent"] is False


@pytest.mark.parametrize("value,expected", [(0.5, False), (0.50001, True), (0.0, False), (1.0, True)])
def test_preregistration_uses_strict_036_boundary_without_hiding_other_regressions(inputs, value, expected):
    before = inputs[2]
    after = deepcopy(before)
    next(row for row in after if row["case_id"] == "rag-v0-036")["metrics"]["recall_at_40"] = value
    after[0]["metrics"]["mrr"] = 0
    result = report.hypothesis(before, after)
    assert result["036_hypothesis_holds"] is expected
    assert not result["no_case_any_metric_decline"]
    assert any(row["case_id"] == before[0]["case_id"] and "mrr" in row["metrics"] for row in result["per_case_metric_regressions"])


def test_parent_change_lists_are_exhaustive_and_gold_separate(inputs):
    old = deepcopy(inputs[2][0])
    new = deepcopy(old)
    new["rrf_top40"] = []
    new["first_seven_vs_h2"] = run.compare_old_legs(old, new)
    result = report.comparison(old, new)
    assert result["parent_changes"]["rrf_top40"]["lost_parents"] == sorted({hit["parent_id"] for hit in old["rrf_top40"]})
    assert result["parent_changes"]["rrf_top40"]["lost_gold_parents"] == sorted({hit["parent_id"] for hit in old["rrf_top40"]} & set(old["relevant_parent_ids"]))


def test_target_parent_presence_distinguishes_original_seven_extra_legs_and_later_stages(inputs):
    row = deepcopy(inputs[2][0])
    for leg in row["leg_results"]:
        leg["hits"] = []
    hit = {"parent_id": run.TARGET, "point_id": "target", "child_id": "child-target", "rank": 1}
    row["leg_results"].append({"leg_id": "q4:decomposed:lexical", "text": "周杰倫", "weight": 1.0, "hits": [hit]})
    for stage in run.STAGES:
        row[stage] = []
    presence = report.target_presence(row, experiment=True)
    assert not presence["original_7_legs"]["present"] and presence["traditional_3_legs"]["present"]
    assert not presence["rrf_top40"]["present"] and not presence["rerank_top12"]["present"]
    assert presence["full_fusion_all_legs"][0]["fusion_rank"] == 1


def test_original_seven_order_and_scores_drift_are_not_silently_hidden(inputs):
    old = inputs[2][0]
    new = deepcopy(old)
    assert run.compare_old_legs(old, new)["identical"]
    new["leg_results"][0]["hits"][0]["raw_score"] += 0.01
    assert not run.compare_old_legs(old, new)["identical"]
    assert run.compare_old_legs(old, new)["legs"][0]["same_membership"]
