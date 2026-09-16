"""Pure budget, selection, context-trimming, and cache helpers for semantic evals."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal, TypedDict


SUPPORTED_BUDGET_METRICS = frozenset({"Faithfulness", "Answer Relevancy"})
CITATION_PATTERN = re.compile(r"\[([A-Za-z0-9][A-Za-z0-9:_-]{1,199})\]")
CONTEXT_ID_PATTERN = re.compile(r"^\[([^\]]+)\]")


class SemanticBudgetConfig(TypedDict):
    schema_version: str
    mode: str
    max_cases: int
    case_ids: list[str]
    semantic_metrics: list[str]
    faithfulness_context: Literal["cited_only"]
    include_reason: bool
    cache_enabled: bool
    retry_errors_only: bool
    max_judge_metric_runs: int
    max_estimated_source_tokens: int
    deepeval_task_timeout_seconds: int


def load_budget_config(path: Path) -> SemanticBudgetConfig:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"deepeval_budget_config_invalid:{path}") from exc
    expected = {
        "schema_version",
        "mode",
        "max_cases",
        "case_ids",
        "semantic_metrics",
        "faithfulness_context",
        "include_reason",
        "cache_enabled",
        "retry_errors_only",
        "max_judge_metric_runs",
        "max_estimated_source_tokens",
        "deepeval_task_timeout_seconds",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise ValueError(f"deepeval_budget_config_invalid:{path}")
    config = SemanticBudgetConfig(**payload)
    _validate_budget_config(config, path)
    return config


def _validate_budget_config(config: SemanticBudgetConfig, path: Path) -> None:
    case_ids = config["case_ids"]
    metrics = config["semantic_metrics"]
    integer_limits = (
        config["max_cases"],
        config["max_judge_metric_runs"],
        config["max_estimated_source_tokens"],
        config["deepeval_task_timeout_seconds"],
    )
    if (
        config["schema_version"] != "v0"
        or config["mode"] != "smoke"
        or any(
            not isinstance(value, int) or isinstance(value, bool) or value < 1
            for value in integer_limits
        )
        or not isinstance(case_ids, list)
        or not case_ids
        or len(case_ids) > config["max_cases"]
        or len(case_ids) != len(set(case_ids))
        or any(not isinstance(case_id, str) or not case_id.strip() for case_id in case_ids)
        or not isinstance(metrics, list)
        or not metrics
        or len(metrics) != len(set(metrics))
        or not set(metrics).issubset(SUPPORTED_BUDGET_METRICS)
        or config["faithfulness_context"] != "cited_only"
        or not isinstance(config["include_reason"], bool)
        or not isinstance(config["cache_enabled"], bool)
        or not isinstance(config["retry_errors_only"], bool)
        or len(case_ids) * len(metrics) > config["max_judge_metric_runs"]
        or not 60 <= config["deepeval_task_timeout_seconds"] <= 3600
    ):
        raise ValueError(f"deepeval_budget_config_invalid:{path}")


def select_budget_payloads(
    payloads: list[dict[str, Any]],
    config: SemanticBudgetConfig,
) -> list[dict[str, Any]]:
    by_id = {payload["case_id"]: payload for payload in payloads}
    missing = [case_id for case_id in config["case_ids"] if case_id not in by_id]
    if missing:
        raise ValueError(f"deepeval_budget_cases_missing:{','.join(missing)}")
    return [by_id[case_id] for case_id in config["case_ids"]]


def prepare_metric_payload(
    payload: dict[str, Any],
    metric_name: str,
    *,
    faithfulness_context: str,
) -> dict[str, Any]:
    prepared = dict(payload)
    if metric_name == "Faithfulness" and faithfulness_context == "cited_only":
        prepared["retrieval_context"] = cited_retrieval_context(payload)
    return prepared


def cited_retrieval_context(payload: dict[str, Any]) -> list[str]:
    citation_ids = list(dict.fromkeys(CITATION_PATTERN.findall(payload["actual_output"])))
    contexts: dict[str, str] = {}
    for value in payload["retrieval_context"]:
        match = CONTEXT_ID_PATTERN.match(value)
        if match is not None:
            contexts[match.group(1)] = value
    selected = [contexts[citation_id] for citation_id in citation_ids if citation_id in contexts]
    if not citation_ids or len(selected) != len(citation_ids):
        raise ValueError(f"deepeval_cited_context_missing:{payload['case_id']}")
    return selected


def metric_cache_key(
    payload: dict[str, Any],
    metric_name: str,
    *,
    judge: dict[str, Any],
    threshold: float,
    include_reason: bool,
    faithfulness_context: str,
) -> str:
    prepared = prepare_metric_payload(
        payload,
        metric_name,
        faithfulness_context=faithfulness_context,
    )
    if metric_name == "Faithfulness":
        semantic_input = {
            "actual_output": prepared["actual_output"],
            "retrieval_context": prepared["retrieval_context"],
        }
    elif metric_name == "Answer Relevancy":
        semantic_input = {
            "input": prepared["input"],
            "actual_output": prepared["actual_output"],
        }
    else:
        raise ValueError(f"deepeval_metric_not_budgeted:{metric_name}")
    material = {
        "schema_version": "semantic-cache-v0",
        "metric": metric_name,
        "semantic_input": semantic_input,
        "judge": {
            "provider": judge["provider"],
            "model": judge["model"],
            "base_url": judge["base_url"],
            "temperature": judge["temperature"],
        },
        "threshold": threshold,
        "include_reason": include_reason,
        "faithfulness_context": faithfulness_context,
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def estimate_source_tokens(
    payload: dict[str, Any],
    metric_name: str,
    *,
    faithfulness_context: str,
) -> int:
    """Return a conservative source-text estimate; provider prompt overhead is excluded."""
    prepared = prepare_metric_payload(
        payload,
        metric_name,
        faithfulness_context=faithfulness_context,
    )
    if metric_name == "Faithfulness":
        text = prepared["actual_output"] + "\n" + "\n".join(
            prepared["retrieval_context"]
        )
    elif metric_name == "Answer Relevancy":
        text = prepared["input"] + "\n" + prepared["actual_output"]
    else:
        raise ValueError(f"deepeval_metric_not_budgeted:{metric_name}")
    # Chinese is often close to one token per character. Counting every Unicode
    # character as a token intentionally overestimates most English text.
    return max(1, len(text))
