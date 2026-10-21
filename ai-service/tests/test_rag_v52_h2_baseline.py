"""Offline V5.2 input, frozen H2, provider and scoring contracts."""

import asyncio
from copy import deepcopy
import json
from threading import RLock

import httpx
import pytest

from evals import rag_v52_h2_baseline as run


@pytest.fixture(scope="module")
def inputs():
    return run.load_inputs()


@pytest.fixture(scope="module")
def resolved(inputs):
    with run.patch.object(run.h1, "registry_components", run.registry_components):
        return asyncio.run(
            run.h1.resolve_inputs(
                [
                    {k: c[k] for k in ("case_id", "question", "answerability")}
                    for c in inputs[0]
                    if c["case_id"] in run.NEW_IDS
                ],
                inputs[-1],
            )
        )


def test_protected_hashes_population_binding_and_parameter_lock(inputs):
    cases, old, records, projection, config, rerank, binding = inputs
    assert len(cases) == 34 and len(old) == len(records) == 27
    assert run.Counter(c["answerability"] for c in cases) == {"full": 24, "partial": 10}
    assert len(projection.children) == 1270 and len(projection.parents) == 689
    assert binding.manifest.collection_name == run.COLLECTION
    frozen = run.read_json(run.OLD / "freeze_manifest.json")
    assert config.model_dump(mode="json") == frozen["retrieval_config"]
    assert rerank.model_dump(mode="json") == frozen["rerank_config"]
    assert config.fusion.rrf_k == 60 and config.fusion.per_leg_top_k == 80
    assert config.fusion.overfetch_multiplier == 2
    assert config.selection.broad_document_cap == 4 and config.selection.source_cap == 5
    assert rerank.maximum_candidates == 40 and rerank.top_k == 12
    for name, digest in run.GOLD_SHAS.items():
        assert run.sha256(run.GOLD.parent / name) == digest


def test_gold_input_allowlist_removes_answers_and_evidence(tmp_path):
    dirty = {
        "case_id": "x",
        "question": "q",
        "answerability": "full",
        "relevant_parent_ids": ["p"],
        "ground_truth": "DO_NOT_PASS",
        "required_facts": ["DO_NOT_PASS"],
        "anchors": ["DO_NOT_PASS"],
        "contexts": ["DO_NOT_PASS"],
        "answer": "DO_NOT_PASS",
    }
    path = tmp_path / "gold.jsonl"
    path.write_text(json.dumps(dirty) + "\n", encoding="utf-8")
    actual = run.gold_rows(path)[0]
    assert set(actual) == {
        "case_id",
        "question",
        "answerability",
        "relevant_parent_ids",
    }
    assert "DO_NOT_PASS" not in json.dumps(actual)


def test_all_27_frozen_bundles_revalidate_without_generation(inputs):
    resolver = run.registry_components(inputs[-1])[1]
    assert len(inputs[1]) == 27
    for row in inputs[1]:
        assert (
            run.validate_stage(row["expansion_raw"], row, "expansion", resolver)[0]
            == row["queries"]
        )
        assert (
            run.validate_stage(row["hyde_raw"], row, "hyde", resolver)[0] == row["hyde"]
        )


def test_seven_resolve_without_catalog_or_context_completion(resolved):
    assert [r["case_id"] for r in resolved] == list(run.NEW_IDS)
    assert all(
        r["question"] == r["completed_query"] and not r["completion_triggered"]
        for r in resolved
    )
    assert not run.h1.FORBIDDEN_CATALOG_CALLS


@pytest.mark.parametrize("stage", ["expansion", "hyde"])
def test_generation_requests_contain_only_question_and_original_reminders(
    resolved, stage
):
    for row in resolved:
        body = run.generation_body(
            {
                **row,
                "ground_truth": "DO_NOT_PASS",
                "required_facts": ["DO_NOT_PASS"],
                "relevant_parent_ids": ["DO_NOT_PASS"],
            },
            stage,
        )
        assert "DO_NOT_PASS" not in json.dumps(body)
        assert (
            body["model"] == "deepseek-chat"
            and type(body["temperature"]) is int
            and body["temperature"] == 0
        )
        assert body["messages"][0]["content"] == (
            run.h1.EXPANSION_PROMPT if stage == "expansion" else run.h1.HYDE_PROMPT
        )
        assert row["question"] in body["messages"][1]["content"]
        cue = run.SOURCE_CUES.get(row["case_id"])
        if stage == "expansion" and cue:
            assert (
                cue in row["question"]
                and "每条子查询" in body["messages"][1]["content"]
            )
    with pytest.raises(run.BaselineError, match="scope"):
        run.generation_body({**resolved[0], "case_id": "rag-v0-001"}, stage)


def test_h1_constraints_reject_added_entities_years_and_lost_source(inputs, resolved):
    resolver = run.registry_components(inputs[-1])[1]
    row = resolved[0]

    def wrap(queries):
        return json.dumps({"queries": queries}, ensure_ascii=False)

    good = [
        "歌曲《七里香》的创作背景",
        "歌曲《七里香》的故事来源",
        "歌曲《七里香》的故事相关资料",
    ]
    assert run.validate_stage(wrap(good), row, "expansion", resolver)[0] == good
    for bad in (
        [good[0] + "2004年", *good[1:]],
        [good[0] + "方文山", *good[1:]],
        [q.replace("七里香", "青花瓷") for q in good],
    ):
        with pytest.raises(run.h1.H1Error):
            run.validate_stage(wrap(bad), row, "expansion", resolver)
    row063 = next(r for r in resolved if r["case_id"] == "rag-v0-063")
    queries = [
        "环球音乐日本 2022年《最伟大的作品》全球专辑销售排名 " + suffix
        for suffix in ("艺人简介", "排名描述", "销售排名资料")
    ]
    assert (
        run.validate_stage(wrap(queries), row063, "expansion", resolver)[0] == queries
    )
    with pytest.raises(run.BaselineError, match="source_restriction"):
        run.validate_stage(
            wrap([queries[0].replace("环球音乐日本", ""), *queries[1:]]),
            row063,
            "expansion",
            resolver,
        )


def test_recorder_scope_and_retry_budget_do_not_export_headers(tmp_path, resolved):
    recorder = run.Recorder(tmp_path / "calls")
    row = resolved[0]
    recorder.scope = (row, "expansion")
    recorder.attempt = 1
    request = httpx.Request(
        "POST",
        run.h1.BASE_URL + "/chat/completions",
        json=run.generation_body(row, "expansion"),
        headers={"Authorization": "Bearer UNIT_TEST_ONLY"},
    )
    asyncio.run(recorder.request(request))
    saved = run.read_json(tmp_path / "calls/001.json")
    assert "UNIT_TEST_ONLY" not in json.dumps(saved) and "headers" not in saved
    with pytest.raises(run.h2.SafetyStop, match="duplicate"):
        asyncio.run(recorder.request(request))
    recorder.attempt = 4
    with pytest.raises(run.h2.SafetyStop, match="scope"):
        asyncio.run(recorder.request(request))


@pytest.mark.parametrize(
    "method,path",
    [
        ("PUT", ""),
        ("DELETE", ""),
        ("POST", "/points"),
        ("GET", "/aliases"),
        ("GET", "/collections"),
        ("GET", "/collections/music_knowledge_v2_e40ab37ffcc7bcf0_v1_v2_v4"),
    ],
)
def test_target_audit_refuses_writes_and_other_collections(method, path):
    target = "/collections/" + run.COLLECTION
    path = path if path.startswith(("/collections", "/aliases")) else target + path
    with run.target_scope(), pytest.raises(run.h2.SafetyStop):
        run.h2.RequestAudit().validate(
            httpx.Request(method, "http://127.0.0.1:6333" + path, json={})
        )


def test_target_audit_accepts_metadata_reads_and_rejects_unfiltered_retry():
    audit = run.h2.RequestAudit()
    with run.target_scope():
        audit.validate(
            httpx.Request("GET", "http://127.0.0.1:6333/collections/" + run.COLLECTION)
        )
        audit.validate(
            httpx.Request(
                "POST",
                "http://127.0.0.1:6333/collections/"
                + run.COLLECTION
                + "/points/scroll",
                json={"limit": 128, "with_payload": True, "with_vector": False},
            )
        )
        audit.active = "rag-v0-006"
        audit.expected_filter = {
            "must": [{"key": "entity_ids", "match": {"any": ["song:qi_li_xiang"]}}]
        }
        audit.expected_lexical = ["q1", "q2", "q3"]
        with pytest.raises(run.h2.SafetyStop):
            audit.validate(
                httpx.Request(
                    "POST",
                    "http://127.0.0.1:6333/collections/"
                    + run.COLLECTION
                    + "/points/query/batch",
                    json={"searches": [{"limit": 80, "with_vector": False}] * 7},
                )
            )


def test_fallback_intercepts_before_unfiltered_search(inputs):
    audit = run.h2.RequestAudit()
    audit.active = "rag-v0-006"
    plan = run.baseline.rag_source.literal_search_plan("test", inputs[-1].profile, 40)
    with pytest.raises(run.h2.SafetyStop, match="fallback_intercepted"):
        run.h2.intercept_fallback(plan, 1, inputs[4], lambda *args: True, audit)
    assert audit.safety_events[0]["unfiltered_request_sent"] is False


def test_new_inventory_matches_all_payloads_and_rejects_missing_or_changed_point(
    inputs,
):
    projection = inputs[3]
    points = [
        {"id": p, "payload": projection.children[c]}
        for p, c in projection.points.items()
    ]
    digest = run.verify_inventory(points, projection)
    assert digest == run.verify_inventory(list(reversed(points)), projection)
    with pytest.raises(run.BaselineError):
        run.verify_inventory(points[:-1], projection)
    changed = deepcopy(points)
    changed[0]["payload"]["entity_ids"] = ["bad"]
    with pytest.raises(run.BaselineError):
        run.verify_inventory(changed, projection)


def test_independent_scores_cut_children_first_and_keep_denominator_12():
    case = {"relevant_parent_ids": ["a", "b"]}
    row = {
        "rrf_top40": [{"parent_id": "a"}] * 40 + [{"parent_id": "b"}],
        "rerank_top12": [{"parent_id": "x"}, {"parent_id": "a"}, {"parent_id": "a"}],
    }
    actual = run.independently_score(case, row)
    assert actual["recall_at_40"] == actual["recall_at_12"] == 0.5
    assert actual["precision_at_12"] == 1 / 12 and actual["mrr"] == 0.5
    assert actual["unique_rerank_parent_count"] == 2


def test_real_h2_collect_uses_only_frozen_legs_original_rerank_and_new_binding(
    inputs, monkeypatch
):
    from evals.rag_h1_bundle_adapter import bundle_plan
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit

    cases, old, _, projection, config, _, binding = inputs
    case, row = cases[0], old[0]
    c = row["constraints"]
    plan, compiled = run.h2.build_plan(
        bundle_plan(
            row["completed_query"], row["queries"], row["hyde"], binding.profile
        ).model_dump(mode="json"),
        {"entity_ids": c["entity_ids"], "year_range": None},
    )
    condition = {
        "effective_qdrant_filter": run.h2.dumped_filter(compiled),
        "intermediate_ast_requires_three_tier_compiler": False,
    }
    hits = [
        RawSearchHit(p, 1.0, projection.children[ch])
        for p, ch in projection.points.items()
        if run.h2.matches(projection.children[ch], condition["effective_qdrant_filter"])
    ][:80]
    observed = {"rerank_queries": [], "searches": 0}

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

    async def validate(_self, supplied):
        assert supplied == binding

    async def search(_self, supplied, legs, query_filter, *, limit):
        assert supplied == binding and limit == 80 and len(legs) == 7
        assert (
            run.h2.dumped_filter(query_filter) == condition["effective_qdrant_filter"]
        )
        observed["searches"] += 1
        return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    monkeypatch.setattr(run.baseline, "AsyncQdrantClient", Client)
    monkeypatch.setattr(
        run.baseline.rag_source,
        "_local_models",
        lambda: (Encoder(), RLock(), Scorer(), RLock()),
    )
    monkeypatch.setattr(
        run.baseline.QdrantHybridSearchBackend, "validate_binding", validate
    )
    monkeypatch.setattr(run.baseline.QdrantHybridSearchBackend, "search", search)
    audit = run.h2.RequestAudit()
    audit.active = case["case_id"]
    actual = run.h2.collect_case(
        {**case, "control_legs": run.v51.expected_legs(row)},
        row,
        plan.model_dump(mode="json"),
        condition,
        projection,
        config,
        binding,
        audit,
    )
    assert actual["healthy"] and observed["searches"] == 1
    assert observed["embedded_texts"] == [*row["queries"], row["hyde"]]
    assert set(observed["rerank_queries"]) == {case["question"]}
    assert actual["relevant_parent_ids"] == case["relevant_parent_ids"]


def test_existing_attempt_cannot_be_overwritten(inputs, monkeypatch, tmp_path):
    monkeypatch.setattr(run, "OUTPUT", tmp_path)
    monkeypatch.setattr(run, "load_inputs", lambda: inputs)
    with pytest.raises(run.BaselineError, match="no_overwrite"):
        run.prepare()
    (tmp_path / "retrieval").mkdir()
    monkeypatch.setattr(run, "load_frozen", lambda: inputs)
    monkeypatch.setattr(run, "read_json", lambda path: [])
    with pytest.raises(run.BaselineError, match="no_repeat"):
        run.run(run.h2.ReadOnlyGuard())


def test_reuse_only_validated_stages_bound_to_actual_provider_records(
    inputs, resolved, monkeypatch
):
    monkeypatch.setattr(
        run,
        "SEED_ATTEMPT",
        run.ROOT / "workspace/moodflow-review/v5-2-h2-baseline-20261019/run01",
    )
    resolver = run.registry_components(inputs[-1])[1]
    seeds, hashes, calls = run.load_seeds(resolved, resolver)
    assert calls == 9
    assert set(seeds) == {"rag-v0-006", "rag-v0-061", "rag-v0-062"}
    assert sum(len(stages) for stages in seeds.values()) == 6
    assert hashes and "rag-v0-063" not in seeds
    changed = deepcopy(resolved)
    changed[0]["question"] += "changed"
    with pytest.raises(run.BaselineError, match="seed_question_drift"):
        run.load_seeds(changed, resolver)


def test_explicit_reminder_repeats_only_original_year_and_all_questions(
    resolved, monkeypatch
):
    monkeypatch.setattr(run, "EXPLICIT_RESTRICTIONS", True)
    row = next(r for r in resolved if r["case_id"] == "rag-v0-063")
    body = run.generation_body(row, "expansion")
    content = json.loads(body["messages"][1]["content"])
    assert (
        "每一条子查询都必须逐字包含原题的全部年份：2022"
        in content["constraint_reminder"]
    )
    assert "2022" in row["question"] and "全部问法" in content["constraint_reminder"]
    assert body["messages"][0]["content"] == run.h1.EXPANSION_PROMPT


def test_recorder_enforces_total_remaining_budget_before_sending(tmp_path, resolved):
    recorder = run.Recorder(tmp_path / "calls")
    recorder.max_calls = 0
    recorder.scope, recorder.attempt = (resolved[0], "expansion"), 1
    with pytest.raises(run.h2.SafetyStop, match="total_generation_budget"):
        asyncio.run(
            recorder.request(
                httpx.Request(
                    "POST",
                    run.h1.BASE_URL + "/chat/completions",
                    json=run.generation_body(resolved[0], "expansion"),
                )
            )
        )
    assert not recorder.entries
