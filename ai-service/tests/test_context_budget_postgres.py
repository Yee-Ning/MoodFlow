"""Budget adapter acceptance against a disposable loopback PostgreSQL DB."""

from types import SimpleNamespace

import pytest
from psycopg import Error

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.llm.budget import PostgresBudgetLedger, TokenUsage
from tests.test_context_loop_postgres_recovery import BASE, database

__all__ = ["database"]
pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


def test_postgres_budget_reuses_immutable_receipts_and_survives_store_reopen(database):
    store = PostgresContinuityStore(database)
    session = LongHorizonContextService(store).open_conversation("phase2-budget")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="one", graph_mode="foundation", run_id="budget-request")
    durable = DurableAccounting(HistoryWriter(store), identity, SimpleNamespace(check=lambda: None))
    ledger = durable.budget_ledger
    assert isinstance(ledger, PostgresBudgetLedger)
    ledger.add(identity.run_id, "curator", 100, 10, "deepseek-chat")
    ledger.add(identity.run_id, "jev", 20, 2, "jev-latest")
    assert ledger.get_task_total(identity.run_id) == TokenUsage(120, 12, 0, 2)
    assert ledger.get_agent_total(identity.run_id, "jev") == TokenUsage(20, 2, 0, 1)
    assert len(store.query_run_usage(session.task_id, identity.run_id)) == 4
    assert store.query_run_usage(session.task_id, "other-request") == []
    with pytest.raises(Error, match="immutable"):
        with store._pool.connection() as connection:
            connection.execute("DELETE FROM agent_history_event WHERE task_id=%s", (session.task_id,))
    with store._pool.connection() as connection:
        assert connection.execute("SELECT to_regclass('public.agent_history_usage_run')").fetchone()["to_regclass"]
    store.close()
    reopened = PostgresContinuityStore(database)
    reloaded = DurableAccounting(HistoryWriter(reopened), identity, SimpleNamespace(check=lambda: None)).budget_ledger
    assert reloaded.get_task_total(identity.run_id) == TokenUsage(120, 12, 0, 2)
    assert reloaded.get_model_breakdown(identity.run_id) == {
        "deepseek-chat": TokenUsage(100, 10, 0, 1), "jev-latest": TokenUsage(20, 2, 0, 1)}
    reopened.close()
