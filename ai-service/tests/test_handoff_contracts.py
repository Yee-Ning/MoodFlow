"""Executable acceptance cases for phase 1, without a live agent runtime."""

import json
from datetime import datetime, timedelta, timezone
from typing import get_type_hints

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.graph.message import add_messages
from pydantic import ValidationError

from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.response import foundation_response
from app.agent.foundation.routing import (
    IntentItem,
    IntentSource,
    IntentType,
    RouteDecision,
    RouteKey,
    RouteTask,
)
from app.agent.foundation.subgraphs.tool_calling.models import (
    ReferencedEntities,
    SongReference,
)
from app.agent.handoff.contracts import (
    HandoffCode,
    HandoffEnvelope,
    HandoffRequest,
    HandoffResult,
    HandoffTrace,
    TransferArguments,
)
from app.agent.handoff.messages import parse_transfer_call, transfer_message_pair
from app.agent.handoff.registry import (
    AGENT_REGISTRY,
    CAPABILITY_OWNERS,
    AgentKey,
    ToolDomain,
    bind_route_tasks,
    next_agent_for_task,
    task_tool_domains,
    transfer_tool_name,
)
from app.agent.handoff.state import (
    HandoffRun,
    HandoffState,
    RunLimits,
    RunStatus,
    StopReason,
    create_handoff_state,
    finish_run,
    new_run,
    record_task_result,
    request_handoff,
)


START = datetime(2026, 10, 1, tzinfo=timezone.utc)


def make_run(
    *routes: RouteKey,
    entry: AgentKey | None = None,
    limits: RunLimits | None = None,
    texts: list[str] | None = None,
) -> HandoffRun:
    clauses = texts or [f"任务 {index}" for index in range(len(routes))]
    original = "；".join(clauses)
    intents, tasks = [], []
    offset = 0
    for index, (route, clause) in enumerate(zip(routes, clauses, strict=True)):
        intents.append(
            IntentItem(
                intent=IntentType(route.value),
                confidence=1,
                source=IntentSource.RULE,
                evidence=clause,
                start=offset,
                end=offset + len(clause),
                order=index,
            )
        )
        tasks.append(
            RouteTask(
                task_id=f"task-{index}",
                route_key=route,
                input_text=clause,
                intent_orders=[index],
                depends_on=[f"task-{index - 1}"] if index else [],
            )
        )
        offset += len(clause) + 1
    return new_run(
        "run-1",
        original,
        RouteDecision(intents=intents, tasks=tasks, is_complete=True, coverage=1),
        started_at=START,
        entry_agent=entry,
        limits=limits,
    )


def result_for(run: HandoffRun, status=TaskStatus.SUCCESS, **kwargs) -> TaskResult:
    task = run.pending_tasks[0]
    return TaskResult(
        task_id=task.task_id, route_key=task.route_key, status=status, **kwargs
    )


def transfer(run: HandoffRun, target: AgentKey, call_id="call-1", **kwargs):
    return request_handoff(
        run,
        HandoffRequest(
            target_agent=target, reason="承接下一项任务", context_summary="保留原始请求"
        ),
        source_agent=run.current_agent,
        tool_call_id=call_id,
        now=START,
        **kwargs,
    )


def test_all_existing_capabilities_have_exactly_one_owner():
    assert set(CAPABILITY_OWNERS) == set(RouteKey)
    assert sum(len(spec.capabilities) for spec in AGENT_REGISTRY.values()) == len(
        RouteKey
    )
    assert CAPABILITY_OWNERS[RouteKey.CURRENT_SONG] is AgentKey.CURATOR
    assert CAPABILITY_OWNERS[RouteKey.TOOL_CALLING] is AgentKey.CURATOR
    assert CAPABILITY_OWNERS[RouteKey.WORLDLINE] is AgentKey.EXPLORER
    assert CAPABILITY_OWNERS[RouteKey.GENERAL_CHAT] is AgentKey.GENERAL_CHAT
    with pytest.raises(TypeError):
        AGENT_REGISTRY[AgentKey.CURATOR] = AGENT_REGISTRY[AgentKey.EXPLORER]


def test_capability_binding_preserves_repeated_tasks_dependencies_and_negations():
    run = make_run(
        RouteKey.TOOL_CALLING,
        RouteKey.WORLDLINE,
        RouteKey.TOOL_CALLING,
        texts=["搜索晴天，不要播放", "探索这首歌的时代", "然后播放第二首"],
    )
    assignments = bind_route_tasks(run.plan)
    assert [item.agent for item in assignments] == [
        AgentKey.CURATOR,
        AgentKey.EXPLORER,
        AgentKey.CURATOR,
    ]
    assert [item.task for item in assignments] == run.plan.tasks
    assert assignments[0].task.input_text == "搜索晴天，不要播放"
    assignments[1].task.depends_on.append("unrelated")
    assert run.plan.tasks[1].depends_on == ["task-0"]


@pytest.mark.parametrize(
    "agent,domains",
    [
        (AgentKey.GENERAL_CHAT, {ToolDomain.SONG_CONTEXT}),
        (
            AgentKey.CURATOR,
            {ToolDomain.SONG_CONTEXT, ToolDomain.MUSIC, ToolDomain.CLIENT_ACTION},
        ),
        (AgentKey.EXPLORER, {ToolDomain.SONG_CONTEXT, ToolDomain.WORLDLINE}),
    ],
)
def test_tool_domains_keep_mutations_in_curator(agent, domains):
    assert AGENT_REGISTRY[agent].tool_domains == domains


def test_tool_domain_sources_cover_existing_implementations():
    from app.agent.foundation.subgraphs.tool_calling.assistant import (
        all_tool_calling_tools,
    )
    from app.agent.foundation.subgraphs.tool_calling.client_tools import (
        CLIENT_ACTION_TOOLS,
    )
    from app.schemas.client_actions import ClientActionName
    from app.tools.music import MUSIC_AGENT_TOOLS

    assert {tool.name for tool in CLIENT_ACTION_TOOLS} == {
        item.value for item in ClientActionName
    }
    assert {tool.name for tool in all_tool_calling_tools()} == {
        tool.name for tool in [*MUSIC_AGENT_TOOLS, *CLIENT_ACTION_TOOLS]
    }
    assert len(MUSIC_AGENT_TOOLS) == 12


def test_only_the_four_planned_edges_are_exposed():
    edges = {
        (source, target)
        for source, spec in AGENT_REGISTRY.items()
        for target in spec.transfer_targets
    }
    assert edges == {
        (AgentKey.GENERAL_CHAT, AgentKey.CURATOR),
        (AgentKey.CURATOR, AgentKey.GENERAL_CHAT),
        (AgentKey.CURATOR, AgentKey.EXPLORER),
        (AgentKey.EXPLORER, AgentKey.CURATOR),
    }


@pytest.mark.parametrize("first", list(RouteKey))
@pytest.mark.parametrize("second", list(RouteKey))
def test_every_ordered_capability_pair_can_complete_with_only_allowed_edges(
    first, second
):
    run = make_run(first, second)
    run = record_task_result(run, result_for(run, content="首项完成"))
    hops = 0
    while run.current_agent != CAPABILITY_OWNERS[second]:
        target = next_agent_for_task(run.current_agent, second)
        assert target in AGENT_REGISTRY[run.current_agent].transfer_targets
        run, outcome = transfer(run, target, f"relay-{hops}")
        assert outcome.accepted and outcome.envelope.active_task.task_id == "task-1"
        if run.current_agent != CAPABILITY_OWNERS[second]:
            assert task_tool_domains(run.current_agent, second) == frozenset()
            with pytest.raises(ValueError, match="capability owner"):
                record_task_result(run, result_for(run, content="禁止中转执行"))
        hops += 1
        assert hops <= 2
    run = record_task_result(run, result_for(run, content="次项完成"))
    assert finish_run(run, "两项已完成").status is RunStatus.COMPLETED


def test_general_entry_can_reach_explorer_and_analysis_has_no_mutation_domain():
    run = make_run(RouteKey.WORLDLINE, entry=AgentKey.GENERAL_CHAT)
    run, _ = transfer(run, AgentKey.CURATOR, "relay")
    assert task_tool_domains(run.current_agent, RouteKey.WORLDLINE) == frozenset()
    run, outcome = transfer(run, AgentKey.EXPLORER, "owner")
    assert outcome.accepted and run.handoff_count == 2
    assert task_tool_domains(AgentKey.CURATOR, RouteKey.CURRENT_SONG) == frozenset(
        {ToolDomain.SONG_CONTEXT}
    )
    assert (
        task_tool_domains(AgentKey.CURATOR, RouteKey.TOOL_CALLING)
        == AGENT_REGISTRY[AgentKey.CURATOR].tool_domains
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"reason": " "},
        {"context_summary": "\n"},
        {"reason": "x" * 513},
        {"context_summary": "x" * 2_001},
        {"authorized_actions": ["play_song"]},
        {"session_cookie": "forbidden"},
        {"pending_tasks": []},
    ],
)
def test_model_cannot_add_authority_or_replace_server_context(updates):
    with pytest.raises(ValidationError):
        TransferArguments.model_validate(
            {"reason": "需要策展", "context_summary": "用户想听歌", **updates}
        )


def test_request_accepts_exact_summary_and_reason_bounds():
    args = TransferArguments(reason="x" * 512, context_summary="x" * 2_000)
    assert len(args.context_summary) == 2_000


def test_general_curator_explorer_curator_general_preserves_order_and_finalizes_once():
    run = make_run(
        RouteKey.TOOL_CALLING,
        RouteKey.WORLDLINE,
        RouteKey.TOOL_CALLING,
        RouteKey.GENERAL_CHAT,
        entry=AgentKey.GENERAL_CHAT,
    )
    for index, target in enumerate(
        [AgentKey.CURATOR, AgentKey.EXPLORER, AgentKey.CURATOR, AgentKey.GENERAL_CHAT]
    ):
        run, outcome = transfer(run, target, f"call-{index}")
        assert outcome.accepted
        assert outcome.envelope.active_task.task_id == f"task-{index}"
        assert len(outcome.envelope.completed_results) == index
        run = record_task_result(run, result_for(run, content=f"完成 {index}"))
    assert run.status is RunStatus.FINALIZING
    assert run.handoff_count == 4
    assert run.handoff_chain == (
        AgentKey.GENERAL_CHAT,
        AgentKey.CURATOR,
        AgentKey.EXPLORER,
        AgentKey.CURATOR,
        AgentKey.GENERAL_CHAT,
    )
    completed = finish_run(run, "已按顺序完成。")
    assert completed.status is RunStatus.COMPLETED
    assert [item.task_id for item in completed.results] == [
        f"task-{i}" for i in range(4)
    ]
    assert completed.pending_tasks == ()
    with pytest.raises(ValueError, match="finalized twice"):
        finish_run(completed, "重复终答")


def test_context_is_grounded_bounded_and_omits_arbitrary_result_data():
    run = make_run(
        RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, texts=["搜索，不要播放", "探索时代"]
    )
    run = record_task_result(
        run,
        result_for(
            run, content="公开结果" * 500, data={"private_scratchpad": "不要复制"}
        ),
    )
    entities = ReferencedEntities(
        songs=[SongReference(position=1, song_id=185809, name="晴天")]
    )
    updated, outcome = transfer(run, AgentKey.EXPLORER, entities=entities)
    payload = outcome.envelope.model_dump_json()
    assert "private_scratchpad" not in payload
    assert outcome.envelope.original_input == "搜索，不要播放；探索时代"
    assert outcome.envelope.active_task.depends_on == ["task-0"]
    assert len(outcome.envelope.completed_results[0].content) == 1_000
    assert outcome.envelope.entities.songs[0].song_id == 185809
    entities.songs.clear()
    assert len(outcome.envelope.entities.songs) == 1
    assert run.current_agent is AgentKey.CURATOR
    assert updated.current_agent is AgentKey.EXPLORER
    assert HandoffEnvelope.model_validate_json(payload) == outcome.envelope


def test_oversized_entities_are_rejected_before_any_state_change():
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    entities = ReferencedEntities(
        songs=[SongReference(position=1, song_id=1, name="歌", cover_url="x" * 66_000)]
    )
    with pytest.raises(ValidationError, match="64 KiB"):
        transfer(run, AgentKey.CURATOR, entities=entities)
    assert run.handoff_count == 0


@pytest.mark.parametrize(
    "routes,entry,target,code",
    [
        (
            (RouteKey.WORLDLINE,),
            AgentKey.GENERAL_CHAT,
            AgentKey.EXPLORER,
            HandoffCode.INVALID_EDGE,
        ),
        (
            (RouteKey.TOOL_CALLING,),
            AgentKey.CURATOR,
            AgentKey.EXPLORER,
            HandoffCode.WRONG_TARGET,
        ),
        (
            (RouteKey.TOOL_CALLING,),
            AgentKey.CURATOR,
            AgentKey.CURATOR,
            HandoffCode.INVALID_EDGE,
        ),
    ],
)
def test_denied_transfer_does_not_jump_or_increment(routes, entry, target, code):
    run = make_run(*routes, entry=entry)
    updated, outcome = transfer(run, target)
    assert updated is run
    assert outcome.code is code
    assert not outcome.accepted and outcome.envelope is None
    assert updated.handoff_count == 0


def test_stale_agent_duplicate_call_and_no_progress_reversal_are_rejected():
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    run, _ = transfer(run, AgentKey.CURATOR)
    updated, outcome = transfer(run, AgentKey.GENERAL_CHAT, "return")
    assert updated is run and outcome.code is HandoffCode.NO_PROGRESS
    _, duplicate = transfer(run, AgentKey.EXPLORER, "call-1")
    assert duplicate.code is HandoffCode.STALE_REQUEST
    _, stale = request_handoff(
        run,
        HandoffRequest(
            target_agent=AgentKey.CURATOR, reason="迟到", context_summary="旧请求"
        ),
        source_agent=AgentKey.GENERAL_CHAT,
        tool_call_id="late",
        now=START,
    )
    assert stale.code is HandoffCode.STALE_REQUEST


def test_fifth_hop_is_allowed_and_sixth_requires_bounded_final_response():
    run = make_run(
        *([RouteKey.TOOL_CALLING, RouteKey.WORLDLINE] * 3), entry=AgentKey.GENERAL_CHAT
    )
    for index in range(5):
        target = CAPABILITY_OWNERS[run.pending_tasks[0].route_key]
        run, outcome = transfer(run, target, f"call-{index}")
        assert outcome.accepted
        run = record_task_result(run, result_for(run, content="完成"))
    assert run.handoff_count == 5
    same, outcome = transfer(run, AgentKey.EXPLORER, "call-5")
    assert same is run and outcome.code is HandoffCode.HANDOFF_LIMIT
    stopped = finish_run(
        run, "已达到转交上限，剩余探索请另发请求。", reason=StopReason.HANDOFF_LIMIT
    )
    assert stopped.status is RunStatus.FAILED
    assert len(stopped.pending_tasks) == 1


@pytest.mark.parametrize(
    "offset,code",
    [
        (-1, HandoffCode.ACCEPTED),
        (0, HandoffCode.TOKEN_LIMIT),
        (1, HandoffCode.TOKEN_LIMIT),
    ],
)
def test_agent_budget_uses_observed_usage_including_overshoot(offset, code):
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    payload = run.model_dump()
    payload["usage"][0]["tokens"] = run.limits.agent_token_budget + offset
    run = HandoffRun.model_validate(payload)
    _, outcome = transfer(run, AgentKey.CURATOR)
    assert outcome.code is code


def test_total_budget_is_shared_across_agents():
    run = make_run(
        RouteKey.TOOL_CALLING,
        entry=AgentKey.GENERAL_CHAT,
        limits=RunLimits(agent_token_budget=10, run_token_budget=15),
    )
    payload = run.model_dump()
    for item in payload["usage"]:
        item["tokens"] = 5
    run = HandoffRun.model_validate(payload)
    _, outcome = transfer(run, AgentKey.CURATOR)
    assert outcome.code is HandoffCode.TOKEN_LIMIT


@pytest.mark.parametrize(
    "seconds,code",
    [
        (119.999, HandoffCode.ACCEPTED),
        (120, HandoffCode.RUN_TIMEOUT),
        (121, HandoffCode.RUN_TIMEOUT),
    ],
)
def test_deadline_boundary(seconds, code):
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    _, outcome = request_handoff(
        run,
        HandoffRequest(
            target_agent=AgentKey.CURATOR, reason="执行", context_summary="请求"
        ),
        source_agent=run.current_agent,
        tool_call_id="call",
        now=START + timedelta(seconds=seconds),
    )
    assert outcome.code is code


@pytest.mark.parametrize(
    "now", [START.replace(tzinfo=None), START - timedelta(seconds=1)]
)
def test_invalid_clock_is_rejected(now):
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    with pytest.raises(ValueError, match="transfer time"):
        request_handoff(
            run,
            HandoffRequest(
                target_agent=AgentKey.CURATOR, reason="执行", context_summary="请求"
            ),
            source_agent=run.current_agent,
            tool_call_id="call",
            now=now,
        )


@pytest.mark.parametrize(
    "status,kwargs,expected",
    [
        (TaskStatus.FAILED, {"error": "上游不可用"}, RunStatus.FINALIZING),
        (TaskStatus.NEEDS_INPUT, {"content": "你希望听哪首？"}, RunStatus.NEEDS_INPUT),
    ],
)
def test_failure_and_clarification_stop_following_tasks(status, kwargs, expected):
    run = make_run(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE)
    run = record_task_result(run, result_for(run, status, **kwargs))
    assert run.status is expected and len(run.results) == 1
    _, outcome = transfer(run, AgentKey.EXPLORER)
    assert outcome.code is HandoffCode.RUN_NOT_RUNNING
    with pytest.raises(ValueError, match="only running"):
        record_task_result(run, result_for(run, content="禁止继续"))
    if status is TaskStatus.FAILED:
        run = finish_run(run, "音乐服务暂时不可用，请稍后再试。")
        assert run.status is RunStatus.FAILED
    assert run.final_response


def test_router_clarification_does_not_dispatch_any_agent_task():
    plan = RouteDecision(
        needs_clarification=True, clarification_question="你希望播放还是探索？"
    )
    run = new_run("clarify", "帮我处理一下", plan, started_at=START)
    assert run.status is RunStatus.NEEDS_INPUT
    assert run.current_agent is AgentKey.GENERAL_CHAT
    assert run.handoff_count == 0
    assert run.final_response == plan.clarification_question


@pytest.mark.parametrize(
    "kind", ["wrong_agent", "wrong_id", "wrong_route", "later_task"]
)
def test_results_cannot_bypass_capability_order_or_dependencies(kind):
    run = make_run(
        RouteKey.TOOL_CALLING,
        RouteKey.WORLDLINE,
        entry=AgentKey.GENERAL_CHAT if kind == "wrong_agent" else None,
    )
    result = result_for(run, content="完成")
    if kind == "wrong_id":
        result = result.model_copy(update={"task_id": "other"})
    elif kind == "wrong_route":
        result = result.model_copy(update={"route_key": RouteKey.CURRENT_SONG})
    elif kind == "later_task":
        result = TaskResult(
            task_id="task-1", route_key=RouteKey.WORLDLINE, status=TaskStatus.SUCCESS
        )
    with pytest.raises(ValueError):
        record_task_result(run, result)
    assert run.results == ()


def test_final_response_cannot_claim_unfinished_tasks_succeeded():
    run = make_run(RouteKey.TOOL_CALLING, RouteKey.WORLDLINE)
    with pytest.raises(ValueError):
        finish_run(run, "已完成")
    with pytest.raises(ValidationError, match="every planned task"):
        finish_run(run, "已完成", reason=StopReason.ALL_TASKS_DONE)
    stopped = finish_run(run, "本次执行已停止。", reason=StopReason.CANCELLED)
    assert stopped.terminal and stopped.status is RunStatus.FAILED


def test_public_response_still_uses_old_capability_names_and_task_ids():
    run = make_run(RouteKey.CURRENT_SONG, RouteKey.TOOL_CALLING, RouteKey.GENERAL_CHAT)
    for index in range(2):
        run = record_task_result(run, result_for(run, content=f"完成 {index}"))
    run, _ = transfer(run, AgentKey.GENERAL_CHAT)
    run = record_task_result(run, result_for(run, content="陪伴回复"))
    run = finish_run(run, "完成")
    public = foundation_response(run.final_response, list(run.results)).model_dump(
        mode="json"
    )
    assert [item["subgraph"] for item in public["results"]] == [
        "current_song",
        "tool_calling",
        "general_chat",
    ]
    assert [item["task_id"] for item in public["results"]] == [
        "task-0",
        "task-1",
        "task-2",
    ]


def test_resume_preserves_counters_usage_results_and_original_deadline():
    run = make_run(
        RouteKey.TOOL_CALLING, RouteKey.WORLDLINE, entry=AgentKey.GENERAL_CHAT
    )
    run, _ = transfer(run, AgentKey.CURATOR)
    run = record_task_result(run, result_for(run, content="已搜索"))
    payload = run.model_dump()
    payload["usage"][1].update(tokens=42, retries=1)
    run = HandoffRun.model_validate(payload)
    restored = HandoffRun.model_validate_json(run.model_dump_json())
    assert restored == run
    assert restored.handoff_count == 1 and restored.total_tokens == 42
    assert (
        restored.started_at == START and restored.pending_tasks[0].task_id == "task-1"
    )
    resumed, outcome = transfer(restored, AgentKey.EXPLORER, "call-2")
    assert outcome.accepted and resumed.handoff_count == 2
    assert resumed.usage[1].retries == 1


@pytest.mark.parametrize(
    "change",
    ["version", "counter", "chain", "result", "usage", "retry", "final", "evidence"],
)
def test_inconsistent_or_unknown_checkpoints_fail_closed(change):
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    run, _ = transfer(run, AgentKey.CURATOR)
    payload = run.model_dump()
    if change == "version":
        payload["schema_version"] = 2
    elif change == "counter":
        payload["handoff_count"] = 0
    elif change == "chain":
        payload["current_agent"] = AgentKey.GENERAL_CHAT
    elif change == "result":
        payload["results"] = [
            TaskResult(
                task_id="other", route_key="tool_calling", status=TaskStatus.SUCCESS
            ).model_dump()
        ]
    elif change == "usage":
        payload["usage"] = [payload["usage"][0]] * 3
    elif change == "retry":
        payload["usage"][0]["retries"] = 2
    elif change == "final":
        payload["final_response"] = "假终答"
    elif change == "evidence":
        payload["original_input"] = "替换授权请求"
    with pytest.raises(ValidationError):
        HandoffRun.model_validate(payload)


def test_new_turn_resets_task_state_but_preserves_session_messages_and_reducer():
    previous = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    previous, _ = transfer(previous, AgentKey.CURATOR)
    previous = finish_run(previous, "已停止", reason=StopReason.CANCELLED)
    fresh = make_run(RouteKey.GENERAL_CHAT)
    history = [HumanMessage(content="之前的请求", id="history")]
    state = create_handoff_state(fresh, messages=history)
    assert state["handoff_run"].handoff_count == 0
    assert state["handoff_run"].results == () and state["handoff_run"].total_tokens == 0
    assert state["handoff_run"].final_response is None
    history.clear()
    assert state["messages"][0].id == "history"
    assert get_type_hints(HandoffState, include_extras=True)[
        "messages"
    ].__metadata__ == (add_messages,)


def call_message(target=AgentKey.CURATOR):
    return AIMessage(
        content="本地推理不复制",
        id="assistant-call",
        tool_calls=[
            {
                "id": "call-1",
                "name": transfer_tool_name(target),
                "args": {"reason": "承接下一项任务", "context_summary": "保留原始请求"},
                "type": "tool_call",
            }
        ],
    )


@pytest.mark.parametrize("accepted", [True, False])
def test_accepted_and_denied_transfer_messages_are_paired_and_idempotent(accepted):
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    message = call_message()
    if accepted:
        _, outcome = transfer(run, AgentKey.CURATOR)
    else:
        outcome = HandoffResult(code=HandoffCode.TOKEN_LIMIT)
    assistant, tool = transfer_message_pair(
        message, outcome, source=AgentKey.GENERAL_CHAT, run_id=run.run_id
    )
    assert assistant.content == ""
    assert tool.tool_call_id == assistant.tool_calls[0]["id"] == "call-1"
    assert json.loads(tool.content)["accepted"] is accepted
    assert "original_input" not in tool.content
    first = add_messages([], [assistant, tool])
    replay = transfer_message_pair(
        message, outcome, source=AgentKey.GENERAL_CHAT, run_id=run.run_id
    )
    assert len(add_messages(first, list(replay))) == 2


@pytest.mark.parametrize(
    "change",
    [
        "parallel",
        "missing_id",
        "long_id",
        "unknown",
        "authority",
        "invalid",
        "wrong_source",
    ],
)
def test_malformed_or_parallel_transfer_calls_do_not_emit_dangling_messages(change):
    message = call_message()
    if change == "parallel":
        message.tool_calls.append(
            {"id": "play", "name": "play_song", "args": {}, "type": "tool_call"}
        )
    elif change == "missing_id":
        message.tool_calls[0]["id"] = " "
    elif change == "long_id":
        message.tool_calls[0]["id"] = "x" * 129
    elif change == "unknown":
        message.tool_calls[0]["name"] = "transfer_to_unknown_agent"
    elif change == "authority":
        message.tool_calls[0]["args"]["authorized_actions"] = ["play_song"]
    elif change == "invalid":
        message.invalid_tool_calls.append(
            {
                "name": "transfer",
                "args": "bad",
                "id": "bad",
                "error": "bad",
                "type": "invalid_tool_call",
            }
        )
    with pytest.raises(ValueError):
        parse_transfer_call(
            message,
            AgentKey.CURATOR if change == "wrong_source" else AgentKey.GENERAL_CHAT,
        )


def test_explorer_can_request_curator_through_its_allowed_tool():
    request = parse_transfer_call(call_message(), AgentKey.EXPLORER)
    assert request.target_agent is AgentKey.CURATOR


def test_message_pair_rejects_context_from_another_call():
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    _, outcome = transfer(run, AgentKey.CURATOR, "other-call")
    with pytest.raises(ValueError, match="does not match"):
        transfer_message_pair(
            call_message(), outcome, source=AgentKey.GENERAL_CHAT, run_id=run.run_id
        )


def test_message_pair_does_not_accept_context_from_another_run():
    run = make_run(RouteKey.TOOL_CALLING, entry=AgentKey.GENERAL_CHAT)
    _, outcome = transfer(run, AgentKey.CURATOR)
    with pytest.raises(ValueError, match="does not match"):
        transfer_message_pair(
            call_message(), outcome, source=AgentKey.GENERAL_CHAT, run_id="other-run"
        )


def test_denied_message_replay_is_stable_even_without_an_assistant_id():
    message = call_message().model_copy(update={"id": None})
    outcome = HandoffResult(code=HandoffCode.TOKEN_LIMIT)
    first = transfer_message_pair(
        message, outcome, source=AgentKey.GENERAL_CHAT, run_id="run"
    )
    replay = transfer_message_pair(
        message, outcome, source=AgentKey.GENERAL_CHAT, run_id="run"
    )
    assert len(add_messages(list(first), list(replay))) == 2


def test_task_without_intent_evidence_cannot_strip_a_user_negation():
    plan = RouteDecision(
        is_complete=True,
        tasks=[
            RouteTask(
                task_id="task",
                route_key=RouteKey.TOOL_CALLING,
                input_text="播放这首歌",
            )
        ],
    )
    with pytest.raises(ValidationError, match="full user input"):
        new_run("run", "不要播放这首歌", plan, started_at=START)
    run = new_run("run", "播放这首歌", plan, started_at=START)
    plan.tasks.clear()
    assert len(run.plan.tasks) == 1


def test_valid_trace_serializes_only_allowlisted_metadata():
    trace = HandoffTrace(
        trace_id="trace",
        run_id="run",
        tool_call_id="call",
        source_agent=AgentKey.CURATOR,
        target_agent=AgentKey.EXPLORER,
        code=HandoffCode.ACCEPTED,
        guard_score=0.8,
        duration_ms=15,
        tokens=10,
    )
    assert HandoffTrace.model_validate_json(trace.model_dump_json()) == trace


def test_legacy_foundation_checkpoint_is_not_implicitly_migrated():
    with pytest.raises(ValidationError):
        HandoffRun.model_validate(
            {"current_input": "旧请求", "messages": [], "task_results": []}
        )


@pytest.mark.parametrize(
    "update",
    [
        {"guard_score": 1.01},
        {"tokens": -1},
        {"original_input": "私密请求"},
        {"session_cookie": "禁止"},
    ],
)
def test_trace_rejects_private_payload_and_invalid_metrics(update):
    with pytest.raises(ValidationError):
        HandoffTrace.model_validate(
            {
                "trace_id": "trace",
                "run_id": "run",
                "tool_call_id": "call",
                "source_agent": AgentKey.GENERAL_CHAT,
                "target_agent": AgentKey.CURATOR,
                "code": HandoffCode.ACCEPTED,
                "duration_ms": 12,
                "tokens": 10,
                **update,
            }
        )


@pytest.mark.parametrize(
    "limits",
    [
        {"max_handoffs": 6},
        {"max_retries_per_agent": 2},
        {"agent_timeout_seconds": 121},
        {"agent_token_budget": RunLimits().run_token_budget + 1},
        {"run_token_budget": 0},
        {"max_handoffs": True},
    ],
)
def test_limit_contract_rejects_unbounded_or_inconsistent_settings(limits):
    with pytest.raises(ValidationError):
        RunLimits(**limits)
