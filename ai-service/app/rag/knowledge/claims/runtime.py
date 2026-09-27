"""Structured-output runtime for claim extraction."""

from __future__ import annotations

import json
from typing import Protocol


class ClaimExtractionRuntime(Protocol):
    async def generate(
        self,
        payload: dict[str, object],
        *,
        instructions: str,
        schema: dict[str, object],
        schema_name: str,
    ) -> str: ...


class ExistingLLMClaimExtractionRuntime:
    def __init__(self, *, max_output_tokens: int | None = None) -> None:
        self.max_output_tokens = max_output_tokens

    async def generate(
        self,
        payload: dict[str, object],
        *,
        instructions: str,
        schema: dict[str, object],
        schema_name: str,
    ) -> str:
        from app.llm.client import create_structured_response

        response = await create_structured_response(
            [
                {
                    "role": "user",
                    "content": json.dumps(
                        payload, ensure_ascii=False, separators=(",", ":")
                    ),
                }
            ],
            instructions,
            schema,
            schema_name=schema_name,
            **({"max_output_tokens": self.max_output_tokens} if self.max_output_tokens is not None else {}),
        )
        if response.status != "completed" or not response.output_text:
            detail = getattr(response, "incomplete_details", None)
            reason = getattr(detail, "reason", "unknown")
            raise RuntimeError(f"claim_extraction_llm_incomplete:{reason}")
        return response.output_text
