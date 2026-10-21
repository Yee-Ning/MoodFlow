"""WorldLine stage recipes; ephemeral inputs, never a replacement workflow."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, replace
from hashlib import sha256
import json
from uuid import uuid4

from langchain_core.messages import AIMessage

from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs, AgentPackInvocation
from app.agent.foundation.context_contracts import PackManifest, SourceSelectionManifest
from app.agent.foundation.context_engineering import (
    POLICIES, ContextCollector, ContextItem, ContextSelector, ContextTrace, ContextTraceEntry,
    _serialize, context_manifest,
)
from app.agent.foundation.context_sources import SourceRegistry, SourceRequest
from app.agent.foundation.hop_sources import ReceivedEnvelopeSource, SessionViewSource, build_hop_pack
from app.agent.foundation.model_input import prepare_pack_model_input
from app.agent.foundation.rag_source import RagSource
from app.agent.foundation.routing import RouteKey, RouteTask
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.foundation.subgraphs.worldline.prompts import (
    BRANCH_SYSTEM_PROMPT, EXPLORATION_BRANCH_BASE_V1, EXPLORATION_BRANCH_RULE_V1,
    NARRATIVE_SYSTEM_PROMPT, WORLDLINE_RETRY_RULE_V1,
)
from app.agent.handoff.hop_context import active_hop
from app.llm.runtime import active_ledger, active_manifest


active_stage_context = ContextVar("explorer_stage_context", default=None)
active_evidence = ContextVar("explorer_stage_evidence", default=None)
active_narrative_input = ContextVar("explorer_narrative_input", default=None)


@dataclass
class NarrativeInputState:
    invocation_id: str = ""
    attempt: int = 0
    boundary_context: object = None


@contextmanager
def narrative_input_scope():
    token = active_narrative_input.set(NarrativeInputState())
    try:
        yield
    finally:
        active_narrative_input.reset(token)


def reset_narrative_input(invocation_id, attempt):
    state = active_narrative_input.get()
    if state is not None:
        state.invocation_id, state.attempt, state.boundary_context = invocation_id, attempt, None


def record_narrative_input(sent):
    from .narrative_boundary import boundary_context_from_input
    state = active_narrative_input.get()
    if state is not None:
        state.boundary_context = boundary_context_from_input(
            sent, invocation_id=state.invocation_id, attempt=state.attempt)


def raise_hard_source_failure(error):
    """Known Source refusals cannot become model repair or catalog fallback."""
    from app.agent.foundation.rag_usage import RagAccountingFailure, RagBudgetRefusal
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (SourceIntegrityError, RagBudgetRefusal, RagAccountingFailure)):
            raise error from None
        error = error.__cause__


@dataclass
class SelectedEvidence:
    sources: list | None = None
    knowledge_available: bool | None = None


@contextmanager
def explorer_stage_context(continuity):
    token = active_stage_context.set(dict(continuity or {}))
    try:
        yield
    finally:
        active_stage_context.reset(token)


@contextmanager
def selected_evidence_scope():
    evidence = SelectedEvidence()
    token = active_evidence.set(evidence)
    try:
        yield evidence
    finally:
        active_evidence.reset(token)


def evidence_row(item, legacy_metadata):
    """Render only selected evidence, retaining actual provenance for grounding."""
    if item.source != "rag":
        evidence_id = item.metadata["evidence_id"]
        return {"id": evidence_id, "title": item.metadata["title"][:160],
                "excerpt": item.content, **legacy_metadata.get(evidence_id, {})}
    metadata = item.metadata
    # WorldLine citations use the knowledge namespace. Derive a bounded ID
    # from this selected chunk/content; do not borrow an old Parent snapshot ID.
    identity = "\n".join((item.id, metadata["chunk_id"], item.content))
    citation = "knowledge:rag:" + sha256(identity.encode("utf-8")).hexdigest()[:24]
    content = json.loads(item.content)
    return {"id": citation, "title": metadata["title"][:160],
            "excerpt": content, "source_type": "knowledge", "source": metadata["source_name"][:160],
            "metadata": {"rag_item_id": item.id, **{key: metadata[key] for key in ("doc_id", "source_id", "chunk_id")}},
            **({"url": metadata["source_url"][:512]} if metadata.get("source_url") else {})}


def record_selected_evidence(rows, *, knowledge_available):
    """Call-local grounding inputs, never model output or future Collect inputs."""
    observation = active_evidence.get()
    if observation is None:
        return
    from app.agent.foundation.subgraphs.worldline.models import EvidenceSource
    observation.sources = [EvidenceSource.model_validate({**row, "excerpt": row["excerpt"][:2000]})
                           for row in rows]
    observation.knowledge_available = knowledge_available


class StageRegistry(SourceRegistry):
    """Keep audit sidecars local while B continues to accept its original API."""

    def __init__(self, sources, query):
        super().__init__(sources)
        self.query = query
        self.audit = {}

    def collect(self, request):
        # Retrieval consumes the stage query, without rewriting B's bound task.
        class StageRag:
            name = "rag"

            def __init__(self, source, query):
                self.source, self.query = source, query

            def collect(self, request, *, limit):
                return self.source.collect(replace(request, query=self.query), limit=limit)

        sources = [StageRag(source, self.query) if name == "rag" else source
                   for name, source in self.sources.items()]
        items, statuses = SourceRegistry(sources).collect(request)
        self.audit = deepcopy(getattr(items, "source_audit", {}))
        return items, statuses


class StageCollector(ContextCollector):
    def __init__(self, counter, feedback):
        super().__init__(counter)
        self.feedback = feedback

    def collect(self, **sources):
        items = super().collect(**sources)
        items = [item.model_copy(update={
            "metadata": {**item.metadata, "transitional": True},
            "references": [*item.references, "transitional:legacy-rag-pack/v1"],
        }) if item.source == "rag_evidence" else item for item in items]
        if self.feedback:
            reference = "structured-output-feedback:sha256:" + sha256(self.feedback.encode("utf-8")).hexdigest()
            items.append(ContextItem(id="stage:retry_feedback", type="business_state", source="retry_feedback",
                content=self.feedback, token_count=self.counter.count(self.feedback),
                required=True, compressible=False, references=[reference]))
        return items


class StageSelector(ContextSelector):
    def __init__(self, counter, registry):
        super().__init__(counter)
        self.registry = registry
        self.original = None
        self.previous = []
        self.trace_context = None

    def _quota_source(self, item):
        return "rag" if item.source in {"rag", "rag_evidence"} else item.source

    def _selection_key(self, item):
        return (not item.required, -item.priority, item.source != "rag", -item.relevance)

    def _drop_key(self, item):
        return (item.priority, item.source == "rag", item.relevance, -item.token_count)

    def build(self, items, policy, trace_context=None, *, intent=None, source_audit=None):
        # Existing required evidence wins; otherwise formal evidence reaches
        # the unchanged Dedup before optional transitional evidence.
        ordered = [item for item in items if item.source != "rag_evidence" or item.required]
        ordered += [item for item in items if item.source == "rag_evidence" and not item.required]
        first = self.original is None
        if first:
            self.original = list(ordered)
            self.trace_context = dict(trace_context or {})
        selected = super().build(ordered, policy, trace_context, intent=intent, source_audit=self.registry.audit)
        entries = list(selected.trace.entries)
        # The existing public WorldLine schema allows 30 sources. Required
        # graph/fact evidence stays intact; trim optional evidence only.
        evidence = [item for item in selected.items if item.type == "rag_evidence"]
        if len(evidence) > 30:
            optional = sorted((item for item in evidence if not item.required), key=lambda item: (
                item.source == "rag", item.priority, item.relevance))
            victims = optional[:len(evidence) - 30]
            if len(victims) < len(evidence) - 30:
                raise ValueError("WorldLine required evidence exceeds source budget boundary")
            victim_ids = {item.id for item in victims}
            entries = [event for event in entries if event.stage != "final"]
            entries += [ContextTraceEntry(item_id=item.id, stage="dropped", reason="worldline_source_limit",
                token_before=item.token_count, token_after=0, **(trace_context or {})) for item in victims]
            selected = super().build([item for item in selected.items if item.id not in victim_ids],
                policy, trace_context, intent=intent, source_audit=self.registry.audit)
            entries += [event for event in selected.trace.entries if event.stage not in {"collected", "selected"}]
        if not first:
            entries = [*self.previous, *[event for event in entries if event.stage not in {"collected", "selected"}]]
        self.previous = [event for event in entries if event.stage != "final"]
        manifest = context_manifest(self.original, selected.items, entries, selected.total_tokens,
                                    selected.budget, source_audit=self.registry.audit)
        return selected.model_copy(update={"trace": ContextTrace(entries=entries), "manifest": manifest,
            "dropped_items": list(dict.fromkeys(event.item_id for event in entries if event.stage == "dropped")),
            "compressed_items": list(dict.fromkeys(event.item_id for event in entries if event.stage == "compressed"))})

    def reselect(self, pack, policy):
        # JSON source labels/URLs are counted at the final framing gate. A
        # large excess must drop optional evidence rather than accidentally
        # shrinking the selector below its required-only assembled floor.
        required = self.assembler.assemble([item for item in pack.items if item.required], intent=pack.intent)
        floor = sum(self.counter.count(str(message.content))
                    + (self.counter.count(_serialize(message.tool_calls))
                       if isinstance(message, AIMessage) and message.tool_calls else 0) for message in required)
        policy = policy.model_copy(update={"total_input_budget": max(policy.total_input_budget,
            floor + policy.reserved_output_tokens + policy.safety_margin)})
        selected = self.build(pack.items, policy, self.trace_context, intent=pack.intent)
        previous = PackManifest.model_validate(pack.manifest).agent_context
        if previous is not None:
            envelope = previous.envelope
            if envelope is not None:
                envelope = envelope.model_copy(update={"selected_fields": tuple(
                    item.metadata["field_path"] for item in selected.items if item.source == "received_envelope")})
            audit = previous.model_copy(update={"envelope": envelope, "source_selection": tuple(
                SourceSelectionManifest(item_id=item.id, source=item.source, type=item.type,
                    references=tuple(item.references), final_tokens=item.token_count,
                    profile_version=item.metadata.get("version") if item.type == "user_profile" else None)
                for item in selected.items)})
            manifest = PackManifest.model_validate({**selected.manifest, "agent_context": audit}).model_dump(mode="json")
            selected = selected.model_copy(update={"manifest": manifest})
        active_manifest.set(selected.manifest)
        return selected


def stage_prompts(phase, instructions, *, retry=False, exploration=False):
    from app.rag.query_understanding.analyzer import QUERY_ANALYSIS_PROMPT_V1

    base, references = {
        "query_analysis": (QUERY_ANALYSIS_PROMPT_V1, ("query-analysis/v1",)),
        "narrative": (NARRATIVE_SYSTEM_PROMPT, ("explorer-narrative/v2",)),
        "branches": (BRANCH_SYSTEM_PROMPT, ("explorer-branches/v1",)),
    }[phase]
    if exploration:
        base = EXPLORATION_BRANCH_BASE_V1 + "\n" + EXPLORATION_BRANCH_RULE_V1
        references = ("explorer-branches-exploration/v1", "explorer-exploration-rules/v1")
    if retry:
        suffix = "\n" + WORLDLINE_RETRY_RULE_V1
        if not instructions.endswith(suffix):
            raise ValueError("Explorer retry prompt must match its static rule")
        instructions = instructions[:-len(suffix)]
        references += ("explorer-retry/v1",)
    if instructions == base:
        return references, None
    if instructions.startswith(base + "\n"):
        return references, instructions[len(base) + 1:]
    raise ValueError("Explorer stage prompt must match its static binding")


def render_feedback(pack, renderer):
    feedback = next((item.content for item in pack.items if item.source == "retry_feedback"), None)
    # Keep Pack invariants intact; renderers ignore feedback as ordinary data.
    inputs, instructions = renderer(pack)
    if feedback:
        suffix = "\n" + WORLDLINE_RETRY_RULE_V1
        if instructions.endswith(suffix):
            instructions = instructions[:-len(suffix)] + "\n" + feedback + suffix
        else:
            instructions += "\n" + feedback
    return inputs, instructions


async def prepare_explorer_input(phase, *, query, instructions, schema, schema_name,
                                 renderer, inputs=None, retry=False, exploration=False,
                                 registry=None, policy=None, trace_context=None):
    references, feedback = stage_prompts(phase, instructions, retry=retry, exploration=exploration)
    ledger = active_ledger.get()
    snapshot = active_hop.get()
    if snapshot is not None:
        snapshot.require_current()
        task = RouteTask.model_validate_json(snapshot.task_json)
        if snapshot.owner_agent != "explorer" or task.route_key is not RouteKey.WORLDLINE:
            raise SourceIntegrityError()
        run_id = snapshot.run_id
    else:
        from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter
        if len(query) > 4000 or UnicodeHeuristicTokenCounter().count(query) > POLICIES[RouteKey.WORLDLINE].input_budget:
            raise ValueError("Explorer required query exceeds input budget boundary")
        run_id = ledger.run_id if ledger is not None else uuid4().hex
        task = RouteTask(task_id="explorer:" + run_id, route_key=RouteKey.WORLDLINE, input_text=query)
    inputs = (inputs or AgentPackInputs()).model_copy(deep=True)
    if inputs.rag_pack is not None:
        from types import SimpleNamespace
        # Collect formal RAG again rather than reusing a previous selected pack.
        rows = [row for row in inputs.rag_pack.items if not isinstance(row, dict)
                or not row.get("_prior_formal_source") or row.get("id") in (inputs.rag_required_ids or set())]
        inputs = inputs.model_copy(update={"rag_pack": SimpleNamespace(items=rows)})
    if phase == "query_analysis":
        inputs = inputs.model_copy(update={"rag_pack": None, "rag_required_ids": None})
        if inputs.structured_output == {"query": task.input_text}:
            # Standalone/canonical query is already a required user_query.
            # A distinct analysis query remains a separate required datum.
            inputs = inputs.model_copy(update={"structured_output": None})
    context = dict(inputs.continuity_context or {})
    registered = inputs.registered_items
    if registered is None:
        registered = [ContextItem.model_validate(row) for row in context.get("entry_candidates", [])]
    # Old retrieval candidates lack the identity tuple needed for cache reuse.
    registered = [item for item in registered if item.type != "rag_evidence"]
    context.pop("entry_candidates", None)
    context.pop("entry_source_audit", None)
    inputs = inputs.model_copy(update={"continuity_context": context, "registered_items": registered})
    default_sources = ([ReceivedEnvelopeSource(), SessionViewSource()] if snapshot is not None else [])
    if phase != "query_analysis":
        default_sources.append(RagSource())
    if registry is not None:
        default_sources = list(registry.sources.values())
        if phase == "query_analysis":
            default_sources = [source for source in default_sources if source.name != "rag"]
    registry = StageRegistry(default_sources, query)
    builder = AgentPackBuilder()
    builder.collector = StageCollector(builder.counter, feedback)
    builder.selector = StageSelector(builder.counter, registry)
    value = AgentPackInvocation(owner_agent="explorer", run_id=run_id, task=task, phase=phase,
        recipe_id="explorer/v1", recipe_version="v1", prompt_refs=references, inputs=inputs)

    def build(selected_policy):
        if snapshot is not None:
            return build_hop_pack(value, builder=builder, registry=registry,
                                  policy=selected_policy, trace_context=trace_context).pack
        candidates, _ = registry.collect(SourceRequest(query, context,
            allow_new_sources=ledger is None or "explorer" not in ledger.source_stops))
        prepared = value.model_copy(update={"inputs": inputs.model_copy(update={
            "registered_items": [*registered, *candidates]})})
        # Formal RAG is explicitly gated to this route. When it is absent,
        # preserve the old standalone renderer's instructions byte for byte.
        intent = ({"routes": [RouteKey.WORLDLINE.value]}
                  if any(item.source == "rag" for item in candidates) and "entry_intent" not in context else None)
        return builder.build(prepared, policy=selected_policy, trace_context=trace_context, intent=intent)

    async def build_async(selected_policy):
        return await asyncio.to_thread(build, selected_policy)

    policy = policy or POLICIES[RouteKey.WORLDLINE]
    policy = policy.model_copy(update={"source_budgets": {
        **policy.source_budgets, "rag": min(5000, policy.source_budgets.get("rag", 5000),
                                          policy.source_budgets.get("rag_evidence", 5000)),
    }})
    return await prepare_pack_model_input(build_async, schema=schema, schema_name=schema_name,
        renderer=lambda pack: render_feedback(pack, renderer), policy=policy,
        reselect=builder.selector.reselect)
