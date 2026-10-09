"""Production budget headroom without bypassing provider or cumulative caps."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from app.agent.foundation.context_engineering import POLICIES
from app.agent.foundation.model_input import prepare_model_input
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.config.settings import Settings
from app.llm.budget import output_limit
from app.llm.client import create_base_response, get_base_chat_model
from app.llm.runtime import (
    RequestLedger,
    RuntimeLimitError,
    accounted_call,
    agent_scope,
    instrument_chat_model,
    request_scope,
)
from tests.test_llm_client import FakeAsyncOpenAI, fake_settings


def ledger(*, usage=None):
    return RequestLedger(
        usage or tuple(AgentUsage(agent=key) for key in AgentKey),
        AgentKey.GENERAL_CHAT,
        RunLimits(),
        deadline=30,
    )


def response(left=524, right=1352):
    # Provider-reported split from the failed Nujabes recommendation.
    return AIMessage(content="推荐结果", usage_metadata={
        "input_tokens": left, "output_tokens": right, "total_tokens": left + right,
    })


@pytest.mark.asyncio
async def test_recommendation_above_old_output_cap_reaches_caller_and_receipt():
    model = SimpleNamespace(model_name="deepseek-flash", ainvoke=AsyncMock(return_value=response()))
    book = ledger()
    with request_scope(book):
        answer = await instrument_chat_model(model).ainvoke([HumanMessage(content="根据现在这首歌推荐")])
    assert answer.content == "推荐结果"
    assert model.ainvoke.await_args.kwargs["max_tokens"] == 4096
    assert book.usage["general_chat"]["tokens"] == 1876
    assert book.call_manifests[0]["output_limit_exceeded"] is False
    assert book.call_manifests[0]["usage_basis"] == "provider_usage"
    assert book.reserved == 0 and book.blocked is None


@pytest.mark.asyncio
@pytest.mark.parametrize("output", [4096, 4097])
async def test_new_output_boundary_is_enforced_without_losing_observed_usage(output):
    model = SimpleNamespace(model_name="deepseek-flash", ainvoke=AsyncMock(return_value=response(right=output)))
    book = ledger()
    with request_scope(book):
        if output == 4096:
            await instrument_chat_model(model).ainvoke([HumanMessage(content="推荐")])
        else:
            with pytest.raises(RuntimeLimitError, match="token_limit"):
                await instrument_chat_model(model).ainvoke([HumanMessage(content="推荐")])
    assert book.call_manifests[0]["output_limit_exceeded"] is (output > 4096)
    assert book.usage["general_chat"]["tokens"] == 524 + output
    assert book.reserved == 0


@pytest.mark.asyncio
async def test_repeated_calls_can_exceed_old_agent_and_task_limits():
    book = ledger()
    operation = AsyncMock(return_value=response(left=7000))
    with request_scope(book):
        for _ in range(5):
            await accounted_call(operation, {"messages": "音" * 6000}, model="deepseek-flash")
    assert operation.await_count == 5
    assert book.usage["general_chat"]["tokens"] == 41760
    assert book.agent_limit == 64000 and book.task_limit == 128000
    assert all(row["max_output_tokens"] == 4096 for row in book.call_manifests)
    assert book.reserved == 0 and book.blocked is None


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [63999, 64000, 64001])
async def test_new_agent_limit_keeps_boundary_and_provider_overshoot(total):
    book = ledger()
    with request_scope(book):
        call = accounted_call(AsyncMock(return_value=response(left=total - 1, right=1)), "input")
        if total < 64000:
            await call
        else:
            with pytest.raises(RuntimeLimitError, match="token_limit"):
                await call
            blocked = AsyncMock()
            with pytest.raises(RuntimeLimitError, match="token_limit"):
                await accounted_call(blocked, "next")
            blocked.assert_not_called()
    assert book.usage["general_chat"]["tokens"] == total
    assert book.reserved == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("total", [127999, 128000, 128001])
async def test_new_task_limit_is_shared_and_keeps_boundary_and_overshoot(total):
    usage = tuple(AgentUsage(agent=key, tokens=62000 if key != AgentKey.EXPLORER else 0) for key in AgentKey)
    book = ledger(usage=usage)
    with request_scope(book), agent_scope(AgentKey.EXPLORER):
        call = accounted_call(AsyncMock(return_value=response(left=total - 124001, right=1)), "input", output_tokens=1)
        if total < 128000:
            await call
        else:
            with pytest.raises(RuntimeLimitError, match="token_limit"):
                await call
            assert book.budget_trace[-1]["level"] == "l3"
    assert sum(row["tokens"] for row in book.usage.values()) == total
    assert all(row["tokens"] < 64000 for row in book.usage.values())
    assert book.reserved == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("level", ["l2", "l3"])
async def test_insufficient_headroom_blocks_dispatch_at_the_correct_level(level):
    used = {AgentKey.GENERAL_CHAT: 63999} if level == "l2" else {
        AgentKey.GENERAL_CHAT: 62000, AgentKey.CURATOR: 64000,
    }
    book = ledger(usage=tuple(AgentUsage(agent=key, tokens=used.get(key, 0)) for key in AgentKey))
    operation = AsyncMock()
    with request_scope(book), pytest.raises(RuntimeLimitError, match="token_limit"):
        await accounted_call(operation, "request")
    operation.assert_not_called()
    assert all(event["level"] == level for event in book.budget_trace)
    assert book.reserved == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("node", list(RouteKey))
async def test_each_route_prepares_input_with_the_full_new_output_reservation(node):
    prepared = await prepare_model_input(node, query="推荐音乐", system="rules", schema={}, schema_name="test")
    policy = POLICIES[node]
    assert policy.reserved_output_tokens == 4096
    previous_input = {
        RouteKey.GENERAL_CHAT: 5888, RouteKey.CURRENT_SONG: 5888,
        RouteKey.TOOL_CALLING: 9984, RouteKey.WORLDLINE: 9532,
    }
    assert policy.input_budget >= previous_input[node]
    assert prepared.budget.input_tokens + 4096 + policy.safety_margin == policy.total_input_budget
    prepared.budget.validate({"input": prepared.input_items, "instructions": prepared.instructions}, output_tokens=4096)


@pytest.mark.asyncio
async def test_service_default_reaches_chat_and_responses_sdk_without_a_hidden_cap():
    configured = fake_settings()
    configured.max_output_tokens = Settings.model_fields["max_output_tokens"].default
    get_base_chat_model.cache_clear()
    try:
        with patch("app.llm.client.get_settings", return_value=configured):
            model = instrument_chat_model(get_base_chat_model())
            assert model.output_tokens == 4096
            assert model.model.max_tokens == 4096
            with patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI):
                await create_base_response([{"role": "user", "content": "推荐音乐"}], "rules")
        assert FakeAsyncOpenAI.last_request["max_output_tokens"] == 4096
    finally:
        get_base_chat_model.cache_clear()


@pytest.mark.asyncio
async def test_explicit_smaller_service_cap_remains_effective():
    configured = fake_settings()
    configured.max_output_tokens = 512
    with patch("app.llm.client.get_settings", return_value=configured), patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI):
        await create_base_response([{"role": "user", "content": "简短回复"}], "rules")
    assert FakeAsyncOpenAI.last_request["max_output_tokens"] == 512
    assert output_limit("jev-latest") == 256
