import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent.foundation.adapters import _ground_same_turn_artist_reference, create_subgraph_adapter
from app.agent.foundation.models import TaskResult, TaskStatus
from app.agent.foundation.routing import RouteDecision, RouteKey, RouteTask
from app.agent.foundation.state.main import create_main_state
from app.rag.query_understanding.context import QueryContextResolver
from app.rag.query_understanding.models import ContextResolutionStatus
from app.schemas.context import MusicAgentContext


class WorldlineReferenceBindingTests(unittest.IsolatedAsyncioTestCase):
    def _state(self, artists: list[str], *, status: TaskStatus = TaskStatus.SUCCESS):
        tasks = [
            RouteTask(task_id="search", route_key=RouteKey.TOOL_CALLING,
                      input_text="搜索 Radiohead"),
            RouteTask(task_id="explore", route_key=RouteKey.WORLDLINE,
                      input_text="探索他的影响关系", depends_on=["search"]),
        ]
        state = create_main_state("搜索 Radiohead，再探索他的影响关系")
        state["route_decision"] = RouteDecision(tasks=tasks, is_complete=True, coverage=1.0)
        state["task_results"] = [TaskResult(
            task_id="search", route_key=RouteKey.TOOL_CALLING, status=status,
            data={"entities": {"songs": [{
                "position": index + 1, "song_id": index + 1, "name": "track",
                "artists": [artist], "album": "", "cover_url": "", "duration": 0,
            } for index, artist in enumerate(artists)], "playlists": []}},
            **({"error": "search failed"} if status is TaskStatus.FAILED else {}),
        )]
        return state, tasks[1]

    async def test_same_turn_search_binds_named_artist_for_worldline(self):
        state, task = self._state(["Radiohead"])

        self.assertEqual(
            _ground_same_turn_artist_reference(state, task),
            "探索歌手《Radiohead》的影响关系",
        )
        graph = SimpleNamespace(ainvoke=AsyncMock(return_value={"task_result": TaskResult(
            task_id="explore", route_key=RouteKey.WORLDLINE, status=TaskStatus.SUCCESS,
            content="fixture",
        )}))
        await create_subgraph_adapter(RouteKey.WORLDLINE, graph)(
            state, SimpleNamespace(context=MusicAgentContext()),
        )
        sent = graph.ainvoke.await_args.args[0]
        self.assertEqual(sent["current_input"], "探索歌手《Radiohead》的影响关系")
        self.assertEqual(sent["user_query"], sent["current_input"])

    async def test_untrusted_or_ambiguous_search_does_not_bind_artist(self):
        for artists, status in [
            (["Other Artist"], TaskStatus.SUCCESS),
            (["Radiohead"], TaskStatus.FAILED),
        ]:
            with self.subTest(artists=artists, status=status):
                state, task = self._state(artists, status=status)
                self.assertEqual(_ground_same_turn_artist_reference(state, task), task.input_text)

    async def test_unbound_pronoun_requests_clarification_before_catalog_search(self):
        result = await QueryContextResolver().resolve("探索他的影响关系", messages=[])

        self.assertEqual(result.status, ContextResolutionStatus.NEEDS_INPUT)
