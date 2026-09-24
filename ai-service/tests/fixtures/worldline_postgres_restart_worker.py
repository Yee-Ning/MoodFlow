"""One isolated process in the PostgreSQL WorldLine restart acceptance test."""

import asyncio
import json
import sys
from unittest.mock import patch

from app.agent.checkpointing import close_runtime_checkpointer, initialize_runtime_checkpointer
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.session import get_worldline_session_service
from app.config.settings import load_runtime_environment
from tests.test_worldline_graph import EmptyRetriever
from tests.test_worldline_session import Catalog, Generator


async def run() -> None:
    load_runtime_environment()
    await initialize_runtime_checkpointer()
    try:
        fake_turn = create_worldline_subgraph(
            music_service=Catalog(), retriever=EmptyRetriever(), generator=Generator(),
        )
        with patch("app.agent.foundation.subgraphs.worldline.session.create_worldline_subgraph", return_value=fake_turn):
            service = get_worldline_session_service()
        mode = sys.argv[1]
        if mode == "create":
            response = await service.create("歌曲《Feather》的世界线")
            action = next(item for item in response.available_actions
                          if item.target_node_id == "netease:artist:2")
            result = {
                "session_id": response.session_id,
                "branch_id": action.id,
                "root": response.graph.root,
                "nodes": [node.id for node in response.graph.nodes],
                "sources": [source.id for source in response.sources],
                "revision": response.revision,
            }
        elif mode == "explore":
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
            }
        elif mode == "verify":
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
