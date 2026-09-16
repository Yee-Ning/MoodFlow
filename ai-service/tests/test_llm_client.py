import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import sentinel, patch

from pydantic import SecretStr

from app.llm.client import (
    create_base_response,
    create_generation_response,
    create_response,
    create_structured_response,
    get_base_chat_model,
)


class FakeResponse:
    id = "resp_test"
    status = "completed"
    output: list[object] = []
    output_text = ""
    incomplete_details = None


class FakeAsyncOpenAI:
    last_request: dict | None = None

    def __init__(self, **kwargs: object) -> None:
        self.responses = self

    async def __aenter__(self) -> "FakeAsyncOpenAI":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def create(self, **kwargs: object) -> FakeResponse:
        type(self).last_request = kwargs
        return FakeResponse()


def fake_settings() -> SimpleNamespace:
    return SimpleNamespace(
        api_key=SecretStr("test-key"),
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
        generation_model="deepseek-flash",
        timeout_seconds=60,
        max_output_tokens=1024,
    )


class CreateResponseTests(unittest.TestCase):
    def setUp(self) -> None:
        FakeAsyncOpenAI.last_request = None
        get_base_chat_model.cache_clear()

    def tearDown(self) -> None:
        get_base_chat_model.cache_clear()

    def test_sends_one_stateless_responses_api_request(self) -> None:
        input_items = [{"role": "user", "content": "你好"}]

        with (
            patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI),
            patch("app.llm.client.get_settings", fake_settings),
        ):
            response = asyncio.run(create_response(input_items))

        self.assertEqual(response.id, "resp_test")
        request = FakeAsyncOpenAI.last_request
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request["input"], input_items)
        self.assertNotIn("previous_response_id", request)
        self.assertEqual(request["model"], "deepseek-v4-flash")
        self.assertEqual(request["reasoning"], {"effort": "none"})
        self.assertEqual(request["max_output_tokens"], 1024)
        self.assertEqual(request["tool_choice"], "auto")
        self.assertEqual(request["tools"][0]["name"], "search_music")
        output_format = request["text"]["format"]
        self.assertEqual(output_format["type"], "json_schema")
        self.assertEqual(output_format["name"], "moodflow_ai_response")
        self.assertIn("oneOf", output_format["schema"])

    def test_base_response_does_not_expose_tools(self) -> None:
        with (
            patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI),
            patch("app.llm.client.get_settings", fake_settings),
        ):
            asyncio.run(
                create_base_response(
                    [{"role": "user", "content": "分析这首歌"}],
                    "current song assistant prompt",
                )
            )

        request = FakeAsyncOpenAI.last_request
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request["instructions"], "current song assistant prompt")
        self.assertNotIn("tools", request)
        self.assertNotIn("tool_choice", request)

    def test_structured_response_inlines_referenced_union_branch(self) -> None:
        schema = {
            "$defs": {
                "Subject": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                    "additionalProperties": False,
                }
            },
            "type": "object",
            "properties": {
                "subject": {
                    "anyOf": [
                        {"$ref": "#/$defs/Subject"},
                        {"type": "null"},
                    ]
                }
            },
            "required": ["subject"],
            "additionalProperties": False,
        }

        with (
            patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI),
            patch("app.llm.client.get_settings", fake_settings),
        ):
            asyncio.run(
                create_structured_response(
                    [{"role": "user", "content": "payload"}],
                    "extract subject",
                    schema,
                    schema_name="subject_schema",
                )
            )

        request = FakeAsyncOpenAI.last_request
        self.assertIsNotNone(request)
        assert request is not None
        output_schema = request["text"]["format"]["schema"]
        subject_branch = output_schema["properties"]["subject"]["anyOf"][0]
        self.assertEqual(subject_branch["type"], "object")
        self.assertIn("name", subject_branch["properties"])
        self.assertNotIn("$ref", subject_branch)
        self.assertEqual(schema["properties"]["subject"]["anyOf"][0], {
            "$ref": "#/$defs/Subject"
        })

    def test_generation_response_uses_independent_generation_model(self) -> None:
        with (
            patch("app.llm.client.AsyncOpenAI", FakeAsyncOpenAI),
            patch("app.llm.client.get_settings", fake_settings),
        ):
            asyncio.run(
                create_generation_response(
                    [{"role": "user", "content": "payload"}],
                    "answer from evidence",
                    {
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                    schema_name="answer",
                )
            )

        request = FakeAsyncOpenAI.last_request
        self.assertIsNotNone(request)
        assert request is not None
        self.assertEqual(request["model"], "deepseek-flash")

    def test_langchain_chat_model_reuses_existing_settings_without_tools(self) -> None:
        with (
            patch("app.llm.client.get_settings", fake_settings),
            patch(
                "app.llm.client.ChatOpenAI",
                return_value=sentinel.chat_model,
            ) as chat_openai,
        ):
            model = get_base_chat_model()

        self.assertIs(model, sentinel.chat_model)
        kwargs = chat_openai.call_args.kwargs
        self.assertEqual(kwargs["api_key"].get_secret_value(), "test-key")
        self.assertEqual(kwargs["base_url"], "https://api.deepseek.com")
        self.assertEqual(kwargs["model"], "deepseek-v4-flash")
        self.assertEqual(kwargs["timeout"], 60)
        self.assertEqual(kwargs["max_completion_tokens"], 1024)
        self.assertTrue(kwargs["streaming"])
        self.assertFalse(kwargs["use_responses_api"])
        self.assertNotIn("tools", kwargs)


if __name__ == "__main__":
    unittest.main()
