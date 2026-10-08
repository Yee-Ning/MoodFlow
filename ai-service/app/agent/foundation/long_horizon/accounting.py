"""Durable pre-dispatch reserves survive a process dying inside the SDK."""

from contextvars import ContextVar
from uuid import uuid4

from .models import HistoryEventType
from .public_events import PublicUsage

active_accounting = ContextVar("continuity_accounting", default=None)


class DurableAccounting:
    def __init__(self, writer, identity, lease):
        self.writer, self.identity, self.lease = writer, identity, lease
        self._budget_adapter = None

    @property
    def budget_ledger(self):
        from app.llm.redis_budget import select_budget_ledger
        if self._budget_adapter is None:
            self._budget_adapter = select_budget_ledger(self)
        return self._budget_adapter

    def flush_budget(self):
        from app.llm.redis_budget import RedisBudgetLedger, RedisBudgetUnavailable
        if isinstance(self._budget_adapter, RedisBudgetLedger):
            import logging
            from sqlite3 import Error as SQLiteError
            from psycopg import Error as PostgresError
            from .execution import ContinuityConflict
            try:
                return self._budget_adapter.flush(self.identity.run_id)
            except RedisBudgetUnavailable:
                # Per-call cold receipts already exist; next bind can rebuild.
                return None
            except (PostgresError, SQLiteError, ContinuityConflict):
                # Secondary snapshots must not mask the original call result or
                # interrupt lease/context cleanup. Primary usage writes still fail.
                logging.getLogger(__name__).warning("Budget snapshot flush unavailable; per-call receipts retained")
                return None

    def write_usage(self, body):
        self.lease.check()
        self.writer.append_public_event(self.identity, f"usage:{body.call_id}:{body.phase}",
                                       HistoryEventType.MODEL_USAGE, body)

    def events(self, run_id):
        if hasattr(self.writer.store, "query_run_usage"):
            return [PublicUsage.model_validate(event.payload) for event in
                    self.writer.store.query_run_usage(self.identity.continuity_task_id, run_id)]
        cursor, result = 0, []
        while True:
            rows = self.writer.store.query_task_history(self.identity.continuity_task_id,
                event_type=HistoryEventType.MODEL_USAGE, after_seq=cursor, limit=100)
            if not rows:
                return result
            for event in rows:
                body = PublicUsage.model_validate(event.payload)
                if body.run_id == run_id:
                    result.append(body)
                cursor = event.event_seq

    def totals(self, run_id, original):
        usage = {row["agent"]: dict(row) for row in original}
        events = self.events(run_id)
        for agent, previous in usage.items():
            selected = [event for event in events if event.agent == agent]
            baseline = next((event for event in selected if event.phase == "baseline"), None)
            if baseline is None:
                continue
            reservations = {event.call_id: event for event in selected if event.phase == "reserved"}
            settlements = {event.call_id: event for event in selected if event.phase == "settled"}
            billed = [settlements.get(call_id, event) for call_id, event in reservations.items()]
            values = {
                "tokens": baseline.tokens + sum(event.tokens for event in billed),
                "reported_tokens": baseline.reported_tokens + sum(event.tokens for event in billed if event.reported),
                "estimated_tokens": baseline.estimated_tokens + sum(event.tokens for event in billed if not event.reported),
                "model_calls": baseline.model_calls + len(billed),
                "retries": baseline.retries + sum(event.phase == "retry" for event in selected),
            }
            # Never reduce any saved usage, even when a previous adapter lacked receipts.
            previous.update({field: max(previous[field], value) for field, value in values.items()})
        return tuple(usage.values())

    def bind(self, ledger, run_id):
        self.lease.check()
        if not run_id:
            raise ValueError("durable model accounting requires a server run identity")
        self.identity = self.identity.model_copy(update={"run_id": run_id})
        for agent, row in ledger.usage.items():
            key = f"usage:{run_id}:{agent}:baseline"
            if self.writer.store.get_history_event(self.writer.event_id(self.identity, key)) is None:
                self.writer.append_public_event(self.identity, key, HistoryEventType.MODEL_USAGE,
                    PublicUsage(run_id=run_id, agent=agent, call_id="baseline", phase="baseline",
                        tokens=row["tokens"], model_calls=row["model_calls"], retries=row["retries"],
                        reported_tokens=row["reported_tokens"], estimated_tokens=row["estimated_tokens"]))
        ledger.usage = {row["agent"]: row for row in self.totals(run_id, tuple(ledger.usage.values()))}
        ledger.source_stops.update(event.agent for event in self.events(run_id)
                                   if event.phase == "budget" and event.budget_stage == "stop_sources")

    def reserve(self, ledger, amount, *, input_tokens=None, output_tokens=None, model="unknown", billing_agent=None):
        self.lease.check()
        call_id = ledger.claim_reservation() or uuid4().hex
        self.write_usage(PublicUsage(run_id=self.identity.run_id, agent=ledger.current_agent, call_id=call_id,
            phase="reserved", tokens=amount, input_tokens=input_tokens, output_tokens=output_tokens,
            model=model, billing_agent=billing_agent, max_output_tokens=output_tokens))
        return call_id

    def settle(self, ledger, call_id, amount, response, *, usage=None, model="unknown", billing_agent=None, owner=None,
               max_output_tokens=None):
        from app.llm.runtime import reported_tokens

        self.lease.check()
        reported = reported_tokens(response)
        tokens = usage.total_tokens if usage is not None else amount if reported is None else reported
        estimated = usage.estimated if usage is not None else reported is None
        self.write_usage(PublicUsage(run_id=self.identity.run_id, agent=owner or ledger.current_agent, call_id=call_id,
            phase="settled", tokens=tokens, reported=reported is not None,
            input_tokens=usage.input_tokens if usage else None, output_tokens=usage.output_tokens if usage else None,
            model=model, billing_agent=billing_agent, estimated=estimated, max_output_tokens=max_output_tokens))

    def retry(self, ledger):
        self.lease.check()
        call_id = uuid4().hex
        self.writer.append_public_event(self.identity, f"usage:{call_id}:retry", HistoryEventType.MODEL_USAGE,
            PublicUsage(run_id=self.identity.run_id, agent=ledger.current_agent, call_id=call_id, phase="retry", tokens=0))
