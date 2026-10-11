"""Capture controlled Explorer acceptance receipts; never read a live account."""

import asyncio
import json
from pathlib import Path

from pytest import MonkeyPatch

from tests.test_explorer_v2 import setup, invoke, hearts, BranchCatalog
from tests.test_explorer_journey_v1 import captured_facts


REPORT = Path(__file__).resolve().parent


def graph_snapshot(result):
    session = result.data["explorer_session"]
    return {
        key: session[key]
        for key in ("session_id", "revision", "current_node", "visited_nodes")
    } | {
        "root": session["graph"]["root"],
        "node_ids": [node["id"] for node in session["graph"]["nodes"]],
        "source_ids": [source["id"] for source in session["sources"]],
        "journey_queue": result.data["journey_queue"],
    }


async def capture():
    music, graph, sessions = setup()
    root = await invoke("探索周杰伦", graph, sessions)
    branch = await invoke("继续沿 R&B 分支", graph, sessions)
    detail = await invoke("刚才那个节点展开讲讲", graph, sessions)

    class FullCatalog(BranchCatalog):
        async def load_entity_facts(self, entity):
            return captured_facts()

    returned_ids = []
    patch = MonkeyPatch()
    try:
        calls = hearts(patch, returned_ids)
        _, graph, sessions = setup(catalog=FullCatalog())
        plain = await invoke("探索周杰伦", graph, sessions, authenticated=True)
        returned_ids.append(210049)
        liked = await invoke(
            "探索周杰伦", graph, sessions, user="u2", authenticated=True
        )
    finally:
        patch.undo()

    _, graph, sessions = setup()
    guided = await invoke("探索90年代港乐", graph, sessions)
    result = {
        "data_class": "controlled_local_acceptance; not production or real user preferences",
        "catalog_id_provenance": "docs/reports/handoff-repair-20261001/combo-final.json (committed historical Java response)",
        "knowledge_and_years": "controlled fixtures; not real music history/release claims",
        "branch": {
            "before": graph_snapshot(root),
            "after": graph_snapshot(branch),
            "catalog_searches": len(music.searches),
            "catalog_details": len(music.loads),
            "same_graph": root.data["explorer_session"]["session_id"]
            == branch.data["explorer_session"]["session_id"],
            "selected_explanation_preserved_snapshot": detail.data["explorer_session"]
            == branch.data["explorer_session"],
        },
        "personalization": {
            "transport": "actual get_liked_songs tool + httpx.MockTransport, GET only",
            "calls": calls,
            "empty_receipt": plain.data["personalization"],
            "liked_receipt": liked.data["personalization"],
            "returned_liked_ids": returned_ids,
            "empty_queue": plain.data["journey_queue"],
            "liked_queue": liked.data["journey_queue"],
            "annotations": [
                node["narration"]
                for node in liked.data["listening_journey"]["nodes"]
                if node["narration"]["preference_song_ids"]
            ],
        },
        "topic_root": {
            "input": "探索90年代港乐",
            "status": guided.status,
            "answer": guided.content,
            "journey_queue": guided.data["journey_queue"],
        },
    }
    (REPORT / "acceptance.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    asyncio.run(capture())
