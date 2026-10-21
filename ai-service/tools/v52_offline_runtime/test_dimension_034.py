"""Batch scope, unchanged constraints, one call/case and scoped H2 exceptions."""

import asyncio
from copy import deepcopy
import json

import httpx
import pytest

from tools.v52_offline_runtime import dimension_034 as run


@pytest.fixture(scope="module")
def source():
    return run.load_source()


def test_exact_34_population_198_pins_and_only_system_prompt_changes(source):
    inputs, bundles, requests, pins = source
    assert len(bundles) == len(requests) == 34 and len(pins) == 198
    assert {c["answerability"] for c in inputs[0]} == {"full", "partial"}
    for cid, (_, old, _, _) in bundles.items():
        original = requests[cid]["request"]
        body = run.generation_body(requests[cid])
        restored = deepcopy(body)
        restored["messages"][0]["content"] = original["messages"][0]["content"]
        assert restored == original and body["messages"][0]["content"] == run.single.EXPANSION_PROMPT
        assert old["completed_query"] in body["messages"][1]["content"]
    condition = bundles["rag-v0-065"][3]
    assert condition["entity_ids"] == [] and condition["year_range"] == [2004, 2004]
    assert len(condition["effective_qdrant_filter"]["must"]) == 1


def test_recorder_allows_34_different_cases_never_second_call(source, tmp_path):
    recorder = run.Recorder(tmp_path / "calls", source[2])
    for cid, original in source[2].items():
        recorder.scope = cid
        req = httpx.Request("POST", run.h1.BASE_URL + "/chat/completions", json=run.generation_body(original))
        asyncio.run(recorder.request(req))
    assert len(recorder.entries) == 34
    with pytest.raises(run.h1.H1Error, match="duplicate"):
        asyncio.run(recorder.request(req))
    assert len(recorder.entries) == 34


@pytest.mark.parametrize("drift", ["model", "temperature", "max_tokens", "user", "endpoint", "case", "hyde"])
def test_generation_rejects_protocol_scope_or_non_system_drift(source, tmp_path, drift):
    requests = source[2]
    cid = "rag-v0-002"
    body = run.generation_body(requests[cid])
    url = run.h1.BASE_URL + "/chat/completions"
    if drift in {"model", "temperature", "max_tokens"}:
        body[drift] = {"model": "wrong", "temperature": 1, "max_tokens": 123}[drift]
    elif drift == "user":
        body["messages"][1]["content"] = "changed original constraints"
    elif drift == "endpoint":
        url = "https://example.com/v1/chat/completions"
    elif drift == "case":
        cid = "rag-v0-999"
    else:
        body["messages"][0]["content"] = run.h1.HYDE_PROMPT
    recorder = run.Recorder(tmp_path / "calls", requests)
    recorder.scope = cid
    with pytest.raises(run.h1.H1Error):
        asyncio.run(recorder.request(httpx.Request("POST", url, json=body)))
    assert not recorder.entries


def test_original_constraint_checker_rejects_year_and_external_entity(source):
    old = source[1]["rag-v0-002"][1]
    _, resolver = run.base.registry_components(source[0][-1])
    for extra in ("2004年", "王力宏", "《东风破》"):
        raw = json.dumps({"queries": ["周杰伦的音乐风格" + extra, "周杰伦的乐器使用", "周杰伦的代表作品"]}, ensure_ascii=False)
        with pytest.raises(run.h1.H1Error):
            run.base.validate_stage(raw, old, "expansion", resolver)


def test_semantic_gate_requires_all_34_and_hash_bound_entries(source, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    run.base.write_json(tmp_path / "generation-state.json", {"status": "generated_pending_semantic_review", "completed_cases": list(source[1])})
    run.base.write_json(tmp_path / "semantic-review.json", {"reviewer": "Codex", "all_passed": True, "cases": {}})
    with pytest.raises(run.base.BaselineError, match="semantic_gate"):
        run.reviewed_bundles(source[1], source[0][-1])


@pytest.fixture
def paired(source):
    result = {}
    for cid, (case, old, plan, condition) in source[1].items():
        new_plan = deepcopy(plan)
        new_plan["plan_id"] = "query-plan:" + ("a" if cid.endswith("055") else "b") * 24
        result[cid] = {"old": (case, old, plan, condition), "new": (case, old, new_plan, condition)}
    return result


def test_four_exact_once_exceptions_and_no_unfiltered_backfill(source, paired, tmp_path):
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.retrieval.service import _needs_exploratory_entity_fallback

    adapter, audit = run.BatchFilteredOnce(tmp_path / "exceptions", paired), run.base.h2.RequestAudit()
    for cid in run.repair.EXCEPTIONS:
        audit.active = cid
        for arm in run.ARMS:
            adapter.arm = arm
            plan = SearchPlan.model_validate(paired[cid][arm][2])
            assert adapter(plan, 10, source[0][4], _needs_exploratory_entity_fallback, audit) is False
            with pytest.raises(run.base.h2.SafetyStop, match="already_consumed"):
                adapter(plan, 10, source[0][4], _needs_exploratory_entity_fallback, audit)
    assert len(adapter.events) == 4 and all(not e["unfiltered_backfill_sent"] for e in adapter.events)


@pytest.mark.parametrize("count,arm", [(8, "old"), (11, "new"), (10, "invalid")])
def test_wrong_exception_scope_stops_before_rerank(source, paired, tmp_path, count, arm):
    from app.rag.query_understanding.models import SearchPlan

    adapter, audit = run.BatchFilteredOnce(tmp_path / "exceptions", paired), run.base.h2.RequestAudit()
    audit.active, adapter.arm = "rag-v0-055", arm
    plan = SearchPlan.model_validate(paired[audit.active]["old"][2])
    with pytest.raises(run.base.h2.SafetyStop, match="scope_drift"):
        adapter(plan, count, source[0][4], lambda *args: True, audit)
    assert not adapter.events


def test_other_case_never_gets_exception(source, paired, tmp_path):
    from app.rag.query_understanding.models import SearchPlan

    adapter, audit = run.BatchFilteredOnce(tmp_path / "exceptions", paired), run.base.h2.RequestAudit()
    audit.active, adapter.arm = "rag-v0-061", "new"
    with pytest.raises(run.base.h2.SafetyStop, match="fallback_intercepted"):
        adapter(SearchPlan.model_validate(paired[audit.active]["new"][2]), 10,
                source[0][4], lambda *args: True, audit)
    assert not adapter.events


def test_generate_and_run_refuse_repeat_before_api_or_models(source, tmp_path, monkeypatch):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    monkeypatch.setattr(run, "load_source", lambda: source)
    with pytest.raises(run.base.BaselineError, match="generation_already"):
        asyncio.run(run.generate())
    monkeypatch.setattr(run, "reviewed_bundles", lambda *args: {})
    (tmp_path / "run_manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(run.base.BaselineError, match="retrieval_already"):
        run.run(run.base.h2.ReadOnlyGuard())
