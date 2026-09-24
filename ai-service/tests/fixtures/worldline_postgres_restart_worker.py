"""One isolated process in the PostgreSQL WorldLine restart acceptance test."""

import asyncio
import json
import sys
from unittest.mock import patch

from app.agent.checkpointing import close_runtime_checkpointer, initialize_runtime_checkpointer
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType, ResolvedEntity
from app.agent.foundation.subgraphs.worldline.session import get_worldline_session_service
from app.config.settings import load_runtime_environment
from tests.test_worldline_genre_session import document_for_artist, genre_executor
from tests.test_worldline_graph import (
    EmptyRetriever,
    FakeMusicService,
    SemanticCitationGenerator,
    SemanticDocsRetriever,
    entity,
)
from tests.test_worldline_session import Catalog, Generator


async def run() -> None:
    load_runtime_environment()
    await initialize_runtime_checkpointer()
    try:
        mode = sys.argv[1]
        genre_mode = mode.startswith("genre_")
        if genre_mode:
            fake_turn = create_worldline_subgraph(
                music_service=FakeMusicService(
                    entity(2, EntityType.ARTIST, "周杰伦")
                ),
                retriever=SemanticDocsRetriever(),
                generator=SemanticCitationGenerator(),
            )
            eason = ResolvedEntity(
                entity_id="netease:artist:9",
                entity_type=EntityType.ARTIST,
                name="陈奕迅",
                artist="陈奕迅",
                source="netease",
            )
            executor = genre_executor(
                documents=[
                    document_for_artist(
                        "artist:eason_chan",
                        "陈奕迅的音乐风格属于 Hip-Hop。",
                    )
                ],
                candidates=[eason],
            )
        else:
            fake_turn = create_worldline_subgraph(
                music_service=Catalog(),
                retriever=EmptyRetriever(),
                generator=Generator(),
            )
            executor = None
        with (
            patch(
                "app.agent.foundation.subgraphs.worldline.session.create_worldline_subgraph",
                return_value=fake_turn,
            ),
            patch(
                "app.agent.foundation.subgraphs.worldline.session.create_genre_executor",
                return_value=executor,
            ),
        ):
            service = get_worldline_session_service()
            if genre_mode:
                service._get_genre_executor()
        if mode in {"create", "genre_create"}:
            response = await service.create(
                "探索歌手周杰伦的世界线"
                if genre_mode
                else "歌曲《Feather》的世界线"
            )
            action = next(item for item in response.available_actions
                          if item.target_node_id == (
                              "genre:hip_hop"
                              if genre_mode
                              else "netease:artist:2"
                          ))
            result = {
                "session_id": response.session_id,
                "branch_id": action.id,
                "root": response.graph.root,
                "nodes": [node.id for node in response.graph.nodes],
                "sources": [source.id for source in response.sources],
                "revision": response.revision,
            }
        elif mode in {"explore", "genre_explore"}:
            session_id, branch_id = sys.argv[2:4]
            before = await service.load(session_id)
            response = await service.explore(session_id, branch_id)
            result = {
                "session_id": session_id,
                "before_revision": before.revision,
                "revision": response.revision,
                "root": response.graph.root,
                "nodes": [node.id for node in response.graph.nodes],
                "sources": [source.id for source in response.sources],
                "depth": response.exploration_depth,
                "current_node": response.current_node,
                "visited": list((await service.load(session_id)).visited_nodes),
            }
        elif mode in {"verify", "genre_verify"}:
            state = await service.load(sys.argv[2])
            result = {
                "revision": state.revision,
                "root": state.current_graph.root,
                "nodes": [node.id for node in state.current_graph.nodes],
                "sources": [source.id for source in state.evidence_history],
                "visited": state.visited_nodes,
            }
        else:
            raise ValueError("unsupported worker mode")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await close_runtime_checkpointer()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except Exception as exc:
        chain = []
        while exc is not None:
            chain.append({"type": type(exc).__name__, "code": getattr(exc, "code", None)})
            exc = exc.__cause__
        print(json.dumps({"error_chain": chain}))
        sys.exit(1)
