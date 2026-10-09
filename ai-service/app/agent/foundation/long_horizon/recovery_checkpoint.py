"""Public recovery evidence, deliberately insufficient to execute a graph."""

from typing import Literal

from pydantic import Field, model_validator

from .models import AgentId, ContinuityContract, HistoryPointer


class RecoveryHop(ContinuityContract):
    from_agent: AgentId
    to_agent: AgentId
    transfer_id: str = Field(pattern=r"^transfer:[0-9a-f]{64}$")
    event_seq: int = Field(gt=0)


class RecoveryMissing(ContinuityContract):
    field: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=160)


class RecoveryBudgetRef(ContinuityContract):
    """Query the existing MODEL_USAGE ledger; no balances or usage snapshots."""

    continuity_task_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    event_type: Literal["model_usage"] = "model_usage"


class RecoveryProvenance(ContinuityContract):
    journal_head_event_seq: int = Field(ge=0)
    # Journal has no original transfer payload. Anchor agreement alone cannot
    # prove its SHA-256 preimage, so scheme A can only make this claim false.
    digest_verified: Literal[False] = False


class RecoveryCheckpoint(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str | None = Field(default=None, min_length=1, max_length=128)
    session_id: str = Field(min_length=1, max_length=160)
    conversation_id: str = Field(min_length=1, max_length=200)
    handoff_chain: tuple[RecoveryHop, ...] = Field(default=(), max_length=5)
    accepted_hop_count: int = Field(default=0, ge=0, le=5)
    last_agent: AgentId | None = None
    last_run_id: str | None = Field(default=None, min_length=1, max_length=128)
    run_status: Literal["running", "finalizing", "completed", "needs_input", "failed"] | None = None
    transfer_phase: Literal[
        "proposed", "rejected", "committed", "reconciled", "target_started", "target_finished",
    ] | None = None
    recent_run_chain: tuple[AgentId, ...] = Field(default=(), max_length=6)
    model_usage_refs: tuple[HistoryPointer, ...] = Field(default=(), max_length=64)
    budget_ref: RecoveryBudgetRef | None = None
    missing: tuple[RecoveryMissing, ...] = Field(default=(), max_length=64)
    rebuilt_from: RecoveryProvenance

    @model_validator(mode="after")
    def evidence_is_consistent(self):
        from app.agent.handoff.registry import AGENT_REGISTRY, AgentKey

        if self.accepted_hop_count != len(self.handoff_chain):
            raise ValueError("recovery count requires committed evidence")
        if len({hop.transfer_id for hop in self.handoff_chain}) != len(self.handoff_chain):
            raise ValueError("duplicate recovery transfer")
        seqs = [hop.event_seq for hop in self.handoff_chain]
        if seqs != sorted(set(seqs)) or any(seq > self.rebuilt_from.journal_head_event_seq for seq in seqs):
            raise ValueError("recovery hops must belong to the ordered journal prefix")
        if any(AgentKey(hop.to_agent) not in AGENT_REGISTRY[AgentKey(hop.from_agent)].transfer_targets
               for hop in self.handoff_chain):
            raise ValueError("recovery contains a forbidden transfer edge")
        if self.handoff_chain and self.last_agent != self.handoff_chain[-1].to_agent:
            raise ValueError("recovery owner disagrees with committed evidence")
        if self.recent_run_chain:
            expected = ((self.handoff_chain[0].from_agent, *(hop.to_agent for hop in self.handoff_chain))
                        if self.handoff_chain else (self.last_agent,))
            if self.recent_run_chain != expected or any(
                left.to_agent != right.from_agent
                for left, right in zip(self.handoff_chain, self.handoff_chain[1:])
            ):
                raise ValueError("recovery chain disagrees with committed evidence")
        if self.last_run_id is not None and self.last_run_id != self.run_id:
            raise ValueError("recovery run identity mismatch")
        if self.budget_ref is not None and self.budget_ref.run_id != self.run_id:
            raise ValueError("recovery budget identity mismatch")
        if any(ref.kind != "event" for ref in self.model_usage_refs):
            raise ValueError("recovery usage must reference history events")
        if len(set(self.model_usage_refs)) != len(self.model_usage_refs):
            raise ValueError("duplicate recovery usage reference")
        if len(set(self.missing)) != len(self.missing):
            raise ValueError("duplicate recovery missing marker")
        required = {"original_input", "plan", "hops", "usage", "messages", "transfer_envelope", "execution_checkpoint"}
        if not required <= {item.field for item in self.missing}:
            raise ValueError("recovery must disclose unavailable execution state")
        return self
