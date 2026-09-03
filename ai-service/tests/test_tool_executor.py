import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.schemas.music import MusicSearchResult
from app.tools.executor import RegisteredTool, TOOL_REGISTRY, execute_tool
from app.tools.music import MusicSearchError
from app.schemas.music import SearchMusicArgs


class ToolExecutorTests(unittest.TestCase):
    def test_validates_arguments_and_executes_registered_tool(self) -> None:
        function = AsyncMock(
            return_value=MusicSearchResult(keyword="周杰伦", limit=2, songs=[])
        )
        registered = RegisteredTool(SearchMusicArgs, function)

        with patch.dict(TOOL_REGISTRY, {"search_music": registered}, clear=True):
            result = asyncio.run(
                execute_tool("search_music", '{"keyword":"周杰伦","limit":2}')
            )

        self.assertTrue(result.success)
        self.assertEqual(result.data["keyword"], "周杰伦")
        function.assert_awaited_once_with(keyword="周杰伦", limit=2)

    def test_rejects_invalid_arguments_before_execution(self) -> None:
        function = AsyncMock()
        registered = RegisteredTool(SearchMusicArgs, function)

        with patch.dict(TOOL_REGISTRY, {"search_music": registered}, clear=True):
            result = asyncio.run(
                execute_tool(
                    "search_music",
                    '{"keyword":"周杰伦","limit":100,"unknown":true}',
                )
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "invalid_arguments")
        function.assert_not_awaited()

    def test_returns_failure_for_unknown_tool(self) -> None:
        result = asyncio.run(execute_tool("missing_tool", "{}"))

        self.assertFalse(result.success)
        self.assertEqual(result.error, "unknown_tool")

    def test_returns_failure_when_tool_raises(self) -> None:
        function = AsyncMock(side_effect=MusicSearchError("timed out"))
        registered = RegisteredTool(SearchMusicArgs, function)

        with patch.dict(TOOL_REGISTRY, {"search_music": registered}, clear=True):
            result = asyncio.run(
                execute_tool("search_music", '{"keyword":"周杰伦"}')
            )

        self.assertFalse(result.success)
        self.assertEqual(result.error, "tool_execution_failed")


if __name__ == "__main__":
    unittest.main()
