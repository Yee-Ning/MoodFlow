"""Configurable DeepEval judges backed by OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
from typing import Any

from deepeval.models import DeepEvalBaseLLM
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, SecretStr


class OpenAICompatibleJudge(DeepEvalBaseLLM):
    """DeepEval custom LLM for any OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        *,
        provider: str,
        model: str,
        base_url: str,
        api_key: str,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.timeout_seconds = timeout_seconds
        self._api_key = SecretStr(api_key)
        super().__init__(model)
        self._async_model = AsyncOpenAI(**self._client_kwargs())

    def _client_kwargs(self) -> dict[str, Any]:
        return {
            "api_key": self._api_key.get_secret_value(),
            "base_url": self.base_url,
            "timeout": self.timeout_seconds,
        }

    def load_model(self) -> OpenAI:
        return OpenAI(**self._client_kwargs())

    def generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        completion = self.model.chat.completions.create(
            **self._request(prompt, schema)
        )
        return self._response(completion, schema)

    async def a_generate(
        self,
        prompt: str,
        schema: type[BaseModel] | None = None,
        **_: Any,
    ) -> str | BaseModel:
        completion = await self._async_model.chat.completions.create(
            **self._request(prompt, schema)
        )
        return self._response(completion, schema)

    def _request(
        self,
        prompt: str,
        schema: type[BaseModel] | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": self.temperature,
        }
        if schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    @staticmethod
    def _response(completion: Any, schema: type[BaseModel] | None) -> str | BaseModel:
        content = completion.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            raise RuntimeError("judge_provider_returned_empty_content")
        if schema is None:
            return content
        return schema.model_validate(_load_json_object(content))

    def get_model_name(self) -> str:
        return f"{self.provider}:{self.name}"

    def supports_temperature(self) -> bool:
        return True

    def supports_structured_outputs(self) -> bool:
        return False

    def supports_json_mode(self) -> bool:
        return True


class DeepSeekJudge(OpenAICompatibleJudge):
    """MoodFlow's default Judge using the configured DeepSeek endpoint."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(provider="deepseek", **kwargs)


def create_judge(
    *,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
    temperature: float,
    timeout_seconds: float,
) -> DeepEvalBaseLLM:
    """Create a custom Judge without coupling metrics to one provider."""
    judge_type = DeepSeekJudge if provider == "deepseek" else OpenAICompatibleJudge
    kwargs = {
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": temperature,
        "timeout_seconds": timeout_seconds,
    }
    if judge_type is DeepSeekJudge:
        return judge_type(**kwargs)
    return judge_type(provider=provider, **kwargs)


def _load_json_object(content: str) -> Any:
    candidate = content.strip()
    if candidate.startswith("```"):
        lines = candidate.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        candidate = "\n".join(lines).strip()
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        start = candidate.find("{")
        end = candidate.rfind("}")
        if start < 0 or end <= start:
            raise ValueError("judge_provider_returned_invalid_json") from None
        try:
            return json.loads(candidate[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError("judge_provider_returned_invalid_json") from exc
