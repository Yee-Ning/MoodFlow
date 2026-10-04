"""Read cloud hearts and derive bounded preferences only along supported relations."""

from types import SimpleNamespace
from uuid import uuid4

from langchain_core.messages import ToolMessage

from app.agent.foundation.subgraphs.worldline.models import (
    EvidenceType,
    WorldLineNodeType,
)
from app.agent.handoff.journey import _song_id
from app.schemas.music import LikedSongsResult
from app.tools.music import get_liked_songs_tool
from app.agent.foundation.long_horizon.execution import ContinuityConflict


async def read_likes(runtime):
    if not getattr(runtime.context, "session_cookie", None):
        return None, {
            "status": "unavailable",
            "source": "get_liked_songs",
            "liked_count": None,
        }

    async def execute(_):
        content = await get_liked_songs_tool.coroutine(runtime=runtime)
        return ToolMessage(
            content=content, name="get_liked_songs", tool_call_id=call_id
        )

    call_id = "explorer-likes:" + uuid4().hex
    try:
        from app.agent.foundation.long_horizon.operations import active_operations

        operations = active_operations.get()
        if operations is None:
            content = (await execute(None)).content
        else:
            result = await operations.call(
                SimpleNamespace(
                    tool_call={"id": call_id, "name": "get_liked_songs", "args": {}}
                ),
                execute,
            )
            content = result.content
        likes = LikedSongsResult.model_validate_json(content)
        return likes, {
            "status": "available" if likes.song_ids else "empty",
            "source": "get_liked_songs",
            "liked_count": len(likes.song_ids),
        }
    except ContinuityConflict:
        raise
    except Exception:
        # Auth/read failures are an honest personalization fallback, not evidence
        # of an empty account. Never expose transport exception text or cookies.
        return None, {
            "status": "unavailable",
            "source": "get_liked_songs",
            "liked_count": None,
        }


def preference_weights(world, snapshots, likes):
    if likes is None or not likes.song_ids:
        return {}, {}, {}
    graph = {node.id: node for node in world.graph.nodes}
    sources = {item.id: item for item in world.sources}
    catalog, api_edges = {}, set()
    for facts in snapshots:
        ids = {
            item.id
            for item in facts.sources
            if item.source_type is EvidenceType.MUSIC_API
            and sources.get(item.id) == item
        }
        if not ids:
            continue
        for item in (facts.entity, *facts.related_entities):
            node = graph.get(item.entity_id)
            if (
                node
                and node.type.value == item.entity_type.value
                and node.name == item.name
            ):
                catalog[item.entity_id] = item
        api_edges.update(
            (item.subject_id, item.object_entity_id, item.predicate)
            for item in facts.facts
            if item.evidence_ids and set(item.evidence_ids).issubset(ids)
        )
    edges = [
        edge
        for edge in world.graph.edges
        if edge.evidence_ids
        and set(edge.evidence_ids).issubset(sources)
        and (
            any(
                sources[key].source_type is EvidenceType.KNOWLEDGE
                and sources[key].excerpt
                for key in edge.evidence_ids
            )
            or (edge.source, edge.target, edge.relation.value) in api_edges
        )
    ]
    conflicts = set()
    for relation in (
        *world.era_relations,
        *world.scene_relations,
        *world.influence_relations,
    ):
        if relation.metadata.get("conflict_flags"):
            target = (
                relation.target_entity
                or getattr(relation, "era", None)
                or getattr(relation, "scene", None)
            )
            conflicts.add(
                (
                    relation.source_entity.node_id,
                    target.node_id,
                    relation.relation_type.value,
                )
            )
    edges = [
        edge
        for edge in edges
        if (edge.source, edge.target, edge.relation.value) not in conflicts
    ]
    weights, reasons, matched = {}, {}, {}

    def mark(node_id, score, song):
        if score > weights.get(node_id, 0):
            weights[node_id] = score
            reasons[node_id] = f"因你红心收藏《{song.name}》，沿已有来源关系优先推荐。"
        matched.setdefault(node_id, set()).add(_song_id(song))

    for song in catalog.values():
        if _song_id(song) not in set(likes.song_ids):
            continue
        mark(song.entity_id, 4, song)
        artists = set()
        for edge in edges:
            if (
                edge.source == song.entity_id
                and edge.relation.value == "performed_by"
                and graph[edge.target].type is WorldLineNodeType.ARTIST
            ):
                artists.add(edge.target)
                mark(edge.target, 3, song)
            if edge.target == song.entity_id and edge.relation.value in {
                "representative_of_era",
                "representative_of_scene",
            }:
                mark(edge.source, 2, song)
            if edge.source == song.entity_id and edge.relation.value in {
                "belongs_to_genre",
                "belongs_to_era",
            }:
                mark(edge.target, 2, song)
        for edge in edges:
            if (
                edge.source in artists
                and edge.relation.value
                in {
                    "associated_with",
                    "belongs_to_genre",
                    "belongs_to_era",
                    "career_phase",
                    "style_era_association",
                }
                and graph[edge.target].type
                in {WorldLineNodeType.GENRE, WorldLineNodeType.ERA}
            ):
                mark(edge.target, 2, song)
    return (
        weights,
        reasons,
        {key: tuple(sorted(value))[:8] for key, value in matched.items()},
    )


def preference_note(receipt, weights):
    if receipt["status"] == "unavailable":
        return "红心读取不可用，未推断你的偏好；使用通用路径。"
    if receipt["status"] == "empty":
        return "本次红心列表为空；使用通用路径。"
    if not weights:
        return (
            "红心中没有命中本图可信目录歌曲；未推断图外风格或时代偏好，使用通用路径。"
        )
    return "按本次云端红心与已有来源关系优先排序；同权重仍按时间线，未核实年代单列。"
