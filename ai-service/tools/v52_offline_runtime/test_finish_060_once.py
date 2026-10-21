"""The final060 exception is single-use, exact and isolated from H2 defaults."""

from threading import RLock

import pytest

from tools.v52_offline_runtime import finish_060_once as run


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_exact_27_reuse_7_remaining_frozen_bindings(source):
    inputs, previous, queries, plans, filters, points = source
    assert (
        len(previous) == 27
        and len(inputs[0]) == len(queries) == len(plans) == len(filters) == 34
    )
    assert tuple(c["case_id"] for c in inputs[0][27:]) == run.REMAINING
    assert len(points) == 8 and plans[27]["plan_id"] == run.EXPECTED_PLAN_ID
    assert (
        next(r for r in previous if r["case_id"] == "rag-v0-055")["metrics"][
            "recall_at_40"
        ]
        == 0
    )


def test_060_consumption_is_durable_and_original_guard_still_stops_next_time(
    source, tmp_path
):
    from app.rag.query_understanding.models import SearchPlan

    once, audit = run.Once060(tmp_path), run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    plan, config = SearchPlan.model_validate(source[3][27]), source[0][4]
    original = run.base.h2.intercept_fallback
    with run.patch.object(run.base.h2, "intercept_fallback", once):
        assert (
            run.base.h2.intercept_fallback(plan, 8, config, lambda *args: True, audit)
            is False
        )
    assert run.base.h2.intercept_fallback is original
    assert (
        once.events[0]["unfiltered_request_sent"] is False and not audit.safety_events
    )
    for adapter in (once, run.Once060(tmp_path)):
        with pytest.raises(run.base.h2.SafetyStop, match="already_consumed"):
            adapter(plan, 8, config, lambda *args: True, audit)
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        original(plan, 8, config, lambda *args: True, audit)


@pytest.mark.parametrize(
    "case_id,index", [("rag-v0-055", 22), ("rag-v0-061", 28), ("rag-v0-066", 33)]
)
def test_other_cases_keep_original_H2_stop_rule(source, tmp_path, case_id, index):
    from app.rag.query_understanding.models import SearchPlan

    once, audit = run.Once060(tmp_path), run.base.h2.RequestAudit()
    audit.active = case_id
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        once(
            SearchPlan.model_validate(source[3][index]),
            8,
            source[0][4],
            lambda *args: True,
            audit,
        )
    assert not once.events and audit.safety_events[0]["case_id"] == case_id
    assert audit.safety_events[0]["unfiltered_request_sent"] is False


@pytest.mark.parametrize(
    "count,triggered,plan_id",
    [
        (7, True, run.EXPECTED_PLAN_ID),
        (8, False, run.EXPECTED_PLAN_ID),
        (8, True, "query-plan:000000000000000000000000"),
    ],
)
def test_wrong_060_scope_fails_closed(source, tmp_path, count, triggered, plan_id):
    from app.rag.query_understanding.models import SearchPlan

    once, audit = run.Once060(tmp_path), run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    plan = SearchPlan.model_validate({**source[3][27], "plan_id": plan_id})
    with pytest.raises(run.base.h2.SafetyStop, match="scope_drift"):
        once(plan, count, source[0][4], lambda *args: triggered, audit)
    assert not once.events and not (tmp_path / "060-exception-consumed.json").exists()


def test_real_H2_060_reranks_original_eight_without_backfill(
    source, tmp_path, monkeypatch
):
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    inputs, _, queries, plans, filters, points = source
    cases, _, _, projection, config, _, binding = inputs
    case, query, plan, condition = cases[27], queries[27], plans[27], filters[27]
    base = run.base.baseline
    hits = [
        RawSearchHit(p, 1.0, projection.children[projection.points[p]])
        for p in sorted(points)
    ]
    observed = {"searches": 0, "rerank_queries": []}

    class Encoder:
        def encode_queries(self, texts):
            assert list(texts) == [*query["queries"], query["hyde"]]
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, items):
            observed["rerank_queries"].extend(i.query for i in items)
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
        observed["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(base, "AsyncQdrantClient", Client)
    monkeypatch.setattr(
        base.rag_source,
        "_local_models",
        lambda: (Encoder(), RLock(), Scorer(), RLock()),
    )
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "search", search)
    once, audit = run.Once060(tmp_path), run.base.h2.RequestAudit()
    audit.active = run.EXCEPTION_CASE
    with run.patch.object(run.base.h2, "intercept_fallback", once):
        row = run.base.h2.collect_case(
            {**case, "control_legs": run.base.v51.expected_legs(query)},
            query,
            plan,
            condition,
            projection,
            config,
            binding,
            audit,
        )
    report = run.case_060_report(row, projection, points)
    assert (
        observed["searches"] == 1
        and observed["rerank_queries"] == [case["question"]] * 8
    )
    assert row["healthy"] and len(row["rerank_top12"]) == 8 and not audit.safety_events
    assert all(row["metrics"][key] == 0 for key in base.METRICS)
    assert len(report["data_bug"]["affected_children"]) == 2
    assert not report["manual_score_adjustment"] and not report["unfiltered_backfill"]


def test_combined_summary_requires_all_34_and_retains_requested_groups(source):
    cases = source[0][0]
    records = []
    for case in cases:
        row = {**case, "healthy": True, "rrf_top40": [], "rerank_top12": []}
        row["metrics"] = run.base.independently_score(case, row)
        records.append(row)
    complete = run.prior.summarize(cases, records)
    assert (
        complete["status"] == "complete" and complete["overall34"]["case_count"] == 34
    )
    assert complete["by_answerability"]["full"]["case_count"] == 24
    assert complete["by_answerability"]["partial"]["case_count"] == 10
    assert complete["timeline5"]["case_count"] == 5 and len(complete["new7"]) == 7
    assert run.prior.summarize(cases, records[:-1])["overall34"] is None
    with pytest.raises(run.base.BaselineError, match="duplicate"):
        run.prior.summarize(cases, [*records, records[0]])


def test_final_entry_refuses_repeat(source, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "load_source", lambda: source)
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    with pytest.raises(run.base.BaselineError, match="no_repeat"):
        run.run(run.base.h2.ReadOnlyGuard())
