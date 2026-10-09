import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.memory.jobs import MemoryJobWorker
from app.agent.memory.models import MemoryExtractionOutput
from tests.test_memory_hardening import artist, setup_runtime


def response(tokens=20):
    return SimpleNamespace(status="completed", output=(), output_text=MemoryExtractionOutput(
        memories=[artist()]).model_dump_json(), usage={"total_tokens": tokens})


def worker_setup(provider=None):
    runtime, store, documents = setup_runtime()
    worker = MemoryJobWorker(runtime, provider=provider or AsyncMock(return_value=response()))
    runtime.job_worker = worker
    worker.start = lambda: None
    return worker, runtime, store, documents


@pytest.mark.asyncio
async def test_enqueue_returns_before_model_and_deduplicates_message():
    provider = AsyncMock(return_value=response())
    worker, _, store, documents = worker_setup(provider)
    assert await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    assert await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    provider.assert_not_awaited()
    assert len(store._jobs) == 1 and store.all_records() == []
    assert await worker.process_one()
    assert len(store.all_records()) == 1 and documents.read("u").entries
    job = next(iter(store._jobs.values()))
    assert job.status == "completed" and job.user_message == "" and job.extraction is None
    assert job.observed_dispatches == job.dispatches == 1


@pytest.mark.asyncio
async def test_deadline_and_revocation_block_dispatch_without_fake_budget():
    provider = AsyncMock(return_value=response())
    worker, runtime, store, _ = worker_setup(provider)
    await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    original = next(iter(store._jobs.values()))
    store._jobs[original.job_id] = original.model_copy(update={"budget": {
        **original.budget, "deadline": (datetime.now(UTC) - timedelta(seconds=1)).isoformat()}})
    assert await worker.process_one()
    provider.assert_not_awaited()
    assert store.get_job("u", original.job_id).status == "blocked"
    await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m2", user_message="我喜欢周杰伦")
    runtime.service.set_enabled("u", False)
    await worker.process_one()
    provider.assert_not_awaited()
    assert store.all_records() == []
    assert all(job.user_message == "" for job in store._jobs.values())


@pytest.mark.asyncio
async def test_prepared_work_retries_storage_without_another_model_call():
    worker, runtime, store, _ = worker_setup()
    await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    original = runtime.service.remember
    runtime.service.remember = lambda **_: (_ for _ in ()).throw(ConnectionError("offline"))
    await worker.process_one()
    job = next(iter(store._jobs.values()))
    assert job.status == "pending" and job.extraction
    runtime.service.remember = original
    await worker.process_one()
    assert worker.provider.await_count == 1 and len(store.all_records()) == 1
    assert store.get_job("u", job.job_id).status == "completed"


@pytest.mark.asyncio
async def test_worker_start_stop_cleans_background_task():
    runtime, _, _ = setup_runtime()
    worker = MemoryJobWorker(runtime, provider=AsyncMock(return_value=response()))
    worker.start()
    await asyncio.sleep(0)
    await worker.stop()
    assert worker.task is None


@pytest.mark.asyncio
async def test_async_job_uses_original_model_usage_reservation_and_settles_once(tmp_path):
    from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
    from app.agent.handoff.registry import AgentKey
    from app.llm.runtime import RequestLedger, request_scope
    from tests.context_loop_helpers import harness
    h = harness(tmp_path / "history.db")
    accounting = DurableAccounting(h.writer, h.identity, SimpleNamespace(check=lambda: None))
    token = active_accounting.set(accounting)
    try:
        ledger = RequestLedger(h.run.usage, AgentKey.GENERAL_CHAT, h.run.limits, deadline=120, run_id=h.run.run_id)
    finally:
        active_accounting.reset(token)
    worker, _, store, _ = worker_setup()
    worker.history_store = h.store
    with request_scope(ledger):
        assert await worker.enqueue(user_id="u", source_thread_id="t", source_message_id="m", user_message="我喜欢周杰伦")
    job = next(iter(store._jobs.values()))
    assert job.budget["kind"] == "durable" and ledger.reserved > 0
    await worker.process_one()
    receipts = h.store.query_task_history(h.session.task_id, event_type="model_usage", limit=100)
    reserved = [row for row in receipts if row.payload["phase"] == "reserved"]
    settled = [row for row in receipts if row.payload["phase"] == "settled"]
    assert len(reserved) == len(settled) == 1
    assert reserved[0].payload["call_id"] == settled[0].payload["call_id"]
    assert settled[0].payload["tokens"] == 20 and settled[0].payload["run_id"] == h.run.run_id
    assert ledger.reserved == 0
    assert ledger.usage["general_chat"]["tokens"] == 20
    assert not await worker.process_one()


def test_offline_policy_eval_and_telemetry_do_not_expose_user_data(caplog):
    from scripts.evaluate_memory import evaluate
    from app.agent.memory.telemetry import record_memory_event
    import logging
    result = evaluate()
    assert result["false_positive_rate"] == result["false_negative_rate"] == 0
    assert result["recall_hit_at_3"] == 1 and not result["live_model_accuracy_measured"]
    with caplog.at_level(logging.INFO, logger="moodflow.memory"):
        record_memory_event("correct", outcome="superseded", latency_ms=12, count=1)
    assert "user_id" not in caplog.text and "周杰伦" not in caplog.text and "latency_ms" in caplog.text
