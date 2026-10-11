"""Bounded source registry. Intent belongs to Select, never to Collect."""

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from app.agent.foundation.long_horizon.history_writer import validate_public_document
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.public_events import PublicText
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.foundation.user_profile import ProfileDocument, ProfileStore


@lru_cache
def load_context_policy() -> dict:
    return json.loads(Path(__file__).with_name("context_policy.json").read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SourceRequest:
    query: str
    continuity: dict[str, Any]
    profile_store: ProfileStore | None = None
    history_store: Any = None
    allow_new_sources: bool = True
    memory_store: Any = None


class ContextSource(Protocol):
    name: str
    def collect(self, request: SourceRequest, *, limit: int) -> list: ...


def source_item(item_id, kind, content, *, source, intents, metadata=None, references=None):
    from app.agent.foundation.context_engineering import ContextItem
    from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter
    # Text fields remain data, including when they contain boundary-like markup.
    text = json.dumps(content, ensure_ascii=False, separators=(",", ":"), default=str)
    text = text.replace("<", "\\u003c").replace(">", "\\u003e")
    return ContextItem(id=item_id, type=kind, source=source, content=text,
                       token_count=UnicodeHeuristicTokenCounter().count(text),
                       metadata={**(metadata or {}), "intents": list(intents)},
                       references=references or [])


def excerpt(text, query, length):
    """Bounded extractive candidate with query overlap; Select still owns intent."""
    normalized = re.sub(r"\W+", "", query.casefold())
    terms = {normalized[index:index + 2] for index in range(len(normalized) - 1)}
    matches = [text.casefold().find(term) for term in terms if term in text.casefold()]
    start = max(0, min(matches) - length // 4) if matches else 0
    return text[start:start + length], len(matches) / max(1, len(terms))


class UserProfileSource:
    name = "user_profile"

    def collect(self, request, *, limit):
        user_id = request.continuity.get("user_id")
        if not user_id:
            return []
        raw = request.continuity.get("user_profile")
        if request.profile_store is None and raw is not None:
            from app.agent.foundation.user_profile import get_profile_store
            current = get_profile_store().read(user_id)
            if current is not None:
                raw = current
        if raw is None:
            from app.agent.foundation.user_profile import get_profile_store
            raw = (request.profile_store or get_profile_store()).read(user_id)
        if raw is None:
            return []
        document = ProfileDocument.model_validate(validate_public_document(ProfileDocument, raw))
        if document.user_id != user_id:
            raise ValueError("profile user scope mismatch")
        if not document.memory_enabled:
            return []
        from app.agent.memory.context import current_memory_items
        entries = current_memory_items(document.entries, user_id, store=request.memory_store)
        # Bounded candidates; relevance and intent filtering happen in Select.
        from app.agent.memory.scopes import infer_scene, is_current, matches_scene
        scene = request.continuity.get("memory_scene") or infer_scene(request.query)
        ranked = sorted((entry for entry in entries
                         if is_current(entry.valid_until) and matches_scene(entry.scene, scene)),
                        key=lambda entry: (excerpt(entry.value, request.query, 120)[1],
                                           entry.updated_at, entry.confidence), reverse=True)
        result = []
        for entry in ranked[:limit]:
            content = entry.model_dump(mode="json")
            projected, relevance = excerpt(entry.value, request.query, load_context_policy()["sources"][self.name]["excerpt_characters"])
            item = source_item(f"profile:{entry.key}", "user_profile", {**content, "value": projected},
                source=self.name, intents=entry.intents,
                metadata={"version": document.version, "confidence": entry.confidence,
                          "memory_key": entry.memory_key, "memory_id": entry.memory_id,
                          "updated_at": entry.updated_at.isoformat(), "provenance": entry.source})
            original = source_item(item.id, item.type, content, source=self.name, intents=entry.intents)
            result.append(item.model_copy(update={"relevance": relevance,
                "metadata": {**item.metadata, "token_before_projection": original.token_count,
                             "projection_reason": "profile_excerpt_truncated"}}))
        return result


class HistoryMemorySource:
    name = "history_memory"

    def collect(self, request, *, limit):
        store = request.history_store
        context = request.continuity
        if store is None or not context.get("conversation_id") or not context.get("session_id"):
            return []
        session = store.require_session(context["session_id"])
        if session.conversation_id != context["conversation_id"] or session.task_id != context.get("task_id"):
            raise ValueError("memory history scope mismatch")
        result = []
        # Only the authenticated conversation's immutable public text, across sessions.
        config = load_context_policy()["sources"][self.name]
        for task in reversed(store.tasks_for_conversation(session.conversation_id)[-config["task_limit"]:]):
            events = store.query_task_history(task.task_id, limit=limit, descending=True)
            routes_by_request = {}
            for event in events:
                if event.event_type == HistoryEventType.ROUTE_DECISION:
                    routes_by_request[(event.session_id, event.request_id)] = [
                        row.get("route_key") if isinstance(row, dict) else row[1]
                        for row in (event.payload or {}).get("tasks", [])
                    ]
            for event in events:
                if event.request_id == context.get("request_id") and event.request_id is not None:
                    continue
                if event.event_type not in {HistoryEventType.USER_MESSAGE, HistoryEventType.ASSISTANT_MESSAGE}:
                    continue
                payload = validate_public_document(PublicText, event.payload or {})
                text = payload.get("text", "")
                if not text.strip() or text.strip() == request.query.strip():
                    continue
                routes = routes_by_request.get((event.session_id, event.request_id)) or [RouteKey.GENERAL_CHAT]
                projected, relevance = excerpt(text, request.query, config["excerpt_characters"])
                content = {"memory_type": "episodic_history", "content": projected,
                           "event_id": event.event_id, "session_id": event.session_id,
                           "updated_at": event.timestamp.isoformat()}
                item = source_item(
                    f"recall:{event.event_id}", "long_term_memory",
                    content,
                    source=self.name, intents=routes,
                    references=[event.event_id], metadata={"memory_id": event.event_id},
                )
                original = source_item(item.id, item.type, {**content, "content": text}, source=self.name, intents=routes)
                result.append(item.model_copy(update={"relevance": relevance,
                    "metadata": {**item.metadata, "token_before_projection": original.token_count,
                                 "projection_reason": "history_excerpt_truncated"}}))
                if len(result) >= limit:
                    return result
        return result


class SourceRegistry:
    def __init__(self, sources=None):
        from .rag_source import RagSource
        self.sources: dict[str, ContextSource] = {}
        for source in sources if sources is not None else (UserProfileSource(), HistoryMemorySource(), RagSource()):
            self.register(source)

    def register(self, source: ContextSource):
        if source.name in self.sources:
            raise ValueError("duplicate context source")
        if source.name not in load_context_policy()["sources"]:
            raise ValueError("context source has no configured policy")
        self.sources[source.name] = source

    def collect(self, request):
        from .rag_source import SourceItems
        from .rag_usage import RagAccountingFailure, RagBudgetRefusal
        result, status = SourceItems(), {}
        if not request.allow_new_sources:
            return [], {name: "budget_stopped" for name in self.sources}
        for name, source in self.sources.items():
            try:
                items = source.collect(request, limit=load_context_policy()["sources"][name]["candidate_limit"])
                if any(item.source != name or item.type != load_context_policy()["sources"][name]["type"] or item.required for item in items):
                    raise ValueError("optional source cannot inject required context")
                result.extend(items)
                if getattr(items, "audit", None) is not None:
                    result.source_audit[name] = items.audit
                status[name] = getattr(items, "status", None) or ("available" if items else "empty")
            except (SourceIntegrityError, RagBudgetRefusal, RagAccountingFailure):
                raise
            except Exception as exc:
                # Never expose history/profile text or exception messages in manifests.
                status[name] = "unavailable:" + type(exc).__name__
        return result, status
