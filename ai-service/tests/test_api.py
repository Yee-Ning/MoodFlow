import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.main import app
from app.schemas.chat import ChatOutput
from app.schemas.context import ResolvedContext, SongContext


class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)

    def test_health(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})

    def test_chat_delegates_to_agent_and_returns_structured_output(self) -> None:
        run_agent = AsyncMock(
            return_value=ChatOutput(
                type="chat",
                message="你好，我是 MoodFlow AI。",
                search_query=None,
            )
        )

        resolved_context = ResolvedContext()
        build_context = AsyncMock(return_value=resolved_context)

        with (
            patch("app.api.chat.run_agent", run_agent),
            patch("app.api.chat.build_context", build_context),
        ):
            response = self.client.post(
                "/api/ai/chat",
                json={"message": "你好，你是谁？"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["type"], "chat")
        build_context.assert_awaited_once_with(None)
        run_agent.assert_awaited_once_with("你好，你是谁？", resolved_context)

    def test_chat_resolves_frontend_context_before_running_agent(self) -> None:
        resolved_context = ResolvedContext(
            current_song=SongContext(
                id=185809,
                name="晴天",
                artists=["周杰伦"],
                album="叶惠美",
                duration_ms=269000,
            ),
            page="/library",
        )
        build_context = AsyncMock(return_value=resolved_context)
        run_agent = AsyncMock(
            return_value=ChatOutput(
                type="chat",
                message="我知道你指的是《晴天》。",
                search_query=None,
            )
        )

        with (
            patch("app.api.chat.build_context", build_context),
            patch("app.api.chat.run_agent", run_agent),
        ):
            response = self.client.post(
                "/api/ai/chat",
                json={
                    "message": "这首歌是谁唱的？",
                    "context": {
                        "current_song_id": 185809,
                        "page": "/library",
                    },
                },
            )

        self.assertEqual(response.status_code, 200)
        submitted_context = build_context.await_args.args[0]
        self.assertEqual(submitted_context.current_song_id, 185809)
        self.assertEqual(submitted_context.page, "/library")
        run_agent.assert_awaited_once_with("这首歌是谁唱的？", resolved_context)


if __name__ == "__main__":
    unittest.main()
