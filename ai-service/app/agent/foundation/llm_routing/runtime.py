import json
from typing import Protocol

from app.agent.foundation.llm_routing.schemas import LLMRouteRequest, LLMStructuredOutput
from app.agent.structured_output import ModelOutput
from app.agent.structured_output.parser import model_output_from_response
from app.llm.client import create_routing_response


class LLMRoutingError(RuntimeError):
    """Provider refusal, incomplete response, or exhausted structural validation."""


class LLMIntentRuntime(Protocol):
    async def classify(
        self, request: LLMRouteRequest, *, instructions: str, retry: bool,
    ) -> str | ModelOutput: ...


class ProviderLLMIntentRuntime:
    async def classify(
        self, request: LLMRouteRequest, *, instructions: str, retry: bool,
    ) -> ModelOutput:
        if retry:
            instructions += "\n上次输出未通过结构/原文 span 校验。请重新核对格式和字符索引。"
        response = await create_routing_response(
            [{"role": "user", "content": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)}],
            instructions,
            LLMStructuredOutput.model_json_schema(),
        )
        return model_output_from_response(response)
