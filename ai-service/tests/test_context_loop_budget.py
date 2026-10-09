import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.foundation.context_engineering import POLICIES
from app.agent.foundation.llm_routing.runtime import ProviderLLMIntentRuntime
from app.agent.foundation.model_input import prepare_model_input
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.generation import ProviderWorldLineRuntime
from app.agent.handoff.registry import AgentKey
from app.agent.structured_output import StructuredOutputError
from app.llm.client import create_structured_response
from app.llm.runtime import ProviderBudget, RequestLedger, RuntimeLimitError, estimate_tokens, request_scope
from app.rag.query_understanding.analyzer import ExistingLLMQueryAnalysisRuntime
from tests.test_llm_client import FakeAsyncOpenAI, FakeResponse, fake_settings
from tests.test_llm_router import request_for
from tests.context_loop_helpers import harness


@pytest.mark.asyncio
async def test_worldline_sends_only_budgeted_projection_and_bounded_metadata():
    payload = {
        "user_query": "核对音乐背景", "root_entity": {"name": "歌曲"},
        "catalog_facts": [], "worldline_graph": {"nodes": [], "edges": [{"evidence_ids": ["one"]}]},
        "sources": [{"id": "one", "title": "来源" * 10000, "url": "https://example.com/" + "元" * 10000,
                     "excerpt": "准确证据", "evidence_span": "准确证据", "raw_prompt": "private source metadata"},
                    {"id": "two", "title": "重复来源", "excerpt": "准确证据"}],
    }
    with patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI), patch("app.llm.client.get_settings", fake_settings):
        await ProviderWorldLineRuntime().generate(payload, instructions="可信生成规则", schema={}, schema_name="test", retry=False)
    request = FakeAsyncOpenAI.last_request
    sent = json.loads(request["input"][0]["content"])
    assert [row["id"] for row in sent["sources"]] == ["one"]
    assert sent["sources"][0]["excerpt"] == "准确证据"
    assert len(sent["sources"][0]["title"]) <= 160 and len(sent["sources"][0]["url"]) <= 512
    assert "raw_prompt" not in str(request)
    policy = POLICIES[RouteKey.WORLDLINE]
    assert estimate_tokens(request) <= policy.input_budget
    assert estimate_tokens(request) + request["max_output_tokens"] + policy.safety_margin <= policy.total_input_budget


@pytest.mark.asyncio
@pytest.mark.parametrize("call", ["routing", "query", "worldline"])
async def test_required_oversize_input_blocks_each_bypass_before_network(call):
    FakeAsyncOpenAI.last_request = None
    huge = "长" * 40000
    with patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI), patch("app.llm.client.get_settings", fake_settings):
        with pytest.raises(StructuredOutputError if call == "query" else ValueError) as failure:
            if call == "routing":
                await ProviderLLMIntentRuntime().classify(request_for(), instructions=huge, retry=False)
            elif call == "query":
                await ExistingLLMQueryAnalysisRuntime().analyze(huge)
            else:
                await ProviderWorldLineRuntime().generate({"user_query": huge}, instructions="trusted", schema={}, schema_name="test", retry=False)
        cause = failure.value.__cause__ if call == "query" else failure.value
        assert isinstance(cause, ValueError) and "budget" in str(cause)
    assert FakeAsyncOpenAI.last_request is None


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["content", "schema", "output"])
async def test_actual_serialized_request_is_checked_after_schema_and_output_are_added(fault):
    FakeAsyncOpenAI.last_request = None
    budget = ProviderBudget(input_tokens=600, total_tokens=2000, safety_margin=256)
    content = "文" * 2000 if fault == "content" else "查询"
    schema = {"description": "说" * 2000} if fault == "schema" else {}
    output = 1900 if fault == "output" else 128
    with (patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI), patch("app.llm.client.get_settings", fake_settings),
          patch("app.llm.budget.output_limit", return_value=3000)):
        with pytest.raises(ValueError, match="final model payload"):
            await create_structured_response([{"role": "user", "content": content}], "rules", schema,
                                             schema_name="test", max_output_tokens=output, request_budget=budget)
    assert FakeAsyncOpenAI.last_request is None


@pytest.mark.asyncio
async def test_routing_preserves_query_json_and_retry_instruction_under_budget():
    request = request_for("逐字保留当前歌曲请求")
    with patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI), patch("app.llm.client.get_settings", fake_settings):
        await ProviderLLMIntentRuntime().classify(request, instructions="trusted", retry=True)
    sent = FakeAsyncOpenAI.last_request
    assert json.loads(sent["input"][0]["content"]) == request.model_dump(mode="json")
    assert "span 校验" in sent["instructions"]
    assert estimate_tokens(sent) <= POLICIES[RouteKey.GENERAL_CHAT].input_budget


@pytest.mark.asyncio
async def test_query_retry_rebuilds_budget_with_schema_feedback():
    class QueryClient(FakeAsyncOpenAI):
        calls = []
        async def create(self, **kwargs):
            self.calls.append(kwargs)
            response = FakeResponse()
            response.output_text = '{"query_types":["wrong"],"confidence":0.8}' if len(self.calls) == 1 else '{"query_types":["exploratory"],"confidence":0.8}'
            return response
    with patch("app.llm.client.AsyncOpenAI", QueryClient), patch("app.llm.client.get_settings", fake_settings):
        result = await ExistingLLMQueryAnalysisRuntime().analyze("深入了解音乐")
    assert len(QueryClient.calls) == 2 and result.confidence == 0.8
    assert QueryClient.calls[0]["instructions"] != QueryClient.calls[1]["instructions"]
    for call in QueryClient.calls:
        assert json.loads(call["input"][0]["content"])["query"] == "深入了解音乐"
        assert estimate_tokens(call) <= POLICIES[RouteKey.WORLDLINE].input_budget


@pytest.mark.asyncio
async def test_remaining_ledger_cannot_be_reset_to_fit_schema(tmp_path):
    h = harness(tmp_path / "budget.db")
    usage = tuple(item.model_copy(update={"tokens": h.run.limits.agent_token_budget - 100}) if item.agent == AgentKey.GENERAL_CHAT else item for item in h.run.usage)
    ledger = RequestLedger(usage, AgentKey.GENERAL_CHAT, h.run.limits, deadline=45)
    with request_scope(ledger), pytest.raises(RuntimeLimitError, match="token_limit"):
        await prepare_model_input(RouteKey.GENERAL_CHAT, query="必须保留", system="trusted", schema={}, schema_name="test")
    assert ledger.blocked == "token_limit" and ledger.reserved == 0


@pytest.mark.asyncio
async def test_rendering_overhead_reselects_pack_without_resurrecting_dropped_sources():
    default = POLICIES[RouteKey.WORLDLINE]
    policy = default.model_copy(update={"total_input_budget": default.reserved_output_tokens + 800})
    seen = []
    def render(pack):
        kept = [item for item in pack.items if item.type == "rag_evidence"]
        seen.append([item.id for item in kept])
        # Deliberately larger framing than Assembler tags.
        return [{"role": "user", "content": json.dumps([{"excerpt": item.content, "label": "栏" * 100} for item in kept], ensure_ascii=False)}], "trusted"
    model_input = await prepare_model_input(RouteKey.WORLDLINE, query="请求", system="trusted", schema={}, schema_name="test", policy=policy, renderer=render,
                                          rag_pack=SimpleNamespace(items=[{"id": "one", "excerpt": "证据" * 70}, {"id": "two", "excerpt": "其他" * 70}]))
    assert len(seen) >= 2 and set(seen[-1]).issubset(seen[0])
    assert estimate_tokens({"input": model_input.input_items, "instructions": model_input.instructions}) <= model_input.budget.input_tokens
