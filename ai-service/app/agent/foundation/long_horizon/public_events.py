"""Public schemas only: never serialize a model response or an execution state."""

from typing import Any, Literal

from pydantic import Field, model_validator

from .models import ContinuityContract, HistoryPointer, RecoveryGap


class PublicText(ContinuityContract):
    schema_version: Literal[1] = 1
    text: str = Field(default="", max_length=12000)
    artifact_ref: HistoryPointer | None = None


class PublicDecision(ContinuityContract):
    schema_version: Literal[1] = 1
    decision_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=4000)
    kind: Literal["decision", "constraint"] = "decision"
    effect: Literal["add", "revoke", "supersede"] = "add"
    supersedes: tuple[HistoryPointer, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def replacement_has_reference(self):
        if self.effect != "add" and not self.supersedes:
            raise ValueError("constraint replacement requires explicit references")
        return self


class PublicRoute(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(max_length=128)
    tasks: tuple[tuple[str, Literal["general_chat", "current_song", "tool_calling", "worldline"]], ...] = Field(max_length=32)
    needs_clarification: bool


class PublicTool(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(max_length=128)
    operation_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=80)
    parameters: dict[str, Any] = Field(default_factory=dict)
    status: Literal["proposed", "success", "failed", "unknown"]
    effect: Literal["read", "write", "client_action"]
    result_ref: HistoryPointer | None = None
    baseline_ref: HistoryPointer | None = None
    reconciliation_ref: HistoryPointer | None = None
    confirmation_source: Literal["response", "readback"] | None = None
    error_code: str | None = Field(default=None, max_length=80)
    error_message: str | None = Field(default=None, max_length=1000)
    auth_scope: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def public_parameters_only(self):
        from app.agent.foundation.subgraphs.tool_calling.assistant import all_tool_calling_tools

        tools = {tool.name: tool for tool in all_tool_calling_tools()}
        if self.name == "get_current_song_context":
            from app.agent.handoff.song_context import SongContextArguments
            SongContextArguments.model_validate(self.parameters)
            if self.effect != "read":
                raise ValueError("context tool is read-only")
            return self
        if self.name not in tools:
            raise ValueError("unknown public tool")
        if self.name == "get_liked_songs" and self.effect != "read":
            raise ValueError("cloud like lookup is read-only")
        schema = tools[self.name].tool_call_schema
        if set(self.parameters) - set(schema.model_fields):
            raise ValueError("non-public tool arguments")
        parsed = schema.model_validate(self.parameters)
        object.__setattr__(self, "parameters", parsed.model_dump(mode="json"))
        return self


class PublicActionAck(ContinuityContract):
    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=160)
    status: Literal["applied", "failed"]
    action_ref: HistoryPointer


class PublicAction(ContinuityContract):
    schema_version: Literal[1] = 1
    action_id: str = Field(min_length=1, max_length=160)
    name: str = Field(max_length=80)
    arguments_digest: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @model_validator(mode="after")
    def known_action(self):
        from app.schemas.client_actions import ClientActionName
        ClientActionName(self.name)
        return self


class PublicRevalidation(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(max_length=128)
    field_results: dict[str, Literal["valid", "changed", "unknown"]]
    transfer_integrity: Literal["valid", "uncommitted", "missing_receipt", "conflict", "unknown"]
    action: Literal["resume_target", "resume_source", "refresh_facts", "needs_input", "stop"]
    reason: str | None = Field(default=None, max_length=160)
    checkpoint_id: str | None = Field(default=None, max_length=160)
    receipt_gaps: tuple[RecoveryGap, ...] = Field(default=(), max_length=64)
    revalidation_reasons: tuple[str, ...] = Field(default=(), max_length=32)


class PublicUsage(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(max_length=128)
    agent: Literal["general_chat", "curator", "explorer"]
    call_id: str = Field(min_length=1, max_length=160)
    phase: Literal["baseline", "reserved", "settled", "retry"]
    tokens: int = Field(ge=0)
    reported: bool = False
    model_calls: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)
    reported_tokens: int = Field(default=0, ge=0)
    estimated_tokens: int = Field(default=0, ge=0)


class PublicCheckpoint(ContinuityContract):
    schema_version: Literal[1] = 1
    checkpoint_id: str = Field(max_length=160)
    run_id: str = Field(max_length=128)
    fence: int = Field(gt=0)
    revision: int = Field(ge=0)


class PublicContextTrace(ContinuityContract):
    schema_version: Literal[1] = 1
    selected_tokens: int = Field(ge=0)
    candidate_tokens: int = Field(ge=0)
    budget: int = Field(ge=0)
    dropped_count: int = Field(ge=0)


class PublicArchive(ContinuityContract):
    schema_version: Literal[1] = 1
    first_cursor: int = Field(ge=0)
    last_cursor: int = Field(gt=0)
    count: int = Field(gt=0, le=200)
    event_refs: tuple[HistoryPointer, ...] = Field(max_length=200)


class PublicMigration(ContinuityContract):
    schema_version: Literal[1] = 1
    run_id: str = Field(max_length=128)
    source_session: str = Field(max_length=160)
    target_session: str = Field(max_length=160)
    source_checkpoint: str = Field(max_length=160)
    target_checkpoint: str = Field(max_length=160)
    phase: Literal["proposed", "committed"]


class PublicSessionHandoff(ContinuityContract):
    schema_version: Literal[1] = 1
    handoff_id: str = Field(max_length=160)
    target_session: str = Field(max_length=160)
    reason: Literal["token_threshold", "explicit_checkpoint", "artifact_created"]


class SafeTextArtifact(ContinuityContract):
    schema_version: Literal[1] = 1
    text: str = Field(max_length=100000)
