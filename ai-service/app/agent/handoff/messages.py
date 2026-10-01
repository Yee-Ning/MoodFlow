"""Transfer messages stay in internal history, outside the public SSE protocol."""

import json
from copy import deepcopy
from hashlib import sha256

from langchain_core.messages import AIMessage, ToolMessage
from pydantic import TypeAdapter

from app.agent.handoff.contracts import (
    HandoffRequest,
    HandoffResult,
    Identifier,
    TransferArguments,
)
from app.agent.handoff.registry import AGENT_REGISTRY, AgentKey, transfer_tool_name


_CALL_ID_ADAPTER = TypeAdapter(Identifier)


def parse_transfer_call(message: AIMessage, source: AgentKey) -> HandoffRequest:
    """One control transfer per assistant turn; business calls cannot run beside it."""
    if len(message.tool_calls) != 1 or message.invalid_tool_calls:
        raise ValueError("a transfer requires exactly one valid tool call")
    call = message.tool_calls[0]
    _CALL_ID_ADAPTER.validate_python(call.get("id"))
    targets = {
        transfer_tool_name(target): target
        for target in AGENT_REGISTRY[source].transfer_targets
    }
    if call["name"] not in targets:
        raise ValueError("transfer tool is outside the source agent's allowlist")
    args = TransferArguments.model_validate(call["args"])
    return HandoffRequest(target_agent=targets[call["name"]], **args.model_dump())


def transfer_message_pair(
    message: AIMessage,
    result: HandoffResult,
    *,
    source: AgentKey,
    run_id: str,
) -> tuple[AIMessage, ToolMessage]:
    _CALL_ID_ADAPTER.validate_python(run_id)
    request = parse_transfer_call(message, source)
    call = message.tool_calls[0]
    if result.envelope is not None and (
        result.envelope.tool_call_id != call["id"]
        or result.envelope.run_id != run_id
        or result.envelope.source_agent != source
        or result.envelope.request != request
    ):
        raise ValueError("accepted context does not match its assistant tool call")
    # Preserve the call ID for add_messages; private reasoning is not transferred.
    identity = sha256(json.dumps([run_id, call["id"]]).encode("utf-8")).hexdigest()
    assistant = AIMessage(
        content="",
        id=message.id or f"handoff-call:{identity}",
        tool_calls=deepcopy(message.tool_calls),
    )
    observation = ToolMessage(
        id=f"handoff-result:{identity}",
        name=call["name"],
        tool_call_id=call["id"],
        content=json.dumps(
            {"accepted": result.accepted, "code": result.code.value}, ensure_ascii=False
        ),
    )
    return assistant, observation
