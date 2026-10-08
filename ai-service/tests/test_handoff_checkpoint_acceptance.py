from datetime import UTC, datetime

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.handoff.state import new_run
from scripts.handoff_checkpoint_acceptance import checkpoint_summary, execute


def test_summary_verifies_tool_pairing_without_exporting_message_content():
    plan = RouteDecision(
        is_complete=True,
        tasks=[
            RouteTask(
                task_id="one", route_key=RouteKey.TOOL_CALLING, input_text="暂停播放"
            )
        ],
    )
    run = new_run("phase6:test", "暂停播放", plan, started_at=datetime.now(UTC))
    call = AIMessage(
        content="private model text",
        tool_calls=[
            {
                "id": "call:one",
                "name": "transfer_to_curator_agent",
                "args": {"reason": "private proposal"},
            },
        ],
    )
    values = {"handoff_run": run.model_dump(mode="json"), "messages": [call]}
    assert not checkpoint_summary(values)["all_calls_paired"]
    values["messages"].append(
        ToolMessage(content="private observation", tool_call_id="call:one")
    )
    summary = checkpoint_summary(values)
    assert summary["all_calls_paired"]
    assert "private" not in str(summary)


@pytest.mark.asyncio
async def test_live_checkpoint_worker_requires_postgres(monkeypatch):
    monkeypatch.setattr(
        "scripts.handoff_checkpoint_acceptance.load_runtime_environment",
        lambda: pytest.fail("helper must not mutate process environment"),
    )
    monkeypatch.setenv("CHECKPOINT_BACKEND", "memory")
    with pytest.raises(ValueError, match="postgres_required"):
        await execute("create", "phase6:test")


@pytest.mark.asyncio
async def test_live_checkpoint_worker_rejects_unscoped_thread_before_connecting(
    monkeypatch,
):
    monkeypatch.setenv("CHECKPOINT_BACKEND", "postgres")
    monkeypatch.setattr(
        "scripts.handoff_checkpoint_acceptance.load_runtime_environment",
        lambda: pytest.fail("helper must not mutate process environment"),
    )
    with pytest.raises(ValueError, match="acceptance_thread_prefix_required"):
        await execute("create", "existing-user-thread")
