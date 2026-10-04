"""Recovery queries must leave ordinary combination progress queries available."""

import pytest

from app.agent.foundation.long_horizon.operations import active_operations
from app.agent.foundation.long_horizon.recovery_receipts import (
    RecoveryReceiptRepair,
    answer_recovery_query,
)
from app.agent.foundation.long_horizon.public_events import RecoveryGap
from tests.context_loop_helpers import checkpoint, harness
from tests.test_recovery_selfhealing import operation


@pytest.mark.asyncio
async def test_generic_progress_without_operations_falls_through():
    token = active_operations.set(None)
    try:
        assert await answer_recovery_query("刚才哪些步骤成功，卡在哪里？") is None
        assert "没有可查询的恢复账本" in await answer_recovery_query("恢复卡在哪里？")
    finally:
        active_operations.reset(token)


@pytest.mark.asyncio
async def test_generic_progress_without_recovery_record_falls_through(tmp_path):
    h = harness(tmp_path / "history.db")
    ops = operation(h)
    token = active_operations.set(ops)
    try:
        assert await answer_recovery_query("刚才哪些步骤成功，卡在哪里？") is None
        assert await answer_recovery_query("恢复卡在哪里？") is not None
    finally:
        active_operations.reset(token)
        ops.accounting.lease.release()


@pytest.mark.asyncio
async def test_existing_recovery_record_still_answers_generic_block_query(tmp_path):
    h = harness(tmp_path / "history.db")
    ops = operation(h)
    repair = RecoveryReceiptRepair(h.writer, h.journal)
    repair.record(checkpoint(h), (RecoveryGap(kind="action_ack", subject_id="one",
        status="blocked", reason="action_ack_missing"),))
    token = active_operations.set(ops)
    try:
        assert "缺少播放器 ACK" in await answer_recovery_query("卡在哪里？")
    finally:
        active_operations.reset(token)
        ops.accounting.lease.release()
