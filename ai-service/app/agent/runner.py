import logging
from typing import Any

from pydantic import ValidationError

from app.context.formatter import format_runtime_context
from app.llm.client import create_response
from app.schemas.chat import STRUCTURED_OUTPUT_ADAPTER, StructuredOutput
from app.schemas.context import ResolvedContext
from app.tools.executor import execute_tool

logger = logging.getLogger(__name__)

MAX_AGENT_STEPS = 5
MAX_STRUCTURED_OUTPUT_RETRIES = 1


def _get_item_value(item: Any, field: str) -> Any:
    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def _serialize_output_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item.copy()
    if hasattr(item, "model_dump"):
        return item.model_dump(mode="json", exclude_none=True)

    fields = (
        "type",
        "id",
        "status",
        "role",
        "content",
        "call_id",
        "name",
        "arguments",
    )
    return {
        field: value
        for field in fields
        if (value := getattr(item, field, None)) is not None
    }


def _ensure_completed(response: Any) -> None:
    if response.status == "completed":
        return

    reason = getattr(response.incomplete_details, "reason", None)
    logger.error(
        "LLM response did not complete: response_id=%s status=%s reason=%s",
        response.id,
        response.status,
        reason,
    )
    raise RuntimeError("LLM response did not complete")


async def run_agent(
    message: str,
    resolved_context: ResolvedContext | None = None,
) -> StructuredOutput:
    input_items: list[dict[str, Any]] = []
    if resolved_context is not None:
        runtime_context = format_runtime_context(resolved_context)
        if runtime_context is not None:
            input_items.append({"role": "system", "content": runtime_context})
    input_items.append({"role": "user", "content": message})
    structured_output_retries = 0

    for step in range(1, MAX_AGENT_STEPS + 1):
        logger.info("Agent step started: step=%d input_items=%d", step, len(input_items))
        response = await create_response(input_items)
        _ensure_completed(response)

        function_calls = [
            item
            for item in response.output
            if _get_item_value(item, "type") == "function_call"
        ]
        logger.info(
            "Agent step response: step=%d function_calls=%d",
            step,
            len(function_calls),
        )

        if not function_calls:
            raw = response.output_text
            if not raw or not raw.strip():
                logger.error(
                    "LLM returned empty final output: response_id=%s step=%d",
                    response.id,
                    step,
                )
                raise RuntimeError("LLM returned an empty response")

            try:
                structured = STRUCTURED_OUTPUT_ADAPTER.validate_json(raw)
            except ValidationError as exc:
                logger.warning(
                    "Final output failed Pydantic validation: response_id=%s "
                    "step=%d retry=%d errors=%s",
                    response.id,
                    step,
                    structured_output_retries,
                    exc,
                )
                if structured_output_retries < MAX_STRUCTURED_OUTPUT_RETRIES:
                    structured_output_retries += 1
                    input_items.extend(
                        _serialize_output_item(item) for item in response.output
                    )
                    input_items.append(
                        {
                            "role": "user",
                            "content": (
                                "Your previous final answer failed strict JSON "
                                "validation. Return exactly one single-line JSON "
                                "object matching the required schema. Escape all "
                                "control characters and do not call a tool again."
                            ),
                        }
                    )
                    continue

                logger.error(
                    "Structured output retry exhausted: response_id=%s step=%d",
                    response.id,
                    step,
                )
                raise RuntimeError("LLM output failed schema validation") from exc

            logger.info(
                "Agent completed: step=%d structured_output_type=%s",
                step,
                structured.type,
            )
            return structured

        input_items.extend(_serialize_output_item(item) for item in response.output)

        for function_call in function_calls:
            name = _get_item_value(function_call, "name")
            call_id = _get_item_value(function_call, "call_id")
            arguments = _get_item_value(function_call, "arguments")
            if not name or not call_id or arguments is None:
                logger.error(
                    "Malformed function_call: step=%d name=%s call_id=%s",
                    step,
                    name,
                    call_id,
                )
                raise RuntimeError("LLM returned a malformed function call")

            logger.info(
                "Function call received: step=%d name=%s call_id=%s arguments=%s",
                step,
                name,
                call_id,
                arguments,
            )
            tool_result = await execute_tool(name, arguments)
            logger.info(
                "Function call handled: step=%d name=%s call_id=%s success=%s",
                step,
                name,
                call_id,
                tool_result.success,
            )
            input_items.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": tool_result.model_dump_json(),
                }
            )

    logger.error("Agent max steps exceeded: max_steps=%d", MAX_AGENT_STEPS)
    raise RuntimeError("Agent max steps exceeded")
