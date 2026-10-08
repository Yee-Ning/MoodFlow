"""Actual call boundaries, conservative unknown usage and cumulative limits."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, SecretStr
from typesafe_sdk import Choice
import httpx
from openai import APIStatusError, OpenAIError

from app.agent.decision.jev.client import JevClient
from app.agent.foundation.context_engineering import build_context_async
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.registry import AgentKey
from app.agent.handoff.state import AgentUsage, RunLimits
from app.agent.structured_output import StructuredOutputError, StructuredOutputService
from app.llm.client import create_generation_response, create_routing_response
from app.llm.runtime import (
    RequestLedger,
    RuntimeLimitError,
    accounted_call,
    active_ledger,
    estimate_tokens,
    instrument_chat_model,
    reported_tokens,
    request_scope,
)
from tests.test_jev_decision_layer import settings
from tests.test_llm_client import fake_settings


def ledger(*, budget=16000, total=32000, agent=AgentKey.GENERAL_CHAT, usage=None):
    return RequestLedger(
        usage or tuple(AgentUsage(agent=key) for key in AgentKey),
        agent,
        RunLimits(agent_token_budget=budget, run_token_budget=total),
        deadline=30,
    )


def answer(tokens):
    return AIMessage(
        content="回答",
        usage_metadata={
            "input_tokens": tokens - 1,
            "output_tokens": 1,
            "total_tokens": tokens,
        },
    )


@pytest.mark.parametrize(
    "response,expected",
    [
        (answer(12), 12),
        (SimpleNamespace(usage={"input_tokens": 7, "output_tokens": 5}), 12),
        (
            SimpleNamespace(
                response_metadata={
                    "token_usage": {"prompt_tokens": 8, "completion_tokens": 4}
                }
            ),
            12,
        ),
        (SimpleNamespace(usage={"total_tokens": -1}), None),
        (SimpleNamespace(usage={"total_tokens": True}), None),
        (SimpleNamespace(usage=None), None),
    ],
)
def test_usage_formats_and_invalid_usage_do_not_masquerade_as_actual(
    response, expected
):
    assert reported_tokens(response) == expected


@pytest.mark.asyncio
async def test_call_reserves_then_settles_actual_and_resets_context():
    book = ledger()

    async def operation():
        assert book.reserved == estimate_tokens("hello") + 100
        return answer(12)

    with request_scope(book):
        await accounted_call(operation, "hello", output_tokens=100)
    assert active_ledger.get() is None and book.reserved == 0
    row = book.usage["general_chat"]
    assert (
        row["tokens"],
        row["reported_tokens"],
        row["estimated_tokens"],
        row["model_calls"],
    ) == (12, 12, 0, 1)


@pytest.mark.asyncio
async def test_unknown_success_usage_estimates_actual_input_and_response():
    book = ledger()
    with request_scope(book):
        await accounted_call(
            AsyncMock(return_value=AIMessage(content="无用量")),
            "hello",
            output_tokens=100,
        )
    from app.llm.runtime import measure_usage
    expected = measure_usage(AIMessage(content="无用量"), "hello", 100).total_tokens
    assert book.usage["general_chat"]["estimated_tokens"] == expected
    assert book.usage["general_chat"]["reported_tokens"] == 0
    assert expected < estimate_tokens("hello") + 100
    assert book.call_manifests[0]["estimated"]


@pytest.mark.asyncio
@pytest.mark.parametrize("budget", [1, 100])
async def test_reservation_refusal_happens_before_request(budget):
    book, operation = ledger(budget=budget), AsyncMock()
    with request_scope(book), pytest.raises(RuntimeLimitError, match="token_limit"):
        await accounted_call(operation, "hello", output_tokens=100)
    operation.assert_not_called()
    assert book.reserved == 0 and book.usage["general_chat"]["model_calls"] == 0


@pytest.mark.asyncio
async def test_actual_overshoot_is_retained_and_response_cannot_dispatch_tools():
    book = ledger(budget=1000)
    response = answer(1500)
    with request_scope(book), pytest.raises(RuntimeLimitError, match="token_limit"):
        await accounted_call(
            AsyncMock(return_value=response), "hello", output_tokens=10
        )
    assert book.usage["general_chat"]["tokens"] == 1500 and book.reserved == 0


@pytest.mark.asyncio
async def test_budget_is_shared_across_agents_and_restored_snapshots():
    usage = (
        AgentUsage(agent=AgentKey.GENERAL_CHAT, tokens=900),
        AgentUsage(agent=AgentKey.CURATOR),
        AgentUsage(agent=AgentKey.EXPLORER),
    )
    book = ledger(budget=1000, total=1000, agent=AgentKey.CURATOR, usage=usage)
    operation = AsyncMock()
    with request_scope(book), pytest.raises(RuntimeLimitError):
        await accounted_call(operation, "hello", output_tokens=100)
    assert book.usage["general_chat"]["tokens"] == 900
    operation.assert_not_called()


@pytest.mark.asyncio
async def test_parallel_calls_cannot_double_reserve_remaining_budget():
    book = ledger(budget=200)
    gate = asyncio.Event()

    async def slow():
        await gate.wait()
        return answer(10)

    with request_scope(book):
        first = asyncio.create_task(accounted_call(slow, "hello", output_tokens=100))
        await asyncio.sleep(0)
        second = AsyncMock()
        with pytest.raises(RuntimeLimitError):
            await accounted_call(second, "hello", output_tokens=100)
        gate.set()
        with pytest.raises(RuntimeLimitError):
            await first
    second.assert_not_called()
    assert book.reserved == 0 and book.usage["general_chat"]["tokens"] == 10


@pytest.mark.asyncio
async def test_exception_and_cancellation_release_reservation_without_free_calls():
    for error in (ValueError("private"), asyncio.CancelledError()):
        book = ledger()
        with request_scope(book), pytest.raises(type(error)):
            await accounted_call(
                AsyncMock(side_effect=error), "hello", output_tokens=100
            )
        assert book.reserved == 0 and book.usage["general_chat"]["estimated_tokens"] > 0
    assert active_ledger.get() is None


@pytest.mark.asyncio
async def test_transient_retry_charges_both_requests_and_shares_agent_allowance():
    book = ledger()
    operation = AsyncMock(side_effect=[OSError("private upstream"), answer(12)])
    with request_scope(book):
        await accounted_call(operation, "hello", output_tokens=100)
        with pytest.raises(RuntimeLimitError, match="retry_limit"):
            await accounted_call(
                AsyncMock(side_effect=OSError()), "next", output_tokens=100
            )
    row = book.usage["general_chat"]
    assert row["retries"] == 1 and row["model_calls"] == 3
    assert row["reported_tokens"] == 12 and row["estimated_tokens"] > 200


@pytest.mark.asyncio
async def test_provider_retries_and_usage_stream_are_isolated_from_cached_model():
    raw = ChatOpenAI(
        api_key="offline",
        model="offline",
        max_completion_tokens=4096,
        max_retries=2,
        stream_usage=False,
    )
    wrapped = instrument_chat_model(raw)
    assert raw.max_retries == 2 and raw.stream_usage is False
    assert wrapped.model.max_retries == 0 and wrapped.model.stream_usage is True
    assert wrapped.model.root_async_client.max_retries == 0
    from app.llm.budget import output_limit
    assert raw.root_async_client.max_retries == 2 and wrapped.output_tokens == output_limit("offline")
    assert wrapped.bind_tools([]).output_tokens == wrapped.output_tokens
    assert raw.max_tokens == 4096 and wrapped.model.max_tokens == wrapped.output_tokens


@pytest.mark.asyncio
async def test_responses_api_router_generation_and_summary_share_ledger():
    calls = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["max_retries"] == 0
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(usage=SimpleUsage())

    class SimpleUsage:
        def model_dump(self):
            return {"input_tokens": 20, "output_tokens": 10}

    book = ledger()
    with (
        patch("app.llm.client.AsyncOpenAI", Client),
        patch("app.llm.client.get_settings", fake_settings),
        request_scope(book),
    ):
        await create_routing_response(
            [{"role": "user", "content": "hi"}], "route", {"type": "object"}
        )
        await create_generation_response(
            [], "generate", {"type": "object"}, schema_name="test"
        )
        from app.llm.client import create_base_response

        await create_base_response([], "summary")
    assert len(calls) == 3 and book.usage["general_chat"]["reported_tokens"] == 90


@pytest.mark.asyncio
async def test_actual_jev_sdk_request_is_accounted_and_never_retried():
    class SDK:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def system_one(self, **kwargs):
            return SimpleNamespace(
                model="test-jev",
                usage=SimpleNamespace(
                    model_dump=lambda: {"input_tokens": 21, "output_tokens": 2}
                ),
                answers={
                    "route": SimpleNamespace(
                        type="choice",
                        choice="chat",
                        confidence=1,
                        probabilities={"chat": 1},
                    )
                },
            )

    book = ledger()
    configured = settings().model_copy(
        update={"typesafe_api_key": SecretStr("offline")}
    )
    with (
        patch("app.agent.decision.jev.client.AsyncTypeSafeClient", SDK),
        request_scope(book),
    ):
        await JevClient(configured).decide(
            "hi", {"route": Choice(instructions="route", criteria={"chat": None})}
        )
    assert book.usage["general_chat"]["reported_tokens"] == 23
    assert book.usage["general_chat"]["retries"] == 0


@pytest.mark.asyncio
async def test_required_context_budget_failure_is_sticky_and_optional_history_shrinks(
    monkeypatch,
):
    monkeypatch.setenv("JEV_ENABLED", "false")
    book = ledger(budget=3000)
    with request_scope(book):
        pack = await build_context_async(
            RouteKey.GENERAL_CHAT,
            system="规则",
            query="你好",
            messages=[HumanMessage(content="旧历史" * 800)],
        )
        assert pack.total_tokens <= 696
        assert [row["stage"] for row in book.budget_trace] == ["truncate_history", "summarize_history"]
        assert "旧历史" in str(pack.messages) and "旧历史" * 800 not in str(pack.messages)
        with pytest.raises(RuntimeLimitError, match="token_limit"):
            await build_context_async(
                RouteKey.GENERAL_CHAT, system="必须保留规则" * 500, query="你好"
            )
    assert book.blocked == "token_limit"


@pytest.mark.asyncio
async def test_structured_output_repair_uses_same_single_retry_allowance():
    class Output(BaseModel):
        count: int

    book = ledger()
    generate = AsyncMock(side_effect=['{"count":"bad"}', '{"count":1}'])
    with request_scope(book):
        assert (await StructuredOutputService("test", Output).run(generate)).count == 1
        second = AsyncMock(return_value='{"count":"bad"}')
        with pytest.raises(StructuredOutputError) as error:
            await StructuredOutputService("test", Output).run(second)
    assert generate.await_count == 2 and second.await_count == 1
    assert book.usage["general_chat"]["retries"] == 1 and error.value.attempts == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,retries", [(400, 0), (401, 0), (403, 0), (429, 1), (500, 1), (503, 1)]
)
async def test_only_transient_http_errors_retry(status, retries):
    failure = APIStatusError(
        "private",
        response=httpx.Response(
            status, request=httpx.Request("POST", "https://example.com")
        ),
        body=None,
    )
    operation = AsyncMock(side_effect=[failure, answer(100)])
    book = ledger()
    with request_scope(book):
        if retries:
            await accounted_call(operation, "hello")
        else:
            with pytest.raises(APIStatusError):
                await accounted_call(operation, "hello")
    assert book.usage["general_chat"]["retries"] == retries
    assert operation.await_count == 1 + retries


@pytest.mark.asyncio
async def test_concurrent_runs_never_share_ledgers():
    first, second = ledger(), ledger()

    async def run(book, tokens):
        with request_scope(book):
            await asyncio.sleep(0)
            await accounted_call(AsyncMock(return_value=answer(tokens)), "request")

    await asyncio.gather(run(first, 100), run(second, 200))
    assert first.usage["general_chat"]["tokens"] == 100
    assert second.usage["general_chat"]["tokens"] == 200 and active_ledger.get() is None


@pytest.mark.asyncio
async def test_responses_api_error_log_never_contains_upstream_private_text(caplog):
    class Client:
        def __init__(self, **kwargs):
            self.responses = self

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def create(self, **kwargs):
            raise OpenAIError("private-token-from-provider")

    with (
        patch("app.llm.client.AsyncOpenAI", Client),
        patch("app.llm.client.get_settings", fake_settings),
        request_scope(ledger()),
        pytest.raises(OpenAIError),
    ):
        await create_routing_response([], "route", {"type": "object"})
    assert "private-token-from-provider" not in caplog.text
