"""Tighten already-selected chat packs against the unchanged final L1 payload."""
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_engineering import POLICIES, ContextPack, ContextTrace
from app.agent.foundation.routing import RouteKey
from app.llm.runtime import RuntimeLimitError, active_ledger, estimate_tokens


def _retain_audit(previous, selected):
    """Observe surviving items without turning an old manifest into a source."""
    before = PackManifest.model_validate(previous.manifest)
    after = PackManifest.model_validate(selected.manifest)
    sources = {}
    survivors = {item.id: item for item in selected.items}
    for name, original in before.sources.items():
        current = after.sources.get(name)
        chunks = tuple(chunk.model_copy(update={
            "included": chunk.item_id in survivors,
            "final_tokens": survivors[chunk.item_id].token_count if chunk.item_id in survivors else 0,
        }) for chunk in original.chunks)
        updates = {
            "final_tokens": current.final_tokens if current else 0,
            "events": [*original.events, *(current.events if current else [])],
            "dedup_dropped": list(dict.fromkeys([*original.dedup_dropped, *(current.dedup_dropped if current else [])])),
        }
        if original.chunks or "chunks" in original.model_fields_set:
            updates["chunks"] = list(chunks)
        sources[name] = original.model_copy(update=updates)
    audit = before.agent_context
    if audit is not None:
        audit = audit.model_copy(update={"source_selection": tuple(
            row.model_copy(update={"final_tokens": survivors[row.item_id].token_count,
                                   "references": tuple(survivors[row.item_id].references)})
            for row in audit.source_selection if row.item_id in survivors)})
        if audit.envelope is not None:
            audit = audit.model_copy(update={"envelope": audit.envelope.model_copy(update={
                "selected_fields": tuple(item.metadata["field_path"] for item in selected.items
                                         if item.source == "received_envelope")})})
    manifest = after.model_copy(update={"sources": sources, "agent_context": audit}).model_dump(mode="json")
    return ContextPack.model_validate({**selected.__dict__, "manifest": manifest,
        "trace": ContextTrace(entries=[*previous.trace.entries, *selected.trace.entries]),
        "dropped_items": list(dict.fromkeys([*previous.dropped_items, *selected.dropped_items])),
        "compressed_items": list(dict.fromkeys([*previous.compressed_items, *selected.compressed_items]))})


def fit_chat_pack(pack, policy, selector, *, payload, output_tokens):
    """Use the same payload/estimator as dispatch; never refill dropped sources."""
    margin = POLICIES[RouteKey.GENERAL_CHAT].safety_margin
    input_limit = max(row.total_input_budget for row in POLICIES.values()) - output_tokens - margin
    for _ in range(len(pack.items) + 2):
        excess = estimate_tokens(payload(pack.messages)) - input_limit
        if excess <= 0:
            return pack
        available = min(pack.budget, input_limit)
        selected_policy = policy.model_copy(update={"total_input_budget":
            available + policy.reserved_output_tokens + policy.safety_margin})
        try:
            selected = selector.build(pack.items, selected_policy, intent=pack.intent,
                                      request_tools=payload(pack.messages)["tools"])
        except ValueError:
            break
        pack = _retain_audit(pack, selected)
    ledger = active_ledger.get()
    if ledger is not None:
        ledger.blocked = "token_limit"
    raise RuntimeLimitError("token_limit")
