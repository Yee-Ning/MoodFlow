"""H1 preparation and seven-leg contract checks; never call live services."""

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from evals import rag_h1_bundle_v5 as h1
from evals.rag_h1_bundle_adapter import bundle_plan, seven_legs
from evals.rag_source_baseline import preflight
from app.rag.query_understanding.models import SearchChannel
from app.rag.retrieval.legs import build_retrieval_legs
from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf


def example(query="周杰伦2003年的音乐风格", *, titles=False):
    if titles:
        query = "周杰伦《晴天》的音乐风格"
    return {"case_id": "rag-v0-001", "completed_query": query,
            "entities": [{"surface": "周杰伦", "canonical_name": "周杰伦", "aliases": ["周杰倫"],
                          "status": "resolved", "knowledge_entity_id": "artist:jay_chou"}],
            "constraints": {"negative_terms": []}, "ground_truth": "MUST_NOT_SEND",
            "contexts": ["MUST_NOT_SEND"], "question_group": "MUST_NOT_SEND"}


def test_prompts_match_approved_gate0_and_hyde_verbatim():
    h1.verify_prompt()
    assert h1.HYDE_PROMPT == (
        "你是一名音乐知识助手。根据下面的问题，写一段与之相关的音乐知识文本。\n"
        "描述相关的音乐背景、风格、时期等通用知识；不要编造具体的作品名、年份、\n"
        "人名、奖项等事实细节；不要直接回答问题；只输出这段文本，不要解释。")


def test_fixed_population_uses_all_30_full_partial_and_no_gold_in_model_body():
    cases, _, _, _, _ = preflight()
    assert tuple(c["case_id"] for c in cases if c["answerability"] in {"full", "partial"}) == h1.CASE_IDS
    row = example()
    for stage in ("expansion", "hyde"):
        body = h1.request_body(row, stage)
        assert "MUST_NOT_SEND" not in json.dumps(body)
        assert body["temperature"] == 0 and type(body["temperature"]) is int
        assert body["model"] == "deepseek-chat"
    assert h1.request_body(row, "hyde")["messages"][1]["content"] == row["completed_query"]


def test_all_30_context_entity_resolution_is_offline_and_observation_only():
    cases, _, _, _, binding = preflight()
    rows = asyncio.run(h1.resolve_inputs([c for c in cases if c["answerability"] in {"full", "partial"}], binding))
    assert len(rows) == 30 and not h1.FORBIDDEN_CATALOG_CALLS
    assert all(not r["completion_changed"] and not r["completion_triggered"] for r in rows)
    assert all(r["context"]["dependency"] == "standalone" for r in rows)
    assert any(r["entities"] for r in rows)
    assert all(r["entity_effect"] == "recorded_only_no_query_rewrite_or_metadata_filter" for r in rows)


def test_context_missing_reference_does_not_guess_song_or_call_catalog():
    _, _, _, _, binding = preflight()
    with pytest.raises(h1.H1Error, match="context_needs_input"):
        asyncio.run(h1.resolve_inputs([{"case_id": "example", "question": "这首歌的背景",
                                       "answerability": "full"}], binding))
    assert not h1.FORBIDDEN_CATALOG_CALLS


def test_expansion_validates_normalized_literals_and_checks_years_entities():
    raw = json.dumps({"queries": [" 周杰伦2003年的风格 ", "周杰伦2003年的曲风", "周杰倫2003年的音乐风格"]})
    queries, checks = h1.validate_expansion(raw, example())
    assert queries[0] == "周杰伦2003年的风格" and len(checks) == 3


@pytest.mark.parametrize("payload", [
    {"queries": ["a", "b"]}, {"queries": ["a", "b", "c"], "note": "no"},
    {"queries": [1, "b", "c"]}, {"queries": ["", "b", "c"]},
    {"queries": ["a", " A ", "c"]},
])
def test_invalid_schema_empty_or_duplicate_queries_stop(payload):
    with pytest.raises(h1.H1Error):
        h1.validate_expansion(json.dumps(payload), example())


@pytest.mark.parametrize("text,code", [
    ("周杰伦的音乐风格", "year_constraint"),
    ("周杰伦2004年的音乐风格", "year_constraint"),
    ("周杰伦2003至2004年的音乐风格", "year_constraint"),
    ("2003年的音乐风格", "entity_constraint"),
])
def test_explicit_constraint_drift_is_not_silently_accepted(text, code):
    with pytest.raises(h1.H1Error, match=code):
        h1.validate_text_constraints(text, example())


def test_title_negative_language_and_new_entity_constraints():
    with pytest.raises(h1.H1Error, match="title_constraint"):
        h1.validate_text_constraints("周杰伦的音乐风格", example(titles=True))
    row = example("周杰伦的音乐风格，不包括演唱会，仅限英文资料")
    row["constraints"]["negative_terms"] = ["演唱会"]
    with pytest.raises(h1.H1Error, match="negative_constraint"):
        h1.validate_text_constraints("周杰伦的音乐风格英文资料", row)
    with pytest.raises(h1.H1Error, match="document_constraint"):
        h1.validate_text_constraints("周杰伦的音乐风格，不包括演唱会", row)
    resolver = SimpleNamespace(resolve=lambda *_a, **_k: [SimpleNamespace(
        knowledge_entity_id="person:added", entity_type=SimpleNamespace(value="person"))])
    with pytest.raises(h1.H1Error, match="added_registry_entity"):
        h1.validate_text_constraints("周杰伦2003年的音乐风格", example(), entity_resolver=resolver)


def test_hyde_allows_general_paragraph_without_requiring_exact_question_year():
    text, checks = h1.validate_hyde(" 流行音乐的曲风融合常体现在节奏与旋律等方面。 ", example())
    assert text.startswith("流行音乐") and checks["checks"] == "passed"
    with pytest.raises(h1.H1Error, match="year_constraint"):
        h1.validate_hyde("这一风格源自2004年的变化。", example())
    with pytest.raises(h1.H1Error, match="title_constraint"):
        h1.validate_hyde("常见作品包括《不存在的歌曲》。", example())
    for bad in ("", "x" * 1001, "```text\n段落\n```"):
        with pytest.raises(h1.H1Error, match="hyde_blank_long_or_fenced"):
            h1.validate_hyde(bad, example())


def test_seven_leg_adapter_preserves_config_and_original_rerank_query():
    _, _, config, _, binding = preflight()
    before = config.model_dump()
    plan = bundle_plan("原问题", ["查询一", "查询二", "查询三"], "假设性通用知识段落", binding.profile)
    assert plan.standalone_query == "原问题" and plan.final_top_k == 12 and plan.candidate_top_k == 40
    assert plan.metadata_filter.root is None and plan.post_exclusions == []
    assert [q.value for q in plan.query_types] == ["exploratory"]
    vectors = {i: [float(i)] for i in range(4)}
    with pytest.raises(ValueError, match="exceeds_maximum_legs"):
        build_retrieval_legs(plan, vectors, config)
    legs = seven_legs(plan, vectors, config)
    assert len(legs) == 7 and [leg.weight for leg in legs] == [1.0] * 7
    assert [leg.channel for leg in legs].count(SearchChannel.LEXICAL) == 3
    assert legs[6].channel == SearchChannel.DENSE and legs[6].variant_index == 3
    assert config.model_dump() == before and config.fusion.maximum_legs == 6
    assert bundle_plan("原问题", ["查询一", "查询二", "查询三"], "假设性通用知识段落", binding.profile) == plan
    with pytest.raises(ValueError, match="contract_mismatch"):
        seven_legs(plan, {i: vectors[i] for i in range(3)}, config)


def test_seven_leg_fusion_is_one_existing_rrf_and_child_dedup():
    _, _, config, _, binding = preflight()
    plan = bundle_plan("原问题", ["查询一", "查询二", "查询三"], "知识段落", binding.profile)
    legs = seven_legs(plan, {i: [1.0] for i in range(4)}, config)
    results = [LegSearchResult(leg=leg, hits=[RawSearchHit("child", 1.0, {"chunk_id": "child"})] * 2) for leg in legs]
    fused = weighted_rrf(results, k=60)
    assert len(fused) == 1 and len(fused[0].evidence) == 7
    assert fused[0].fusion_score == pytest.approx(7 / 61)


def test_recorder_guard_and_raw_usage_are_checked_without_network(tmp_path):
    row = example()
    recorder = h1.Recorder(tmp_path / "calls")
    recorder.scope = (row, "expansion")
    request = httpx.Request("POST", h1.BASE_URL + "/chat/completions", json=h1.request_body(row, "expansion"))
    asyncio.run(recorder.request(request))
    response = httpx.Response(200, request=request, json={"usage": {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}})
    asyncio.run(recorder.response(response))
    assert h1.usage_summary(recorder.entries)["all"]["total_tokens"] == 15
    assert "authorization" not in (tmp_path / "calls/001.json").read_text(encoding="utf-8").lower()
    with pytest.raises(h1.H1Error, match="second_http_attempt"):
        asyncio.run(recorder.request(request))
    invalid = h1.request_body(row, "expansion")
    invalid["temperature"] = 0.01
    with pytest.raises(h1.H1Error, match="outbound_body"):
        asyncio.run(recorder.request(httpx.Request("POST", h1.BASE_URL + "/chat/completions", json=invalid)))
    with pytest.raises(h1.H1Error, match="unapproved_http"):
        asyncio.run(recorder.request(httpx.Request("GET", "http://localhost:6333/")))
    with pytest.raises(h1.H1Error, match="unapproved_http"):
        asyncio.run(recorder.request(httpx.Request("POST", "https://other.example/v1/chat/completions")))


def test_secret_guard_requires_explicit_scoped_authorization():
    with pytest.raises(h1.H1Error, match="secret_read"):
        h1.secret_guard("open", (h1.AI_ROOT / ".env",))
    token = h1.ENV_READ.set(True)
    try:
        h1.secret_guard("open", (h1.AI_ROOT / ".env",))
        with pytest.raises(h1.H1Error, match="secret_read"):
            h1.secret_guard("open", (Path("D:/unapproved/.env"),))
    finally:
        h1.ENV_READ.reset(token)


def test_real_sdk_serialization_matches_http_guard_with_mock_transport(tmp_path):
    from openai import AsyncOpenAI
    row = example()
    recorder = h1.Recorder(tmp_path / "sdk_calls")
    recorder.scope = (row, "expansion")

    async def respond(request):
        return httpx.Response(200, json={"id": "offline", "object": "chat.completion", "created": 0,
            "model": h1.MODEL, "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": '{"queries":["周杰伦2003年风格","周杰伦2003年曲风","周杰伦2003年音乐"]}'}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond),
                event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key="offline-only", base_url=h1.BASE_URL, max_retries=0, http_client=http) as client:
                result = await client.chat.completions.create(**h1.request_body(row, "expansion"))
                assert result.choices[0].finish_reason == "stop"
    asyncio.run(run())
    assert len(recorder.entries) == 1 and recorder.entries[0]["usage"]["total_tokens"] == 3


def test_formal_rag_source_seven_leg_mock_keeps_80_selection_40_rerank_12_output(monkeypatch):
    from threading import RLock
    from app.agent.foundation import rag_source
    from app.rag.retrieval import service
    _, projection, _, _, binding = preflight()
    hits, parents = [], set()
    for point, child in projection.points.items():
        payload = projection.children[child]
        if payload["parent_id"] in parents:
            continue
        parents.add(payload["parent_id"])
        hits.append(RawSearchHit(point, 1.0, payload))
        if len(hits) == 80:
            break
    observed = {}

    class Encoder:
        def encode_queries(self, texts):
            observed["encoded_texts"] = list(texts)
            return [[0.0] * 1024 for _ in texts]

    class Scorer:
        def score(self, inputs):
            observed["rerank_inputs"] = inputs
            return [1 - i / len(inputs) for i in range(len(inputs))]

    class Backend:
        async def validate_binding(self, _binding):
            assert _binding == binding

        async def search(self, _binding, legs, query_filter, *, limit):
            assert query_filter is None and limit == 80
            observed["legs"] = list(legs)
            return [LegSearchResult(leg=leg, hits=hits) for leg in legs]

    original_select = service.select_candidates

    def select(hits, plan, config):
        observed["selection_input"] = len(hits)
        return original_select(hits, plan, config)

    encoder, scorer = Encoder(), Scorer()
    monkeypatch.setattr(rag_source, "_local_models", lambda: (encoder, RLock(), scorer, RLock()))
    monkeypatch.setattr(rag_source, "literal_search_plan", lambda q, p, limit: bundle_plan(
        q, ["查询一", "查询二", "查询三"], "假设性知识段落", p, limit=limit))
    monkeypatch.setattr(service, "build_retrieval_legs", seven_legs)
    monkeypatch.setattr(service, "select_candidates", select)
    source = rag_source.RagSource(settings=SimpleNamespace(retrieval_mode="hybrid", index_manifest=None),
                                  binding=binding, encoder=encoder, scorer=scorer, backend=Backend())
    result = source.collect(SimpleNamespace(query="补全后的原问题"), limit=40)
    assert not result.status and len(result) == 12
    assert len(observed["legs"]) == 7 and len(observed["encoded_texts"]) == 4
    assert observed["selection_input"] == 80 and len(observed["rerank_inputs"]) == 40
    assert all(item.query == "补全后的原问题" for item in observed["rerank_inputs"])
    assert len({item.metadata["parent_id"] for item in result}) == 12
