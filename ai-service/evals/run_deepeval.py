"""Run LLM-as-a-Judge metrics in an isolated DeepEval environment."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Sequence, TypedDict


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent / "config" / "judge_v0.json"


class JudgeConfig(TypedDict):
    schema_version: str
    model: str
    threshold: float
    max_concurrent: int
    api_key_env: str


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run explicit DeepEval Judge metrics over exported MoodFlow cases"
    )
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG_PATH)
    parser.add_argument(
        "--model",
        help=(
            "DeepEval judge model; CLI overrides DEEPEVAL_JUDGE_MODEL and config"
        ),
    )
    parser.add_argument("--threshold", type=float)
    parser.add_argument("--max-concurrent", type=int)
    parser.add_argument("--output", type=Path)
    return parser


def load_judge_config(path: Path) -> JudgeConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"deepeval_config_invalid:{path}") from exc
    required = {
        "schema_version",
        "model",
        "threshold",
        "max_concurrent",
        "api_key_env",
    }
    if not isinstance(payload, dict) or set(payload) != required:
        raise ValueError(f"deepeval_config_invalid:{path}")
    if (
        payload.get("schema_version") != "v0"
        or not isinstance(payload.get("model"), str)
        or not payload["model"].strip()
        or not isinstance(payload.get("threshold"), (int, float))
        or isinstance(payload.get("threshold"), bool)
        or not 0 <= payload["threshold"] <= 1
        or not isinstance(payload.get("max_concurrent"), int)
        or isinstance(payload.get("max_concurrent"), bool)
        or not 1 <= payload["max_concurrent"] <= 20
        or payload.get("api_key_env") != "OPENAI_API_KEY"
    ):
        raise ValueError(f"deepeval_config_invalid:{path}")
    return JudgeConfig(
        schema_version="v0",
        model=payload["model"],
        threshold=float(payload["threshold"]),
        max_concurrent=payload["max_concurrent"],
        api_key_env=payload["api_key_env"],
    )


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


def run_judge(
    payloads: Sequence[dict[str, Any]],
    *,
    model: str,
    threshold: float,
    max_concurrent: int,
) -> Any:
    if not model or not model.strip():
        raise ValueError("DeepEval judge model is required")
    if not 0 <= threshold <= 1:
        raise ValueError("DeepEval threshold must be between 0 and 1")
    if not 1 <= max_concurrent <= 20:
        raise ValueError("DeepEval maximum concurrency must be between 1 and 20")
    if model.startswith("gpt-") and not os.getenv("OPENAI_API_KEY"):
        raise ValueError(
            "OPENAI_API_KEY is required in the Judge process environment"
        )

    # Avoid reading MoodFlow's runtime .env in the isolated evaluation process.
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
    except ImportError as exc:
        raise RuntimeError(
            "DeepEval is unavailable; install evals/requirements.txt in .venv-eval"
        ) from exc

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
        FaithfulnessMetric(threshold=threshold, model=model),
        AnswerRelevancyMetric(threshold=threshold, model=model),
        ContextualPrecisionMetric(threshold=threshold, model=model),
        ContextualRecallMetric(threshold=threshold, model=model),
    ]
    return evaluate(
        test_cases=test_cases,
        metrics=metrics,
        async_config=AsyncConfig(
            run_async=True,
            max_concurrent=max_concurrent,
            throttle_value=0,
        ),
        display_config=DisplayConfig(
            show_indicator=True,
            print_results=True,
            inspect_after_run=False,
        ),
        cache_config=CacheConfig(write_cache=False, use_cache=False),
    )


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
        "status": "success" if cases and all(item["success"] for item in cases) else "failed",
        "case_count": len(cases),
        "passed_case_count": sum(bool(item["success"]) for item in cases),
        "cases": cases,
        "confident_link": getattr(result, "confident_link", None),
    }


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_judge_config(args.config.resolve())
        model = args.model or os.getenv("DEEPEVAL_JUDGE_MODEL") or config["model"]
        threshold = (
            args.threshold if args.threshold is not None else config["threshold"]
        )
        max_concurrent = (
            args.max_concurrent
            if args.max_concurrent is not None
            else config["max_concurrent"]
        )
        payloads = load_payloads(args.dataset.resolve())
        result = run_judge(
            payloads,
            model=model,
            threshold=threshold,
            max_concurrent=max_concurrent,
        )
        payload = serialize_result(result)
        payload["judge"] = {
            "config_version": config["schema_version"],
            "model": model,
            "threshold": threshold,
            "max_concurrent": max_concurrent,
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
