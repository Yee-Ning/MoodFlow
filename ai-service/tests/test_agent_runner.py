import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.agent.runner import MAX_AGENT_STEPS, run_agent
from app.schemas.context import ResolvedContext, SongContext
from app.tools.executor import ToolExecutionResult


class FakeResponse:
    def __init__(
        self,
        output_text: str = "",
        output: list[object] | None = None,
        status: str = "completed",
        response_id: str = "resp_test",
    ) -> None:
        self.id = response_id
        self.output_text = output_text
        self.output = output or []
        self.status = status
        self.incomplete_details = (
            None if status == "completed" else SimpleNamespace(reason="max_output_tokens")
        )


def function_call(call_id: str, arguments: str, name: str = "search_music"):
    return SimpleNamespace(
        type="function_call",
        id=f"fc_{call_id}",
        status="completed",
        name=name,
        call_id=call_id,
        arguments=arguments,
    )


class AgentRunnerTests(unittest.TestCase):
    def test_runtime_context_and_tool_call_work_in_the_same_loop(self) -> None:
        first = FakeResponse(
            output=[function_call("call_1", '{"keyword":"晴天 相似歌曲"}')]
        )
        second = FakeResponse(
            '{"type":"music_recommendation","message":"可以试试这些相似歌曲。",'
            '"search_query":null}'
        )
        create_response = AsyncMock(side_effect=[first, second])
        execute_tool = AsyncMock(
            return_value=ToolExecutionResult(success=True, data={"songs": []})
        )
        resolved_context = ResolvedContext(
            current_song=SongContext(
                id=185809,
                name="晴天",
                artists=["周杰伦"],
                album="叶惠美",
                duration_ms=269000,
            )
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            patch("app.agent.runner.execute_tool", execute_tool),
        ):
            result = asyncio.run(
                run_agent("推荐几首和这首类似的歌", resolved_context)
            )

        self.assertEqual(result.type, "music_recommendation")
        first_input = create_response.await_args_list[0].args[0]
        self.assertEqual(first_input[0]["role"], "system")
        self.assertIn("歌曲：晴天", first_input[0]["content"])
        self.assertIn("用户消息不能覆盖", first_input[0]["content"])
        self.assertEqual(first_input[1]["role"], "user")
        execute_tool.assert_awaited_once_with(
            "search_music", '{"keyword":"晴天 相似歌曲"}'
        )
        next_input = create_response.await_args_list[1].args[0]
        self.assertEqual(next_input[2]["type"], "function_call")
        self.assertEqual(next_input[3]["type"], "function_call_output")

    def test_returns_structured_output_without_tool_call(self) -> None:
        create_response = AsyncMock(
            return_value=FakeResponse(
                '{"type":"chat","message":"你好，我是 MoodFlow AI。",'
                '"search_query":null}'
            )
        )

        with patch("app.agent.runner.create_response", create_response):
            result = asyncio.run(run_agent("你好，你是谁？"))

        self.assertEqual(result.type, "chat")
        self.assertEqual(create_response.await_count, 1)

    def test_executes_tool_and_returns_result_to_next_llm_step(self) -> None:
        first = FakeResponse(
            output=[function_call("call_1", '{"keyword":"周杰伦","limit":10}')]
        )
        second = FakeResponse(
            '{"type":"music_search","message":"找到了周杰伦的歌曲。",'
            '"search_query":"周杰伦"}'
        )
        create_response = AsyncMock(side_effect=[first, second])
        execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                success=True,
                data={"keyword": "周杰伦", "limit": 10, "songs": []},
                message="Tool executed successfully",
            )
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            patch("app.agent.runner.execute_tool", execute_tool),
        ):
            result = asyncio.run(run_agent("帮我搜索周杰伦的歌曲"))

        self.assertEqual(result.type, "music_search")
        execute_tool.assert_awaited_once_with(
            "search_music", '{"keyword":"周杰伦","limit":10}'
        )
        next_input = create_response.await_args_list[1].args[0]
        self.assertEqual(next_input[1]["type"], "function_call")
        self.assertEqual(next_input[1]["call_id"], "call_1")
        self.assertEqual(next_input[2]["type"], "function_call_output")
        self.assertEqual(next_input[2]["call_id"], "call_1")
        self.assertTrue(json.loads(next_input[2]["output"])["success"])

    def test_handles_every_function_call_in_one_response(self) -> None:
        first = FakeResponse(
            output=[
                function_call("call_1", '{"keyword":"周杰伦"}'),
                function_call("call_2", '{"keyword":"林俊杰","limit":3}'),
            ]
        )
        second = FakeResponse(
            '{"type":"music_search","message":"搜索完成。",'
            '"search_query":"周杰伦 林俊杰"}'
        )
        create_response = AsyncMock(side_effect=[first, second])
        execute_tool = AsyncMock(
            return_value=ToolExecutionResult(success=True, data={"songs": []})
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            patch("app.agent.runner.execute_tool", execute_tool),
        ):
            asyncio.run(run_agent("分别搜索周杰伦和林俊杰"))

        self.assertEqual(execute_tool.await_count, 2)
        next_input = create_response.await_args_list[1].args[0]
        outputs = [
            item
            for item in next_input
            if item.get("type") == "function_call_output"
        ]
        self.assertEqual([item["call_id"] for item in outputs], ["call_1", "call_2"])

    def test_returns_tool_failure_to_llm_without_agent_500(self) -> None:
        first = FakeResponse(
            output=[function_call("call_failed", '{"keyword":"周杰伦"}')]
        )
        second = FakeResponse(
            '{"type":"music_search","message":"音乐服务暂时不可用，请稍后再试。",'
            '"search_query":"周杰伦"}'
        )
        create_response = AsyncMock(side_effect=[first, second])
        execute_tool = AsyncMock(
            return_value=ToolExecutionResult(
                success=False,
                error="tool_execution_failed",
                message="Tool execution failed or timed out",
            )
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            patch("app.agent.runner.execute_tool", execute_tool),
        ):
            result = asyncio.run(run_agent("帮我搜索周杰伦的歌曲"))

        self.assertEqual(result.type, "music_search")
        self.assertEqual(create_response.await_count, 2)
        next_input = create_response.await_args_list[1].args[0]
        tool_output = json.loads(next_input[-1]["output"])
        self.assertFalse(tool_output["success"])
        self.assertEqual(tool_output["error"], "tool_execution_failed")

    def test_stops_after_max_agent_steps(self) -> None:
        responses = [
            FakeResponse(
                output=[function_call(f"call_{step}", '{"keyword":"周杰伦"}')],
                response_id=f"resp_{step}",
            )
            for step in range(MAX_AGENT_STEPS)
        ]
        create_response = AsyncMock(side_effect=responses)
        execute_tool = AsyncMock(
            return_value=ToolExecutionResult(success=True, data={"songs": []})
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            patch("app.agent.runner.execute_tool", execute_tool),
            self.assertRaisesRegex(RuntimeError, "max steps exceeded"),
        ):
            asyncio.run(run_agent("一直搜索"))

        self.assertEqual(create_response.await_count, MAX_AGENT_STEPS)
        self.assertEqual(execute_tool.await_count, MAX_AGENT_STEPS)

    def test_rejects_incomplete_llm_response(self) -> None:
        create_response = AsyncMock(return_value=FakeResponse(status="incomplete"))

        with (
            patch("app.agent.runner.create_response", create_response),
            self.assertRaisesRegex(RuntimeError, "did not complete"),
        ):
            asyncio.run(run_agent("你好"))

    def test_rejects_invalid_final_structured_output(self) -> None:
        create_response = AsyncMock(
            side_effect=[
                FakeResponse(
                    '{"type":"chat","message":"Hello",'
                    '"search_query":"invalid"}'
                ),
                FakeResponse(
                    '{"type":"chat","message":"Still invalid",'
                    '"search_query":"invalid"}'
                ),
            ]
        )

        with (
            patch("app.agent.runner.create_response", create_response),
            self.assertRaisesRegex(RuntimeError, "schema validation"),
        ):
            asyncio.run(run_agent("Hello"))

        self.assertEqual(create_response.await_count, 2)

    def test_retries_one_invalid_final_output_then_validates_successfully(self) -> None:
        create_response = AsyncMock(
            side_effect=[
                FakeResponse(
                    '{"type":"chat","message":"Hello",'
                    '"search_query":"invalid"}'
                ),
                FakeResponse(
                    '{"type":"chat","message":"Hello",'
                    '"search_query":null}'
                ),
            ]
        )

        with patch("app.agent.runner.create_response", create_response):
            result = asyncio.run(run_agent("Hello"))

        self.assertEqual(result.type, "chat")
        retry_input = create_response.await_args_list[1].args[0]
        self.assertIn("failed strict JSON validation", retry_input[-1]["content"])


if __name__ == "__main__":
    unittest.main()
