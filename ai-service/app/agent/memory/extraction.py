from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agent.structured_output import (
    ModelOutput,
    SemanticValidationError,
    StructuredOutputService,
)
from app.agent.structured_output.parser import model_output_from_response
from app.llm.client import create_structured_response

from .models import MemoryExtractionOutput
from .policy import MemoryAdmissionError, safe_user_message, validate_candidate

ExtractionGenerator = Callable[[int, str | None], Awaitable[str | ModelOutput]]

MEMORY_EXTRACTION_PROMPT = """你是 MoodFlow 的长期用户记忆提取器。输入只包含一条真实用户消息。

只提取跨 thread 仍有价值的用户知识：明确偏好/拒绝、稳定事实、多次行为形成的重要反馈，以及值得长期记住的具体历史体验。不要保存普通问句、一次性指令、助手内容、工具或 RAG 文档内容、推理过程。

关键边界：
- “以后不要给我推荐现场版”是 profile，category=format，subject=live_version，preference=avoid。
- “我喜欢周杰伦”是 profile，category=artist，subject=周杰伦，preference=like。
- “这首歌先跳过”“这次换一首”“暂时不听”是短期动作，memories 必须为空。
- 某次健身推荐中连续跳过慢歌，只有消息明确总结了这个长期反馈时才可成为 episodic。
- evidence_quote 必须逐字来自用户消息；content 是简短事实摘要。
- 只有用户明确陈述时使用 explicit；单条行为不得伪装成 repeated_behavior。
- 不确定时返回空 memories，不要猜测。

返回严格符合 JSON Schema 的单个对象，不要输出额外文本。"""

_TRANSIENT_MARKERS = (
    "先跳过",
    "这首先",
    "这次跳过",
    "暂时不听",
    "现在不听",
    "先换一首",
)
_PERSISTENT_MARKERS = ("以后", "今后", "总是", "一直", "从来", "长期", "不要再")


def is_transient_only(message: str) -> bool:
    normalized = "".join(message.split())
    return any(marker in normalized for marker in _TRANSIENT_MARKERS) and not any(
        marker in normalized for marker in _PERSISTENT_MARKERS
    )


class MemoryExtractor:
    """Strict structured extraction; no database is reachable from this class."""

    def __init__(self, *, max_attempts: int = 2) -> None:
        self.max_attempts = max_attempts

    async def extract(
        self,
        user_message: str,
        *,
        generator: ExtractionGenerator | None = None,
    ) -> MemoryExtractionOutput:
        if is_transient_only(user_message) or not safe_user_message(user_message):
            return MemoryExtractionOutput()

        def grounded(output: MemoryExtractionOutput) -> None:
            for memory in output.memories:
                try:
                    validate_candidate(memory, user_message=user_message)
                except MemoryAdmissionError as exc:
                    raise SemanticValidationError(str(exc)) from None
            if is_transient_only(user_message) and output.memories:
                raise SemanticValidationError("transient_action_is_not_long_term_memory")

        service = StructuredOutputService(
            "memory_extraction_v1",
            MemoryExtractionOutput,
            semantic_validator=grounded,
            max_attempts=self.max_attempts,
        )

        async def generate(attempt: int, feedback: str | None) -> ModelOutput:
            response = await create_structured_response(
                [
                    {
                        "role": "user",
                        "content": user_message
                        + (f"\n\nValidation feedback: {feedback}" if feedback else ""),
                    }
                ],
                MEMORY_EXTRACTION_PROMPT,
                MemoryExtractionOutput.model_json_schema(),
                schema_name="memory_extraction_v1",
            )
            return model_output_from_response(response)

        return await service.run(generator or generate)
