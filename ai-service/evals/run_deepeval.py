"""Run semantic LLM-as-a-Judge metrics in an isolated DeepEval environment."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Sequence, TypedDict
from urllib.parse import urlsplit


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "judge_v0.json"
SUPPORTED_PROVIDERS = frozenset({"deepseek", "openai", "openai_compatible"})
SEMANTIC_METRICS = (
    "Faithfulness",
    "Answer Relevancy",
    "Contextual Precision",
    "Contextual Recall",
)
ENV_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,99}$")


class JudgeProviderConfig(TypedDict):
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key_env_file: str | None
    temperature: float
    timeout_seconds: float


class JudgeConfig(TypedDict):
    schema_version: str
    judge: JudgeProviderConfig
    threshold: float
    max_concurrent: int


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run semantic DeepEval metrics with a configurable Judge provider"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument("--provider")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env")
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--timeout-seconds", type=float)
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--max-concurrent", type=int)
    parser.add_argument("--output", type=Path)
    return parser


def load_judge_config(path: Path) -> JudgeConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"deepeval_config_invalid:{path}") from exc
    if not isinstance(payload, dict) or set(payload) != {
        "schema_version",
        "judge",
        "threshold",
        "max_concurrent",
    }:
        raise ValueError(f"deepeval_config_invalid:{path}")
    judge = payload.get("judge")
    if not isinstance(judge, dict) or set(judge) != {
        "provider",
        "model",
        "base_url",
        "api_key_env",
        "api_key_env_file",
        "temperature",
        "timeout_seconds",
    }:
        raise ValueError(f"deepeval_config_invalid:{path}")
    normalized = JudgeConfig(
        schema_version=payload.get("schema_version"),
        judge=JudgeProviderConfig(
            provider=judge.get("provider"),
            model=judge.get("model"),
            base_url=judge.get("base_url"),
            api_key_env=judge.get("api_key_env"),
            api_key_env_file=judge.get("api_key_env_file"),
            temperature=judge.get("temperature"),
            timeout_seconds=judge.get("timeout_seconds"),
        ),
        threshold=payload.get("threshold"),
        max_concurrent=payload.get("max_concurrent"),
    )
    _validate_config(normalized, path)
    return normalized


def _validate_config(config: JudgeConfig, path: Path) -> None:
    judge = config["judge"]
    env_file = judge["api_key_env_file"]
    if (
        config["schema_version"] != "v0"
        or judge["provider"] not in SUPPORTED_PROVIDERS
        or not isinstance(judge["model"], str)
        or not judge["model"].strip()
        or not _valid_base_url(judge["base_url"])
        or not isinstance(judge["api_key_env"], str)
        or ENV_NAME_PATTERN.fullmatch(judge["api_key_env"]) is None
        or not (env_file is None or isinstance(env_file, str) and env_file.strip())
        or not _number_between(judge["temperature"], 0, 2)
        or not _number_between(judge["timeout_seconds"], 1, 300)
        or not _number_between(config["threshold"], 0, 1)
        or not isinstance(config["max_concurrent"], int)
        or isinstance(config["max_concurrent"], bool)
        or not 1 <= config["max_concurrent"] <= 20
    ):
        raise ValueError(f"deepeval_config_invalid:{path}")


def _number_between(value: Any, minimum: float, maximum: float) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and minimum <= value <= maximum
    )


def _valid_base_url(value: Any) -> bool:
    if not isinstance(value, str) or not value.strip():
        return False
    parsed = urlsplit(value)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.hostname is not None
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
    )


def resolve_judge_config(
    config: JudgeConfig,
    *,
    provider: str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key_env: str | None = None,
    temperature: float | None = None,
    timeout_seconds: float | None = None,
) -> JudgeProviderConfig:
    current = config["judge"]
    resolved = JudgeProviderConfig(
        provider=(
            provider
            or os.getenv("DEEPEVAL_JUDGE_PROVIDER")
            or current["provider"]
        ),
        model=model or os.getenv("DEEPEVAL_JUDGE_MODEL") or current["model"],
        base_url=(
            base_url or os.getenv("DEEPEVAL_JUDGE_BASE_URL") or current["base_url"]
        ),
        api_key_env=(
            api_key_env
            or os.getenv("DEEPEVAL_JUDGE_API_KEY_ENV")
            or current["api_key_env"]
        ),
        api_key_env_file=current["api_key_env_file"],
        temperature=_configured_float(
            temperature,
            "DEEPEVAL_JUDGE_TEMPERATURE",
            current["temperature"],
        ),
        timeout_seconds=_configured_float(
            timeout_seconds,
            "DEEPEVAL_JUDGE_TIMEOUT_SECONDS",
            current["timeout_seconds"],
        ),
    )
    candidate = JudgeConfig(
        schema_version=config["schema_version"],
        judge=resolved,
        threshold=config["threshold"],
        max_concurrent=config["max_concurrent"],
    )
    _validate_config(candidate, DEFAULT_CONFIG_PATH)
    return resolved


def _configured_float(
    cli_value: float | None,
    env_name: str,
    default: float,
) -> float:
    if cli_value is not None:
        return cli_value
    raw = os.getenv(env_name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"deepeval_environment_invalid:{env_name}") from exc


def load_payloads(path: Path) -> list[dict[str, Any]]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"deepeval_dataset_unreadable:{path}") from exc
    payloads: list[dict[str, Any]] = []
    case_ids: set[str] = set()
    required = {
        "schema_version": str,
        "case_id": str,
        "input": str,
        "actual_output": str,
        "expected_output": str,
        "retrieval_context": list,
        "reference_context": list,
    }
    for line_number, raw_line in enumerate(lines, start=1):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"deepeval_case_invalid:{path}:{line_number}") from exc
        if not isinstance(payload, dict) or any(
            not isinstance(payload.get(key), value_type)
            for key, value_type in required.items()
        ):
            raise ValueError(f"deepeval_case_invalid:{path}:{line_number}")
        if payload["schema_version"] != "v0" or any(
            not payload[key].strip()
            for key in ("case_id", "input", "actual_output", "expected_output")
        ):
            raise ValueError(f"deepeval_case_invalid:{path}:{line_number}")
        case_id = payload["case_id"]
        if not case_id or case_id in case_ids:
            raise ValueError(f"deepeval_case_id_invalid:{path}:{line_number}")
        if not payload["retrieval_context"] or any(
            not isinstance(item, str) or not item.strip()
            for item in payload["retrieval_context"]
        ):
            raise ValueError(f"deepeval_retrieval_context_invalid:{path}:{line_number}")
        if not payload["reference_context"] or any(
            not isinstance(item, str) or not item.strip()
            for item in payload["reference_context"]
        ):
            raise ValueError(f"deepeval_reference_context_invalid:{path}:{line_number}")
        case_ids.add(case_id)
        payloads.append(payload)
    if not payloads:
        raise ValueError(f"deepeval_dataset_empty:{path}")
    return payloads


def resolve_api_key(config: JudgeProviderConfig, project_root: Path) -> str:
    env_name = config["api_key_env"]
    api_key = os.getenv(env_name)
    env_file = config["api_key_env_file"]
    if not api_key and env_file:
        from dotenv import dotenv_values

        selected = Path(env_file)
        selected = selected if selected.is_absolute() else project_root / selected
        values = dotenv_values(selected.resolve())
        api_key = values.get(env_name)
    if not isinstance(api_key, str) or not api_key.strip():
        raise ValueError(f"{env_name} is required in the Judge process environment")
    return api_key


def run_judge(
    payloads: Sequence[dict[str, Any]],
    *,
    judge_config: JudgeProviderConfig,
    api_key: str,
    threshold: float,
    max_concurrent: int,
) -> Any:
    if not 0 <= threshold <= 1:
        raise ValueError("DeepEval threshold must be between 0 and 1")
    if not 1 <= max_concurrent <= 20:
        raise ValueError("DeepEval maximum concurrency must be between 1 and 20")

    _ensure_utf8_console()
    os.environ.setdefault("DEEPEVAL_DISABLE_DOTENV", "1")
    try:
        from deepeval import evaluate
        from deepeval.evaluate import AsyncConfig, CacheConfig, DisplayConfig
        from deepeval.metrics import (
            AnswerRelevancyMetric,
            ContextualPrecisionMetric,
            ContextualRecallMetric,
            FaithfulnessMetric,
        )
        from deepeval.test_case import LLMTestCase
        from evals.judges import create_judge
    except ImportError as exc:
        raise RuntimeError(
            "DeepEval is unavailable; install evals/requirements.txt in .venv-eval"
        ) from exc

    judge = create_judge(
        provider=judge_config["provider"],
        model=judge_config["model"],
        base_url=judge_config["base_url"],
        api_key=api_key,
        temperature=judge_config["temperature"],
        timeout_seconds=judge_config["timeout_seconds"],
    )
    test_cases = [
        LLMTestCase(
            name=payload["case_id"],
            input=payload["input"],
            actual_output=payload["actual_output"],
            expected_output=payload["expected_output"],
            context=payload["reference_context"],
            retrieval_context=payload["retrieval_context"],
            metadata=payload.get("metadata"),
            tags=payload.get("tags"),
        )
        for payload in payloads
    ]
    metrics = [
        FaithfulnessMetric(threshold=threshold, model=judge),
        AnswerRelevancyMetric(threshold=threshold, model=judge),
        ContextualPrecisionMetric(threshold=threshold, model=judge),
        ContextualRecallMetric(threshold=threshold, model=judge),
    ]
    try:
        return evaluate(
            test_cases=test_cases,
            metrics=metrics,
            async_config=AsyncConfig(
                run_async=True,
                max_concurrent=max_concurrent,
                throttle_value=0,
            ),
            display_config=DisplayConfig(
                show_indicator=False,
                print_results=False,
                inspect_after_run=False,
            ),
            cache_config=CacheConfig(write_cache=False, use_cache=False),
        )
    except Exception as exc:  # noqa: BLE001 - sanitize provider/SDK failures
        raise RuntimeError(f"deepeval_judge_failed:{type(exc).__name__}") from exc


def _ensure_utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")


def serialize_result(result: Any) -> dict[str, Any]:
    test_results = list(getattr(result, "test_results", []))
    cases: list[dict[str, Any]] = []
    for item in test_results:
        metrics = []
        for metric in getattr(item, "metrics_data", []):
            metrics.append(
                {
                    "name": getattr(metric, "name", "unknown"),
                    "score": getattr(metric, "score", None),
                    "threshold": getattr(metric, "threshold", None),
                    "success": getattr(metric, "success", False),
                    "reason": getattr(metric, "reason", None),
                    "error": getattr(metric, "error", None),
                }
            )
        cases.append(
            {
                "case_id": getattr(item, "name", None),
                "success": getattr(item, "success", False),
                "metrics": metrics,
            }
        )
    return {
        "status": (
            "success" if cases and all(item["success"] for item in cases) else "failed"
        ),
        "case_count": len(cases),
        "passed_case_count": sum(bool(item["success"]) for item in cases),
        "cases": cases,
        "confident_link": getattr(result, "confident_link", None),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_judge_config(args.config.resolve())
        judge_config = resolve_judge_config(
            config,
            provider=args.provider,
            model=args.model,
            base_url=args.base_url,
            api_key_env=args.api_key_env,
            temperature=args.temperature,
            timeout_seconds=args.timeout_seconds,
        )
        threshold = args.threshold if args.threshold is not None else config["threshold"]
        max_concurrent = (
            args.max_concurrent
            if args.max_concurrent is not None
            else config["max_concurrent"]
        )
        payloads = load_payloads(args.dataset.resolve())
        result = run_judge(
            payloads,
            judge_config=judge_config,
            api_key=resolve_api_key(judge_config, PROJECT_ROOT),
            threshold=threshold,
            max_concurrent=max_concurrent,
        )
        payload = serialize_result(result)
        payload["judge"] = {
            "config_version": config["schema_version"],
            "provider": judge_config["provider"],
            "model": judge_config["model"],
            "base_url": judge_config["base_url"],
            "api_key_env": judge_config["api_key_env"],
            "temperature": judge_config["temperature"],
            "timeout_seconds": judge_config["timeout_seconds"],
            "threshold": threshold,
            "max_concurrent": max_concurrent,
            "semantic_metrics": list(SEMANTIC_METRICS),
        }
        if args.output is not None:
            output = args.output.resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            payload["output"] = str(output)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
