"""Graph deadlines, side-effect-safe retries, traces and recovery."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import tool
from langgraph.types import Command

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.routing import RouteKey
from app.agent.handoff.contracts import HandoffTrace
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.guard import GuardVerdict
from app.agent.handoff.state import HandoffRun, RunLimits, RunStatus, StopReason
from app.agent.handoff.streaming import stream_handoff_assistant
from app.llm.runtime import accounted_call, active_ledger
from app.schemas.music import CreatePlaylistArgs, CreatedPlaylistResult
from tests.test_handoff_budget import answer
from tests.test_handoff_guard import guard
from tests.test_handoff_runtime import Router, ToolModel, call, input_state


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setenv("JEV_ENABLED", "false")
    monkeypatch.setattr(
        "app.agent.handoff.agents.get_base_chat_model",
        lambda: ToolModel(responses=[answer(100)]),
    )


class SequenceModel:
    def __init__(self, *values):
        self.values = list(values)
        self.calls = 0

    def bind_tools(self, *args, **kwargs):
        return self

    async def ainvoke(self, *args, **kwargs):
        self.calls += 1
        value = self.values.pop(0)
        if isinstance(value, BaseException):
            raise value
        return value


@pytest.mark.asyncio
async def test_transient_chat_request_retries_once_then_completes_with_usage():
    model = SequenceModel(OSError("private token"), answer(100))
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model
    ).compile()
    out = await graph.ainvoke(input_state("陪我聊聊"))
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.COMPLETED and model.calls == 2
    assert run.usage[0].retries == 1 and run.usage[0].reported_tokens == 100
    assert run.usage[0].estimated_tokens > 1000
    assert "private token" not in str(out["handoff_trace"])


@pytest.mark.asyncio
async def test_second_transient_failure_exits_once_without_extra_fallback_model():
    model = SequenceModel(OSError("private"), OSError("private"), answer(100))
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model
        )
        .compile()
        .ainvoke(input_state("陪我聊聊"))
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.RETRY_LIMIT
    assert model.calls == 2 and len(run.results) == 1
    assert out["final_response"] and "private" not in out["final_response"]


@pytest.mark.asyncio
async def test_nonrecoverable_error_is_not_retried_and_has_general_text_fallback():
    model = SequenceModel(ValueError("private token"))
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model
        )
        .compile()
        .ainvoke(input_state("陪我聊聊"))
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.TASK_FAILED
    assert model.calls == 1 and run.usage[0].retries == 0
    assert (
        "我还在这里" in out["final_response"] and "private" not in out["final_response"]
    )


@pytest.mark.asyncio
async def test_budget_preflight_blocks_first_transfer_model_and_action():
    model = SequenceModel(call())
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=model,
            limits=RunLimits(agent_token_budget=100),
        )
        .compile()
        .ainvoke(input_state())
    )
    # Full-entry preflight now refuses before a business run can be created.
    assert out["handoff_failed"] and out["handoff_run"] is None
    assert out.get("task_results", []) == [] and model.calls == 0
    assert StopReason.TOKEN_LIMIT.value in {event["code"] for event in out["handoff_trace"]}
    assert all(row["model_calls"] == 0 for row in out["routing_usage"])
    assert not any(isinstance(message, ToolMessage) or getattr(message, "tool_calls", [])
                   for message in out["messages"])
    assert "预算已用完" in out["final_response"]


@pytest.mark.asyncio
async def test_actual_transfer_overshoot_cannot_dispatch_next_agent():
    total = RunLimits().agent_token_budget + 2
    response = call()
    response.usage_metadata = {
        "input_tokens": total - 1,
        "output_tokens": 1,
        "total_tokens": total,
    }
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=SequenceModel(response),
        )
        .compile()
        .ainvoke(input_state())
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.stop_reason is StopReason.TOKEN_LIMIT and run.handoff_count == 0
    assert run.total_tokens == total and run.results == ()


@pytest.mark.asyncio
async def test_guard_usage_overshoot_cannot_commit_preview_hop():
    guard_tokens = RunLimits().run_token_budget + 8000
    class ExpensiveGuard:
        async def review(self, envelope):
            await accounted_call(
                AsyncMock(return_value=answer(guard_tokens)), "guard", output_tokens=100
            )

    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=ToolModel(responses=[call()]),
            handoff_guard=ExpensiveGuard(),
        )
        .compile()
        .ainvoke(input_state())
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.stop_reason is StopReason.TOKEN_LIMIT and run.handoff_count == 0
    assert run.total_tokens == guard_tokens + 100 and run.results == ()
    observations = [item for item in out["messages"] if isinstance(item, ToolMessage)]
    assert len(observations) == 1 and observations[0].tool_call_id == "handoff-call"


@pytest.mark.asyncio
async def test_router_usage_is_retained_and_can_block_business_calls():
    total = RunLimits().agent_token_budget + 2
    class ExpensiveRouter(Router):
        async def route(self, *args):
            await accounted_call(
                AsyncMock(return_value=answer(total)), "route", output_tokens=100
            )
            return await super().route(*args)

    model = SequenceModel(call())
    out = (
        await build_handoff_graph(
            intent_router=ExpensiveRouter(RouteKey.TOOL_CALLING), transfer_model=model
        )
        .compile()
        .ainvoke(input_state())
    )
    assert out["handoff_failed"] and model.calls == 0
    assert out["routing_usage"][0]["reported_tokens"] == total
    assert "token_limit" in {row["code"] for row in out["handoff_trace"]}


@pytest.mark.asyncio
async def test_deadline_covers_guard_and_leaves_zero_hops_with_paired_call():
    class SlowGuard:
        async def review(self, envelope):
            await asyncio.sleep(2)

    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=ToolModel(responses=[call()]),
            handoff_guard=SlowGuard(),
            limits=RunLimits(agent_timeout_seconds=1, run_timeout_seconds=3),
        )
        .compile()
        .ainvoke(input_state())
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.stop_reason is StopReason.TIME_LIMIT and run.handoff_count == 0
    assert len([item for item in out["messages"] if isinstance(item, ToolMessage)]) == 1


@pytest.mark.asyncio
async def test_cancelled_model_request_is_accounted_and_stops_later_task():
    model = SequenceModel(asyncio.CancelledError())
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.GENERAL_CHAT, RouteKey.TOOL_CALLING),
            general_model=model,
        )
        .compile()
        .ainvoke(input_state("陪我聊聊；暂停一下"))
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.stop_reason is StopReason.CANCELLED and model.calls == 1
    assert len(run.results) == 1 and run.usage[0].estimated_tokens > 0
    assert active_ledger.get() is None


@pytest.mark.asyncio
async def test_post_write_model_retry_never_reexecutes_cloud_tool():
    writes = []

    @tool("create_playlist_tool", args_schema=CreatePlaylistArgs)
    async def create(name: str, privacy: int = 0) -> str:
        """受控创建歌单。"""
        writes.append((name, privacy))
        return CreatedPlaylistResult(
            playlist_id=31, name=name, status="created", message="创建成功"
        ).model_dump_json()

    model = SequenceModel(
        call(
            "create_playlist_tool", args={"name": "测试", "privacy": 0}, call_id="write"
        ),
        OSError("upstream"),
        answer(100),
    )
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=ToolModel(responses=[call()]),
            curator_model=model,
            tool_tools=[create],
        )
        .compile()
        .ainvoke(input_state("创建歌单测试"))
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.COMPLETED and model.calls == 3
    assert writes == [("测试", 0)] and run.usage[1].retries == 1


@pytest.mark.asyncio
async def test_trace_allowlist_and_hashed_call_ids_do_not_expose_private_data(caplog):
    private_id = "private-user-text-api-key"
    events = []
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call(call_id=private_id)]),
        trace_sink=events.append,
    ).compile()
    with caplog.at_level("INFO", logger="app.agent.handoff.trace"):
        out = await graph.ainvoke(input_state())
    assert events == out["handoff_trace"] and events
    assert len({row["trace_id"] for row in events}) == 1
    for row in events:
        HandoffTrace.model_validate(row)
        assert set(row) == set(HandoffTrace.model_fields)
    assert (
        private_id not in caplog.text and "private transfer summary" not in caplog.text
    )
    assert "private control reasoning" not in json.dumps(events)


@pytest.mark.asyncio
async def test_trace_sink_failure_does_not_interrupt_business_or_log_exception(caplog):
    def sink(event):
        raise ValueError("private credential")

    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.GENERAL_CHAT),
            general_model=SequenceModel(answer(100)),
            trace_sink=sink,
        )
        .compile()
        .ainvoke(input_state("陪我聊聊"))
    )
    assert (
        out["handoff_run"]["status"] == "completed"
        and "private credential" not in caplog.text
    )


@pytest.mark.asyncio
async def test_guard_retry_checkpoint_keeps_usage_retries_and_trace_on_resume():
    review, client = guard(0.1, 0.9)
    model = ToolModel(responses=[call(call_id="first"), call(call_id="second")])
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=model,
        handoff_guard=review,
    ).compile(
        checkpointer=create_memory_checkpointer(),
        interrupt_after=["general_chat_agent"],
    )
    config = {"configurable": {"thread_id": "phase4-resume"}}
    before = await graph.ainvoke(input_state(), config)
    snapshot = HandoffRun.model_validate(before["handoff_run"])
    assert (
        snapshot.usage[0].retries == 1
        and snapshot.total_tokens == 100
        and snapshot.handoff_count == 0
    )
    resumed = await graph.ainvoke(Command(resume=True), config)
    # The successful General node also interrupts once before Curator dispatch.
    if resumed["handoff_run"]["status"] == "running":
        resumed = await graph.ainvoke(Command(resume=True), config)
    run = HandoffRun.model_validate(resumed["handoff_run"])
    assert run.status is RunStatus.COMPLETED and run.usage[0].retries == 1
    assert (
        run.total_tokens == 200
        and run.handoff_count == 1
        and client.decide.await_count == 2
    )
    assert (
        len([row for row in resumed["handoff_trace"] if row["code"] == "model_call"])
        == 2
    )


@pytest.mark.asyncio
async def test_new_turn_clears_usage_retry_and_trace_with_new_run_id():
    model = SequenceModel(OSError(), answer(100), answer(100))
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.GENERAL_CHAT), general_model=model
    ).compile(checkpointer=create_memory_checkpointer())
    config = {"configurable": {"thread_id": "phase4-two-turns"}}
    first = await graph.ainvoke(input_state("陪我聊聊"), config)
    second = await graph.ainvoke(input_state("继续聊聊"), config)
    run = HandoffRun.model_validate(second["handoff_run"])
    assert run.total_tokens == 100 and run.usage[0].retries == 0
    assert first["handoff_run"]["run_id"] != run.run_id
    assert all(row["run_id"] == run.run_id for row in second["handoff_trace"])


@pytest.mark.asyncio
async def test_trace_usage_and_guard_metadata_stay_out_of_sse_public_protocol():
    review, _ = guard(1)
    graph = build_handoff_graph(
        intent_router=Router(RouteKey.TOOL_CALLING),
        transfer_model=ToolModel(responses=[call()]),
        handoff_guard=review,
    ).compile()
    chunks = [
        chunk.model_dump(mode="json")
        async for chunk in stream_handoff_assistant(
            message="暂停一下",
            thread_id="phase4-sse",
            routing_context=input_state()["routing_context"],
            graph=graph,
        )
    ]
    serialized = json.dumps(chunks, ensure_ascii=False)
    assert "client_action" in serialized
    for private in (
        "handoff_trace",
        "guard_score",
        "reported_tokens",
        "estimated_tokens",
        "candidate_summary",
    ):
        assert private not in serialized


@pytest.mark.asyncio
async def test_guard_redecision_keeps_original_node_deadline():
    class SlowGuard:
        calls = 0

        async def review(self, envelope):
            self.calls += 1
            await asyncio.sleep(0.6)
            return GuardVerdict(
                self.calls == 2,
                "accepted" if self.calls == 2 else "guard_rejected",
                1 if self.calls == 2 else 0,
            )

    model = ToolModel(responses=[call(call_id="first"), call(call_id="second")])
    out = (
        await build_handoff_graph(
            intent_router=Router(RouteKey.TOOL_CALLING),
            transfer_model=model,
            handoff_guard=SlowGuard(),
            limits=RunLimits(agent_timeout_seconds=1, run_timeout_seconds=4),
        )
        .compile()
        .ainvoke(input_state())
    )
    run = HandoffRun.model_validate(out["handoff_run"])
    assert run.status is RunStatus.FAILED and run.stop_reason is StopReason.TIME_LIMIT
    assert run.handoff_count == 0 and run.results == () and run.usage[0].retries == 1
