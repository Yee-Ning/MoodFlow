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
        from app.agent.foundation.agent_pack import AgentPackInputs
        from app.agent.foundation.subgraphs.worldline.stage_pack import active_stage_context, prepare_explorer_input
        from app.llm.runtime import agent_scope

        def render(pack):
            system, context = "", []
            projected = {}
            for item in pack.items:
                if item.type == "system":
                    system = item.content
                elif item.source == "retry_feedback":
                    continue
                elif item.type == "structured_output":
                    projected.update(json.loads(item.content))
                elif item.type == "user_query":
                    if item.content != query:
                        context.append({"type": "active_task", "content": item.content})
                    else:
                        projected["query"] = item.content
                else:
                    context.append({"type": item.type, "content": item.content})
            if "query" not in projected:
                raise ValueError("query analysis pack must contain the current query")
            if context:
                projected["context"] = context
            return [{"role": "user", "content": json.dumps(projected, ensure_ascii=False, separators=(",", ":"))}], system

        async def generate(attempt: int, feedback: str | None):
            instructions = QUERY_ANALYSIS_PROMPT_V1
            if feedback:
                instructions += f"\n{feedback}"
            schema = strict_model_schema(QueryTypeOutput)
            with agent_scope("explorer"):
                model_input = await prepare_explorer_input("query_analysis", query=query, instructions=instructions,
                    schema=schema, schema_name="query_type_analysis_v1", renderer=render,
                    inputs=AgentPackInputs(structured_output={"query": query},
                                          continuity_context=active_stage_context.get()))
                response = await create_structured_response(model_input.input_items, model_input.instructions, schema,
                    schema_name="query_type_analysis_v1", request_budget=model_input.budget)
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
            r"时代|時代|时期|時期|年代|早期|初期|突破期|巅峰期|巔峰期|晚期|"
            r"(?:18|19|20)\d{2}\s*年|"
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
            except Exception as exc:
                from app.agent.foundation.subgraphs.worldline.stage_pack import raise_hard_source_failure
                raise_hard_source_failure(exc)
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
