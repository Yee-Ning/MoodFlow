"""Versioned, immutable references to code-owned prompt text, never a Source."""

from dataclasses import dataclass
from hashlib import sha256
from types import MappingProxyType

from app.agent.foundation.context_contracts import AgentOwner, AgentPhase, PromptManifest
from app.agent.foundation.subgraphs.current_song.assistant import CURRENT_SONG_SYSTEM_PROMPT
from app.agent.foundation.subgraphs.general_chat.assistant import (
    GENERAL_CHAT_RESULT_EVIDENCE_PROMPT,
    GENERAL_CHAT_SYSTEM_PROMPT,
)
from app.agent.foundation.subgraphs.tool_calling.assistant import (
    SEARCH_ONLY_PROMPT,
    SONG_PLAYBACK_PROMPT,
    TOOL_CALLING_SYSTEM_PROMPT,
)
from app.agent.foundation.subgraphs.worldline.prompts import (
    BRANCH_SYSTEM_PROMPT, EXPLORATION_BRANCH_BASE_V1, EXPLORATION_BRANCH_RULE_V1,
    NARRATIVE_SYSTEM_PROMPT, WORLDLINE_RETRY_RULE_V1,
)
from app.agent.handoff.prompts import (
    GENERAL_CHAT_HANDOFF_READ_PROMPT,
    HANDOFF_TRANSFER_PROMPT,
    TRANSFER_RETRY_PROMPT,
)
from app.agent.handoff.curator_prompts import PLAN_PROMPT, SELECTION_PROMPT
from app.rag.query_understanding.analyzer import QUERY_ANALYSIS_PROMPT_V1


@dataclass(frozen=True)
class PromptBinding:
    prompt_id: str
    text: str
    owners: tuple[AgentOwner, ...]
    phases: tuple[AgentPhase, ...]
    version: str = "v1"
    variant: str | None = None
    separator: str = ""

    @property
    def reference(self) -> str:
        return f"{self.prompt_id}/{self.version}"

    @property
    def manifest(self) -> PromptManifest:
        return PromptManifest(prompt_id=self.prompt_id, version=self.version, variant=self.variant,
                              content_digest="sha256:" + sha256(self.text.encode("utf-8")).hexdigest())


_BINDINGS = (
    PromptBinding("general-chat-base", GENERAL_CHAT_SYSTEM_PROMPT, ("general_chat",), ("answer", "read_observation")),
    PromptBinding("general-chat-result-evidence", GENERAL_CHAT_RESULT_EVIDENCE_PROMPT, ("general_chat",),
                  ("answer", "read_observation"), variant="result-evidence"),
    PromptBinding("general-chat-handoff-read", GENERAL_CHAT_HANDOFF_READ_PROMPT, ("general_chat",),
                  ("answer", "read_observation"), variant="handoff-read"),
    PromptBinding("curator-tools-base", TOOL_CALLING_SYSTEM_PROMPT, ("curator",), ("tool_step", "tool_observation")),
    PromptBinding("curator-plan", PLAN_PROMPT, ("curator",), ("tool_step",)),
    PromptBinding("curator-selection", SELECTION_PROMPT, ("curator",), ("tool_observation",)),
    PromptBinding("search-only", SEARCH_ONLY_PROMPT, ("curator",), ("tool_step", "tool_observation"),
                  variant="search-only", separator="\n"),
    PromptBinding("song-playback", SONG_PLAYBACK_PROMPT, ("curator",), ("tool_step", "tool_observation"),
                  variant="song-playback", separator="\n"),
    PromptBinding("curator-current-song", CURRENT_SONG_SYSTEM_PROMPT, ("curator",), ("song_analysis",)),
    PromptBinding("query-analysis", QUERY_ANALYSIS_PROMPT_V1, ("explorer",), ("query_analysis",)),
    PromptBinding("explorer-narrative", NARRATIVE_SYSTEM_PROMPT, ("explorer",), ("narrative",)),
    PromptBinding("explorer-branches", BRANCH_SYSTEM_PROMPT, ("explorer",), ("branches",)),
    PromptBinding("explorer-branches-exploration", EXPLORATION_BRANCH_BASE_V1, ("explorer",), ("branches",)),
    PromptBinding("explorer-exploration-rules", EXPLORATION_BRANCH_RULE_V1, ("explorer",), ("branches",),
                  variant="exploration", separator="\n"),
    PromptBinding("explorer-retry", WORLDLINE_RETRY_RULE_V1, ("explorer",), ("narrative", "branches"),
                  variant="retry", separator="\n"),
    PromptBinding("handoff-transfer", HANDOFF_TRANSFER_PROMPT, ("general_chat", "curator", "explorer"), ("relay",)),
    PromptBinding("transfer-retry", TRANSFER_RETRY_PROMPT, ("general_chat", "curator", "explorer"),
                  ("relay",), variant="retry"),
)
PROMPT_BINDINGS = MappingProxyType({binding.reference: binding for binding in _BINDINGS})


def bind_prompts(references: tuple[str, ...], *, owner: AgentOwner, phase: AgentPhase) -> tuple[str, tuple[PromptManifest, ...]]:
    """Resolve explicit static references; no file/config/source/model reads."""
    if not references or len(set(references)) != len(references):
        raise ValueError("prompt references must be nonempty and unique")
    bindings = []
    for reference in references:
        try:
            binding = PROMPT_BINDINGS[reference]
        except KeyError:
            raise ValueError("unknown static prompt reference") from None
        if owner not in binding.owners or phase not in binding.phases:
            raise ValueError("prompt does not belong to this owner and phase")
        bindings.append(binding)
    if bindings[0].variant is not None or any(binding.variant is None for binding in bindings[1:]):
        raise ValueError("one base prompt must precede its static variants")
    if {binding.variant for binding in bindings} >= {"search-only", "song-playback"}:
        raise ValueError("mutually exclusive prompt variants")
    return "".join(binding.separator + binding.text for binding in bindings), tuple(binding.manifest for binding in bindings)
