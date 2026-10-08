"""Foundation continuation preserves public intent, committed progress and budgets."""

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from app.agent.checkpointing import create_memory_checkpointer
from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.execution import ContinuityConflict
from app.agent.foundation.long_horizon.foundation_projection import FoundationProgressProjector
from app.agent.foundation.long_horizon.models import (
    ContinuityIdentity, HistoryEventType, HistoryPointer, PublicTaskResult, RolloverReason,
)
from app.agent.foundation.long_horizon.public_events import PublicCheckpoint, PublicDecision, PublicRoute
from app.agent.foundation.long_horizon.runner import invoke
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask, RoutingContext

GOAL = "建立十首录音室版本的学习歌单，先筛选候选，再等我确认最终名单"
CONCLUSION = "已筛选十首候选；现场版均已排除"
TODO = "确认最终名单"


@pytest.fixture
def material(tmp_path):
    store = SQLiteContinuityStore(tmp_path / "foundation.db")
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    session = coordinator.service.open_conversation("foundation-projection")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="first", run_id="foundation:one", graph_mode="foundation")
    return SimpleNamespace(store=store, coordinator=coordinator, session=session, identity=identity)


def commit(h, *, content=CONCLUSION, pending_status="needs_input"):
    history, writer = h.coordinator.history, h.coordinator.writer
    goal = history.public_text(h.identity, "goal", HistoryEventType.USER_MESSAGE, GOAL)
    writer.append_public_event(h.identity, "checkpoint", HistoryEventType.CHECKPOINT,
        PublicCheckpoint(checkpoint_id="checkpoint-one", run_id=h.identity.run_id, fence=1, revision=1))
    route = RouteDecision(is_complete=True, tasks=[RouteTask(task_id="search", route_key=RouteKey.GENERAL_CHAT, input_text="筛选候选"),
        RouteTask(task_id="confirm", route_key=RouteKey.GENERAL_CHAT, input_text=TODO)])
    writer.append_public_event(h.identity, "route", HistoryEventType.ROUTE_DECISION,
        PublicRoute(run_id=h.identity.run_id, tasks=(("search", "general_chat"), ("confirm", "general_chat")), needs_clarification=False))
    writer.task_result(h.identity, PublicTaskResult(run_id=h.identity.run_id, route_task_id="search",
        route_key="general_chat", status="success", content=content))
    writer.task_result(h.identity, PublicTaskResult(run_id=h.identity.run_id, route_task_id="confirm",
        route_key="general_chat", status=pending_status, content="请确认候选名单"))
    history.public_text(h.identity, "answer", HistoryEventType.ASSISTANT_MESSAGE, "筛选完成，待你确认")
    state = {"current_input": "继续刚才那个任务", "route_decision": route.model_dump(),
             "continuity_context": {"run_id": h.identity.run_id}}
    return state, goal


def project(h, state):
    seed = h.coordinator.draft(h.session, None, None, state.get("current_input"))
    return FoundationProgressProjector(h.coordinator.service).project(h.session, state, seed)


@pytest.mark.parametrize("pending_status", ["needs_input", "failed"])
def test_read_only_projection_keeps_original_goal_conclusion_and_pending(material, pending_status):
    h = material
    state, goal = commit(h, pending_status=pending_status)
    h.coordinator.writer.append_public_event(h.identity, "constraint", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="constraint", kind="constraint", text="不要现场版"))
    h.coordinator.writer.append_public_event(h.identity, "decision", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="decision", text="选录音室版本"))
    before = [row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=100)]
    draft = project(h, state)
    projection = h.coordinator.service.build_handoff(h.session.session_id, draft)
    missing = [name for name, present in {"goal": projection.goal == GOAL,
        "conclusion": CONCLUSION in projection.summary and CONCLUSION in projection.completed_steps,
        "pending": TODO in projection.pending_items and "继续待办 " + TODO in projection.next_actions,
        "constraints": "不要现场版" in projection.constraints,
        "decisions": "选录音室版本" in projection.decisions,
        "key_messages": HistoryPointer(kind="event", target_id=goal.event_id) in projection.key_message_refs}.items() if not present]
    assert missing == []
    assert projection.agent_progress.run_status == pending_status
    assert projection.agent_progress.last_agent is None and projection.agent_progress.recent_run_chain == ()
    assert projection.source_checkpoint_ref is None
    assert before == [row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=100)]
    assert projection.token_count == h.coordinator.service.counter.count(projection.model_dump_json(exclude_none=True)) <= 1200


def test_uncommitted_results_and_private_checkpoint_content_are_excluded(material):
    h = material
    state, _ = commit(h)
    ghost = h.identity.model_copy(update={"run_id": "foundation:uncommitted"})
    h.coordinator.writer.task_result(ghost, PublicTaskResult(run_id=ghost.run_id, route_task_id="ghost",
        route_key="general_chat", status="success", content="不应变成已完成结论"))
    state["task_results"] = [{"content": "private scratchpad must never project"}]
    draft = project(h, state)
    assert "不应变成已完成结论" not in draft.model_dump_json()
    assert "private scratchpad" not in draft.model_dump_json()


def test_saved_route_must_match_public_commit(material):
    state, _ = commit(material)
    state["route_decision"]["tasks"][0]["task_id"] = "fabricated-task"
    with pytest.raises(ValueError, match="disagrees"):
        project(material, state)
    state["continuity_context"]["run_id"] = "uncommitted"
    with pytest.raises(ValueError, match="lacks committed"):
        project(material, state)


def test_long_projection_uses_existing_priority_trimming_and_required_field_failure(material):
    h = material
    state, _ = commit(h, content="确定性结论" * 200)
    draft = project(h, state)
    assert h.coordinator.service.counter.count(draft.model_dump_json()) > 1200
    projection = h.coordinator.service.build_handoff(h.session.session_id, draft)
    assert projection.dropped_fields[0] == "summary"
    assert projection.goal == GOAL and projection.pending_items == [TODO]
    assert projection.token_count <= 1200
    assert projection.token_count == h.coordinator.service.counter.count(projection.model_dump_json(exclude_none=True))
    draft.goal = "不可丢失的任务约束" * 400
    with pytest.raises(ValueError, match="required handoff fields exceed token budget"):
        h.coordinator.service.build_handoff(h.session.session_id, draft)


def test_inherited_goal_and_progress_survive_second_continuation_window(material):
    h = material
    state, _ = commit(h)
    first = h.coordinator.service.rollover(h.session.session_id, project(h, state), RolloverReason.EXPLICIT_CHECKPOINT)
    h.session = first.new_session
    h.identity = h.identity.model_copy(update={"session_id": h.session.session_id, "run_id": "foundation:two", "request_id": "second"})
    h.coordinator.history.public_text(h.identity, "continue", HistoryEventType.USER_MESSAGE, "继续刚才那个任务")
    second = h.coordinator.service.build_handoff(h.session.session_id, project(h, {"current_input": "继续"}))
    assert second.goal == GOAL and CONCLUSION in second.completed_steps and CONCLUSION in second.summary
    assert second.pending_items == [TODO]
    assert second.next_actions == ["继续待办 " + TODO]


def test_earlier_unresolved_turn_survives_later_success_in_same_window(material):
    h = material
    state, _ = commit(h)
    second = h.identity.model_copy(update={"run_id": "foundation:second", "request_id": "second"})
    h.coordinator.history.public_text(second, "continue", HistoryEventType.USER_MESSAGE, "继续刚才那个任务")
    h.coordinator.writer.append_public_event(second, "second-checkpoint", HistoryEventType.CHECKPOINT,
        PublicCheckpoint(checkpoint_id="second-checkpoint", run_id=second.run_id, fence=1, revision=2))
    h.coordinator.writer.append_public_event(second, "second-route", HistoryEventType.ROUTE_DECISION,
        PublicRoute(run_id=second.run_id, tasks=(("followup", "general_chat"),), needs_clarification=False))
    h.coordinator.writer.task_result(second, PublicTaskResult(run_id=second.run_id, route_task_id="followup",
        route_key="general_chat", status="success", content="后续问题已回答"))
    state = {"current_input": "继续刚才那个任务", "continuity_context": {"run_id": second.run_id},
        "route_decision": RouteDecision(is_complete=True, tasks=[
            RouteTask(task_id="followup", route_key=RouteKey.GENERAL_CHAT, input_text="后续问题")])}
    draft = project(h, state)
    assert draft.goal == GOAL and CONCLUSION in draft.completed_steps
    assert "请确认候选名单" in draft.pending_items
    assert "继续待办 请确认候选名单" in draft.next_actions


def test_inherited_pending_is_removed_only_by_matching_committed_success(material):
    h = material
    state, _ = commit(h)
    rolled = h.coordinator.service.rollover(h.session.session_id, project(h, state), RolloverReason.EXPLICIT_CHECKPOINT)
    h.session = rolled.new_session
    h.identity = h.identity.model_copy(update={"session_id": h.session.session_id, "run_id": "foundation:resolved", "request_id": "resolved"})
    h.coordinator.writer.append_public_event(h.identity, "resolved-checkpoint", HistoryEventType.CHECKPOINT,
        PublicCheckpoint(checkpoint_id="resolved-checkpoint", run_id=h.identity.run_id, fence=1, revision=2))
    h.coordinator.writer.append_public_event(h.identity, "resolved-route", HistoryEventType.ROUTE_DECISION,
        PublicRoute(run_id=h.identity.run_id, tasks=(("confirmed", "general_chat"),), needs_clarification=False))
    h.coordinator.writer.task_result(h.identity, PublicTaskResult(run_id=h.identity.run_id, route_task_id="confirmed",
        route_key="general_chat", status="success", content="名单已确认"))
    route = RouteDecision(is_complete=True, tasks=[RouteTask(task_id="confirmed", route_key=RouteKey.GENERAL_CHAT, input_text=TODO)])
    draft = project(h, {"route_decision": route, "continuity_context": {"run_id": h.identity.run_id}})
    assert draft.goal == GOAL and CONCLUSION in draft.completed_steps
    assert draft.pending_items == [] and draft.next_actions == []
    assert "名单已确认" in draft.completed_steps


def test_clarification_without_dispatched_tasks_is_pending_not_completed(material):
    h = material
    h.coordinator.history.public_text(h.identity, "goal", HistoryEventType.USER_MESSAGE, GOAL)
    h.coordinator.writer.append_public_event(h.identity, "clarify-checkpoint", HistoryEventType.CHECKPOINT,
        PublicCheckpoint(checkpoint_id="clarify", run_id=h.identity.run_id, fence=1, revision=1))
    h.coordinator.writer.append_public_event(h.identity, "clarify-route", HistoryEventType.ROUTE_DECISION,
        PublicRoute(run_id=h.identity.run_id, tasks=(), needs_clarification=True))
    route = RouteDecision(needs_clarification=True, clarification_question="确认歌单用途")
    draft = project(h, {"route_decision": route, "continuity_context": {"run_id": h.identity.run_id}})
    assert draft.completed_steps == []
    assert draft.pending_items == ["确认歌单用途"]
    assert draft.agent_progress.run_status == "needs_input"


def test_original_task_input_can_be_safe_artifact_backed_and_other_task_is_not_selected(material):
    h = material
    other = h.coordinator.service.start_task("another-conversation")
    other_identity = h.identity.model_copy(update={"conversation_id": other.conversation_id,
        "continuity_task_id": other.task_id, "session_id": other.session_id})
    h.coordinator.history.public_text(other_identity, "other", HistoryEventType.USER_MESSAGE, "无关任务")
    original = "学习歌单" * 800
    h.coordinator.history.public_text(h.identity, "artifact-goal", HistoryEventType.USER_MESSAGE, original)
    draft = project(h, {"current_input": "继续"})
    assert draft.goal == original
    assert "无关任务" not in draft.model_dump_json()


@pytest.mark.asyncio
async def test_sqlite_pressure_still_does_not_roll_over_and_explicit_request_still_rejected(material):
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.subgraphs.general_chat import create_general_chat_subgraph
    h = material
    h.coordinator.service.policy = h.coordinator.service.policy.model_copy(update={"active_context_threshold": 1})
    async def answer(*args):
        return AIMessage(content="已提交回答")
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT],
        general_chat_subgraph=create_general_chat_subgraph(assistant=answer)).compile(checkpointer=create_memory_checkpointer())
    options = dict(thread_id=h.session.conversation_id, graph_mode="foundation", graph=graph,
        coordinator=h.coordinator, routing_context=RoutingContext())
    await invoke(message=GOAL, request_id="one", **options)
    await invoke(message="继续刚才那个任务", request_id="two", **options)
    assert len(h.store.sessions_for_task(h.session.task_id)) == 1
    with pytest.raises(ContinuityConflict, match="SQLite automatic rollover is disabled"):
        await invoke(message="继续", request_id="three", new_session=True, **options)
