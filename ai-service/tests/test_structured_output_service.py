import asyncio
import unittest
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from app.agent.structured_output import (
    FailureType,
    ModelOutput,
    SemanticValidationError,
    StructuredOutputError,
    StructuredOutputService,
)


class Mode(StrEnum):
    PLAY = "play"
    PAUSE = "pause"


class SampleOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Mode
    answer: str = Field(min_length=1)
    count: int = Field(ge=1, le=5)


def grounded(output: SampleOutput) -> None:
    if output.status is Mode.PLAY and output.answer != "ready":
        raise SemanticValidationError("play_answer_not_grounded")


class StructuredOutputServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = StructuredOutputService(
            "sample", SampleOutput, semantic_validator=grounded,
        )

    def test_first_pass_returns_valid_pydantic_object(self) -> None:
        calls = []

        async def generate(attempt, feedback):
            calls.append((attempt, feedback))
            return '{"status":"play","answer":"ready","count":1}'

        output = asyncio.run(self.service.run(generate))
        self.assertIsInstance(output, SampleOutput)
        self.assertEqual(calls, [(1, None)])

    def test_strips_only_complete_markdown_fence(self) -> None:
        output = self.service.validate(
            '```json\n{"status":"pause","answer":"stopped","count":2}\n```'
        )
        self.assertEqual(output.status, Mode.PAUSE)
        with self.assertRaises(StructuredOutputError) as caught:
            self.service.validate('Here is JSON: {"status":"pause"}')
        self.assertEqual(caught.exception.failure_type, FailureType.PARSE_ERROR)

    def test_parse_schema_and_semantic_failures_are_distinct(self) -> None:
        cases = [
            ('{"status":', FailureType.TRUNCATION),
            ('{"status":bad}', FailureType.PARSE_ERROR),
            ('{"status":"play","answer":"ready"}', FailureType.SCHEMA_VALIDATION_ERROR),
            ('{"status":"play","answer":"ready","count":"many"}', FailureType.SCHEMA_VALIDATION_ERROR),
            ('{"status":"skip","answer":"ready","count":1}', FailureType.SCHEMA_VALIDATION_ERROR),
            ('{"status":"play","answer":"","count":1}', FailureType.SCHEMA_VALIDATION_ERROR),
            ('{"status":"play","answer":"ready","count":7}', FailureType.SCHEMA_VALIDATION_ERROR),
            ('{"status":"play","answer":"unsupported","count":1}', FailureType.SEMANTIC_VALIDATION_ERROR),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw), self.assertRaises(StructuredOutputError) as caught:
                self.service.validate(raw)
            self.assertEqual(caught.exception.failure_type, expected)

    def test_deterministic_repair_precedes_retry(self) -> None:
        def repair(payload, failure):
            if failure.failure_type is FailureType.SCHEMA_VALIDATION_ERROR and payload.get("total") == 2:
                return {key: value for key, value in payload.items() if key != "total"} | {"count": 2}
            return None

        service = StructuredOutputService("sample", SampleOutput, repair=repair)
        calls = []

        async def generate(attempt, feedback):
            calls.append(attempt)
            return '{"status":"pause","answer":"stopped","total":2}'

        output = asyncio.run(service.run(generate))
        self.assertEqual(output.count, 2)
        self.assertEqual(calls, [1])

    def test_retry_carries_safe_failure_type_and_succeeds(self) -> None:
        calls = []

        async def generate(attempt, feedback):
            calls.append((attempt, feedback))
            return ('not-json' if attempt == 1 else
                    '{"status":"pause","answer":"stopped","count":1}')

        self.assertEqual(asyncio.run(self.service.run(generate)).status, Mode.PAUSE)
        self.assertIn("not one valid JSON object", calls[1][1])
        self.assertNotIn("not-json", calls[1][1])

    def test_retry_exhaustion_and_truncation_are_fail_closed(self) -> None:
        async def incomplete(attempt, feedback):
            return ModelOutput("", status="incomplete", incomplete_reason="max_output_tokens")

        with self.assertLogs("app.agent.structured_output.service") as logs:
            with self.assertRaises(StructuredOutputError) as caught:
                asyncio.run(self.service.run(incomplete))
        self.assertEqual(caught.exception.failure_type, FailureType.RETRY_EXHAUSTED)
        self.assertEqual(caught.exception.last_failure_type, FailureType.TRUNCATION)
        self.assertEqual(caught.exception.attempts, 2)
        self.assertEqual(logs.records[-1].failure_type, "retry_exhausted")
        self.assertEqual(logs.records[-1].last_failure_type, "truncation")
        self.assertEqual(logs.records[-1].retry_count, 1)

    def test_model_error_does_not_expose_exception_text(self) -> None:
        async def failed(attempt, feedback):
            raise RuntimeError("private provider payload")

        with self.assertRaises(StructuredOutputError) as caught:
            asyncio.run(self.service.run(failed))
        self.assertEqual(caught.exception.last_failure_type, FailureType.MODEL_ERROR)
        self.assertNotIn("private", str(caught.exception))

    def test_attempt_limit_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            StructuredOutputService("sample", SampleOutput, max_attempts=4)


if __name__ == "__main__":
    unittest.main()
