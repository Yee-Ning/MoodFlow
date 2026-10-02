"""Opt-in request accounting; inactive outside a handoff execution scope."""

import asyncio
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from time import monotonic, perf_counter

from openai import APIConnectionError, APIStatusError

from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter


class RuntimeLimitError(Exception):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


active_ledger: ContextVar = ContextVar("active_request_ledger", default=None)


def estimate_tokens(value):
    def encode(item):
        if hasattr(item, "model_dump"):
            return item.model_dump(mode="json")
        raise TypeError("unsupported request payload")

    text = json.dumps(value, ensure_ascii=False, default=encode)
    return UnicodeHeuristicTokenCounter().count(text) + 32


@dataclass(frozen=True)
class ProviderBudget:
    """Validate the serialized final request, including format/schema framing."""

    input_tokens: int
    total_tokens: int
    safety_margin: int

    def validate(self, payload, *, output_tokens):
        tokens = estimate_tokens(payload)
        if tokens > self.input_tokens or tokens + output_tokens + self.safety_margin > self.total_tokens:
            ledger = active_ledger.get()
            if ledger is not None:
                ledger.blocked = "token_limit"
                raise RuntimeLimitError("token_limit")
            raise ValueError("final model payload exceeds token budget")
        return tokens


def reported_tokens(response):
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        usage = getattr(response, "usage", None)
    if usage is None:
        usage = getattr(response, "response_metadata", {}).get("token_usage")
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    if isinstance(usage, dict):
        total = usage.get("total_tokens")
        if total is None:
            left = usage.get("input_tokens", usage.get("prompt_tokens"))
            right = usage.get("output_tokens", usage.get("completion_tokens"))
            if type(left) is int and type(right) is int:
                total = left + right
        if type(total) is int and total >= 0:
            return total
    return None


class RequestLedger:
    """Reserve before dispatch, settle actual usage, retain unknown-cost reserves.

    One mutable ledger is shared by child asyncio tasks; reservations are made
    synchronously before their first await. Persisted snapshots contain no text.
    """

    def __init__(self, usage, agent, limits, *, deadline, emit=lambda **_: None):
        self.usage = {item.agent.value: item.model_dump(mode="json") for item in usage}
        self.agent = agent.value
        self.limits = limits
        self.deadline = monotonic() + max(0, deadline)
        self.reserved = 0
        self.blocked = None
        self.emit = emit

    @property
    def remaining(self):
        return (
            min(
                self.limits.agent_token_budget - self.usage[self.agent]["tokens"],
                self.limits.run_token_budget
                - sum(row["tokens"] for row in self.usage.values()),
            )
            - self.reserved
        )

    def check(self):
        if self.blocked:
            raise RuntimeLimitError(self.blocked)
        if monotonic() >= self.deadline:
            self.blocked = "time_limit"
            raise RuntimeLimitError(self.blocked)

    def reserve(self, amount):
        self.check()
        if amount > self.remaining:
            self.blocked = "token_limit"
            self.emit(code="token_limit", tokens=0, duration_ms=0)
            raise RuntimeLimitError(self.blocked)
        self.reserved += amount

    def settle(self, amount, response=None):
        self.reserved -= amount
        actual = reported_tokens(response)
        cost = amount if actual is None else actual
        row = self.usage[self.agent]
        row["tokens"] += cost
        row["estimated_tokens" if actual is None else "reported_tokens"] += cost
        row["model_calls"] += 1
        # Keep overshoot as observed, never truncate a bill to the configured cap.
        if (
            row["tokens"] >= self.limits.agent_token_budget
            or sum(item["tokens"] for item in self.usage.values())
            >= self.limits.run_token_budget
        ):
            self.blocked = "token_limit"
        return cost

    def retry(self):
        self.check()
        row = self.usage[self.agent]
        if row["retries"] >= self.limits.max_retries_per_agent:
            return False
        row["retries"] += 1
        self.emit(code="retry", tokens=0, duration_ms=0)
        return True


@contextmanager
def request_scope(ledger):
    token = active_ledger.set(ledger)
    try:
        yield ledger
    finally:
        active_ledger.reset(token)


def retryable(exc):
    return isinstance(exc, (TimeoutError, APIConnectionError, OSError)) or (
        isinstance(exc, APIStatusError)
        and (exc.status_code == 429 or exc.status_code >= 500)
    )


async def accounted_call(operation, payload, *, output_tokens=1024, retry=True):
    ledger = active_ledger.get()
    if ledger is None:
        return await operation()
    reserve = estimate_tokens(payload) + output_tokens
    while True:
        ledger.reserve(reserve)
        response = None
        error = None
        started = perf_counter()
        try:
            async with asyncio.timeout(max(0, ledger.deadline - monotonic())):
                response = await operation()
        except asyncio.CancelledError:
            ledger.blocked = ledger.blocked or "cancelled"
            raise
        except Exception as exc:
            if not retry or not retryable(exc):
                raise
            # Settle in finally before testing whether a retry fits the budget.
            error = exc
        finally:
            cost = ledger.settle(reserve, response)
            ledger.emit(
                code="model_call",
                tokens=cost,
                duration_ms=round((perf_counter() - started) * 1000),
            )
        ledger.check()
        if error is None:
            return response
        if not ledger.retry():
            ledger.blocked = "retry_limit"
            raise RuntimeLimitError("retry_limit")


class AccountedChatModel:
    """Wrap bound/injected models at the actual ainvoke boundary."""

    def __init__(self, model, *, tools=(), output_tokens=None):
        self.model = model
        self.tools = tools
        source = getattr(model, "bound", model)
        self.output_tokens = (
            output_tokens
            or getattr(source, "max_tokens", None)
            or getattr(source, "max_completion_tokens", None)
            or 1024
        )

    def bind_tools(self, tools, **kwargs):
        return AccountedChatModel(
            self.model.bind_tools(tools, **kwargs),
            tools=tools,
            output_tokens=self.output_tokens,
        )

    async def ainvoke(self, messages, *args, **kwargs):
        schemas = [tool.tool_call_schema.model_json_schema() for tool in self.tools]
        return await accounted_call(
            lambda: self.model.ainvoke(messages, *args, **kwargs),
            {"messages": messages, "tools": schemas},
            output_tokens=self.output_tokens,
        )


def instrument_chat_model(model):
    if isinstance(model, AccountedChatModel):
        return model
    # Copy configuration rather than changing the shared cached Foundation model.
    from langchain_openai import ChatOpenAI

    if isinstance(model, ChatOpenAI):
        model = model.model_copy(update={"max_retries": 0, "stream_usage": True})
        if model.root_async_client is not None:
            isolated = model.root_async_client.with_options(max_retries=0)
            model.root_async_client = isolated
            model.async_client = isolated.chat.completions
    return AccountedChatModel(model)
