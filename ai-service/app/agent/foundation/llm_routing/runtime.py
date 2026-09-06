import json
from typing import Protocol

from app.agent.foundation.llm_routing.schemas import LLMRouteRequest, LLMStructuredOutput
from app.llm.client import create_routing_response


class LLMRoutingError(RuntimeError):
    """Provider refusal, incomplete response, or exhausted structural validation."""


class LLMIntentRuntime(Protocol):
    async def classify(
        self, request: LLMRouteRequest, *, instructions: str, retry: bool,
    ) -> str: ...


class ProviderLLMIntentRuntime:
    async def classify(
        self, request: LLMRouteRequest, *, instructions: str, retry: bool,
    ) -> str:
        if retry:
            instructions += "\n上次输出未通过结构/原文 span 校验。请重新核对格式和字符索引。"
        response = await create_routing_response(
            [{"role": "user", "content": json.dumps(request.model_dump(mode="json"), ensure_ascii=False)}],
            instructions,
            LLMStructuredOutput.model_json_schema(),
        )
        if response.status != "completed" or not response.output_text:
            raise LLMRoutingError("LLM routing returned no completed structured response")
        if any(item.type != "message" and item.type != "reasoning" for item in response.output):
            raise LLMRoutingError("LLM routing returned an unexpected output type")
        return response.output_text
