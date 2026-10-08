"""Default entry → real Hybrid → handoff execution, with controlled workers."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
import pytest_asyncio
from langchain_core.messages import AIMessage
from langgraph.graph import END, START, StateGraph

from app.agent.foundation.hybrid_routing import HybridIntentRouter
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import IntentType, RouteKey, RoutingContext
from app.agent.foundation.subgraphs.base import SubGraphOutput
from app.agent.foundation.subgraphs.general_chat import GeneralChatState
from app.agent.foundation.subgraphs.worldline.state import create_worldline_state
from app.agent.handoff.graph import build_handoff_graph
from app.agent.handoff.routing import HybridFallback, plan_signature
from tests.test_handoff_runtime import Router, ToolModel, call, input_state
from tests.test_handoff_network import worldline
from tests.test_hybrid_graph import successful_subgraph
from tests.test_intent_merger import model_result


@pytest_asyncio.fixture
async def worldline_worker():
    # Reuse a real, schema-valid Worldline result for the controlled worker.
    # Entity interpretation is independent of the entry span contract.
    result = await worldline().ainvoke(create_worldline_state(
        "探索歌手《周杰伦》的世界线", "worldline-fixture",
    ))
    assert result["task_result"].status is TaskStatus.SUCCESS

    def complete(state):
        return {"task_result": TaskResult(
            task_id=state["task_id"], route_key=RouteKey.WORLDLINE,
            status=TaskStatus.SUCCESS, content="探索完成。",
            data=result["task_result"].data,
        )}

    graph = StateGraph(GeneralChatState, output_schema=SubGraphOutput)
    graph.add_node("complete", complete)
    graph.add_edge(START, "complete")
    graph.add_edge("complete", END)
    return graph.compile()


@pytest.mark.asyncio
@pytest.mark.parametrize("separator", [
    "，并", ",并", "，并且", "；然后", "，接着", "，随后", "，顺便",
    "，另外", "，同时", "，以及", "；最后", "，再", "，", "；", "！并",
    "， 并  ", "；\n然后\t",
])
@pytest.mark.parametrize("third_task", [False, True])
async def test_default_entry_preserves_hybrid_plan_and_executes_every_task(
    monkeypatch, separator, third_task, worldline_worker,
):
    text = "播放周杰伦的晴天" + separator + "探索周杰伦的世界线"
    routes = [RouteKey.TOOL_CALLING, RouteKey.WORLDLINE]
    if third_task:
        text += "；然后陪我聊聊"
        routes.append(RouteKey.GENERAL_CHAT)
    # Only the classifier result is controlled. Hybrid's rules, exact spans,
    # intent merging and task building, and the default entry remain real.
    model = Mock(side_effect=lambda value: model_result(value, (IntentType.TOOL_CALLING,)))
    llm = AsyncMock()
    hybrid = HybridIntentRouter(SimpleNamespace(route=model), llm)
    expected = await hybrid.route(text, RoutingContext())
    factory = Mock(return_value=hybrid)
    monkeypatch.setattr("app.agent.handoff.graph.create_hybrid_router", factory)
    transfers = [call(), call("transfer_to_explorer_agent", call_id="explore")]
    if third_task:
        transfers.extend([
            call("transfer_to_curator_agent", call_id="relay"),
            call("transfer_to_general_chat_agent", call_id="chat"),
        ])
    out = await build_handoff_graph(
        transfer_model=ToolModel(responses=transfers),
        general_model=ToolModel(responses=[AIMessage(content="已完成。")]),
        subgraphs={route: worldline_worker if route is RouteKey.WORLDLINE
                   else successful_subgraph(route) for route in routes},
    ).compile().ainvoke(input_state(text))
    run = out["handoff_run"]
    assert run["status"] == "completed", (
        run["stop_reason"],
        [(item["route_key"], item["status"], item.get("error")) for item in run["results"]],
    )
    assert not out["handoff_failed"] and not out["handoff_needs_input"]
    assert plan_signature(out["route_decision"]) == plan_signature(expected)
    assert [item["route_key"] for item in run["plan"]["tasks"]] == routes
    assert [item["task_id"] for item in run["results"]] == [
        task["task_id"] for task in run["plan"]["tasks"]
    ]
    assert all(item["status"] == "success" for item in run["results"])
    for intent in run["plan"]["intents"]:
        assert text[intent["start"]:intent["end"]] == intent["evidence"]
    factory.assert_called_once()
    assert model.call_count > 0
    llm.route.assert_not_awaited()
    if separator == "，并":
        assert run["plan"]["intents"][1]["start"] == 9
        assert run["plan"]["intents"][1]["end"] == 19


@pytest.mark.asyncio
@pytest.mark.parametrize("text,start,end,wrong_evidence", [
    ("请勿暂停播放，然后陪我聊聊", 0, 13, False),
    ("暂停播放，并探索周杰伦的世界线", 0, 15, False),
    ("暂停播放，并探索周杰伦的世界线", 5, 6, False),  # connector alone
    ("暂停播放，并探索周杰伦的世界线", 6, 15, True),
    ("暂停播放，并探索周杰伦的世界线；陪我聊聊", 5, 20, False),
    ("请勿暂停播放，并探索周杰伦的世界线", 0, 17, False),
])
async def test_prefix_tolerance_still_rejects_ungrounded_or_excluded_spans(
    text, start, end, wrong_evidence,
):
    # Build a schema-valid plan: evidence/task text must agree even when the
    # alleged evidence has been forged or crosses preflight's clause boundary.
    original = await Router(RouteKey.WORLDLINE).route("占位", RoutingContext())
    data = original.model_dump()
    evidence = "伪造证据" if wrong_evidence else text[start:end]
    data["intents"][0].update(start=start, end=end, evidence=evidence)
    data["tasks"][0]["input_text"] = evidence
    baseline = AsyncMock()
    baseline.route.return_value = original.model_validate(data)
    plan = await HybridFallback(baseline).route(text, RoutingContext())
    assert plan.needs_clarification and not plan.tasks
    baseline.route.assert_awaited_once_with(text, RoutingContext())


@pytest.mark.asyncio
async def test_valid_connector_after_excluded_action_does_not_restore_that_action():
    text = "请勿暂停播放，并探索周杰伦的世界线"
    start = text.index("并")
    original = await Router(RouteKey.WORLDLINE).route(text, RoutingContext())
    data = original.model_dump()
    data["intents"][0].update(start=start, evidence=text[start:])
    data["tasks"][0]["input_text"] = text[start:]
    baseline = AsyncMock()
    baseline.route.return_value = original.model_validate(data)
    plan = await HybridFallback(baseline).route(text, RoutingContext())
    assert plan.is_complete and len(plan.tasks) == 1
    assert plan.tasks[0].input_text == text[start:]
    assert "请勿" not in plan.tasks[0].input_text
