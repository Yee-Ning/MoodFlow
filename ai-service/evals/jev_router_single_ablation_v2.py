"""Frozen paired Jev Choice ablation on capability-adjudicated Single Gold V2."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Any

from typesafe_sdk import Choice

from app.agent.decision.jev.capability_criteria import (
    CONTRACT_PATH, ROUTER_QUESTION_INSTRUCTIONS, router_criteria,
)
from app.agent.decision.jev.client import JevClient
from app.config.settings import get_settings
from evals.jev_router_baseline_v1 import single_metrics, timing
from evals.jev_shadow_v1 import read_jsonl, write_jsonl

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals/datasets/router_eval_gold_v2.jsonl"
RESULTS = ROOT / "evals/reports/jev_router_single_ablation_v2_results.jsonl"
METRICS = ROOT / "evals/reports/jev_router_single_ablation_v2_metrics.json"
FAILURES = ROOT / "evals/reports/jev_router_single_failure_cases_v2.jsonl"
LEGACY_RESULTS = ROOT / "evals/reports/jev_router_baseline_v1_results.jsonl"
GOLD_SHA256 = "fb4961666dd15c693a45ea16e4ac7f5d3de404e77dcea72cc61c26c948f416a6"
CONTRACT_SHA256 = "238d373ff43db0eea72dae17525525276a666c3819f959de8925206b0915a97c"
LEGACY_SHA256 = "41ce211e13b5d3a8661c6c531099265b27a439ac73cd49c47a88fda6e6078b1f"
LOW_CONFIDENCE_THRESHOLD = .8  # Existing production setting; analysis only.
ROUTES = ("current_song", "tool_calling", "general_chat", "worldline")

# Exact criteria from commit 3d3cd3c, kept solely as the historical comparator.
OLD_CRITERIA = {
    "current_song": "Analyze the currently playing song or lyrics",
    "tool_calling": "Perform a music player or library operation",
    "worldline": "Research music knowledge or relationships",
    "general_chat": "General conversation or questions",
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def eligible_cases() -> list[dict[str, Any]]:
    if sha256(GOLD) != GOLD_SHA256 or sha256(CONTRACT_PATH) != CONTRACT_SHA256:
        raise RuntimeError("Gold V2 or capability contract changed after freeze")
    rows = [row for row in read_jsonl(GOLD)
            if row["scoring_status"] == "scored" and row["task"] == "single_intent"]
    if len(rows) != 46 or len({r["case_id"] for r in rows}) != len(rows):
        raise RuntimeError("Single Gold V2 case count or IDs changed")
    if any(len(row["adjudicated_label"]) != 1 for row in rows):
        raise RuntimeError("Single Gold V2 contains a non-single label")
    return rows


async def call_variant(client: JevClient, query: str, criteria: dict[str, str]) -> dict[str, Any]:
    start = perf_counter()
    try:
        response = await client.decide(
            {"query": query[:4000]},
            {"route": Choice(instructions=ROUTER_QUESTION_INSTRUCTIONS, criteria=criteria)},
        )
        answer = response.answers["route"]
        return {"predicted_routes": [answer.choice] if answer.choice else [],
                "structured_output": {"route": asdict(answer)},
                "confidence": answer.confidence, "probabilities": answer.probabilities,
                "response_model": response.response_model,
                "evaluation_timestamp": response.evaluation_timestamp,
                "api_latency_ms": response.latency_ms,
                "wall_latency_ms": round((perf_counter() - start) * 1000, 2),
                "usage": response.usage, "error_category": None, "error_type": None}
    except Exception as exc:
        # JevClient sanitizes upstream exception text. Never retain request secrets.
        error_type = str(exc) if type(exc).__name__ == "JevDecisionError" else type(exc).__name__
        schema_types = {"ValueError", "ValidationError", "KeyError", "TypeError", "AttributeError"}
        return {"predicted_routes": [], "structured_output": None,
                "confidence": None, "probabilities": None, "response_model": None,
                "evaluation_timestamp": datetime.now(UTC).isoformat(),
                "api_latency_ms": None,
                "wall_latency_ms": round((perf_counter() - start) * 1000, 2),
                "usage": None,
                "error_category": "parse_schema_failure" if error_type in schema_types else "api_failure",
                "error_type": error_type}


def _metric_rows(rows: list[dict[str, Any]], variant: str, label_field: str) -> list[dict[str, Any]]:
    return [{"gold_routes": row[label_field], "predicted_routes": row[variant]["predicted_routes"]}
            for row in rows]


def _confusion_count(metrics: dict[str, Any]) -> int:
    matrix = metrics["confusion_matrix"]
    return matrix["general_chat"]["worldline"] + matrix["worldline"]["general_chat"]


def variant_metrics(rows: list[dict[str, Any]], variant: str,
                    label_field: str = "adjudicated_label") -> dict[str, Any]:
    classification = single_metrics(_metric_rows(rows, variant, label_field), "predicted_routes")
    outputs = [row[variant] for row in rows]
    usage = Counter()
    for output in outputs:
        usage.update({key: value for key, value in (output["usage"] or {}).items()
                      if isinstance(value, int)})
    return {**classification,
            "general_chat_worldline_confusion": _confusion_count(classification),
            "low_confidence_count": sum(o["confidence"] is not None
                                        and o["confidence"] < LOW_CONFIDENCE_THRESHOLD for o in outputs),
            "parse_schema_failure_count": sum(o["error_category"] == "parse_schema_failure" for o in outputs),
            "api_failure_count": sum(o["error_category"] == "api_failure" for o in outputs),
            "latency_ms": {"api": timing(outputs, "api_latency_ms"),
                           "wall": timing(outputs, "wall_latency_ms")},
            "token_usage": dict(usage),
            "response_models": dict(Counter(o["response_model"] for o in outputs
                                            if o["response_model"]))}


def legacy_gold_correction(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """Rescore phase-one predictions; hold predictions and criteria fixed."""
    if sha256(LEGACY_RESULTS) != LEGACY_SHA256:
        raise ValueError("phase-one replay results changed")
    gold = {r["case_id"]: r for r in cases if r["original_task"] == "single_intent"}
    replay = [r for r in read_jsonl(LEGACY_RESULTS) if r["case_id"] in gold
              and r["task"] == "single_intent"]
    if len(gold) != 30 or len(replay) != 30:
        raise ValueError("phase-one paired cohort changed")
    old = single_metrics([{"gold_routes": gold[r["case_id"]]["original_label"],
                           "predicted_routes": r["jev_routes"]} for r in replay], "predicted_routes")
    new = single_metrics([{"gold_routes": gold[r["case_id"]]["adjudicated_label"],
                           "predicted_routes": r["jev_routes"]} for r in replay], "predicted_routes")
    flips = {"newly_correct": 0, "newly_incorrect": 0}
    for row in replay:
        item = gold[row["case_id"]]
        before = row["jev_routes"] == item["original_label"]
        after = row["jev_routes"] == item["adjudicated_label"]
        flips["newly_correct" if after else "newly_incorrect"] += before != after
    return {"legacy_results_sha256": LEGACY_SHA256, "paired_count": len(replay),
            "same_phase_one_predictions_old_labels": old,
            "same_phase_one_predictions_adjudicated_labels": new,
            "accuracy_delta": round(new["accuracy"] - old["accuracy"], 4),
            "label_flip_outcomes": flips}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    expected = {row["case_id"] for row in eligible_cases()}
    if {row["case_id"] for row in rows} != expected or len(rows) != len(expected):
        raise ValueError("Ablation results do not exactly cover frozen Single Gold V2")
    if any(row.get("dataset_sha256") != GOLD_SHA256 for row in rows):
        raise ValueError("Ablation result dataset hash mismatch")
    criteria_hashes = rows[0]["criteria_sha256"]
    if any(row.get("criteria_sha256") != criteria_hashes for row in rows):
        raise ValueError("Ablation criteria changed during run")
    old_cohort = [row for row in rows if row["original_task"] == "single_intent"
                  and len(row["original_label"]) == 1]
    if len(old_cohort) != 30:
        raise ValueError("historical single-intent paired cohort changed")
    old_labels = variant_metrics(old_cohort, "A", "original_label")
    new_labels = variant_metrics(old_cohort, "A", "adjudicated_label")
    a = variant_metrics(rows, "A")
    b = variant_metrics(rows, "B")
    return {"freeze": {"dataset_sha256": GOLD_SHA256, "contract_sha256": CONTRACT_SHA256,
                       "criteria_sha256": criteria_hashes,
                       "requested_model": "jev-latest", "question_type": "Choice",
                       "question_instructions": ROUTER_QUESTION_INSTRUCTIONS,
                       "confidence_threshold_analysis_only": LOW_CONFIDENCE_THRESHOLD,
                       "sdk_client": "JevClient / AsyncTypeSafeClient", "max_retries": 0,
                       "timeout_seconds": 5, "order": "AB for even index; BA for odd index"},
            "gold_correction_effect": {
                "phase_one_replay": legacy_gold_correction(old_cohort),
                "paired_historical_single_count": len(old_cohort),
                "same_A_predictions_old_labels": old_labels,
                "same_A_predictions_adjudicated_labels": new_labels,
                "accuracy_delta": round(new_labels["accuracy"] - old_labels["accuracy"], 4),
                "macro_f1_delta": round(new_labels["macro_f1"] - old_labels["macro_f1"], 4),
            },
            "criteria_alignment_effect": {
                "A_old_criteria": a, "B_contract_aligned_v2": b,
                "accuracy_delta": round(b["accuracy"] - a["accuracy"], 4),
                "macro_f1_delta": round(b["macro_f1"] - a["macro_f1"], 4),
                "general_chat_worldline_confusion_delta": (
                    b["general_chat_worldline_confusion"] - a["general_chat_worldline_confusion"]),
            },
            "composition": {"formal_single": len(rows),
                            "historical_single_paired": len(old_cohort),
                            "new_boundary_single": sum(r["action"] == "ADD_BOUNDARY" for r in rows),
                            "historical_multi_to_single": sum(r["original_task"] == "multi_intent" for r in rows)}}


def failure_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        gold = row["adjudicated_label"]
        categories = []
        for variant in ("A", "B"):
            result = row[variant]
            if result["predicted_routes"] != gold:
                categories.append(f"{variant}_wrong_route")
            if result["confidence"] is not None and result["confidence"] < LOW_CONFIDENCE_THRESHOLD:
                categories.append(f"{variant}_low_confidence")
            if result["error_category"]:
                categories.append(f"{variant}_{result['error_category']}")
        if not categories:
            continue
        output.append({"case_id": row["case_id"], "query": row["query"],
                       "original_label": row["original_label"], "adjudicated_label": gold,
                       "adjudication_action": row["action"], "pair_id": row.get("pair_id"),
                       "categories": categories, "A_old_criteria": row["A"],
                       "B_contract_aligned_v2": row["B"],
                       "structured_output_level": "validated JevClient response; SDK wire payload unavailable"})
    return output


def write_summary(rows: list[dict[str, Any]]) -> None:
    METRICS.write_text(json.dumps(summarize(rows), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_jsonl(FAILURES, failure_rows(rows))


async def run() -> None:
    cases = eligible_cases()
    settings = get_settings()
    if (settings.jev_model != "jev-latest" or settings.jev_timeout_seconds != 5
            or settings.jev_router_confidence_threshold != LOW_CONFIDENCE_THRESHOLD
            or not settings.jev_shadow_mode):
        raise RuntimeError("Jev model, timeout, confidence setting and Shadow Mode must match freeze")
    if not settings.typesafe_api_key:
        raise RuntimeError("missing_api_key")
    criteria = {"A": OLD_CRITERIA, "B": router_criteria()}
    if list(criteria["A"]) != list(criteria["B"]):
        raise RuntimeError("Choice key order changed between A and B")
    client = JevClient(settings)
    output = []
    for index, case in enumerate(cases):
        row = {**case, "dataset_sha256": GOLD_SHA256, "requested_model": settings.jev_model,
               "criteria_sha256": {key: hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()
                                   for key, value in criteria.items()}}
        for variant in (("A", "B") if index % 2 == 0 else ("B", "A")):
            row[variant] = await call_variant(client, case["query"], criteria[variant])
        output.append(row)
        write_jsonl(RESULTS, output)
        print(f"{index + 1}/{len(cases)} {case['case_id']} A={row['A']['predicted_routes']} B={row['B']['predicted_routes']}", flush=True)
    write_summary(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("run", "summarize"))
    args = parser.parse_args()
    if args.command == "run":
        asyncio.run(run())
    else:
        write_summary(read_jsonl(RESULTS))


if __name__ == "__main__":
    main()
