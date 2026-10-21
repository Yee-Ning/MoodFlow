import asyncio
from copy import deepcopy
from threading import RLock

import httpx
import pytest

from evals import rag_h2_metadata_v5 as run
from evals import rag_h2_metadata_report_v5 as report


@pytest.fixture(scope="module")
def inputs():
    return run.load_inputs()


@pytest.mark.parametrize("payload,expected", [
    ({}, True), ({"year": None}, True), ({"year": []}, True),
    ({"year": 2000}, True), ({"year": 2005}, True), ({"year": 1999}, False), ({"year": 2006}, False),
    ({"start_year": 1998, "end_year": 2000}, True), ({"start_year": 2005, "end_year": 2010}, True),
    ({"min_mentioned_year": 2003, "max_mentioned_year": 2008}, True),
    ({"min_mentioned_year": 2006, "max_mentioned_year": 2008}, False),
    ({"start_year": 2000}, False), ({"year": "2003"}, False),
])
def test_three_tiers_inclusive_boundaries_and_partial_metadata(payload, expected):
    _, condition = run.filter_parts([], [2000, 2005])
    assert run.matches(payload, run.dumped_filter(condition)) is expected


def test_unknown_year_requires_all_five_fields_empty():
    _, condition = run.filter_parts([], [2000, 2005])
    for field in run.YEAR_FIELDS:
        assert not run.matches({field: 2020}, run.dumped_filter(condition))
    assert run.matches({field: None for field in run.YEAR_FIELDS}, run.dumped_filter(condition))


def test_missing_explicit_year_omits_whole_year_group_and_entities_use_in():
    _, condition = run.filter_parts(["artist:jay_chou", "artist:jj_lin"], None)
    compiled = run.dumped_filter(condition)
    assert compiled == {"must": [{"key": "entity_ids", "match": {"any": ["artist:jay_chou", "artist:jj_lin"]}}]}
    assert run.matches({"entity_ids": ["artist:jj_lin"], "year": 2026}, compiled)
    assert not run.matches({"entity_ids": ["artist:other"]}, compiled)
    assert run.filter_parts([], None)[1] is None


def test_entity_and_year_are_conjoined_without_published_date_fallback():
    _, condition = run.filter_parts(["artist:jay_chou"], [2000, 2005])
    compiled = run.dumped_filter(condition)
    assert not run.matches({"entity_ids": ["artist:other"]}, compiled)
    assert run.matches({"entity_ids": ["artist:jay_chou"], "source_published_at": "2026-01-01"}, compiled)
    assert not run.matches({"entity_ids": ["artist:jay_chou"], "year": 2010, "source_published_at": "2003-01-01"}, compiled)


def test_all_frozen_plans_keep_only_metadata_and_identity_changes(inputs):
    queries, _, controls, plans, gate0, *_rest = inputs
    assert len(queries) == len(plans) == len(controls) == 25
    for before, constraints in zip(plans, gate0, strict=True):
        after, _condition = run.build_plan(before, constraints)
        payload = after.model_dump(mode="json")
        assert {key: value for key, value in before.items() if key not in {"plan_id", "metadata_filter"}} == {
            key: value for key, value in payload.items() if key not in {"plan_id", "metadata_filter"}}
        assert payload["query_types"] == ["exploratory"]
    row031 = next(row for row in gate0 if row["case_id"] == "rag-v0-031")
    assert row031["entity_ids"] == ["artist:jay_chou", "artist:jj_lin"]
    assert len(next(row for row in gate0 if row["case_id"] == "rag-v0-049")["entity_ids"]) == 5


@pytest.mark.parametrize("value,expected", [(0.6, "成立"), (0.599999, "部分成立"), (0.351001, "部分成立"), (0.351, "不成立"), (0.0, "不成立")])
def test_west_literal_preregistration_boundaries(value, expected):
    assert report.conclusion(value) == expected


def test_safety_valve_stops_before_extra_request_and_is_not_swallowed(inputs):
    from app.rag.retrieval.service import _needs_exploratory_entity_fallback

    *_, config, _rerank, _binding = inputs
    plan, _condition = run.build_plan(inputs[3][4], inputs[4][4])
    audit = run.RequestAudit()
    audit.active = "rag-v0-007"
    assert not run.intercept_fallback(plan, 12, config, _needs_exploratory_entity_fallback, audit)
    with pytest.raises(run.SafetyStop):
        try:
            run.intercept_fallback(plan, 11, config, _needs_exploratory_entity_fallback, audit)
        except Exception:
            pytest.fail("production Exception handling must not swallow the stop")
    assert audit.safety_events == [{"case_id": "rag-v0-007", "selection_count": 11, "threshold": 12,
                                    "plan_id": plan.plan_id, "unfiltered_request_sent": False,
                                    "event": "exploratory_entity_fallback_intercepted"}]
    assert not audit.requests


def test_http_boundary_rejects_writes_queries_outside_case_and_external_destinations():
    audit = run.RequestAudit()
    base = f"http://127.0.0.1:6333/collections/{run.COLLECTION}"
    audit.validate(httpx.Request("GET", base))
    audit.validate(httpx.Request("POST", base + "/points/scroll", json={"limit": 128, "with_payload": True, "with_vector": False}))
    for request in (httpx.Request("DELETE", base), httpx.Request("PUT", base + "/points/payload"),
                    httpx.Request("POST", base + "/points/query/batch", json={"searches": []}),
                    httpx.Request("GET", base.replace("127.0.0.1", "example.com"))):
        with pytest.raises(run.SafetyStop):
            audit.entry(request)
    assert len(audit.safety_events) == 4 and all(row["request_sent"] is False for row in audit.safety_events)


def mock_pipeline(monkeypatch, inputs, index, *, only_one=False):
    _, baseline = run.stack()
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    queries, cases, controls, plans, gate0, projection, config, _rerank, binding = inputs
    plan, compiled = run.build_plan(plans[index], gate0[index])
    condition = {"effective_qdrant_filter": run.dumped_filter(compiled), "intermediate_ast_requires_three_tier_compiler": gate0[index]["year_range"] is not None}
    hits, parents = [], set()
    for point, child in projection.points.items():
        payload = projection.children[child]
        if not run.matches(payload, condition["effective_qdrant_filter"]):
            continue
        if index == 0 and payload["parent_id"] in parents:
            continue
        parents.add(payload["parent_id"])
        hits.append(RawSearchHit(point, 1.0, payload))
        if only_one or len(hits) == 80:
            break
    observed = {"searches": 0, "rerank_queries": [], "client_api_keys": []}

    class Encoder:
        def encode_queries(self, texts):
            observed["embedding_texts"] = list(texts)
            return [[0.0] * 1024 for _text in texts]

    class Scorer:
        def score(self, items):
            observed["rerank_queries"].extend(item.query for item in items)
            return [1 - rank / len(items) for rank in range(len(items))]

    class Client:
        def __init__(self, **kwargs):
            observed["client_api_keys"].append(kwargs["api_key"])

        async def close(self):
            pass

    async def validate(_self, supplied):
        assert supplied == binding

    async def search(_self, supplied, legs, actual_filter, *, limit):
        observed["searches"] += 1
        assert supplied == binding and limit == 80 and len(legs) == 7
        assert run.dumped_filter(actual_filter) == condition["effective_qdrant_filter"]
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(baseline, "AsyncQdrantClient", Client)
    monkeypatch.setattr(baseline.rag_source, "_local_models", lambda: (Encoder(), RLock(), Scorer(), RLock()))
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "search", search)
    audit = run.RequestAudit()
    audit.active = cases[index]["case_id"]

    def collect():
        return run.collect_case({**cases[index], "control_legs": controls[index]["leg_results"]}, queries[index],
            plan.model_dump(mode="json"), condition, projection, config, binding, audit)

    return collect, observed, audit


def test_actual_production_path_keeps_seven_legs_original_query_and_parameters(inputs, monkeypatch):
    collect, observed, audit = mock_pipeline(monkeypatch, inputs, 0)
    row = collect()
    assert row["healthy"] and row["search_calls"] == row["rrf_calls"] == 1
    assert len(row["rrf_top40"]) == 40 and len(row["selection_input_rrf"]) == 80
    assert len(row["selection"]) == 40 and len(row["rerank_top12"]) == 12
    assert observed["embedding_texts"] == [*inputs[0][0]["queries"], inputs[0][0]["hyde"]]
    assert set(observed["rerank_queries"]) == {inputs[0][0]["completed_query"]}
    assert observed["client_api_keys"] == [None] and not audit.safety_events


def test_narrow_filter_does_not_pad_or_misclassify_valid_underfilled_rrf(inputs, monkeypatch):
    collect, observed, _audit = mock_pipeline(monkeypatch, inputs, 4)
    row = collect()
    assert row["healthy"] and len(row["rrf_top40"]) == 39 and len(row["selection_input_rrf"]) == 39
    assert len(row["selection"]) == 34 and len(row["rerank_top12"]) == 12
    assert observed["searches"] == 1


def test_production_fallback_attempt_stops_before_reranking_or_unfiltered_search(inputs, monkeypatch):
    collect, observed, audit = mock_pipeline(monkeypatch, inputs, 4, only_one=True)
    with pytest.raises(run.SafetyStop, match="exploratory_entity_fallback_intercepted"):
        collect()
    assert observed["searches"] == 1 and not observed["rerank_queries"]
    assert audit.safety_events[0]["selection_count"] == 1
    assert audit.safety_events[0]["unfiltered_request_sent"] is False


def test_036_three_unknown_gold_children_survive_and_2007_gold_child_is_rejected(inputs):
    special = next(row for row in inputs[4] if row["case_id"] == "rag-v0-036")
    children = special["h1_top40_excluded_gold_child_ids"]
    assert len(children) == 4
    _, compiled = run.filter_parts(special["entity_ids"], special["year_range"])
    expected = {"child-5c1c24c691f7435f0901ca5a": True, "child-1b9ce62950c959900980a186": True,
                "child-0052182d6a63bc52537a9511": True, "child-cd9d81188f03ccec556e1d37": False}
    assert set(children) == set(expected)
    for child in children:
        payload = inputs[5].children[child]
        assert run.matches(payload, run.dumped_filter(compiled)) is expected[child]
        assert run.tier(payload, special["year_range"]) == ("unknown_year_pass" if expected[child] else "known_year_outside_reject")
        if not expected[child]:
            assert payload["year"] == 2007


def test_http_boundary_checks_actual_seven_leg_filters_before_send():
    audit = run.RequestAudit()
    audit.active = "rag-v0-036"
    audit.expected_filter = {"must": [{"key": "entity_ids", "match": {"any": ["artist:jay_chou"]}}]}
    audit.expected_lexical = ["first", "second", "third"]
    searches = []
    for text in audit.expected_lexical:
        searches.extend([{"using": "dense", "query": [0.1], "params": {"hnsw_ef": 128},
                          "filter": audit.expected_filter, "limit": 80, "with_vector": False},
                         {"using": "bm25", "query": {"text": text}, "filter": audit.expected_filter, "limit": 80, "with_vector": False}])
    searches.append({"using": "dense", "query": [0.1], "params": {"hnsw_ef": 128}, "filter": audit.expected_filter, "limit": 80, "with_vector": False})
    path = f"http://127.0.0.1:6333/collections/{run.COLLECTION}/points/query/batch"
    audit.validate(httpx.Request("POST", path, json={"searches": searches}))
    searches[-1]["filter"] = None
    with pytest.raises(run.SafetyStop, match="filter_or_seven_leg_contract_drift"):
        audit.entry(httpx.Request("POST", path, json={"searches": searches}))
    assert audit.safety_events[-1]["request_sent"] is False


def test_installed_qdrant_client_nearest_wire_serialization_is_accepted_offline(inputs):
    from qdrant_client import AsyncQdrantClient, models
    from app.rag.retrieval.qdrant import QdrantHybridSearchBackend
    from evals.rag_h1_bundle_adapter import seven_legs

    audit = run.RequestAudit()
    audit.active = "rag-v0-001"
    audit.expected_filter = run.dumped_filter(run.filter_parts(inputs[4][0]["entity_ids"], None)[1])
    audit.expected_lexical = inputs[0][0]["queries"]
    plan, _compiled = run.build_plan(inputs[3][0], inputs[4][0])
    observed = []

    async def handler(request):
        audit.validate(request)
        observed.append(request.url.path)
        return httpx.Response(200, json={"result": [{"points": []} for _index in range(7)], "status": "ok", "time": 0}, request=request)

    async def exercise():
        client = AsyncQdrantClient(url="http://127.0.0.1:6333", api_key=None, check_compatibility=False,
                                  trust_env=False, transport=httpx.MockTransport(handler))
        backend = object.__new__(QdrantHybridSearchBackend)
        backend.config = inputs[6]
        backend.client = client
        legs = seven_legs(plan, {index: [0.1] * 1024 for index in range(4)}, inputs[6])
        requests = [backend._request(leg, models.Filter.model_validate(audit.expected_filter), 80) for leg in legs]
        try:
            result = await client.query_batch_points(collection_name=run.COLLECTION, requests=requests, timeout=5)
            assert len(result) == 7
        finally:
            await client.close()

    asyncio.run(exercise())
    assert observed == [f"/collections/{run.COLLECTION}/points/query/batch"]


def test_malformed_wire_data_fails_closed_without_individual_query_retry():
    audit = run.RequestAudit()
    audit.active = "rag-v0-001"
    request = httpx.Request("POST", f"http://127.0.0.1:6333/collections/{run.COLLECTION}/points/query/batch", json={"unexpected": []})
    with pytest.raises(run.SafetyStop, match="audit_validation_error"):
        audit.entry(request)
    assert audit.safety_events[0]["error_type"] == "KeyError"
    assert audit.safety_events[0]["request_sent"] is False


def test_losses_include_non_gold_all_legs_and_downstream_misses(inputs):
    before = deepcopy(inputs[2][0])
    after = deepcopy(before)
    removed = before["rrf_top40"][0]["parent_id"]
    for stage in run.STAGES:
        after[stage] = [row for row in after[stage] if row["parent_id"] != removed]
    after["effective_qdrant_filter"] = None
    changes = report.parent_changes(before, after)
    assert removed in changes["rrf_top40"]["lost_parents"]
    assert not changes["all_legs"]["lost_parents"]
    losses = report.loss_audit(before, after, changes, inputs[5].children)
    assert {row["parent_id"] for row in losses["rrf_top40"]} == set(changes["rrf_top40"]["lost_parents"])
    assert losses["rrf_top40"][0]["reason"] == "still_retrieved_but_absent_at_this_stage"
    assert losses["rrf_top40"][0]["eligible_corpus_child_ids"]
    assert report.verify_loss_coverage(changes, losses)
    losses["rrf_top40"] = []
    with pytest.raises(run.H2Error, match="lost_parent_omission"):
        report.verify_loss_coverage(changes, losses)


def test_loss_coverage_rejects_missing_stage_and_duplicate_parent():
    changes = {"all_legs": {"lost_parents": ["parent-other"], "lost_gold_parents": []}}
    with pytest.raises(run.H2Error, match="loss_stage_omission"):
        report.verify_loss_coverage(changes, {})
    rows = [{"parent_id": "parent-other", "is_gold": False}] * 2
    with pytest.raises(run.H2Error, match="lost_parent_omission_or_duplicate"):
        report.verify_loss_coverage(changes, {"all_legs": rows})


def test_existing_attempt_is_never_automatically_retried(inputs, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    monkeypatch.setattr(run, "load_prepared", lambda: inputs)
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(run.H2Error, match="already_attempted_no_automatic_repeat"):
        run.run(run.ReadOnlyGuard())
