"""Rule-first query-type analysis with one optional structured LLM fallback."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field

from app.agent.structured_output import StructuredOutputService
from app.agent.structured_output.parser import model_output_from_response
from app.rag.knowledge.structured import strict_model_schema
from app.rag.query_understanding.config import QueryUnderstandingConfig
from app.rag.query_understanding.models import QueryType


QUERY_ANALYSIS_PROMPT_V1 = """You classify an already context-completed music knowledge query.
Return only the retrieval query types supported by the schema. Do not add entities,
years, facts, constraints, or rewritten text. Use comparison for explicit comparisons,
timeline for chronology or change over time, relationship for creator/collaboration
relations, influence_style for musical style or influence, fact for narrow factual
questions, and exploratory for broad background exploration."""


class QueryTypeOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query_types: list[QueryType] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)


class QueryAnalysisRuntime(Protocol):
    async def analyze(self, query: str) -> QueryTypeOutput: ...


class ExistingLLMQueryAnalysisRuntime:
    async def analyze(self, query: str) -> QueryTypeOutput:
        from app.llm.client import create_structured_response

        payload = [
            {
                "role": "user",
                "content": json.dumps(
                    {"query": query},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            }
        ]

        async def generate(attempt: int, feedback: str | None):
            instructions = QUERY_ANALYSIS_PROMPT_V1
            if feedback:
                instructions += f"\n{feedback}"
            response = await create_structured_response(
                payload,
                instructions,
                strict_model_schema(QueryTypeOutput),
                schema_name="query_type_analysis_v1",
            )
            return model_output_from_response(response)

        return await StructuredOutputService(
            "query_type_analysis_v1", QueryTypeOutput,
        ).run(generate)


@dataclass(frozen=True, slots=True)
class AnalyzedQueryTypes:
    query_types: list[QueryType]
    confidence: float
    warnings: list[str]


_TYPE_RULES: tuple[tuple[QueryType, re.Pattern[str]], ...] = (
    (
        QueryType.COMPARISON,
        re.compile(r"比较|對比|对比|区别|差异|异同|異同|相比"),
    ),
    (
        QueryType.TIMELINE,
        re.compile(
            r"时间线|時間線|历程|歷程|阶段|階段|演变|演變|"
            r"\d{4}\s*年?\s*(?:到|至|[-—–~～])\s*\d{4}"
        ),
    ),
    (
        QueryType.RELATIONSHIP,
        re.compile(r"合作|关系|關係|作词|作詞|作曲|编曲|編曲|制作人|製作人|演唱"),
    ),
    (
        QueryType.INFLUENCE_STYLE,
        re.compile(r"风格|風格|曲风|曲風|影响|影響|融合|创新|創新"),
    ),
    (QueryType.FACT, re.compile(r"谁|誰|哪年|何时|何時|什么|什麼|是否|多少|哪张|哪張")),
    (
        QueryType.EXPLORATORY,
        re.compile(r"背景|故事|介绍|介紹|世界线|世界線|探索|讲讲|談談"),
    ),
)


class QueryAnalyzer:
    def __init__(
        self,
        config: QueryUnderstandingConfig,
        *,
        runtime: QueryAnalysisRuntime | None = None,
    ) -> None:
        self.config = config
        self.runtime = runtime or ExistingLLMQueryAnalysisRuntime()

    async def analyze(self, query: str) -> AnalyzedQueryTypes:
        detected = [
            query_type
            for query_type, pattern in _TYPE_RULES
            if pattern.search(query)
        ]
        detected = list(dict.fromkeys(detected))[:3]
        if detected:
            return AnalyzedQueryTypes(detected, 0.94, [])
        if self.config.llm_fallback_enabled:
            try:
                output = await self.runtime.analyze(query)
                return AnalyzedQueryTypes(
                    list(dict.fromkeys(output.query_types))[:3],
                    output.confidence,
                    [],
                )
            except Exception:
                return AnalyzedQueryTypes(
                    [QueryType.EXPLORATORY],
                    0.6,
                    ["query_type_llm_fallback_failed"],
                )
        return AnalyzedQueryTypes(
            [QueryType.EXPLORATORY],
            0.65,
            ["query_type_defaulted_to_exploratory"],
        )
