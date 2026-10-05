import json
from typing import Protocol

from app.agent.foundation.llm_routing.schemas import LLMRouteRequest, LLMStructuredOutput
from app.agent.foundation.model_input import prepare_model_input
from app.agent.foundation.routing import RouteKey
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
        schema = LLMStructuredOutput.model_json_schema()
        from app.agent.foundation.entry_context import active_triage_pack
        from app.agent.foundation.context_engineering import ContextAssembler
        pack = active_triage_pack.get()
        triage_items = []
        if pack is not None:
            # Only already truncated lite material can enter the classification call.
            triage_items = ContextAssembler().triage_items(pack)
        model_input = await prepare_model_input(
            RouteKey.GENERAL_CHAT, system=instructions,
            query=json.dumps(request.model_dump(mode="json"), ensure_ascii=False),
            schema=schema, schema_name="intent_routing",
            triage_items=triage_items,
        )
        response = await create_routing_response(
            model_input.input_items, model_input.instructions, schema, request_budget=model_input.budget,
        )
        return model_output_from_response(response)
