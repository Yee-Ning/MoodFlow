"""Agent identities are internal; RouteKey remains the public capability key."""

from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask


class AgentKey(StrEnum):
    GENERAL_CHAT = "general_chat"
    CURATOR = "curator"
    EXPLORER = "explorer"


class ToolDomain(StrEnum):
    SONG_CONTEXT = "song_context"
    MUSIC = "music"
    CLIENT_ACTION = "client_action"
    WORLDLINE = "worldline"


@dataclass(frozen=True)
class AgentSpec:
    node_name: str
    capabilities: frozenset[RouteKey]
    tool_domains: frozenset[ToolDomain]
    transfer_targets: frozenset[AgentKey]


AGENT_REGISTRY = MappingProxyType(
    {
        AgentKey.GENERAL_CHAT: AgentSpec(
            "general_chat_agent",
            frozenset({RouteKey.GENERAL_CHAT}),
            frozenset({ToolDomain.SONG_CONTEXT}),
            frozenset({AgentKey.CURATOR}),
        ),
        AgentKey.CURATOR: AgentSpec(
            "curator_agent",
            frozenset({RouteKey.CURRENT_SONG, RouteKey.TOOL_CALLING}),
            frozenset(
                {ToolDomain.SONG_CONTEXT, ToolDomain.MUSIC, ToolDomain.CLIENT_ACTION}
            ),
            frozenset({AgentKey.GENERAL_CHAT, AgentKey.EXPLORER}),
        ),
        AgentKey.EXPLORER: AgentSpec(
            "explorer_agent",
            frozenset({RouteKey.WORLDLINE}),
            frozenset({ToolDomain.SONG_CONTEXT, ToolDomain.WORLDLINE}),
            frozenset({AgentKey.CURATOR}),
        ),
    }
)

CAPABILITY_OWNERS = MappingProxyType(
    {
        capability: agent
        for agent, spec in AGENT_REGISTRY.items()
        for capability in spec.capabilities
    }
)


def transfer_tool_name(target: AgentKey) -> str:
    return f"transfer_to_{target.value}_agent"


def next_agent_for_task(source: AgentKey, capability: RouteKey) -> AgentKey | None:
    """Use one allowed edge toward the owner, including a bounded Curator relay."""
    owner = CAPABILITY_OWNERS[capability]
    if source == owner:
        return None
    targets = AGENT_REGISTRY[source].transfer_targets
    if owner in targets:
        return owner
    for target in sorted(targets):
        if owner in AGENT_REGISTRY[target].transfer_targets:
            return target
    raise ValueError("capability owner is unreachable through allowed edges")


def task_tool_domains(agent: AgentKey, capability: RouteKey) -> frozenset[ToolDomain]:
    """Relays have transfer tools only; song analysis remains read-only."""
    if CAPABILITY_OWNERS[capability] != agent:
        return frozenset()
    if capability is RouteKey.CURRENT_SONG:
        return frozenset({ToolDomain.SONG_CONTEXT})
    return AGENT_REGISTRY[agent].tool_domains


@dataclass(frozen=True)
class AgentAssignment:
    agent: AgentKey
    task: RouteTask


def bind_route_tasks(decision: RouteDecision) -> tuple[AgentAssignment, ...]:
    """Keep order, repeated capabilities, IDs and dependencies from the router."""
    return tuple(
        AgentAssignment(CAPABILITY_OWNERS[task.route_key], task.model_copy(deep=True))
        for task in decision.tasks
    )
