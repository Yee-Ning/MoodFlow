"""Fail-closed private history views; public conversation stays shared.

The store has no agent-scoped query API. Never infer ownership from a route,
node, text or position. New runtime messages carry the actual producer's scope
in the existing BaseMessage.additional_kwargs carrier; old private rows without
that scope are excluded, without backfilling or persisting an attribution.
"""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.agent.foundation.context_sources import HistoryMemorySource
from app.agent.foundation.source_errors import SourceIntegrityError


SCOPE_FIELDS = ("owner_agent", "run_id", "route_task_id", "user_id")


def invocation_scope(invocation):
    return {
        "owner_agent": invocation.owner_agent,
        "run_id": invocation.run_id,
        "route_task_id": invocation.task.task_id,
        "user_id": (invocation.inputs.continuity_context or {}).get("user_id"),
    }


def private_metadata(metadata):
    return bool(metadata.get("private") or metadata.get("private_history")
                or any(key in metadata for key in SCOPE_FIELDS[:3]))


class AgentHistorySource:
    """Gate messages before the original collector pairs their tool protocol."""

    def __init__(self, invocation):
        self.invocation = invocation
        self.scope = invocation_scope(invocation)

    def owns(self, metadata):
        return all(isinstance(metadata.get(key), str) and metadata[key]
                   and metadata[key] == self.scope[key] for key in SCOPE_FIELDS[:3]) \
            and metadata.get("user_id") == self.scope["user_id"]

    def read_private(self, message):
        if not self.owns(message.additional_kwargs):
            raise SourceIntegrityError()
        return message.model_copy(deep=True)

    def collect(self, messages):
        candidates = []
        for message in messages or []:
            if message.additional_kwargs.get("user_id") not in {None, self.scope["user_id"]}:
                continue
            protocol = isinstance(message, ToolMessage) or (
                isinstance(message, AIMessage) and (message.tool_calls or message.invalid_tool_calls))
            if protocol or private_metadata(message.additional_kwargs):
                if not self.owns(message.additional_kwargs):
                    continue
                candidates.append(self.read_private(message))
            elif isinstance(message, (HumanMessage, AIMessage)):
                candidates.append(message.model_copy(deep=True))
        # Unknown/foreign/orphaned protocol is excluded as a complete unit.
        results = {message.tool_call_id for message in candidates if isinstance(message, ToolMessage)}
        paired = {call["id"] for message in candidates if isinstance(message, AIMessage)
                  and message.tool_calls and not message.invalid_tool_calls
                  and all(call.get("id") in results for call in message.tool_calls)
                  for call in message.tool_calls}
        return [message for message in candidates
                if (not isinstance(message, ToolMessage) or message.tool_call_id in paired)
                and (not isinstance(message, AIMessage) or not message.invalid_tool_calls
                     and (not message.tool_calls or all(call.get("id") in paired for call in message.tool_calls)))]

    def filter_rows(self, rows):
        result = []
        for row in rows or []:
            raw = row.model_dump() if hasattr(row, "model_dump") else row
            metadata = raw.get("metadata", raw) if isinstance(raw, dict) else {}
            if metadata.get("user_id") not in {None, self.scope["user_id"]}:
                continue
            if not private_metadata(metadata) or self.owns(metadata):
                result.append(row)
        return result


class ScopedHistoryMemorySource(HistoryMemorySource):
    """Source-side isolation, including stores lacking an agent query filter."""

    def __init__(self, invocation):
        self.history = AgentHistorySource(invocation)

    def collect(self, request, *, limit):
        from dataclasses import replace

        history, store, scopes = self.history, request.history_store, {}
        context = self.history.invocation.inputs.continuity_context or {}
        if any(request.continuity.get(key) != context.get(key)
               for key in ("user_id", "conversation_id", "session_id", "task_id")):
            raise SourceIntegrityError()
        if store is None:
            return []

        class ScopedStore:
            def __getattr__(self, name):
                return getattr(store, name)

            def query_task_history(self, *args, **kwargs):
                rows = history.filter_rows(store.query_task_history(*args, **kwargs))
                scopes.update({row.event_id: row.metadata for row in rows if private_metadata(row.metadata)})
                return rows

        items = super().collect(replace(request, history_store=ScopedStore()), limit=limit)
        from app.agent.foundation.business_pack import _reference
        return [item.model_copy(update={"references": [*item.references,
                _reference("source_scope", source=self.name, user=history.scope["user_id"],
                    agent=history.scope["owner_agent"], run_id=history.scope["run_id"],
                    route_task_id=history.scope["route_task_id"])]})
                if item.metadata["memory_id"] in scopes else item for item in items]


def bind_produced_message(message, invocation):
    """Only call at the actual model/tool producer, never on incoming history."""
    return message.model_copy(update={"additional_kwargs": {
        **message.additional_kwargs, **invocation_scope(invocation), "private_history": True,
    }}, deep=True)


def bind_tool_result(message, call_message):
    """A tool producer may inherit the matching call's existing runtime scope."""
    if not isinstance(message, ToolMessage) or not isinstance(call_message, AIMessage):
        return message
    metadata = call_message.additional_kwargs
    if not all(isinstance(metadata.get(key), str) and metadata[key] for key in SCOPE_FIELDS[:3]):
        return message
    if message.tool_call_id not in {call.get("id") for call in call_message.tool_calls}:
        raise SourceIntegrityError()
    return message.model_copy(update={"additional_kwargs": {
        **message.additional_kwargs, **{key: metadata.get(key) for key in SCOPE_FIELDS}, "private_history": True,
    }}, deep=True)
