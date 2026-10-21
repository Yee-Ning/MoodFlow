"""Exact repair overlay, 065 filter variant, and isolated one-use validation."""

from copy import deepcopy
from threading import RLock

import pytest

from tools.v52_offline_runtime import verify_payload_fix as run


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_exact_overlay_changes_only_four_entity_id_arrays(source):
    original = source[0][3]
    patched = run.overlay_projection(original)
    changed = {c for c in original.children if original.children[c] != patched.children[c]}
    assert changed == set(run.PATCH_POINTS)
    assert original.points == patched.points and original.parents == patched.parents
    for child in changed:
        expected = deepcopy(original.children[child])
        expected["entity_ids"].append("album:capricorn")
        assert patched.children[child] == expected
        assert "album:capricorn" not in original.children[child]["entity_ids"]
    points = [{"id": p, "payload": patched.children[c]} for p, c in patched.points.items()]
    assert run.base.verify_inventory(points, patched)
    points[0]["payload"] = {**points[0]["payload"], "unexpected": True}
    with pytest.raises(run.base.BaselineError, match="live_payload_projection_drift"):
        run.base.verify_inventory(points, patched)


def test_overlay_fails_closed_for_wrong_child_identity(source):
    original = deepcopy(source[0][3])
    original.points[next(iter(run.PATCH_POINTS.values()))] = "wrong-child"
    with pytest.raises(run.base.BaselineError, match="identity_drift"):
        run.overlay_projection(original)


def test_065_only_removes_entity_constraint_keeps_year_and_queries(source):
    _, bundles, _ = source
    filters = run.base.read_json(run.prior.SOURCE / "filters.json")
    plans = run.base.read_json(run.prior.SOURCE / "plans.json")
    queries = run.base.read_json(run.prior.SOURCE / "frozen_queries.json")
    original_f = next(f for f in filters if f["case_id"] == "rag-v0-065")
    i = next(i for i, q in enumerate(queries) if q["case_id"] == "rag-v0-065")
    _, query, plan, condition = bundles["rag-v0-065"]
    assert query == queries[i]
    assert condition["effective_qdrant_filter"] == {
        "must": original_f["effective_qdrant_filter"]["must"][1:]
    }
    restored = deepcopy(plan)
    restored["metadata_filter"] = plans[i]["metadata_filter"]
    assert restored == plans[i]
    projection = run.overlay_projection(source[0][3])
    gold = [p for p in projection.children.values()
            if p["parent_id"] == "parent-6438757cb83a23d11bc50025"]
    assert len(gold) == 3
    assert all(run.base.h2.matches(p, condition["effective_qdrant_filter"]) for p in gold)
    assert not any(run.base.h2.matches(p, original_f["effective_qdrant_filter"]) for p in gold)
    assert all("person:vincent_fang" not in p["entity_ids"] for p in gold)
    assert sum(run.base.h2.matches(p, condition["effective_qdrant_filter"])
               for p in projection.children.values()) == 901
    assert set(bundles) == set(run.RUN_IDS) and len(bundles) == 8


@pytest.mark.parametrize("case_id", run.EXCEPTIONS)
def test_ten_parent_exception_is_exact_durable_and_does_not_change_default(source, tmp_path, case_id):
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.retrieval.service import _needs_exploratory_entity_fallback

    config = source[0][4]
    plan = SearchPlan.model_validate(source[1][case_id][2])
    audit = run.base.h2.RequestAudit()
    audit.active = case_id
    once = run.FilteredRepairOnce(tmp_path)
    assert _needs_exploratory_entity_fallback(plan, 10, config)
    assert once(plan, 10, config, _needs_exploratory_entity_fallback, audit) is False
    for adapter in (once, run.FilteredRepairOnce(tmp_path)):
        with pytest.raises(run.base.h2.SafetyStop, match="already_consumed"):
            adapter(plan, 10, config, _needs_exploratory_entity_fallback, audit)
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        once.original(plan, 10, config, _needs_exploratory_entity_fallback, audit)
    assert once.events[0]["unfiltered_request_sent"] is False


@pytest.mark.parametrize("count", [8, 9, 11, 12])
def test_wrong_candidate_count_never_receives_exception(source, tmp_path, count):
    from app.rag.query_understanding.models import SearchPlan

    audit = run.base.h2.RequestAudit()
    audit.active = "rag-v0-055"
    once = run.FilteredRepairOnce(tmp_path)
    with pytest.raises(run.base.h2.SafetyStop, match="scope_drift"):
        once(SearchPlan.model_validate(source[1][audit.active][2]), count,
             source[0][4], lambda *args: True, audit)
    assert not once.events and not list(tmp_path.iterdir())


def test_exception_does_not_extend_to_other_cases(source, tmp_path):
    from app.rag.query_understanding.models import SearchPlan

    audit = run.base.h2.RequestAudit()
    audit.active = "rag-v0-061"
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        run.FilteredRepairOnce(tmp_path)(
            SearchPlan.model_validate(source[1][audit.active][2]), 10,
            source[0][4], lambda *args: True, audit,
        )
    assert not list(tmp_path.iterdir())


def test_real_collector_reranks_only_filtered_repaired_ten(source, tmp_path, monkeypatch):
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    inputs, bundles, _ = source
    _, _, _, original, config, _, binding = inputs
    projection = run.overlay_projection(original)
    case, query, plan, condition = bundles["rag-v0-055"]
    points = [p for p, c in projection.points.items()
              if run.base.h2.matches(projection.children[c], condition["effective_qdrant_filter"])]
    hits = [RawSearchHit(p, 1.0, projection.children[projection.points[p]]) for p in sorted(points)]
    assert len(hits) == 12 and len({h.payload["parent_id"] for h in hits}) == 10
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
        assert len(legs) == 7 and limit == 80 and supplied == binding
        assert run.base.h2.dumped_filter(query_filter) == condition["effective_qdrant_filter"]
        observed["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    base = run.base.baseline
    monkeypatch.setattr(base, "AsyncQdrantClient", Client)
    monkeypatch.setattr(base.rag_source, "_local_models", lambda: (Encoder(), RLock(), Scorer(), RLock()))
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(base.QdrantHybridSearchBackend, "search", search)
    audit = run.base.h2.RequestAudit()
    audit.active = case["case_id"]
    with run.patch.object(run.base.h2, "intercept_fallback", run.FilteredRepairOnce(tmp_path)):
        row = run.base.h2.collect_case(
            {**case, "control_legs": run.base.v51.expected_legs(query)}, query,
            plan, condition, projection, config, binding, audit,
        )
    assert row["healthy"] and observed["searches"] == 1
    assert observed["rerank_queries"] == [case["question"]] * 10
    assert row["metrics"]["recall_at_40"] == row["metrics"]["recall_at_12"] == 1
    assert not audit.safety_events


def test_run_refuses_repeat_before_loading_models(source, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "load_source", lambda: source)
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(run.base.BaselineError, match="already_attempted"):
        run.run(run.base.h2.ReadOnlyGuard())
