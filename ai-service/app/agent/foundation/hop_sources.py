"""Read-only Envelope/session candidates and deterministic per-edge projection."""

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from types import SimpleNamespace

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import PackManifest, SessionManifest
from app.agent.foundation.context_engineering import POLICIES, ContextItem, ContextPack
from app.agent.foundation.context_pointers import EventPointer
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest, load_context_policy, source_item
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.handoff.continuity import ProgressProjector
from app.agent.handoff.hop_context import CurrentHopSnapshot, active_hop
from app.agent.handoff.registry import CAPABILITY_OWNERS
from app.llm.budget_strategy import prepare_sources
from app.llm.runtime import active_ledger, active_manifest, replace_context_pack


@dataclass(frozen=True)
class SourceCall:
    snapshot: CurrentHopSnapshot
    invocation: AgentPackInvocation
    overlaps: frozenset[str]


active_source_call: ContextVar[SourceCall | None] = ContextVar("agent_pack_source_call", default=None)


def event_reference(pointer: EventPointer) -> str:
    return "event:" + pointer.model_dump_json(exclude_none=True)


def source_call():
    call = active_source_call.get()
    if call is None and active_hop.get() is not None:
        raise SourceIntegrityError()
    if call is not None:
        snapshot, invocation = call.snapshot, call.invocation
        snapshot.require_current()
        if (snapshot.run_id, snapshot.owner_agent, snapshot.task_json) != (
            invocation.run_id, invocation.owner_agent, invocation.task.model_dump_json()
        ):
            raise SourceIntegrityError()
        # Check even when a budget stop will prevent collection.
        snapshot.envelope()
    return call


def bounded_item(call, name, item_id, field, data, *, text_key=None, references=()):
    """Bound serialized text units; never truncate an entity/JSON structure."""
    config = load_context_policy()["sources"][name]
    metadata = {"field_path": field, "truncated": False}
    if call.snapshot.envelope_audit is not None and name == "received_envelope":
        audit = call.snapshot.envelope_audit
        metadata.update(transfer_id=audit.transfer_id, payload_digest=audit.payload_digest,
                        proposal_ref=audit.summary_ref.model_dump(mode="json"),
                        commit_ref=audit.commit_ref.model_dump(mode="json"))
        references = (event_reference(audit.summary_ref), event_reference(audit.commit_ref), *references)

    def item(value):
        return source_item(item_id, "business_state", value, source=name,
                           intents=[call.invocation.task.route_key.value], metadata=metadata,
                           references=list(dict.fromkeys(references)))

    result = item(data)
    if result.token_count <= config["excerpt_tokens"]:
        return result
    if text_key is None:
        return None  # Complete entities/references are indivisible.
    original = data[text_key]
    data = {**data, "truncated": True}
    metadata["truncated"] = True
    metadata.update(token_before_projection=result.token_count, projection_reason=f"{name}_excerpt_truncated")
    low, high, fitting = 0, len(original), None
    while low <= high:
        middle = (low + high) // 2
        projected = item({**data, text_key: original[:middle]})
        if projected.token_count <= config["excerpt_tokens"]:
            fitting, low = projected, middle + 1
        else:
            high = middle - 1
    return fitting


class ReceivedEnvelopeSource:
    name = "received_envelope"

    def collect(self, request: SourceRequest, *, limit: int) -> list[ContextItem]:
        call = source_call()
        if call is None:
            return []
        envelope = call.snapshot.envelope()
        if envelope is None:
            return []
        tid = call.snapshot.envelope_audit.transfer_id
        rows = []

        def add(field, logical_id, data, **kwargs):
            candidate = bounded_item(call, self.name, f"envelope:{tid}:{field}:{logical_id}", field, data, **kwargs)
            if candidate is not None:
                rows.append(candidate)

        if call.invocation.phase == "relay":
            from app.agent.handoff.registry import AgentKey, next_agent_for_task
            add("route_path", envelope.active_task.task_id, {
                "task_id": envelope.active_task.task_id, "route_key": envelope.active_task.route_key.value,
                "current_agent": call.snapshot.owner_agent,
                "next_agent": next_agent_for_task(AgentKey(call.snapshot.owner_agent), envelope.active_task.route_key).value,
            })
            return rows[:min(limit, 4)]
        add("request.context_summary", "summary", {"summary": envelope.request.context_summary}, text_key="summary")
        for index, result in enumerate(envelope.completed_results):
            # Relay/authorization is runtime data; successful public summaries
            # cannot stand in for failed/pending progress or executable objects.
            if envelope.request.target_agent.value == "explorer" and result.route_key.value == "general_chat":
                continue
            if envelope.source_agent.value == "explorer" and result.route_key.value != "worldline":
                continue
            refs = (event_reference(call.snapshot.result_refs[index]),) if index < len(call.snapshot.result_refs) else ()
            add(f"completed_results.{result.task_id}", result.task_id,
                {"task_id": result.task_id, "route_key": result.route_key.value, "status": result.status.value,
                 "summary": result.content}, text_key="summary", references=refs)
        for group in ("songs", "artists", "albums", "playlists"):
            if group == "playlists" and (envelope.source_agent.value == "explorer"
                                          or envelope.request.target_agent.value == "explorer"):
                continue
            for entity in getattr(envelope.entities, group):
                raw = entity.model_dump(mode="json")
                # Read-only catalog identity is a complete projected entity;
                # exclude playback objects, cover URLs and action parameters.
                fields = {"id", "type", "name", "source"}
                if envelope.request.target_agent.value != "general_chat":
                    fields |= {"song_id", "playlist_id", "artists", "artist", "album"}
                projected = {key: raw[key] for key in fields if key in raw}
                entity_ref = f"catalog:{raw['source']}:{raw['type']}:{raw['id']}"
                field = f"entities.{group}.{raw['id']}"
                before = len(rows)
                add(field, str(raw["id"]), {"entity": projected}, references=(entity_ref,))
                if len(rows) == before:
                    add(field, str(raw["id"]), {"entity_ref": entity_ref, "projection": "reference_only",
                        "omitted": "entity_exceeds_excerpt_limit"}, references=(entity_ref,))
        for index, ref in enumerate(envelope.evidence_refs):
            # Reference only. No Journal drilldown, tool execution or retrieval.
            add(f"evidence_refs.{index}", ref.target_id, {"evidence_ref": ref.model_dump(mode="json")},
                references=(f"{ref.kind}:{ref.target_id}",))
        owner_limit = 16 if call.invocation.owner_agent == "general_chat" else 24
        return rows[:min(limit, owner_limit, load_context_policy()["sources"][self.name]["candidate_limit"])]


class SessionViewSource:
    name = "session_view"

    def collect(self, request: SourceRequest, *, limit: int) -> list[ContextItem]:
        call = source_call()
        if call is None or call.invocation.phase == "relay" or call.snapshot.store is None:
            return []
        snapshot, identity = call.snapshot, call.snapshot.identity
        events = snapshot.store.query_task_history(identity.continuity_task_id, session_id=identity.session_id,
            event_type=HistoryEventType.RUN_PROGRESS, descending=True, limit=20)
        record, results, origin = None, [], None
        projector = ProgressProjector(SimpleNamespace(store=snapshot.store))
        try:
            for event in events:
                if (event.task_id, event.session_id) != (identity.continuity_task_id, identity.session_id):
                    raise SourceIntegrityError()
                candidate, public_results = projector._validate_record(event)
                if candidate.run.run_id == snapshot.run_id:
                    record, results, origin = candidate, public_results, event
                    break
            if record is None:
                return []
            if tuple(record.run.chain) != snapshot.chain or record.run.current_agent != snapshot.owner_agent:
                raise SourceIntegrityError()
        except (ValueError, KeyError, TypeError):
            raise SourceIntegrityError() from None
        ref = event_reference(EventPointer(task_id=origin.task_id, event_id=origin.event_id))
        audit = SessionManifest(session_id=identity.session_id, source_event_cursor=origin.event_seq,
                                checkpoint_ref=record.checkpoint_ref.model_dump(mode="json"))
        rows = []

        def add(field, logical_id, data, *, extra_refs=(), **kwargs):
            if field.split(".")[0] in call.overlaps:
                return
            candidate = bounded_item(call, self.name, f"session:{identity.session_id}:{snapshot.run_id}:{field}:{logical_id}",
                                     field, data, references=(ref, *extra_refs), **kwargs)
            if candidate is not None:
                rows.append(candidate.model_copy(update={"metadata": {**candidate.metadata,
                    "session_audit": audit.model_dump(mode="json"), "progress_ref": ref}}))

        add("run_status", snapshot.run_id, {"run_id": snapshot.run_id, "status": record.run.status})
        add("handoff_chain", snapshot.run_id, {"run_id": snapshot.run_id, "chain": list(record.run.chain)})
        # Task IDs and status are observations, never new task text/authority.
        for task_id in record.run.pending_task_ids:
            add("pending_tasks", task_id, {"task_id": task_id, "status": "pending"})
        envelope_result_ids = {result.task_id for result in snapshot.envelope().completed_results} if snapshot.envelope_json else set()
        for result, result_ref in zip(results, record.result_refs, strict=True):
            if result.route_task_id in envelope_result_ids:
                continue
            add("completed_results", result.route_task_id, {"task_id": result.route_task_id,
                "status": result.status, "summary": result.content}, text_key="summary",
                extra_refs=(event_reference(EventPointer(task_id=identity.continuity_task_id, event_id=result_ref.target_id)),))
        return rows[:min(limit, 8)]


def inherited_overlaps(inputs):
    raw = inputs.handoff_projection or (inputs.continuity_context or {}).get("handoff")
    if hasattr(raw, "model_dump"):
        raw = raw.model_dump(mode="json")
    if not isinstance(raw, dict):
        return frozenset()
    overlaps = set()
    progress = raw.get("agent_progress", {})
    if isinstance(progress, dict):
        if progress.get("run_status") not in (None, "unknown"):
            overlaps.add("run_status")
        if progress.get("recent_run_chain"):
            overlaps.add("handoff_chain")
    if raw.get("pending_items") or raw.get("next_actions"):
        overlaps.add("pending_tasks")
    if raw.get("completed_steps"):
        overlaps.add("completed_results")
    return frozenset(overlaps)


@contextmanager
def invocation_source_scope(invocation):
    snapshot = active_hop.get()
    if snapshot is None:
        raise SourceIntegrityError()
    token = active_source_call.set(SourceCall(snapshot, invocation, inherited_overlaps(invocation.inputs)))
    try:
        source_call()
        relay = CAPABILITY_OWNERS[invocation.task.route_key].value != invocation.owner_agent
        if relay != (invocation.phase == "relay"):
            raise SourceIntegrityError()
        yield snapshot
    finally:
        active_source_call.reset(token)


@dataclass(frozen=True)
class PreparedHopSources:
    invocation: AgentPackInvocation
    source_status: dict[str, str]


def prepare_hop_sources(invocation: AgentPackInvocation, *, registry=None) -> PreparedHopSources:
    """Add only these formal Sources through A's existing registered_items."""
    invocation = invocation.model_copy(deep=True)
    with invocation_source_scope(invocation) as snapshot:
        relay = invocation.phase == "relay"
        if relay:
            invocation = invocation.model_copy(update={"inputs": AgentPackInputs()})
        ledger = active_ledger.get()
        if ledger is not None:
            if (ledger.run_id, ledger.current_agent) != (invocation.run_id, invocation.owner_agent):
                raise SourceIntegrityError()
            ledger.check_task_budget()
            if ledger.remaining <= 0:
                ledger.fail_budget("l2")
            from app.agent.foundation.prompt_bindings import bind_prompts
            system, _ = bind_prompts(invocation.prompt_refs, owner=invocation.owner_agent, phase=invocation.phase)
            sources = prepare_sources({**{name: getattr(invocation.inputs, name) for name in AgentPackInputs.model_fields},
                "system": system, "query": invocation.task.input_text},
                reserved_output=POLICIES[invocation.task.route_key].reserved_output_tokens)
            sources.pop("system")
            sources.pop("query")
            invocation = invocation.model_copy(update={"inputs": AgentPackInputs(**sources)})
        registry = registry or SourceRegistry((ReceivedEnvelopeSource(), SessionViewSource()))
        # Filter before calling collect: a relay cannot read profile/RAG/tools.
        if relay:
            registry = SourceRegistry(source for name, source in registry.sources.items()
                                      if name in {"received_envelope", "session_view"})
        # Refresh the call descriptor after input degradation/relay sanitization.
        token = active_source_call.set(SourceCall(snapshot, invocation, inherited_overlaps(invocation.inputs)))
        try:
            candidates, statuses = registry.collect(SourceRequest(invocation.task.input_text,
                dict(invocation.inputs.continuity_context or {}), history_store=snapshot.store,
                allow_new_sources=ledger is None or invocation.owner_agent not in ledger.source_stops))
        finally:
            active_source_call.reset(token)
        existing = invocation.inputs.registered_items
        if existing is None:
            existing = [ContextItem.model_validate(item) for item in
                        (invocation.inputs.continuity_context or {}).get("entry_candidates", [])]
        if any(item.source in {"received_envelope", "session_view"} for item in existing):
            raise SourceIntegrityError()  # A prior-hop Pack/candidate is not a new source.
        session = snapshot.session_audit
        for item in candidates:
            if item.source == "session_view":
                session = SessionManifest.model_validate(item.metadata["session_audit"])
                break
        prepared = invocation.model_copy(update={"inputs": invocation.inputs.model_copy(update={
            "registered_items": [*existing, *candidates]}), "envelope": snapshot.envelope_audit, "session": session})
        return PreparedHopSources(prepared, statuses)


@dataclass(frozen=True)
class HopPackResult:
    pack: ContextPack
    source_status: dict[str, str]


def build_hop_pack(invocation: AgentPackInvocation, *, builder=None, registry=None, **kwargs) -> HopPackResult:
    prepared = prepare_hop_sources(invocation, registry=registry)
    # Recipe soft limits; L1, required materials and selector remain unchanged.
    policy = kwargs.pop("policy", None) or POLICIES[invocation.task.route_key]
    quota = 256 if invocation.phase == "relay" else {"general_chat": 768, "curator": 1024, "explorer": 1280}[invocation.owner_agent]
    policy = policy.model_copy(update={"source_budgets": {**policy.source_budgets, "received_envelope": quota}})
    if kwargs.get("intent") is None:
        kwargs["intent"] = {"routes": [invocation.task.route_key.value]}
    pack = (builder or AgentPackBuilder()).build(prepared.invocation, policy=policy, **kwargs)
    if pack.manifest.get("agent_context", {}).get("envelope") is not None:
        projection = PackManifest.model_validate(pack.manifest)
        fields = tuple(item.metadata["field_path"] for item in pack.items if item.source == "received_envelope")
        audit = projection.agent_context.model_copy(update={"envelope": projection.agent_context.envelope.model_copy(
            update={"selected_fields": fields})})
        manifest = projection.model_copy(update={"agent_context": audit}).model_dump(mode="json")
        previous = pack
        pack = ContextPack.model_validate({**pack.__dict__, "manifest": manifest})
        active_manifest.set(pack.manifest)
        replace_context_pack(previous, pack)
    return HopPackResult(pack, prepared.source_status)
