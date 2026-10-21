"""V5.1 population, unchanged H1 constraints and H2 execution contracts."""

import asyncio
from copy import deepcopy
import hashlib
import json
from threading import RLock

import httpx
import pytest

from evals import rag_v51_h2_baseline as run
from evals import rag_v51_h2_report as report


@pytest.fixture(scope="module")
def inputs():
    return run.load_inputs()


@pytest.fixture(scope="module")
def resolved(inputs):
    return asyncio.run(run.h1.resolve_inputs(inputs[0], inputs[-1]))


@pytest.fixture(scope="module")
def resolver(inputs):
    return run.h1.registry_components(inputs[-1])[1]


def test_exact_27_population_new_bindings_and_h2_config(inputs):
    cases, _, _, _, config, rerank, _ = inputs
    assert len(cases) == 27 and len({row["case_id"] for row in cases}) == 27
    assert run.Counter(row["answerability"] for row in cases) == {"full": 17, "partial": 10}
    assert not {"rag-v0-005", "rag-v0-031", "rag-v0-045"} & {row["case_id"] for row in cases}
    assert run.sha256(run.GOLD) == run.GOLD_SHA
    assert next(row for row in cases if row["case_id"] == "rag-v0-039")["relevant_parent_ids"] == [
        "parent-a03c56f88de76eda774a6e18", "parent-a9a1ce0be198f68e5eae8ffc"]
    assert config.model_dump(mode="json") == run.read_json(run.CONTROL / "pre_call_manifest.json")["retrieval_config"]
    assert rerank.top_k == 12 and config.fusion.rrf_k == 60


def test_24_common_frozen_inputs_revalidate_without_api(inputs, resolved, resolver):
    by_id = {row["case_id"]: row for row in resolved}
    old = {row["case_id"]: row for row in inputs[1]}
    records = run.read_json(run.OUTPUT / "reused_queries.json")
    assert len(records) == 24
    for record in records:
        after = run.revalidate(record, by_id[record["case_id"]], resolver)
        assert after["queries"] == old[record["case_id"]]["queries"]
        assert after["hyde"] == old[record["case_id"]]["hyde"]


def test_three_added_cases_resolve_without_new_catalog_or_question_rewrite(resolved):
    for row in resolved:
        assert not row["completion_changed"] and not row["completion_triggered"]
        assert row["question"] == row["completed_query"]
    assert not run.h1.FORBIDDEN_CATALOG_CALLS
    assert {row["case_id"] for row in resolved if row["case_id"] in run.NEW_IDS} == set(run.NEW_IDS)


@pytest.mark.parametrize("stage", ["expansion", "hyde"])
def test_provider_bodies_are_h1_verbatim_without_gold_labels_parents_or_history(resolved, stage):
    for row in resolved:
        if row["case_id"] not in run.NEW_IDS:
            continue
        dirty = {**row, "ground_truth": "DO_NOT_SEND_GOLD", "relevant_parent_ids": ["DO_NOT_SEND_PARENT"],
                 "contexts": ["DO_NOT_SEND_CONTEXT"], "history": "DO_NOT_SEND_HISTORY"}
        body = run.h1.request_body(dirty, stage)
        assert "DO_NOT_SEND" not in json.dumps(body)
        assert body == run.h1.request_body(row, stage)
        assert body["model"] == "deepseek-chat" and type(body["temperature"]) is int and body["temperature"] == 0


@pytest.mark.parametrize("stage", ["expansion", "hyde"])
def test_constraint_clarification_only_repeats_existing_restrictions(resolved, stage):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-009")
    body = run.generation_body({**row, "ground_truth": "DO_NOT_SEND_GOLD", "relevant_parent_ids": ["DO_NOT_SEND_PARENT"]}, stage, clarify=True)
    original = run.h1.request_body(row, stage)
    assert body["messages"][0] == original["messages"][0]
    assert body["messages"][1]["content"].endswith(run.CONSTRAINT_REMINDER) if stage == "hyde" else run.CONSTRAINT_REMINDER in body["messages"][1]["content"]
    assert row["completed_query"] in body["messages"][1]["content"]
    assert "DO_NOT_SEND" not in json.dumps(body)
    assert body["temperature"] == original["temperature"] == 0 and body["model"] == original["model"]


def test_source_reminder_echoes_only_source_already_in_original_question(resolved):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-054")
    body = run.generation_body(row, "expansion", clarify=True)
    content = json.loads(body["messages"][1]["content"])
    assert content["question"] == row["question"]
    assert "每一条子查询都必须逐字包含“索尼”" in content["constraint_reminder"]
    assert "索尼" in row["question"] and body["messages"][0] == run.h1.request_body(row, "expansion")["messages"][0]


def test_h1_constraints_reject_added_people_years_missing_titles_and_source(resolved, resolver):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-008")
    queries = ["歌曲《东风破》创作背景", "歌曲《东风破》创作缘起", "歌曲《东风破》音乐创作背景资料"]
    wrap = lambda texts: json.dumps({"queries": texts}, ensure_ascii=False)
    assert run.validate_stage(wrap(queries), row, "expansion", resolver)[0] == queries
    for changed in ([queries[0] + " 方文山", *queries[1:]], [queries[0] + "2003年", *queries[1:]], [q.replace("东风破", "青花瓷") for q in queries]):
        with pytest.raises(run.h1.H1Error):
            run.validate_stage(wrap(changed), row, "expansion", resolver)
    row054 = next(row for row in resolved if row["case_id"] == "rag-v0-054")
    q054 = ["索尼《魔杰座》歌曲《稻香》的曲风资料", "索尼介绍《魔杰座》歌曲《稻香》的主题", "索尼文案中《魔杰座》歌曲《稻香》的描述"]
    assert run.validate_stage(wrap(q054), row054, "expansion", resolver)[0] == q054
    with pytest.raises(run.BaselineError, match="source_restriction_lost"):
        run.validate_stage(wrap([q054[0].replace("索尼", ""), *q054[1:]]), row054, "expansion", resolver)


def test_hyde_stays_general_and_rejects_new_year_or_person(resolved, resolver):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-008")
    assert run.validate_stage("歌曲的创作背景常与叙事、文化意象和音乐表达有关。", row, "hyde", resolver)[0]
    for text in ("2003年的歌曲创作背景", "方文山参与歌曲的创作"):
        with pytest.raises(run.h1.H1Error):
            run.validate_stage(text, row, "hyde", resolver)


def test_recorder_preserves_all_attempts_without_overwrite_or_headers(tmp_path, resolved):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-008")
    recorder = run.Recorder(tmp_path / "calls")
    body = run.h1.request_body(row, "expansion")
    recorder.scope = (row, "expansion")
    for attempt in (1, 2, 3):
        recorder.attempt = attempt
        request = httpx.Request("POST", run.h1.BASE_URL + "/chat/completions", json=body,
                                headers={"Authorization": "Bearer FAKE_UNIT_TEST_CREDENTIAL"})
        asyncio.run(recorder.request(request))
        response = httpx.Response(200, json={"choices": [{"message": {"content": str(attempt)}}], "usage": {}}, request=request)
        asyncio.run(recorder.response(response))
    entries = [run.read_json(path) for path in sorted((tmp_path / "calls").glob("*.json"))]
    assert [entry["call_number"] for entry in entries] == [1, 2, 3]
    assert [entry["attempt"] for entry in entries] == [1, 2, 3]
    assert [entry["response"]["choices"][0]["message"]["content"] for entry in entries] == ["1", "2", "3"]
    assert "FAKE_UNIT_TEST_CREDENTIAL" not in json.dumps(entries)
    with pytest.raises(run.h2.SafetyStop):
        asyncio.run(recorder.request(request))


@pytest.mark.parametrize("mutation", ["destination", "method", "temperature", "scope", "attempt"])
def test_provider_guard_stops_before_sending_unapproved_requests(tmp_path, resolved, mutation):
    row = next(row for row in resolved if row["case_id"] == "rag-v0-008")
    recorder = run.Recorder(tmp_path / "calls")
    recorder.scope, recorder.attempt = (row, "expansion"), 1
    body, path, method = run.h1.request_body(row, "expansion"), run.h1.BASE_URL + "/chat/completions", "POST"
    if mutation == "destination": path = path.replace("api.deepseek.com", "example.com")
    if mutation == "method": method = "GET"
    if mutation == "temperature": body["temperature"] = 0.0
    if mutation == "scope": recorder.scope = ({**row, "case_id": "rag-v0-001"}, "expansion")
    if mutation == "attempt": recorder.attempt = 4
    with pytest.raises(run.h2.SafetyStop):
        asyncio.run(recorder.request(httpx.Request(method, path, json=body)))
    assert not recorder.entries and not list((tmp_path / "calls").glob("*.json"))


def test_actual_h2_path_keeps_seven_legs_original_query_filter_and_gold(inputs, resolved, monkeypatch):
    from evals.rag_h1_bundle_adapter import bundle_plan
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit
    _, baseline = run.h2.stack()
    cases, _, _, projection, config, _, binding = inputs
    case, row = cases[0], resolved[0]
    c = row["constraints"]
    plan, compiled = run.h2.build_plan(bundle_plan(row["completed_query"], ["周杰伦音乐生涯", "周杰伦作品", "周杰伦音乐历程"], "流行音乐的发展。", binding.profile).model_dump(mode="json"), {"entity_ids": c["entity_ids"], "year_range": None})
    query = {**row, "queries": [v.text for v in plan.queries[:3]], "hyde": plan.queries[3].text}
    condition = {"effective_qdrant_filter": run.h2.dumped_filter(compiled), "intermediate_ast_requires_three_tier_compiler": False}
    observed = {"searches": 0, "rerank_queries": []}
    hits = [RawSearchHit(point, 1.0, projection.children[child]) for point, child in projection.points.items()
            if run.h2.matches(projection.children[child], condition["effective_qdrant_filter"])][:80]

    class Encoder:
        def encode_queries(self, texts):
            observed["embedded_texts"] = list(texts)
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, items):
            observed["rerank_queries"].extend(item.query for item in items)
            return [1 - i / len(items) for i in range(len(items))]

    class Client:
        def __init__(self, **kwargs): assert kwargs["api_key"] is None
        async def close(self): pass

    async def validate(_self, supplied): assert supplied == binding
    async def search(_self, supplied, legs, query_filter, *, limit):
        assert supplied == binding and limit == 80 and len(legs) == 7
        assert run.h2.dumped_filter(query_filter) == condition["effective_qdrant_filter"]
        observed["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(baseline, "AsyncQdrantClient", Client)
    monkeypatch.setattr(baseline.rag_source, "_local_models", lambda: (Encoder(), RLock(), Scorer(), RLock()))
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "validate_binding", validate)
    monkeypatch.setattr(baseline.QdrantHybridSearchBackend, "search", search)
    audit = run.h2.RequestAudit()
    audit.active = case["case_id"]
    record = run.h2.collect_case({**case, "control_legs": run.expected_legs(query)}, query, plan.model_dump(mode="json"),
                                condition, projection, config, binding, audit)
    assert record["healthy"] and record["answerability"] == "partial"
    assert record["relevant_parent_ids"] == case["relevant_parent_ids"]
    assert len(record["leg_results"]) == 7 and observed["searches"] == 1
    assert observed["embedded_texts"] == [*query["queries"], query["hyde"]]
    assert set(observed["rerank_queries"]) == {case["question"]}


def test_step2_scores_use_v51_bindings_and_parent_denominators(inputs):
    _, baseline = run.h2.stack()
    case = next(row for row in inputs[0] if row["case_id"] == "rag-v0-039")
    hit = {"parent_id": case["relevant_parent_ids"][0]}
    score = baseline.score_case(case, [hit], [hit], [hit])["metrics"]
    assert score["recall_at_40"] == score["recall_at_12"] == 0.5
    assert score["precision_at_12"] == 1/12 and score["mrr"] == 1


def test_independent_report_metrics_cut_children_before_parent_dedup(inputs):
    case = next(row for row in inputs[0] if row["case_id"] == "rag-v0-039")
    first, second = case["relevant_parent_ids"]
    record = {"rrf_top40": [{"parent_id": first}] * 40 + [{"parent_id": second}],
              "rerank_top12": [{"parent_id": "irrelevant"}, {"parent_id": first}] + [{"parent_id": first}] * 10 + [{"parent_id": second}]}
    value = report.recompute(record, case)
    assert value == {"recall_at_40": 0.5, "recall_at_12": 0.5, "precision_at_12": 1/12, "mrr": 0.5}


def test_reference_comparison_keeps_old_gold_and_scores_separate(inputs):
    case = next(row for row in inputs[0] if row["case_id"] == "rag-v0-039")
    old = next(row for row in inputs[2] if row["case_id"] == "rag-v0-039")
    new = {**deepcopy(old), "relevant_parent_ids": case["relevant_parent_ids"], "answerability": case["answerability"]}
    value = report.reference_comparison([new], [case], inputs[2])[0]
    assert value["H2_v5_reference_only"]["metrics"]["recall_at_40"] == 0
    assert value["H2_v5_reference_only"]["gold_parents"] != value["v51_gold_parents"]
    assert value["saved_H2_trace_rescored_with_v51_gold"] == report.recompute(old, case)
    assert "reference only" in value["warning"] and "denominators changed" in value["warning"]
