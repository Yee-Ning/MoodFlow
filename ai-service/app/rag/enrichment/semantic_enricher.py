"""One-call structured semantic enrichment with retries and grounding checks."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

from pydantic import ValidationError

from .config import EnrichmentConfig
from .inheritance import EnrichmentContext
from .models import BaseChunk, SemanticEnrichmentOutput
from .normalizer import EnrichmentNormalizer, normalize_text
from .prompts import SEMANTIC_ENRICHMENT_PROMPT_V1


class SemanticEnrichmentError(RuntimeError):
    pass


class SemanticEnrichmentRuntime(Protocol):
    async def generate(
        self,
        payload: dict[str, object],
        *,
        instructions: str,
        schema: dict[str, object],
        schema_name: str,
    ) -> str: ...


class ExistingLLMSemanticRuntime:
    """Adapter over MoodFlow's existing Responses API structured-output client."""

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
                        payload,
                        ensure_ascii=False,
                        separators=(",", ":"),
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
            raise SemanticEnrichmentError(f"llm_incomplete:{reason}")
        return response.output_text


class SemanticEnricher:
    def __init__(
        self,
        config: EnrichmentConfig,
        *,
        runtime: SemanticEnrichmentRuntime | None = None,
        normalizer: EnrichmentNormalizer | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime or ExistingLLMSemanticRuntime()
        self.normalizer = normalizer or EnrichmentNormalizer()

    async def enrich(
        self, chunk: BaseChunk, context: EnrichmentContext
    ) -> SemanticEnrichmentOutput:
        payload = self._payload(chunk, context)
        last_error: Exception | None = None
        for attempt in range(self.config.retry_count + 1):
            instructions = SEMANTIC_ENRICHMENT_PROMPT_V1
            if attempt:
                instructions += (
                    "\nThe previous response failed schema or grounding validation. "
                    "Correct only the structure or unsupported fields; add no facts."
                )
            try:
                raw = await asyncio.wait_for(
                    self.runtime.generate(
                        payload,
                        instructions=instructions,
                        schema=SemanticEnrichmentOutput.model_json_schema(),
                        schema_name=f"music_metadata_enrichment_{self.config.prompt_version}",
                    ),
                    timeout=self.config.timeout_seconds,
                )
                value = SemanticEnrichmentOutput.model_validate_json(raw)
                value = self.normalizer.normalize_semantic(value)
                self._validate_grounding(value, context)
                return value
            except Exception as exc:
                last_error = exc
        code = self._error_code(last_error)
        raise SemanticEnrichmentError(code) from last_error

    @staticmethod
    def _payload(chunk: BaseChunk, context: EnrichmentContext) -> dict[str, object]:
        document_type = chunk.metadata.get("document_type")
        return {
            "chunk_content": chunk.content,
            "title": context.title,
            "section_path": chunk.section_path,
            "provided_metadata": {
                "language": context.language,
                "document_type": document_type,
                "source": context.source.model_dump(mode="json"),
                "topics": list(context.topics),
                "keywords": list(context.keywords),
                "inherited_primary_subject": (
                    context.primary_subject.model_dump(mode="json")
                    if context.primary_subject is not None
                    else None
                ),
                "inherited_entity_mentions": [
                    item.model_dump(mode="json") for item in context.entity_mentions
                ],
            },
        }

    @staticmethod
    def _validate_grounding(
        value: SemanticEnrichmentOutput, context: EnrichmentContext
    ) -> None:
        evidence = "\n".join(
            normalize_text(item).casefold() for item in context.evidence_texts
        )
        for mention in value.entity_mentions:
            if normalize_text(mention.text).casefold() not in evidence:
                raise SemanticEnrichmentError("ungrounded_entity_mention")
        if (
            value.primary_subject is not None
            and normalize_text(value.primary_subject.text).casefold() not in evidence
        ):
            raise SemanticEnrichmentError("ungrounded_primary_subject")

    @staticmethod
    def _error_code(error: Exception | None) -> str:
        if isinstance(error, asyncio.TimeoutError):
            return "semantic_enrichment_timeout"
        if isinstance(error, ValidationError):
            return "semantic_enrichment_invalid_output"
        if isinstance(error, SemanticEnrichmentError):
            return str(error)[:500]
        return "semantic_enrichment_request_failed"
