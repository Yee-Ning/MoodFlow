import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

from app.schemas.music import SearchMusicArgs
from app.tools.music import search_music

logger = logging.getLogger(__name__)


class ToolExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None


ToolFunction = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class RegisteredTool:
    args_model: type[BaseModel]
    function: ToolFunction


TOOL_REGISTRY: dict[str, RegisteredTool] = {
    "search_music": RegisteredTool(
        args_model=SearchMusicArgs,
        function=search_music,
    ),
}


async def execute_tool(name: str, arguments: str) -> ToolExecutionResult:
    """Validate model arguments and execute one registered Python tool."""
    started_at = perf_counter()
    registered = TOOL_REGISTRY.get(name)

    if registered is None:
        duration_ms = (perf_counter() - started_at) * 1000
        logger.error(
            "Unknown tool: name=%s success=false duration_ms=%.2f",
            name,
            duration_ms,
        )
        return ToolExecutionResult(
            success=False,
            error="unknown_tool",
            message=f"Unknown tool: {name}",
        )

    try:
        validated_args = registered.args_model.model_validate_json(arguments)
    except ValidationError as exc:
        duration_ms = (perf_counter() - started_at) * 1000
        validation_message = json.dumps(
            exc.errors(include_url=False, include_input=False),
            ensure_ascii=False,
        )
        logger.warning(
            "Invalid tool arguments: name=%s arguments=%s success=false "
            "duration_ms=%.2f errors=%s",
            name,
            arguments,
            duration_ms,
            validation_message,
        )
        return ToolExecutionResult(
            success=False,
            error="invalid_arguments",
            message=validation_message,
        )

    logger.info("Executing tool: name=%s arguments=%s", name, arguments)
    try:
        result = await registered.function(**validated_args.model_dump())
    except Exception:
        duration_ms = (perf_counter() - started_at) * 1000
        logger.exception(
            "Tool execution failed: name=%s success=false duration_ms=%.2f",
            name,
            duration_ms,
        )
        return ToolExecutionResult(
            success=False,
            error="tool_execution_failed",
            message="Tool execution failed or timed out",
        )

    data = result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    duration_ms = (perf_counter() - started_at) * 1000
    logger.info(
        "Tool execution completed: name=%s success=true duration_ms=%.2f",
        name,
        duration_ms,
    )
    return ToolExecutionResult(
        success=True,
        data=data,
        message="Tool executed successfully",
    )
