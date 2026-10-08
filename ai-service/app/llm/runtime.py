"""Model boundary accounting with cumulative request scopes and final L1 gates."""

import asyncio
import json
import logging
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
active_agent: ContextVar = ContextVar("active_budget_agent", default=None)
active_manifest: ContextVar = ContextVar("active_call_manifest", default=None)
logger = logging.getLogger(__name__)
BUDGET_LIMIT_MESSAGE = "本次任务的 token 预算已用完，已停止继续调用模型。现有结果已保留；请缩小请求范围后重试。"


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
                ledger.ensure_available(tokens + output_tokens)
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


def response_content(response):
    if response is None:
        return None
    if hasattr(response, "content"):
        return {"content": response.content, "tool_calls": getattr(response, "tool_calls", [])}
    if hasattr(response, "answers"):
        return response.answers
    return getattr(response, "output", getattr(response, "output_text", ""))


@dataclass(frozen=True)
class CallUsage:
    input_tokens: int
    output_tokens: int
    estimated: bool
    basis: str

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


def measure_usage(response, payload, output_limit):
    usage = getattr(response, "usage_metadata", None) or getattr(response, "usage", None)
    if usage is None:
        usage = getattr(response, "response_metadata", {}).get("token_usage")
    if hasattr(usage, "model_dump"):
        usage = usage.model_dump()
    left = usage.get("input_tokens", usage.get("prompt_tokens")) if isinstance(usage, dict) else None
    right = usage.get("output_tokens", usage.get("completion_tokens")) if isinstance(usage, dict) else None
    def valid(number):
        return type(number) is int and number >= 0
    if valid(left) and valid(right):
        return CallUsage(left, right, False, "provider_usage")
    counter = UnicodeHeuristicTokenCounter()
    text = json.dumps(response_content(response), ensure_ascii=False,
                      default=lambda value: value.model_dump(mode="json") if hasattr(value, "model_dump") else str(value))
    estimated_output = counter.count(text) if response is not None else output_limit
    total = reported_tokens(response)
    if total is not None:
        # Provider total stays authoritative; only its missing split is estimated.
        return CallUsage(max(0, total - min(estimated_output, total)), min(estimated_output, total),
                         True, "provider_total_estimated_split")
    return CallUsage(left if valid(left) else estimate_tokens(payload), right if valid(right) else estimated_output,
                     True, "token_counter" if response is not None else "unsettled_reservation")


@contextmanager
def agent_scope(agent):
    token = active_agent.set(getattr(agent, "value", agent))
    try:
        yield
    finally:
        active_agent.reset(token)


class RequestLedger:
    """Reserve before dispatch, settle actual usage, retain unknown-cost reserves.

    One mutable ledger is shared by child asyncio tasks; reservations are made
    synchronously before their first await. Persisted snapshots contain no text.
    """

    def __init__(self, usage, agent, limits, *, deadline, emit=lambda **_: None, run_id=None):
        self.usage = {item.agent.value: item.model_dump(mode="json") for item in usage}
        self.agent = agent.value
        self.limits = limits
        self.deadline = monotonic() + max(0, deadline)
        self.reserved = 0
        self.blocked = None
        self.emit = emit
        from app.llm.budget import policy
        from uuid import uuid4
        self.run_id = run_id or uuid4().hex
        self.agent_limit = min(limits.agent_token_budget, policy()["l2_tokens"])
        self.task_limit = min(limits.run_token_budget, policy()["l3_tokens"])
        self.call_manifests = []
        self.budget_trace = []
        self.source_stops = set()
        self.hot_budget = None
        self._pending_reservations = []
        self._reservation_context = ContextVar("request_budget_reservation", default=None)
        from app.agent.foundation.long_horizon.accounting import active_accounting
        self.accounting = active_accounting.get()
        if self.accounting is not None:
            self.accounting.bind(self, run_id)
            from app.llm.redis_budget import RedisBudgetLedger, RedisBudgetUnavailable
            adapter = self.accounting.budget_ledger
            if isinstance(adapter, RedisBudgetLedger):
                adapter.ttl = max(adapter.config.ttl_seconds, limits.run_timeout_seconds)
                try:
                    adapter.reconcile(self.run_id, minimum={owner: row["tokens"] for owner, row in self.usage.items()})
                    self.hot_budget = adapter
                except RedisBudgetUnavailable:
                    pass

    @property
    def current_agent(self):
        return active_agent.get() or self.agent

    @property
    def budget_ledger(self):
        return self.accounting.budget_ledger if self.accounting is not None else None

    def budget_event(self, level, stage):
        from app.agent.handoff.observability import emit
        if stage == "stop_sources":
            self.source_stops.add(self.current_agent)
        event = {"trace_id": self.run_id, "task_id": self.run_id, "agent": self.current_agent,
                 "level": level, "stage": stage,
                 "agent_tokens": self.usage[self.current_agent]["tokens"],
                 "task_tokens": sum(row["tokens"] for row in self.usage.values())}
        self.budget_trace.append(event)
        emit("budget.exceeded", event)
        self.emit(code="token_limit", tokens=0, duration_ms=0)
        if self.accounting is not None:
            from app.agent.foundation.long_horizon.public_events import PublicUsage
            from uuid import uuid4
            self.accounting.write_usage(PublicUsage(run_id=self.run_id, agent=self.current_agent,
                call_id=uuid4().hex, phase="budget", tokens=0, budget_level=level, budget_stage=stage))

    @property
    def limit_message(self):
        return BUDGET_LIMIT_MESSAGE

    def check_task_budget(self):
        self.check()
        hot = self._hot_counters()
        total = hot[1] if hot is not None else sum(row["tokens"] for row in self.usage.values()) + self.reserved
        if total >= self.task_limit:
            self.fail_budget("l3")

    def _hot_counters(self):
        from app.llm.redis_budget import RedisBudgetUnavailable
        if self.hot_budget is not None:
            try:
                return self.hot_budget.counters(self.run_id, self.current_agent)
            except RedisBudgetUnavailable:
                self.hot_budget = None
        return None

    def fail_budget(self, level):
        hot = self._hot_counters()
        if hot is not None and hot[1] >= self.task_limit:
            level = "l3"
        stages = ("truncate_history", "summarize_history", "stop_sources", "honest_degradation") if level == "l2" else ("task_degradation", "refuse")
        for stage in stages:
            if not any(row["level"] == level and row["stage"] == stage for row in self.budget_trace):
                self.budget_event(level, stage)
        self.blocked = "token_limit"
        raise RuntimeLimitError("token_limit")

    @property
    def remaining(self):
        hot = self._hot_counters()
        if hot is not None:
            return min(self.agent_limit - hot[0], self.task_limit - hot[1])
        return (
            min(
                self.agent_limit - self.usage[self.current_agent]["tokens"],
                self.task_limit
                - sum(row["tokens"] for row in self.usage.values()),
            )
            - self.reserved
        )

    def check(self):
        if self.accounting is not None:
            self.accounting.lease.check()
        if self.blocked:
            raise RuntimeLimitError(self.blocked)
        if monotonic() >= self.deadline:
            self.blocked = "time_limit"
            raise RuntimeLimitError(self.blocked)

    def ensure_available(self, amount):
        self.check()
        if amount > self.remaining:
            hot = self._hot_counters()
            task_remaining = (self.task_limit - hot[1] if hot is not None else
                              self.task_limit - sum(row["tokens"] for row in self.usage.values()) - self.reserved)
            self.fail_budget("l3" if amount > task_remaining else "l2")

    def reserve(self, amount):
        from app.llm.redis_budget import RedisBudgetUnavailable
        from uuid import uuid4
        self.check()
        call_id = uuid4().hex
        hot_reserved = False
        if self.hot_budget is not None:
            try:
                result, _ = self.hot_budget.reserve(self.run_id, self.current_agent, amount,
                                                    self.agent_limit, self.task_limit, call_id=call_id)
                if result in {"l2", "l3"}:
                    self.fail_budget(result)
                if result != "accepted":
                    raise RedisBudgetUnavailable("unconfirmed reservation")
                hot_reserved = True
            except RedisBudgetUnavailable:
                self.hot_budget = None
        if not hot_reserved:
            self.ensure_available(amount)
        self.reserved += amount
        receipt = {"call_id": call_id, "agent": self.current_agent,
                   "amount": amount, "hot": hot_reserved, "claimed": False}
        self._pending_reservations.append(receipt)
        self._reservation_context.set(receipt)

    def claim_reservation(self):
        """Give the cold receipt the same identity as the just-created hot hold."""
        receipt = self._reservation_context.get()
        if receipt is not None and not receipt["claimed"] and receipt["agent"] == self.current_agent:
            receipt["claimed"] = True
            return receipt["call_id"]
        return None

    def settle(self, amount, response=None, *, usage=None, owner=None):
        self.reserved -= amount
        actual = reported_tokens(response)
        cost = usage.total_tokens if usage is not None else amount if actual is None else actual
        from app.llm.redis_budget import RedisBudgetUnavailable
        current = self._reservation_context.get()
        for receipt in self._pending_reservations:
            if receipt is current:
                self._pending_reservations.remove(receipt)
                self._reservation_context.set(None)
                if receipt["hot"] and self.hot_budget is not None:
                    try:
                        self.hot_budget.settle(self.run_id, receipt["agent"], receipt["call_id"], cost)
                    except RedisBudgetUnavailable:
                        self.hot_budget = None
                break
        row = self.usage[owner or self.current_agent]
        row["tokens"] += cost
        row["estimated_tokens" if actual is None else "reported_tokens"] += cost
        row["model_calls"] += 1
        hot = self._hot_counters()
        # Keep overshoot as observed, never truncate a bill to the configured cap.
        if (
            row["tokens"] >= self.agent_limit
            or sum(item["tokens"] for item in self.usage.values())
            >= self.task_limit
            or hot is not None and (hot[0] > self.agent_limit or hot[1] > self.task_limit)
        ):
            self.blocked = "token_limit"
        return cost

    def retry(self):
        self.check()
        row = self.usage[self.current_agent]
        if row["retries"] >= self.limits.max_retries_per_agent:
            return False
        if self.accounting is not None:
            self.accounting.retry(self)
        row["retries"] += 1
        self.emit(code="retry", tokens=0, duration_ms=0)
        return True


@contextmanager
def request_scope(ledger):
    parent = active_ledger.get()
    token = active_ledger.set(ledger)
    manifest_token = active_manifest.set(None)
    try:
        yield ledger
    finally:
        active_manifest.reset(manifest_token)
        active_ledger.reset(token)
        if parent is None:
            # End of the outer request: never resurrect a prior ephemeral Pack.
            active_manifest.set(None)
            accounting = getattr(ledger, "accounting", None)
            if accounting is not None:
                accounting.flush_budget()


def retryable(exc):
    return isinstance(exc, (TimeoutError, APIConnectionError, OSError)) or (
        isinstance(exc, APIStatusError)
        and (exc.status_code == 429 or exc.status_code >= 500)
    )


async def accounted_call(operation, payload, *, output_tokens=1024, retry=True, model="unknown", billing_agent=None):
    ledger = active_ledger.get()
    from app.llm.budget import output_limit
    from app.agent.foundation.context_engineering import POLICIES
    from app.agent.foundation.routing import RouteKey
    from uuid import uuid4
    output_tokens = min(output_tokens, output_limit(model))
    l1 = POLICIES[RouteKey.GENERAL_CHAT]
    # Final provider framing is checked even outside a cumulative execution scope.
    if estimate_tokens(payload) + output_tokens + l1.safety_margin > max(row.total_input_budget for row in POLICIES.values()):
        if ledger is not None:
            ledger.blocked = "token_limit"
        raise RuntimeLimitError("token_limit")
    reserve = estimate_tokens(payload) + output_tokens
    selected_manifest = active_manifest.get() or {}
    active_manifest.set(None)
    owner = ledger.current_agent if ledger else "general_chat"
    while True:
        if ledger is not None:
            ledger.reserve(reserve)
        call_id = (ledger.accounting.reserve(ledger, reserve, input_tokens=estimate_tokens(payload),
                   output_tokens=output_tokens, model=model, billing_agent=billing_agent or owner)
                   if ledger is not None and ledger.accounting is not None else uuid4().hex)
        response = None
        error = None
        started = perf_counter()
        try:
            async with asyncio.timeout(max(0, ledger.deadline - monotonic()) if ledger else None):
                response = await operation()
        except asyncio.CancelledError:
            if ledger is not None:
                ledger.blocked = ledger.blocked or "cancelled"
            raise
        except Exception as exc:
            if ledger is None or not retry or not retryable(exc):
                raise
            # Settle in finally before testing whether a retry fits the budget.
            error = exc
        finally:
            measured = measure_usage(response, payload, output_tokens)
            if ledger is not None:
                cost = ledger.settle(reserve, response, usage=measured, owner=owner)
                if ledger.accounting is not None:
                    ledger.accounting.settle(ledger, call_id, reserve, response, usage=measured, model=model,
                        billing_agent=billing_agent or owner, owner=owner, max_output_tokens=output_tokens)
                ledger.emit(code="model_call", tokens=cost,
                            duration_ms=round((perf_counter() - started) * 1000))
            manifest = {**selected_manifest, "call_id": call_id, "trace_id": ledger.run_id if ledger else call_id,
                "task_id": ledger.run_id if ledger else call_id, "agent": billing_agent or owner, "model": model,
                "input_tokens": measured.input_tokens, "output_tokens": measured.output_tokens,
                "total_tokens": measured.total_tokens, "estimated": measured.estimated,
                "usage_basis": measured.basis, "max_output_tokens": output_tokens,
                "output_limit_exceeded": measured.output_tokens > output_tokens,
                "status": "completed" if response is not None else "unknown",
                "budget_events": list(ledger.budget_trace) if ledger else []}
            if ledger is not None and ledger.accounting is not None:
                # Numeric observability points at the already-settled fact.
                identity = ledger.accounting.identity
                manifest["usage_ref"] = {"task_id": identity.continuity_task_id,
                    "event_id": ledger.accounting.writer.event_id(identity, f"usage:{call_id}:settled")}
            from app.agent.foundation.context_contracts import CallManifest
            # Validate observability only; authoritative settlement already exists.
            CallManifest.model_validate(manifest)
            if ledger:
                ledger.call_manifests.append(manifest)
            logger.info("context_engineering.call_manifest %s", json.dumps(manifest, ensure_ascii=False))
        if ledger is not None and ledger.blocked == "token_limit":
            level = "l3" if sum(row["tokens"] for row in ledger.usage.values()) >= ledger.task_limit else "l2"
            ledger.fail_budget(level)
        if measured.output_tokens > output_tokens:
            if ledger is not None:
                ledger.blocked = "token_limit"
            raise RuntimeLimitError("token_limit")
        if ledger is not None:
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
        from app.llm.budget import output_limit
        self.model_name = getattr(source, "model_name", None) or getattr(source, "model", None) or "unknown"
        self.output_tokens = min(output_limit(self.model_name), (
            output_tokens
            or getattr(source, "max_tokens", None)
            or getattr(source, "max_completion_tokens", None)
            or 1024
        ))

    def bind_tools(self, tools, **kwargs):
        return AccountedChatModel(
            self.model.bind_tools(tools, **kwargs),
            tools=tools,
            output_tokens=self.output_tokens,
        )

    async def ainvoke(self, messages, *args, **kwargs):
        schemas = ([tool.tool_call_schema.model_json_schema() for tool in self.tools]
                   if self.tools else getattr(self.model, "kwargs", {}).get("tools", []))
        kwargs["max_tokens"] = min(kwargs.get("max_tokens", self.output_tokens), self.output_tokens)
        return await accounted_call(
            lambda: self.model.ainvoke(messages, *args, **kwargs),
            {"messages": messages, "tools": schemas},
            output_tokens=kwargs["max_tokens"],
            model=self.model_name,
        )


def instrument_chat_model(model):
    if isinstance(model, AccountedChatModel):
        return model
    # Copy configuration rather than changing the shared cached Foundation model.
    from langchain_openai import ChatOpenAI

    source = getattr(model, "bound", model)
    if isinstance(source, ChatOpenAI):
        from app.llm.budget import output_limit
        copied = source.model_copy(update={"max_retries": 0, "stream_usage": True,
            "max_tokens": min(source.max_tokens or output_limit(source.model_name), output_limit(source.model_name))})
        if copied.root_async_client is not None:
            isolated = copied.root_async_client.with_options(max_retries=0)
            copied.root_async_client = isolated
            copied.async_client = isolated.chat.completions
        model = copied if source is model else model.model_copy(update={"bound": copied})
    return AccountedChatModel(model)


def instrument_continuity_model(model):
    """Use durable accounting at Foundation SDK boundaries during production calls."""
    return instrument_chat_model(model)
