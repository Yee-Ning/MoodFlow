"""The approved exception cannot silently become a new H2 rule."""

from copy import deepcopy
from threading import RLock

import pytest

from tools.v52_offline_runtime import resume_055_once as run


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_exact_22_reuse_12_remaining_and_original_eight(source):
    inputs, previous, queries, plans, filters, points = source
    assert (
        len(previous) == 22
        and len(inputs[0]) == len(queries) == len(plans) == len(filters) == 34
    )
    assert tuple(c["case_id"] for c in inputs[0][22:]) == run.REMAINING
    assert len(points) == 8
    assert {c["answerability"] for c in inputs[0]} == {"full", "partial"}


def test_055_consumes_once_persists_and_restores_H2(source, tmp_path):
    from app.rag.query_understanding.models import SearchPlan

    once = run.Once055(tmp_path)
    audit = run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    plan = SearchPlan.model_validate(source[3][22])
    config = source[0][4]
    original = run.base.h2.intercept_fallback
    with run.patch.object(run.base.h2, "intercept_fallback", once):
        assert (
            run.base.h2.intercept_fallback(plan, 8, config, lambda *args: True, audit)
            is False
        )
    assert run.base.h2.intercept_fallback is original
    assert once.events[0]["unfiltered_request_sent"] is False
    assert not audit.safety_events
    for adapter in (once, run.Once055(tmp_path)):
        with pytest.raises(run.base.h2.SafetyStop, match="already_consumed"):
            adapter(plan, 8, config, lambda *args: True, audit)


@pytest.mark.parametrize(
    "count,triggered,plan_id",
    [
        (7, True, run.EXPECTED_PLAN_ID),
        (8, False, run.EXPECTED_PLAN_ID),
        (8, True, "query-plan:000000000000000000000000"),
    ],
)
def test_055_wrong_scope_fails_closed(source, tmp_path, count, triggered, plan_id):
    from app.rag.query_understanding.models import SearchPlan

    once, audit = run.Once055(tmp_path), run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    plan = SearchPlan.model_validate({**source[3][22], "plan_id": plan_id})
    with pytest.raises(run.base.h2.SafetyStop, match="scope_drift"):
        once(plan, count, source[0][4], lambda *args: triggered, audit)
    assert not once.events and not (tmp_path / "055-exception-consumed.json").exists()


def test_other_case_still_raises_original_safety_stop(source, tmp_path):
    from app.rag.query_understanding.models import SearchPlan

    once, audit = run.Once055(tmp_path), run.base.h2.RequestAudit()
    audit.active = "rag-v0-060"
    plan = SearchPlan.model_validate(source[3][27])
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        once(plan, 8, source[0][4], lambda *args: True, audit)
    assert audit.safety_events[0]["case_id"] == "rag-v0-060"
    assert not audit.safety_events[0]["unfiltered_request_sent"] and not once.events


def test_real_H2_055_reranks_exact_eight_without_unfiltered_search(
    source, tmp_path, monkeypatch
):
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    inputs, _, queries, plans, filters, points = source
    cases, _, _, projection, config, _, binding = inputs
    case, query, plan, condition = cases[22], queries[22], plans[22], filters[22]
    base = run.base.baseline
    hits = [
        RawSearchHit(p, 1.0, projection.children[projection.points[p]])
        for p in sorted(points)
    ]
    calls = {"searches": 0, "rerank_inputs": []}

    class Encoder:
        def encode_queries(self, texts):
            assert list(texts) == [*query["queries"], query["hyde"]]
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, items):
            calls["rerank_inputs"].extend(items)
            return [1 - i / len(items) for i in range(len(items))]

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] is None

        async def close(self):
            pass

    async def validate(_self, supplied):
        assert supplied == binding

    async def search(_self, supplied, legs, query_filter, *, limit):
        assert supplied == binding and len(legs) == 7 and limit == 80
        assert (
            run.base.h2.dumped_filter(query_filter)
            == condition["effective_qdrant_filter"]
        )
        calls["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(base, "AsyncQdrantClient", Client)
    monkeypatch.setattr(
        base.rag_source,
        "_local_models",
        lambda: (Encoder(), RLock(), Scorer(), RLock()),
    )
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "search", search)
    once, audit = run.Once055(tmp_path), run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    original = run.base.h2.intercept_fallback
    with run.patch.object(run.base.h2, "intercept_fallback", once):
        record = run.base.h2.collect_case(
            {**case, "control_legs": run.base.v51.expected_legs(query)},
            query,
            plan,
            condition,
            projection,
            config,
            binding,
            audit,
        )
    assert run.base.h2.intercept_fallback is original
    assert calls["searches"] == 1 and len(calls["rerank_inputs"]) == 8
    assert {i.query for i in calls["rerank_inputs"]} == {case["question"]}
    report = run.case_055_report(record, projection, points)
    assert record["healthy"] and len(record["rerank_top12"]) == 8
    assert all(record["metrics"][key] == 0 for key in run.base.baseline.METRICS)
    assert len(report["data_bug"]["affected_children"]) == 2
    assert report["manual_score_adjustment"] is False and not audit.safety_events
    broken = deepcopy(record)
    broken["selection"] = broken["selection"][:-1]
    with pytest.raises(run.base.BaselineError, match="eight_rerank"):
        run.case_055_report(broken, projection, points)


def test_existing_resume_cannot_be_reused(source, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "load_source", lambda: source)
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    with pytest.raises(run.base.BaselineError, match="no_repeat"):
        run.run(run.base.h2.ReadOnlyGuard())
