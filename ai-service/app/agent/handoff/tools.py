"""Transfer tools return parent graph Commands; business tools never execute here."""

from collections.abc import Callable
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.types import Command

from app.agent.handoff.contracts import TransferArguments
from app.agent.handoff.context import latest_entities
from app.agent.handoff.messages import parse_transfer_call, transfer_message_pair
from app.agent.handoff.registry import AGENT_REGISTRY, AgentKey, transfer_tool_name
from app.agent.handoff.state import HandoffRun, StopReason, finish_run, request_handoff

TRANSFER_FAILURE = "本次任务转交未能完成，请稍后重试或将请求拆成更小的任务。"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def rejected_command(run: HandoffRun, message: AIMessage | None) -> Command:
    """Normalize malformed calls and pair each one, without running any tool."""
    messages = []
    if message is not None:
        calls, replies, seen = [], [], set()
        for index, call in enumerate(
            [*message.tool_calls, *message.invalid_tool_calls]
        ):
            call_id = call.get("id")
            if (
                not isinstance(call_id, str)
                or not call_id.strip()
                or len(call_id) > 128
                or call_id in seen
            ):
                digest = sha256(f"{run.run_id}:{index}".encode()).hexdigest()
                call_id = f"rejected:{digest}"
            seen.add(call_id)
            name = str(call.get("name") or "invalid_transfer")[:100]
            calls.append({"id": call_id, "name": name, "args": {}, "type": "tool_call"})
            identity = sha256(f"{run.run_id}:{index}:{call_id}".encode()).hexdigest()
            replies.append(
                ToolMessage(
                    id=f"handoff-rejected:{identity}",
                    name=name,
                    tool_call_id=call_id,
                    content='{"accepted":false,"code":"invalid_transfer"}',
                    status="error",
                )
            )
        if calls:
            messages = [
                AIMessage(
                    id=message.id or f"handoff-rejected-call:{run.run_id}",
                    content="",
                    tool_calls=calls,
                ),
                *replies,
            ]
    stopped = finish_run(run, TRANSFER_FAILURE, reason=StopReason.INVALID_TRANSFER)
    return Command(
        goto="final_response",
        update={
            "handoff_run": stopped.model_dump(mode="json"),
            "messages": messages,
            "transfer_envelope": None,
        },
    )


def create_transfer_tools(
    source: AgentKey,
    run: HandoffRun,
    *,
    message: AIMessage | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> list[BaseTool]:
    def create(target: AgentKey) -> BaseTool:
        @tool(transfer_tool_name(target), args_schema=TransferArguments)
        async def transfer(reason: str, context_summary: str) -> Any:
            """把下一待办的控制权交给目标 Agent；原始任务及授权由服务端保留。"""
            if message is None:
                raise RuntimeError("transfer execution requires its assistant call")
            try:
                request = parse_transfer_call(message, source)
                if request.target_agent != target or (
                    request.reason,
                    request.context_summary,
                ) != (reason, context_summary):
                    raise ValueError("transfer arguments disagree")
                updated, result = request_handoff(
                    run,
                    request,
                    source_agent=source,
                    tool_call_id=message.tool_calls[0]["id"],
                    now=clock(),
                    entities=latest_entities(run.results),
                )
                pair = transfer_message_pair(
                    message, result, source=source, run_id=run.run_id
                )
            except ValueError:
                return rejected_command(run, message)
            if not result.accepted:
                stop = {
                    "handoff_limit": StopReason.HANDOFF_LIMIT,
                    "token_limit": StopReason.TOKEN_LIMIT,
                    "run_timeout": StopReason.TIME_LIMIT,
                }.get(result.code.value, StopReason.INVALID_TRANSFER)
                updated = finish_run(run, TRANSFER_FAILURE, reason=stop)
            return Command(
                goto=AGENT_REGISTRY[target].node_name
                if result.accepted
                else "final_response",
                update={
                    "handoff_run": updated.model_dump(mode="json"),
                    "messages": list(pair),
                    "transfer_envelope": result.envelope.model_dump(mode="json")
                    if result.envelope
                    else None,
                },
            )

        return transfer

    return [
        create(target) for target in sorted(AGENT_REGISTRY[source].transfer_targets)
    ]


async def execute_transfer(
    message: AIMessage, run: HandoffRun, *, clock=utc_now
) -> Command:
    try:
        request = parse_transfer_call(message, run.current_agent)
        selected = next(
            item
            for item in create_transfer_tools(
                run.current_agent, run, message=message, clock=clock
            )
            if item.name == transfer_tool_name(request.target_agent)
        )
        return await selected.ainvoke(
            {"reason": request.reason, "context_summary": request.context_summary}
        )
    except (ValueError, StopIteration):
        return rejected_command(run, message)
