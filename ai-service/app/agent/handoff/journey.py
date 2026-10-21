"""Explorer-owned listening paths from WorldLine evidence and catalog records.

This module performs no retrieval, model calls, playback or authorization. The
existing WorldLine subgraph owns all knowledge expansion and catalog reads.
"""

import json
from collections import deque
from datetime import datetime, timezone
from enum import StrEnum
from typing import Literal

from langchain_core.messages import AIMessage
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from app.agent.foundation.models import TaskStatus
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.subgraphs.worldline.models import (
    EntityType,
    EvidenceType,
    MusicEntityFacts,
    WorldLineNodeType,
    WorldLineRelation,
    WorldLineResult,
)
from app.agent.handoff.contracts import TransferArguments
from app.agent.handoff.registry import AgentKey


MAX_TRANSFER_SUMMARY = 2_000
MAX_JOURNEY_NODES = 8


class EvidenceState(StrEnum):
    CATALOG = "catalog_fact"
    KNOWLEDGE = "knowledge_evidence"
    UNVERIFIED = "unverified"


class JourneyModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class JourneyQueueItem(JourneyModel):
    node_id: str = Field(min_length=1, max_length=128)
    node_title: str = Field(min_length=1, max_length=500)
    narrative: str = Field(min_length=1, max_length=240)
    song_id: int | None = Field(default=None, gt=0, strict=True)
    song_title: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def paired_song(self):
        if (self.song_id is None) != (self.song_title is None):
            raise ValueError("song ID and title must be present together")
        return self


class JourneyNarrative(JourneyModel):
    evidence_state: EvidenceState
    statement: str = Field(min_length=1, max_length=240)
    transition: str = Field(min_length=1, max_length=240)
    source_ids: tuple[str, ...] = Field(min_length=1, max_length=8)
    # GeneralChat v1 may later fill its four effect states from actual receipts.
    # Preparing this path supplies no evidence of an applied/confirmed effect.
    effect_state: None = None
    preference_song_ids: tuple[int, ...] = Field(default=(), max_length=8)


class JourneyNode(JourneyQueueItem):
    node_kind: str = Field(pattern="^(playable|knowledge_only)$")
    time_year: int | None = Field(default=None, ge=1000, le=2999)
    time_basis: str | None = None
    narration: JourneyNarrative
    representative_reason: str

    @model_validator(mode="after")
    def playable_requires_song(self):
        if (self.node_kind == "playable") != (self.song_id is not None):
            raise ValueError("only a verified catalog song can be playable")
        return self


class JourneyTransferPayload(JourneyModel):
    schema_version: Literal[1] = 1
    journey_queue: tuple[JourneyQueueItem, ...] = Field(max_length=MAX_JOURNEY_NODES)
    text_fields: Literal["untrusted"] = "untrusted"
    action_authority: Literal[
        "none; requires current user request and catalog validation"
    ] = "none; requires current user request and catalog validation"

    @model_validator(mode="after")
    def bounded_unique_queue(self):
        ids = [item.node_id for item in self.journey_queue]
        if len(ids) != len(set(ids)):
            raise ValueError("journey queue nodes must be unique")
        if len(self.to_summary()) > MAX_TRANSFER_SUMMARY:
            raise ValueError("journey exceeds the existing transfer summary budget")
        return self

    def to_summary(self) -> str:
        return json.dumps(
            self.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")
        )


class ListeningJourney(JourneyModel):
    schema_version: Literal[1] = 1
    root_entity_id: str | None
    ordering: Literal["timeline", "preference_then_timeline"] = "timeline"
    ordering_reason: str = (
        "根实体导览；其余按来源时间递增，未知时间单列；顺序不宣称因果。"
    )
    evidence_state: EvidenceState
    nodes: tuple[JourneyNode, ...] = Field(max_length=MAX_JOURNEY_NODES)
    excluded_node_ids: tuple[str, ...] = ()
    omitted_node_ids: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def transfer_payload(self) -> JourneyTransferPayload:
        return JourneyTransferPayload(
            journey_queue=tuple(
                JourneyQueueItem.model_validate(
                    node.model_dump(include=set(JourneyQueueItem.model_fields))
                )
                for node in self.nodes
            )
        )


def _short(text: str, limit: int = 120) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _song_id(entity) -> int | None:
    parts = entity.entity_id.split(":")
    if (
        entity.entity_type is not EntityType.SONG
        or entity.source != "netease"
        or len(parts) != 3
        or parts[:2] != ["netease", "song"]
        or not parts[2].isascii()
        or not parts[2].isdecimal()
    ):
        return None
    value = int(parts[2])
    return value if value > 0 else None


def _publish_year(value) -> int | None:
    # Java's publish_time contract is epoch milliseconds, never a guessed year.
    if type(value) is not int or value <= 0:
        return None
    try:
        year = datetime.fromtimestamp(value / 1_000, tz=timezone.utc).year
        return year if 1000 <= year <= 2999 else None
    except (ValueError, OverflowError, OSError):
        return None


def build_listening_journey(
    world: WorldLineResult,
    facts: MusicEntityFacts | None,
    *,
    catalog_history: tuple[MusicEntityFacts, ...] = (),
    node_priorities: dict[str, int] | None = None,
    recommendation_reasons: dict[str, str] | None = None,
    preference_song_ids: dict[str, tuple[int, ...]] | None = None,
    node_limit: int = MAX_JOURNEY_NODES,
) -> ListeningJourney:
    if type(node_limit) is not int or not 1 <= node_limit <= MAX_JOURNEY_NODES:
        raise ValueError("journey node limit must stay within the existing bound")
    sources = {item.id: item for item in world.sources}
    graph_nodes = {item.id: item for item in world.graph.nodes}
    catalog = {}
    catalog_node_sources = {}
    catalog_edges = {}
    snapshots = (*catalog_history, *((facts,) if facts is not None else ()))
    for snapshot in snapshots:
        root = graph_nodes.get(snapshot.entity.entity_id)
        if (
            root is None
            or root.name != snapshot.entity.name
            or root.type.value != snapshot.entity.entity_type.value
        ):
            continue
        snapshot_sources = {
            item.id
            for item in snapshot.sources
            if item.source_type is EvidenceType.MUSIC_API
            and sources.get(item.id) == item
        }
        if snapshot_sources:
            for entity in (snapshot.entity, *snapshot.related_entities):
                node = graph_nodes.get(entity.entity_id)
                if (
                    node is not None
                    and node.name == entity.name
                    and node.type.value == entity.entity_type.value
                ):
                    catalog[entity.entity_id] = entity
                    catalog_node_sources.setdefault(entity.entity_id, set()).update(
                        snapshot_sources
                    )
            for item in snapshot.facts:
                if item.evidence_ids and set(item.evidence_ids).issubset(
                    snapshot_sources
                ):
                    key = (item.subject_id, item.object_entity_id, item.predicate)
                    catalog_edges.setdefault(key, set()).update(item.evidence_ids)
    conflicts = set()
    dates = {}
    for relation in (
        *world.era_relations,
        *world.scene_relations,
        *world.influence_relations,
    ):
        target = (
            relation.target_entity
            or getattr(relation, "era", None)
            or getattr(relation, "scene", None)
        )
        if relation.metadata.get("conflict_flags"):
            conflicts.add(
                (
                    relation.source_entity.node_id,
                    target.node_id,
                    relation.relation_type.value,
                )
            )
            continue
        year = getattr(relation, "start_year", None)
        if (
            year is not None
            and target is not None
            and target.node_id != world.graph.root
        ):
            previous = dates.get(target.node_id, (3000, ""))
            dates[target.node_id] = min(
                previous, (year, "relation_evidence_time; not origin or release date")
            )
    for node_id, entity in catalog.items():
        year = _publish_year(entity.metadata.get("publish_time"))
        if year is not None:
            dates[node_id] = (year, "catalog_publish_time")

    backed_edges = []
    node_sources = {key: set(value) for key, value in catalog_node_sources.items()}
    neighbors = {key: set() for key in graph_nodes}
    for edge in world.graph.edges:
        key = (edge.source, edge.target, edge.relation.value)
        if (
            key in conflicts
            or not edge.evidence_ids
            or not set(edge.evidence_ids).issubset(sources)
        ):
            continue
        evidence = {
            item
            for item in edge.evidence_ids
            if (
                sources[item].source_type is EvidenceType.KNOWLEDGE
                and sources[item].excerpt
            )
            or item in catalog_edges.get(key, ())
        }
        if not evidence:
            continue
        backed_edges.append(edge)
        for node_id in (edge.source, edge.target):
            node_sources.setdefault(node_id, set()).update(evidence)
        neighbors[edge.source].add(edge.target)
        neighbors[edge.target].add(edge.source)
    reachable = set()
    pending = deque([world.graph.root])
    while pending:
        node_id = pending.popleft()
        if node_id in reachable or not node_sources.get(node_id):
            continue
        reachable.add(node_id)
        pending.extend(sorted(neighbors[node_id] - reachable))
    ordered = sorted(
        reachable - {world.graph.root},
        key=lambda key: (
            -(node_priorities or {}).get(key, 0),
            dates.get(key, (3000, ""))[0],
            key,
        ),
    )
    if world.graph.root in reachable:
        ordered.insert(0, world.graph.root)
    trusted_songs = {
        key: item for key, item in catalog.items() if _song_id(item) is not None
    }
    catalog_rank = {key: index for index, key in enumerate(catalog)}
    nodes = []
    omitted = []
    for node_id in ordered:
        if len(nodes) >= node_limit:
            omitted.extend(ordered[ordered.index(node_id):])
            break
        node = graph_nodes[node_id]
        evidence_ids = tuple(sorted(node_sources[node_id]))[:8]
        knowledge = any(
            sources[key].source_type is EvidenceType.KNOWLEDGE for key in evidence_ids
        )
        state = EvidenceState.KNOWLEDGE if knowledge else EvidenceState.CATALOG
        candidates = []
        if node_id in trusted_songs:
            candidates.append((0, node_id, "节点本身是已读取的目录歌曲"))
        for edge in backed_edges:
            other = (
                edge.target
                if edge.source == node_id
                else edge.source
                if edge.target == node_id
                else None
            )
            if other not in trusted_songs:
                continue
            representative = edge.source == node_id and edge.relation in {
                WorldLineRelation.REPRESENTATIVE_OF_ERA,
                WorldLineRelation.REPRESENTATIVE_OF_SCENE,
            }
            membership = edge.target == node_id and (
                edge.relation is WorldLineRelation.PERFORMED_BY
                and node.type is WorldLineNodeType.ARTIST
                or edge.relation is WorldLineRelation.BELONGS_TO_ALBUM
                and node.type is WorldLineNodeType.ALBUM
                or edge.relation is WorldLineRelation.BELONGS_TO_GENRE
                and node.type is WorldLineNodeType.GENRE
            )
            if representative or membership:
                candidates.append(
                    (
                        0 if representative else 1,
                        other,
                        "来源明确标记的代表歌曲"
                        if representative
                        else "有来源的所属歌曲，按目录返回顺序选首项",
                    )
                )
        candidates.sort(key=lambda item: (item[0], catalog_rank[item[1]], item[1]))
        song = trusted_songs[candidates[0][1]] if candidates else None
        reason = (
            candidates[0][2]
            if candidates
            else "未找到有关系证据且经目录读取确认的代表歌曲；纯知识节点，只讲解不播放"
        )
        statement = (
            "目录事实：" if state is EvidenceState.CATALOG else "知识证据："
        ) + _short(node.name, 80)
        relevant = next(
            (
                edge
                for edge in backed_edges
                if node_id in (edge.source, edge.target)
                and edge.description
                and (
                    not knowledge
                    or any(
                        sources[key].source_type is EvidenceType.KNOWLEDGE
                        for key in edge.evidence_ids
                    )
                )
            ),
            None,
        )
        if relevant is not None:
            statement += "；" + _short(relevant.description, 100)
        else:
            statement += (
                "；目录已确认该实体，尚无可支持其时代、场景或影响结论的知识证据"
            )
        if (recommendation_reasons or {}).get(node_id):
            statement += "；" + recommendation_reasons[node_id]
        year, time_basis = dates.get(node_id, (None, None))
        transition = (
            "从根实体开始导览。"
            if node_id == world.graph.root
            else f"下一站查看来源记录的 {year} 年关联；不据此推断因果。"
            if year is not None
            else "接着单列时间未核实的节点，不猜测其年代。"
        )
        built = JourneyNode(
            node_id=node_id,
            node_title=node.name,
            narrative=_short(transition + statement, 240),
            song_id=_song_id(song) if song else None,
            song_title=song.name if song else None,
            node_kind="playable" if song else "knowledge_only",
            time_year=year,
            time_basis=time_basis,
            narration=JourneyNarrative(
                evidence_state=state,
                statement=_short(statement, 240),
                transition=transition,
                source_ids=evidence_ids,
                preference_song_ids=(preference_song_ids or {}).get(node_id, ()),
            ),
            representative_reason=reason,
        )
        proposed = [*nodes, built]
        try:
            JourneyTransferPayload(
                journey_queue=tuple(
                    JourneyQueueItem.model_validate(
                        item.model_dump(include=set(JourneyQueueItem.model_fields))
                    )
                    for item in proposed
                )
            )
        except ValidationError:
            # Preserve a deterministic prefix; never split JSON, IDs or queue rows.
            omitted.extend(ordered[ordered.index(node_id) :])
            break
        nodes.append(built)
    overall = (
        EvidenceState.KNOWLEDGE
        if any(
            item.narration.evidence_state is EvidenceState.KNOWLEDGE for item in nodes
        )
        else EvidenceState.CATALOG
        if nodes
        else EvidenceState.UNVERIFIED
    )
    limitations = []
    if overall is not EvidenceState.KNOWLEDGE:
        limitations.append(
            "知识证据缺失或不可用；目录事实不证明风格、时代、场景或影响，相关结论未核实。"
        )
    if conflicts:
        limitations.append("存在冲突的关系未用于路径或歌曲映射。")
    if omitted:
        limitations.append(
            f"按本轮要求保留最多{node_limit}个有来源节点；没有补写未核实的节点。"
            if node_limit < MAX_JOURNEY_NODES and len(nodes) == node_limit else
            "路径为有界前缀，达到既有转交载荷预算；没有截断歌曲 ID 或 JSON。"
        )
    return ListeningJourney(
        root_entity_id=world.root_entity.entity_id,
        ordering="preference_then_timeline" if node_priorities else "timeline",
        ordering_reason="根实体导览；红心命中关系权重优先，同权重按来源时间，未知时间单列。"
        if node_priorities
        else ListeningJourney.model_fields["ordering_reason"].default,
        evidence_state=overall,
        nodes=tuple(nodes),
        excluded_node_ids=tuple(sorted(set(graph_nodes) - reachable)),
        omitted_node_ids=tuple(omitted),
        limitations=tuple(limitations),
    )


def render_journey(journey: ListeningJourney) -> str:
    if not journey.nodes:
        if journey.omitted_node_ids:
            return "路径内容超过既有转交载荷预算，未生成队列；请缩小探索范围。"
        return "没有可核实来源的探索节点，暂时不能生成聆听路径；请提供可识别的歌曲、歌手或专辑。"
    lines = [
        "聆听路径（根实体导览＋红心关系权重/来源时间线；未知时间单列）："
        if journey.ordering == "preference_then_timeline"
        else "聆听路径（根实体导览＋来源时间线；未知时间单列）："
    ]
    for index, node in enumerate(journey.nodes, 1):
        song = (
            f"代表歌曲《{node.song_title}》（目录 ID {node.song_id}，未执行播放）"
            if node.song_id
            else "纯知识节点，只讲解不播放"
        )
        lines.append(
            f"{index}. {node.node_title}：{node.narrative}；{song}。来源：{', '.join(node.narration.source_ids[:2])}"
        )
    lines.extend(journey.limitations)
    return "\n".join(lines)


def attach_journey_transfer(message: AIMessage, run) -> AIMessage:
    """Replace only Explorer's summary; never alter target/reason/task/edge."""
    if (
        run.current_agent is not AgentKey.EXPLORER
        or message.invalid_tool_calls
        or len(message.tool_calls) != 1
    ):
        return message
    call = message.tool_calls[0]
    if call.get("name") != "transfer_to_curator_agent":
        return message
    try:
        arguments = TransferArguments.model_validate(call.get("args"))
    except ValidationError:
        return message
    result = next(
        (
            item
            for item in reversed(run.results)
            if item.route_key == RouteKey.WORLDLINE
            and item.status is TaskStatus.SUCCESS
            and "listening_journey" in item.data
        ),
        None,
    )
    if result is None:
        return message
    journey = ListeningJourney.model_validate(result.data["listening_journey"])
    if not journey.nodes:
        return message
    return message.model_copy(
        update={
            "tool_calls": [
                {
                    **call,
                    "args": {
                        **arguments.model_dump(),
                        "context_summary": journey.transfer_payload().to_summary(),
                    },
                }
            ]
        }
    )
