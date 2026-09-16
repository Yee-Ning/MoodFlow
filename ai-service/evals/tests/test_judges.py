import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pydantic import BaseModel

from evals.judges import DeepSeekJudge, OpenAICompatibleJudge, create_judge
from evals.run_deepeval import run_judge


class Verdict(BaseModel):
    score: int


def completion(content: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class FakeSyncCompletions:
    def __init__(self) -> None:
        self.requests = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return completion('{"score": 1}')


class FakeAsyncCompletions:
    def __init__(self) -> None:
        self.requests = []

    async def create(self, **kwargs):
        self.requests.append(kwargs)
        return completion('{"score": 1}')


class JudgeAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.sync_completions = FakeSyncCompletions()
        self.async_completions = FakeAsyncCompletions()
        self.sync_client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.sync_completions)
        )
        self.async_client = SimpleNamespace(
            chat=SimpleNamespace(completions=self.async_completions)
        )

    def create(self, provider: str = "deepseek"):
        with (
            patch("evals.judges.OpenAI", return_value=self.sync_client),
            patch("evals.judges.AsyncOpenAI", return_value=self.async_client),
        ):
            return create_judge(
                provider=provider,
                model="judge-model",
                base_url="https://judge.example/v1",
                api_key="fixture-secret",
                temperature=0.1,
                timeout_seconds=30,
            )

    def test_deepseek_factory_returns_named_custom_judge(self) -> None:
        judge = self.create()

        self.assertIsInstance(judge, DeepSeekJudge)
        self.assertEqual(judge.get_model_name(), "deepseek:judge-model")
        self.assertNotIn("fixture-secret", repr(judge.__dict__))

    def test_generic_provider_uses_openai_compatible_transport_and_schema(self) -> None:
        judge = self.create("openai_compatible")

        result = judge.generate("score this", schema=Verdict)

        self.assertIsInstance(judge, OpenAICompatibleJudge)
        self.assertEqual(result, Verdict(score=1))
        request = self.sync_completions.requests[0]
        self.assertEqual(request["model"], "judge-model")
        self.assertEqual(request["temperature"], 0.1)
        self.assertEqual(request["response_format"], {"type": "json_object"})

    async def test_async_generation_uses_the_same_configured_transport(self) -> None:
        judge = self.create()

        result = await judge.a_generate("score this", schema=Verdict)

        self.assertEqual(result, Verdict(score=1))
        self.assertEqual(self.async_completions.requests[0]["model"], "judge-model")

    def test_runner_records_provider_errors_instead_of_aborting_batch(self) -> None:
        judge = self.create()
        expected = SimpleNamespace(test_results=[])
        payload = {
            "case_id": "rag-v0-001",
            "input": "question",
            "actual_output": "answer",
            "expected_output": "reference",
            "reference_context": ["reference evidence"],
            "retrieval_context": ["retrieved evidence"],
            "metadata": {},
            "tags": [],
        }
        with (
            patch("evals.judges.create_judge", return_value=judge),
            patch("deepeval.evaluate", return_value=expected) as evaluate,
        ):
            result = run_judge(
                [payload],
                judge_config={
                    "provider": "deepseek",
                    "model": "judge-model",
                    "base_url": "https://judge.example/v1",
                    "api_key_env": "JUDGE_KEY",
                    "api_key_env_file": None,
                    "temperature": 0.0,
                    "timeout_seconds": 30,
                },
                api_key="fixture-secret",
                threshold=0.5,
                max_concurrent=2,
            )

        self.assertIs(result, expected)
        self.assertTrue(evaluate.call_args.kwargs["error_config"].ignore_errors)


if __name__ == "__main__":
    unittest.main()
