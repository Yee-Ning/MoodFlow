"""Structured-output runtime for source-local claim verification."""

from __future__ import annotations

import json
from typing import Protocol


class ClaimVerificationRuntime(Protocol):
    async def generate(
        self,
        payload: dict[str, object],
        *,
        instructions: str,
        schema: dict[str, object],
        schema_name: str,
    ) -> str: ...


class ExistingLLMClaimVerificationRuntime:
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
        )
        if response.status != "completed" or not response.output_text:
            detail = getattr(response, "incomplete_details", None)
            reason = getattr(detail, "reason", "unknown")
            raise RuntimeError(f"claim_verification_llm_incomplete:{reason}")
        return response.output_text
