"""Budget policy and views over the existing immutable usage receipts."""

from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


def policy():
    from app.agent.foundation.context_sources import load_context_policy
    return load_context_policy()["budget"]


def output_limit(model="default"):
    limits = policy()["max_output_tokens"]
    return limits.get(model, limits["default"])


@dataclass(frozen=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_tokens: int = 0
    calls: int = 0

    @property
    def total_tokens(self):
        return self.input_tokens + self.output_tokens


class BudgetLedger(Protocol):
    def add(self, task_id, agent, input_tokens, output_tokens, model, *, estimated=False): ...
    def get_task_total(self, task_id) -> TokenUsage: ...
    def get_agent_total(self, task_id, agent) -> TokenUsage: ...
    def get_model_breakdown(self, task_id) -> dict[str, TokenUsage]: ...


class HistoryBudgetLedger:
    """Storage adapter: no second balance, only views of MODEL_USAGE events.

    task_id is the original run_id (one user request, including resumed hops),
    not a route subtask or the long-lived continuity task. Unsettled reserves
    remain estimated charges; a settlement replaces its reserve exactly once.
    """

    def __init__(self, accounting):
        self.accounting = accounting

    def add(self, task_id, agent, input_tokens, output_tokens, model, *, estimated=False):
        if task_id != self.accounting.identity.run_id:
            raise ValueError("budget task scope mismatch")
        if agent not in {"general_chat", "curator", "explorer", "jev"}:
            raise ValueError("unknown budget agent")
        from app.agent.foundation.long_horizon.public_events import PublicUsage
        from uuid import uuid4
        owner = "general_chat" if agent == "jev" else agent
        call_id = uuid4().hex
        body = PublicUsage(run_id=task_id, agent=owner, billing_agent=agent, call_id=call_id,
            phase="reserved", tokens=input_tokens + output_tokens, input_tokens=input_tokens,
            output_tokens=output_tokens, model=model, estimated=estimated, reported=not estimated)
        self.accounting.write_usage(body)
        self.accounting.write_usage(body.model_copy(update={"phase": "settled"}))

    def _rows(self, task_id):
        if task_id != self.accounting.identity.run_id:
            raise ValueError("budget task scope mismatch")
        events = self.accounting.events(task_id)
        settled = {event.call_id: event for event in events if event.phase == "settled"}
        rows = [settled.get(event.call_id, event) for event in events if event.phase == "reserved"]
        # Older nonzero baselines have no model/split information. Preserve
        # their cost explicitly as unknown-model estimates, never invent usage.
        baselines = {}
        for event in events:
            if event.phase == "baseline":
                baselines.setdefault(event.agent, event)
        rows.extend(event for event in baselines.values() if event.tokens)
        return rows

    @staticmethod
    def _sum(rows):
        left = right = estimated = 0
        calls = 0
        for row in rows:
            valid_split = row.input_tokens is not None and row.output_tokens is not None
            left += row.input_tokens if valid_split else row.tokens
            right += row.output_tokens if valid_split else 0
            if not valid_split or row.estimated or not row.reported:
                estimated += row.tokens
            calls += row.model_calls if row.phase == "baseline" else 1
        return TokenUsage(left, right, estimated, calls)

    def get_task_total(self, task_id):
        return self._sum(self._rows(task_id))

    def get_agent_total(self, task_id, agent):
        return self._sum(row for row in self._rows(task_id) if (row.billing_agent or row.agent) == agent)

    def get_model_breakdown(self, task_id):
        rows = self._rows(task_id)
        return {model: self._sum(row for row in rows if row.model == model)
                for model in dict.fromkeys(row.model for row in rows)}

    def get_cost(self, task_id):
        prices = policy()["prices"]
        cost, unpriced = Decimal(0), []
        for model, usage in self.get_model_breakdown(task_id).items():
            price = prices.get(model)
            if price is None:
                unpriced.append(model)
                continue
            cost += (usage.input_tokens * Decimal(str(price["input_per_million"]))
                     + usage.output_tokens * Decimal(str(price["output_per_million"]))) / Decimal(1_000_000)
        return {"currency": policy()["currency"], "priced_cost": str(cost),
                "complete": not unpriced, "unpriced_models": unpriced}


class PostgresBudgetLedger(HistoryBudgetLedger):
    """Postgres implementation reuses the continuity pool and JSONB receipts."""

    def __init__(self, accounting):
        from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
        if not isinstance(accounting.writer.store, PostgresContinuityStore):
            raise ValueError("PostgresBudgetLedger requires the existing PostgreSQL store")
        super().__init__(accounting)
