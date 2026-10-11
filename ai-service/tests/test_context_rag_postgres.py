"""Real PG MODEL_USAGE replay on an isolated loopback test database."""

from types import SimpleNamespace

import pytest

from app.agent.foundation.context_pointers import EventPointer
from app.agent.foundation.long_horizon.accounting import active_accounting
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.journal_contracts import JournalReader
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.agent.foundation.long_horizon.postgres_store import PostgresContinuityStore
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.llm.budget import PostgresBudgetLedger
from app.llm.runtime import request_scope
from tests.test_context_budget_phase2 import accounting, book
from tests.test_context_loop_postgres_recovery import BASE, database  # noqa: F401
from tests.test_context_rag_source import collected, pack, source
from tests.test_retrieval_service import FakeEncoder

pytestmark = pytest.mark.skipif(not BASE, reason="isolated PostgreSQL acceptance requires DATABASE_URL")


@pytest.mark.parametrize("unknown_embedding", [False, True])
def test_rag_separate_dimensions_durable_pg_reopen_and_conservative_recovery(database, unknown_embedding):  # noqa: F811
    store = PostgresContinuityStore(database)
    session = LongHorizonContextService(store).open_conversation("handoff:v1:rag-pg")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="request:rag-pg", run_id="run:rag-pg", graph_mode="handoff")
    h = SimpleNamespace(store=store, identity=identity, writer=HistoryWriter(store))
    token = active_accounting.set(accounting(h))
    try:
        ledger = book(run_id=identity.run_id)
        with request_scope(ledger):
            encoder = FakeEncoder(error=ValueError("controlled failure")) if unknown_embedding else FakeEncoder()
            items, _ = collected(source(encoder=encoder))
        usages = pack(items).manifest["sources"]["rag"]["model_usage"]
        assert isinstance(ledger.budget_ledger, PostgresBudgetLedger)
        assert len(usages) == 2
        total = sum(row["total_tokens"] for row in usages)
        assert ledger.budget_ledger.get_task_total(identity.run_id).total_tokens == total
        assert ledger.budget_ledger.get_task_total(identity.run_id).calls == 2
        assert len(ledger.budget_ledger.get_model_breakdown(identity.run_id)) == 2
        for row in usages:
            event = JournalReader(store).resolve(EventPointer.model_validate(row["usage_ref"]))
            assert event.payload["model"] == row["model"]
            assert event.payload["phase"] == ("reserved" if row["status"] == "unknown" else "settled")
        original_usage = ledger.usage
    finally:
        active_accounting.reset(token)
        store.close()
    reopened = PostgresContinuityStore(database)
    token = active_accounting.set(accounting(SimpleNamespace(writer=HistoryWriter(reopened), identity=identity)))
    try:
        recovered = book(run_id=identity.run_id)
        assert recovered.usage == original_usage
        assert recovered.budget_ledger.get_task_total(identity.run_id).total_tokens == total
    finally:
        active_accounting.reset(token)
        reopened.close()
