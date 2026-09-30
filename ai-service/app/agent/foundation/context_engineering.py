"""Deterministic, per-call context selection for Foundation LLM nodes.

The RAG pack is an input source here, not a replacement for retrieval or packing.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import defaultdict
from typing import Any, Literal

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import BaseModel, ConfigDict, Field

from app.agent.foundation.routing import RouteKey
from app.rag.chunk.token_counter import TokenCounter, UnicodeHeuristicTokenCounter

logger = logging.getLogger(__name__)

ContextType = Literal[
    "system", "user_query", "conversation", "current_song", "business_state",
    "route_decision", "structured_output", "search_plan", "tool_result", "rag_evidence",
    "handoff_projection", "history_drilldown", "artifact_projection", "memory_source",
    "long_term_memory",
]


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str
    type: ContextType
    source: str
    content: str
    priority: int = Field(default=50, ge=0, le=100)
    relevance: float = Field(default=1.0, ge=0, le=1)
    token_count: int = Field(default=0, ge=0)
    required: bool = False
    compressible: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)


class ContextTraceEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    item_id: str
    stage: Literal["collected", "selected", "deduplicated", "compressed", "dropped", "final"]
    reason: str
    token_before: int = Field(ge=0)
    token_after: int = Field(ge=0)
    task_id: str | None = None
    session_id: str | None = None
    request_id: str | None = None


class ContextTrace(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    entries: list[ContextTraceEntry]

    def for_item(self, item_id: str) -> list[ContextTraceEntry]:
        return [entry for entry in self.entries if entry.item_id == item_id]


class ContextPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    node: RouteKey
    total_input_budget: int = Field(default=8192, gt=0)
    reserved_output_tokens: int = Field(default=2048, ge=0)
    safety_margin: int = Field(default=256, ge=0)
    source_budgets: dict[str, int] = Field(default_factory=dict)
    allowed_types: frozenset[ContextType]
    priorities: dict[str, int] = Field(default_factory=dict)

    @property
    def input_budget(self) -> int:
        return self.total_input_budget - self.reserved_output_tokens - self.safety_margin


class ContextPack(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    items: list[ContextItem]
    total_tokens: int
    budget: int
    dropped_items: list[str]
    compressed_items: list[str]
    messages: list[BaseMessage]
    trace: ContextTrace


POLICIES: dict[RouteKey, ContextPolicy] = {
    RouteKey.CURRENT_SONG: ContextPolicy(
        node=RouteKey.CURRENT_SONG,
        allowed_types=frozenset({"system", "user_query", "conversation", "current_song", "business_state", "handoff_projection", "history_drilldown", "artifact_projection", "memory_source", "long_term_memory"}),
        source_budgets={"conversation": 1600, "current_song": 2800, "long_term_memory": 700},
        priorities={"current_song": 90, "long_term_memory": 70, "conversation": 35, "business_state": 60},
    ),
    RouteKey.TOOL_CALLING: ContextPolicy(
        node=RouteKey.TOOL_CALLING,
        total_input_budget=12288,
        reserved_output_tokens=2048,
        allowed_types=frozenset({"system", "user_query", "conversation", "business_state", "structured_output", "tool_result", "route_decision", "handoff_projection", "history_drilldown", "artifact_projection", "memory_source", "long_term_memory"}),
        source_budgets={"conversation": 2200, "tool_result": 4500, "long_term_memory": 900},
        priorities={"structured_output": 90, "long_term_memory": 80, "tool_result": 75, "conversation": 40},
    ),
    RouteKey.GENERAL_CHAT: ContextPolicy(
        node=RouteKey.GENERAL_CHAT,
        allowed_types=frozenset({"system", "user_query", "conversation", "business_state", "structured_output", "handoff_projection", "history_drilldown", "artifact_projection", "memory_source", "long_term_memory"}),
        source_budgets={"conversation": 2400, "structured_output": 1000, "long_term_memory": 1000},
        priorities={"conversation": 80, "long_term_memory": 75, "structured_output": 65, "business_state": 55},
    ),
    RouteKey.WORLDLINE: ContextPolicy(
        node=RouteKey.WORLDLINE,
        total_input_budget=12288,
        reserved_output_tokens=2500,
        allowed_types=frozenset({"system", "user_query", "conversation", "business_state", "route_decision", "structured_output", "search_plan", "tool_result", "rag_evidence", "handoff_projection", "history_drilldown", "artifact_projection", "memory_source", "long_term_memory"}),
        source_budgets={"conversation": 800, "rag_evidence": 5000, "tool_result": 1800, "long_term_memory": 600},
        priorities={"search_plan": 90, "rag_evidence": 85, "long_term_memory": 68, "tool_result": 65, "conversation": 30},
    ),
}


def _serialize(value: Any) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _project_tool_result(raw: Any) -> Any:
    """Keep actionable IDs/status and bounded result fields, including full play_song tracks."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return raw[:4000]
    if isinstance(raw, list):
        return [_project_tool_result(value) for value in raw[:10]]
    if not isinstance(raw, dict):
        return raw
    keys = {
        "status", "error", "message", "song_id", "album_id", "playlist_id", "name", "artists",
        "artist", "album", "cover_url", "duration", "editable", "songs", "playlists",
        "entities", "entity_type", "type", "tracks", "lyrics", "original_lyrics",
        "translated_lyrics", "song", "client_action",
        "total", "count", "result", "data", "id", "title", "keyword", "limit", "success",
    }
    return {key: _project_tool_result(value) for key, value in raw.items() if key in keys}


class ContextCollector:
    def __init__(self, counter: TokenCounter | None = None):
        self.counter = counter or UnicodeHeuristicTokenCounter()

    def collect(
        self, *, query: str, system: str, messages: list[BaseMessage] | None = None,
        current_song: Any = None, business_state: Any = None, route_decision: Any = None,
        structured_output: Any = None, search_plan: Any = None, tool_results: list[Any] | None = None,
        rag_pack: Any = None, rag_required_ids: set[str] | None = None,
        handoff_projection: Any = None, history_drilldown: list[Any] | None = None,
        artifact_projections: list[Any] | None = None, memory_source: Any = None,
        long_term_memory: list[Any] | None = None,
        continuity_context: dict[str, Any] | None = None,
    ) -> list[ContextItem]:
        items: list[ContextItem] = []

        def add(item_id: str, kind: ContextType, value: Any, *, required: bool = False,
                compressible: bool = True, metadata: dict[str, Any] | None = None,
                references: list[str] | None = None, relevance: float = 1.0,
                priority: int = 50) -> None:
            if value is None:
                return
            content = _serialize(value)
            if not content.strip():
                return
            items.append(ContextItem(
                id=item_id, type=kind, source=kind, content=content,
                token_count=self.counter.count(content), required=required,
                compressible=compressible, metadata=metadata or {}, references=references or [],
                relevance=relevance, priority=priority,
            ))

        add("system", "system", system, required=True, compressible=False)
        add("query", "user_query", query, required=True, compressible=False)
        tool_message_ids = {
            message.tool_call_id for message in messages or [] if isinstance(message, ToolMessage)
        }
        paired_call_ids = {
            call["id"]
            for message in messages or [] if isinstance(message, AIMessage) and message.tool_calls
            if all(call.get("id") in tool_message_ids for call in message.tool_calls)
            for call in message.tool_calls
        }
        for index, message in enumerate(messages or []):
            if isinstance(message, (HumanMessage, AIMessage)):
                tool_calls = message.tool_calls if isinstance(message, AIMessage) else []
                if tool_calls and not all(call.get("id") in paired_call_ids for call in tool_calls):
                    tool_calls = []
                value = _serialize(tool_calls) if tool_calls else str(message.text)
                add(f"history:{index}", "conversation", value,
                    required=bool(tool_calls), compressible=not bool(tool_calls),
                    metadata={"role": "user" if isinstance(message, HumanMessage) else "assistant",
                              "tool_calls": tool_calls, "text": str(message.text)})
            elif isinstance(message, ToolMessage):
                projected = _project_tool_result(message.content)
                add(f"tool:{message.tool_call_id}", "tool_result", projected,
                    required=message.tool_call_id in paired_call_ids,
                    metadata={"tool_call_id": message.tool_call_id if message.tool_call_id in paired_call_ids else None,
                                             "name": message.name or "",
                                             "token_before_projection": self.counter.count(_serialize(message.content))})
        add("current_song", "current_song", current_song, required=current_song is not None)
        add("business_state", "business_state", business_state)
        add("route_decision", "route_decision", route_decision)
        add("structured_output", "structured_output", structured_output, required=structured_output is not None, compressible=False)
        add("search_plan", "search_plan", search_plan)
        if continuity_context:
            handoff_projection = handoff_projection or continuity_context.get("handoff")
            history_drilldown = [*(history_drilldown or []), *continuity_context.get("key_events", [])]
            artifact_projections = [*(artifact_projections or []), *continuity_context.get("artifacts", [])]
            long_term_memory = [
                *(long_term_memory or []),
                *continuity_context.get("long_term_memory", []),
            ]
        add("handoff", "handoff_projection", handoff_projection, required=handoff_projection is not None,
            compressible=False)
        for index, result in enumerate(history_drilldown or []):
            add(f"history_drilldown:{index}", "history_drilldown", result)
        for index, artifact in enumerate(artifact_projections or []):
            add(f"artifact:{index}", "artifact_projection", artifact)
        add("memory_source", "memory_source", memory_source)
        for index, memory in enumerate(long_term_memory or []):
            get = memory.get if isinstance(memory, dict) else lambda key, default=None: getattr(memory, key, default)
            memory_id = str(get("memory_id", index))
            content = {
                "memory_type": get("memory_type"),
                "content": get("content"),
                "structured_content": get("structured_content", {}),
                "confidence": get("confidence"),
                "importance": get("importance"),
            }
            safe_content = _serialize(content).replace("<", "\\u003c").replace(
                ">", "\\u003e"
            )
            add(
                f"long_term_memory:{memory_id}",
                "long_term_memory",
                safe_content,
                metadata={"memory_id": memory_id},
                relevance=float(get("relevance", 1.0) or 0.0),
                priority=min(100, max(0, int(float(get("importance", 0.5) or 0.0) * 100))),
            )
        for index, result in enumerate(tool_results or []):
            call_id = result.get("tool_call_id", index) if isinstance(result, dict) else index
            add(f"tool_result:{call_id}", "tool_result", _project_tool_result(result),
                metadata={"result_id": str(call_id),
                          "token_before_projection": self.counter.count(_serialize(result))})
        for index, evidence in enumerate(getattr(rag_pack, "items", []) if rag_pack is not None else []):
            get = evidence.get if isinstance(evidence, dict) else lambda key, default=None: getattr(evidence, key, default)
            evidence_id = str(get("parent_id") or get("id", index))
            citation = str(get("source_id") or evidence_id)
            content = get("context") or get("excerpt") or get("title") or _serialize(evidence)
            add(f"evidence:{evidence_id}", "rag_evidence", content,
                required=evidence_id in (rag_required_ids or set()),
                metadata={"evidence_id": evidence_id, "citation": citation,
                          "title": str(get("title", "")),
                          "exact_text": str(get("evidence_span") or get("exact_text") or "")},
                references=[citation])
        return items


def _dedup_keys(item: ContextItem) -> set[str]:
    normalized = " ".join(item.content.casefold().split())
    keys = {"content:" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()}
    if item.type == "rag_evidence" and item.metadata.get("evidence_id"):
        keys.add("evidence:" + str(item.metadata["evidence_id"]))
    return keys


def _truncate(text: str, limit: int, counter: TokenCounter) -> str:
    if limit <= 0:
        return ""
    if counter.count(text) <= limit:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if counter.count(text[:mid]) <= limit:
            low = mid
        else:
            high = mid - 1
    return text[:low].rstrip()


def _compress_song_xml(text: str, limit: int, counter: TokenCounter) -> str:
    """Shorten lyric fields while keeping their trust boundary tags well formed."""
    value = text
    for tag in ("translated_lyrics", "lyrics"):
        pattern = re.compile(rf'(<{tag} trusted="false">\s*)(.*?)(\s*</{tag}>)', re.DOTALL)
        match = pattern.search(value)
        if match is None:
            continue
        fixed = value[:match.start(2)] + value[match.end(2):]
        available = max(0, limit - counter.count(fixed))
        replacement = _truncate(match.group(2), available, counter)
        value = value[:match.start(2)] + replacement + value[match.end(2):]
        if counter.count(value) <= limit:
            break
    return value


def _compress_tool_json(text: str, limit: int, counter: TokenCounter) -> str:
    """Keep projected tool JSON parseable and retain the first actionable result."""
    try:
        value = json.loads(text)
    except ValueError:
        return _truncate(text, limit, counter)
    if not isinstance(value, dict):
        return _truncate(text, limit, counter)

    def render() -> str:
        return _serialize(value)

    for field in ("songs", "playlists"):
        rows = value.get(field)
        while isinstance(rows, list) and len(rows) > 1 and counter.count(render()) > limit:
            rows.pop()
    for field in ("translated_lyrics", "original_lyrics", "lyrics", "message"):
        current = value.get(field)
        while isinstance(current, str) and len(current) > 32 and counter.count(render()) > limit:
            current = current[: max(32, len(current) // 2)]
            value[field] = current
    return render()


class ContextAssembler:
    ORDER = {"system": 0, "handoff_projection": 1, "route_decision": 2,
             "structured_output": 3, "search_plan": 4, "current_song": 5,
             "business_state": 6, "artifact_projection": 7, "history_drilldown": 8,
             "memory_source": 9, "long_term_memory": 9, "rag_evidence": 10, "conversation": 11,
             "tool_result": 11, "user_query": 12}

    def assemble(self, items: list[ContextItem]) -> list[BaseMessage]:
        ordered = sorted(enumerate(items), key=lambda pair: (self.ORDER[pair[1].type], pair[0]))
        result: list[BaseMessage] = []
        instructions: list[str] = []
        for _, item in ordered:
            if item.type == "system":
                instructions.append(item.content)
            elif item.type == "user_query":
                result.append(HumanMessage(content=item.content))
            elif item.type == "conversation":
                role = item.metadata.get("role")
                if role == "user":
                    result.append(HumanMessage(content=item.content))
                elif item.metadata.get("tool_calls"):
                    result.append(AIMessage(content=item.metadata.get("text", ""),
                                            tool_calls=item.metadata["tool_calls"]))
                else:
                    result.append(AIMessage(content=item.content))
            elif item.type == "tool_result" and item.metadata.get("tool_call_id"):
                result.append(ToolMessage(content=item.content,
                                          tool_call_id=item.metadata["tool_call_id"],
                                          name=item.metadata.get("name") or None))
            else:
                label = item.type
                citation = f' citation="{item.metadata["citation"]}"' if item.type == "rag_evidence" else ""
                trust = (
                    ' trusted="true" text_fields="untrusted"'
                    if item.type == "long_term_memory"
                    else ""
                )
                instructions.append(f"<{label}{citation}{trust}>{item.content}</{label}>")
        return [SystemMessage(content="\n".join(instructions)), *result]


class ContextSelector:
    def __init__(self, counter: TokenCounter | None = None):
        self.counter = counter or UnicodeHeuristicTokenCounter()
        self.assembler = ContextAssembler()

    def build(self, items: list[ContextItem], policy: ContextPolicy,
              trace_context: dict[str, str] | None = None) -> ContextPack:
        if policy.input_budget <= 0:
            raise ValueError("context input budget must be positive")
        trace: list[ContextTraceEntry] = []
        dropped: list[str] = []
        compressed: list[str] = []
        kept: list[ContextItem] = []
        seen: set[str] = set()

        def event(item: ContextItem, stage: str, reason: str, after: int | None = None) -> None:
            trace.append(ContextTraceEntry(item_id=item.id, stage=stage, reason=reason,
                                           token_before=item.token_count,
                                           token_after=item.token_count if after is None else after,
                                           **(trace_context or {})))

        for original in items:
            item = original.model_copy(update={"priority": policy.priorities.get(original.type, original.priority)})
            event(item, "collected", "source_available")
            before_projection = item.metadata.get("token_before_projection")
            if isinstance(before_projection, int) and before_projection > item.token_count:
                compressed.append(item.id)
                trace.append(ContextTraceEntry(item_id=item.id, stage="compressed", reason="schema_projection",
                                               token_before=before_projection, token_after=item.token_count,
                                               **(trace_context or {})))
            if item.type not in policy.allowed_types and not item.required:
                dropped.append(item.id)
                event(item, "dropped", "policy_excluded", 0)
                continue
            event(item, "selected", "required" if item.required else "policy_allowed")
            keys = _dedup_keys(item)
            if keys & seen and not item.required:
                dropped.append(item.id)
                event(item, "deduplicated", "duplicate_content_or_evidence", 0)
                event(item, "dropped", "duplicate_content_or_evidence", 0)
                continue
            seen.update(keys)
            kept.append(item)

        # Projection is performed during collection. Enforce source quotas before global budget.
        source_used: dict[str, int] = defaultdict(int)
        candidates: list[ContextItem] = []
        for item in sorted(kept, key=lambda value: (not value.required, -value.priority, -value.relevance)):
            source_limit = policy.source_budgets.get(item.source)
            if not item.required and source_limit is not None and source_used[item.source] + item.token_count > source_limit:
                dropped.append(item.id)
                event(item, "dropped", f"{item.source}_budget_exceeded", 0)
                continue
            source_used[item.source] += item.token_count
            candidates.append(item)

        def total(values: list[ContextItem]) -> int:
            assembled = self.assembler.assemble(values)
            return sum(
                self.counter.count(str(message.content))
                + (self.counter.count(_serialize(message.tool_calls)) if isinstance(message, AIMessage) and message.tool_calls else 0)
                for message in assembled
            )

        # Drop lowest-value optional items before any extractive compression.
        while total(candidates) > policy.input_budget:
            optional = [item for item in candidates if not item.required]
            if not optional:
                break
            victim = min(optional, key=lambda value: (value.priority, value.relevance, -value.token_count))
            candidates.remove(victim)
            dropped.append(victim.id)
            event(victim, "dropped", "total_budget_exceeded", 0)

        if total(candidates) > policy.input_budget:
            for item in sorted(candidates, key=lambda value: value.priority):
                if not item.compressible or item.type in {"system", "user_query", "structured_output", "route_decision", "handoff_projection"}:
                    continue
                excess = total(candidates) - policy.input_budget
                target = max(0, item.token_count - excess)
                if item.type == "rag_evidence":
                    # The citation lives in metadata and survives the extracted text.
                    exact = item.metadata.get("exact_text", "")
                    target = max(1, target, self.counter.count(exact))
                shortened = (
                    _compress_song_xml(item.content, target, self.counter)
                    if item.type == "current_song" and "<song_context" in item.content
                    else _compress_tool_json(item.content, target, self.counter)
                    if item.type == "tool_result" else _truncate(item.content, target, self.counter)
                )
                if item.type == "rag_evidence":
                    exact = item.metadata.get("exact_text", "")
                    if exact and exact in item.content and exact not in shortened:
                        remainder = max(0, target - self.counter.count(exact) - 1)
                        shortened = exact + ("\n" + _truncate(item.content, remainder, self.counter)
                                             if remainder else "")
                if not shortened:
                    continue
                replacement = item.model_copy(update={"content": shortened, "token_count": self.counter.count(shortened)})
                candidates[candidates.index(item)] = replacement
                compressed.append(item.id)
                event(item, "compressed", "extractive_token_limit", replacement.token_count)
                if total(candidates) <= policy.input_budget:
                    break
        if total(candidates) > policy.input_budget:
            raise ValueError("required context exceeds input budget")
        final = sorted(candidates, key=lambda item: (self.assembler.ORDER[item.type], items.index(next(original for original in items if original.id == item.id))))
        for item in final:
            event(item, "final", "included_in_model_input")
        pack = ContextPack(items=final, total_tokens=total(final), budget=policy.input_budget,
                           dropped_items=dropped, compressed_items=compressed,
                           messages=self.assembler.assemble(final), trace=ContextTrace(entries=trace))
        logger.info("context_engineering node=%s collected=%d final=%d tokens=%d budget=%d dropped=%s compressed=%s",
                    policy.node, len(items), len(final), pack.total_tokens, pack.budget, dropped, compressed,
                    extra=trace_context or {})
        if policy.node is RouteKey.WORLDLINE:
            logger.info("context_engineering.rag retrieved=%d final=%d dropped=%s",
                        sum(item.type == "rag_evidence" for item in items),
                        sum(item.type == "rag_evidence" for item in final),
                        [item_id for item_id in dropped if item_id.startswith("evidence:")])
        return pack


def build_context(node: RouteKey, *, policy: ContextPolicy | None = None, counter: TokenCounter | None = None,
                  trace_context: dict[str, str] | None = None,
                  **sources: Any) -> ContextPack:
    token_counter = counter or UnicodeHeuristicTokenCounter()
    collected = ContextCollector(token_counter).collect(**sources)
    return ContextSelector(token_counter).build(
        collected, policy or POLICIES[node], trace_context=trace_context,
    )


async def build_context_async(node: RouteKey, *, policy: ContextPolicy | None = None,
                              counter: TokenCounter | None = None,
                              trace_context: dict[str, str] | None = None,
                              jev_client: Any = None, **sources: Any) -> ContextPack:
    """Apply Jev before the unchanged dedup, budget, compression and assembly stages."""
    token_counter = counter or UnicodeHeuristicTokenCounter()
    collected = ContextCollector(token_counter).collect(**sources)
    from app.config.settings import get_settings, jev_requested
    if jev_client is not None or jev_requested():
        settings = get_settings()
    else:
        settings = None
    if settings is not None and settings.jev_enabled and settings.jev_context_select_enabled:
        from app.agent.decision.jev.client import JevClient
        from app.agent.decision.jev.context_selector import select_context
        collected = await select_context(
            collected, str(sources.get("query", "")), settings=settings,
            client=jev_client or JevClient(settings),
            trace_id=(trace_context or {}).get("request_id"),
        )
    return ContextSelector(token_counter).build(
        collected, policy or POLICIES[node], trace_context=trace_context,
    )
