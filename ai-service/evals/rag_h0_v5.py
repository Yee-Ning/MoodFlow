"""Approved H0: replay 32 successes offline, rerun exactly 18 prior failures.

West approved A+B, prompt-only C2 and this run's in-memory .env LLM access.
Baseline scripts and artifacts remain unchanged. Generation/judge reuse Step 4.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
import logging
import sys
from types import SimpleNamespace
from unittest.mock import patch

from evals import ragas_full_v5 as full
from evals import ragas_full_v5_report as reporting
from evals.ragas_gate0 import REPO_ROOT, Gate0Guard, configure_offline, protected_hashes, sha256, write_json
from evals.ragas_smoke_v5_report import load_calls


BASELINE = full.OUTPUT
OUTPUT = REPO_ROOT / "workspace/moodflow-review/rag-h0-abstention-gate1-20261017"
CASE_IDS = tuple("rag-v0-" + value for value in (
    "001", "002", "003", "006", "025", "026", "031", "035", "039", "043", "044", "045",
    "046", "048", "057", "062", "064", "066"))
APPROVED_PRODUCTION_FILES = {
    "app/agent/foundation/subgraphs/worldline/grounding.py",
    "app/agent/foundation/subgraphs/worldline/generation.py",
    "app/agent/foundation/subgraphs/worldline/prompts.py",
}
read_json = full.read_json


def verify_baseline():
    completion = read_json(BASELINE / "completion.json")
    if any(sha256(BASELINE / name) != digest for name, digest in completion["artifact_hashes"].items()):
        raise ValueError("h0_step4_artifact_drift")
    if protected_hashes() != completion["protected_after"]:
        raise ValueError("h0_frozen_input_drift")
    old = read_json(BASELINE / "preparation.json")["production_before"]
    current = full.gate0.production_hashes()
    if set(current) != set(old) or {name for name in old if old[name] != current[name]} != APPROVED_PRODUCTION_FILES:
        raise ValueError("h0_unapproved_production_change")
    rows = read_json(BASELINE / "inputs.json")
    failures = tuple(r["case_id"] for r in read_json(BASELINE / "generation_status.json")
                     if r["generation_status"] != "success")
    if failures != CASE_IDS:
        raise ValueError("h0_failure_population_changed")
    return rows


def prepare():
    rows = verify_baseline()
    selected = [r for r in rows if r["case_id"] in CASE_IDS]
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "answers").mkdir()
    write_json(OUTPUT / "inputs.json", selected)
    write_json(OUTPUT / "preparation.json", {
        "task": "H0", "gate": 1, "status": "prepared_before_api_calls", "case_ids": list(CASE_IDS),
        "inputs_sha256": sha256(OUTPUT / "inputs.json"), "baseline_completion_sha256": sha256(BASELINE / "completion.json"),
        "protected_before": protected_hashes(), "production_after_fix": full.gate0.production_hashes(),
        "approved_files": sorted(APPROVED_PRODUCTION_FILES),
        "scope": "A fixed templates + B single-field prompt/retry + C2 prompt only; no A extension or citation repair",
        "api_authorization": "West explicitly approved rerunning exactly these 18 failures in H0 Gate 1.",
        "secret_authorization": "West separately authorized this H0 run's in-memory .env LLM key/config access.",
        "success32_validation": "offline saved-response regression only; no fresh API or faithfulness measurement",
    })


def inputs():
    all_rows = verify_baseline()
    preparation = read_json(OUTPUT / "preparation.json")
    rows = read_json(OUTPUT / "inputs.json")
    if (sha256(OUTPUT / "inputs.json") != preparation["inputs_sha256"]
            or rows != [r for r in all_rows if r["case_id"] in CASE_IDS]
            or tuple(r["case_id"] for r in rows) != CASE_IDS):
        raise ValueError("h0_fixed_18_inputs_changed")
    if full.gate0.production_hashes() != preparation["production_after_fix"]:
        raise ValueError("h0_code_changed_during_run")
    return rows


async def replay_success32():
    import httpx
    rows = [r for r in verify_baseline() if r["case_id"] not in CASE_IDS]
    originals = {r["case_id"]: read_json(BASELINE / "answers" / (r["case_id"] + ".json")) for r in rows}
    assert len(rows) == 32 and all(r["generation_status"] == "success" for r in originals.values())
    saved_calls = load_calls(BASELINE / "generation_calls")
    last = {row["case_id"]: [c for c in saved_calls if c["case_id"] == row["case_id"]][-1] for row in rows}
    seen = set()

    class ReplayRecorder:
        async def request(self, request):
            case_id = full.smoke.SCOPE.get()["case_id"]
            saved = last[case_id]
            body = json.loads(request.content)
            if case_id in seen or {k: v for k, v in body.items() if k != "instructions"} != {
                    k: v for k, v in saved["request"].items() if k != "instructions"}:
                raise ValueError("h0_offline_32_non_prompt_protocol_changed")
            seen.add(case_id)
            request.extensions["saved_response"] = saved["response"]

        async def response(self, response):
            await response.aread()

    transport_type = httpx.MockTransport

    async def respond(request):
        return httpx.Response(200, json=request.extensions["saved_response"])

    config = read_json(BASELINE / "generation_config.json")
    settings = SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: "H0_OFFLINE_ONLY"),
        base_url=config["base_url"], model=config["model"], generation_model=config["model"],
        timeout_seconds=config["timeout_seconds"], max_output_tokens=config["max_output_tokens"])
    with (patch.object(httpx, "MockTransport", lambda unused: transport_type(respond)), redirect_stdout(io.StringIO())):
        results = await full.smoke.generate_all(rows, settings, ReplayRecorder(), offline=True)
    for result in results:
        prior = originals[result["case_id"]]
        if any(result[key] != prior[key] for key in ("answer", "citation_ids", "generation_status")):
            raise ValueError("h0_offline_32_delivery_changed")
    scores = [r for r in full.gate0.read_jsonl(BASELINE / "fifty_cases.jsonl") if r["case_id"] in originals]
    outcomes = {(r["case_id"], r["metric"]): r for r in
                [read_json(p) for p in (BASELINE / "metric_outcomes").glob("*.json")]}
    replay = reporting.replay_scores(scores, load_calls(BASELINE / "judge_calls"), outcomes)
    write_json(OUTPUT / "offline_success32.json", {"status": "passed", "case_ids": [r["case_id"] for r in rows],
        "saved_answers_and_citations_exact": 32, "non_prompt_request_json_exact": 32,
        "old_successful_responses_pass_current_production": 32, "saved_scores_recomputed_exact": 64,
        "metric_replay": replay, "real_api_calls": 0, "credentials_loaded": False,
        "limitation": "32 题未做真实 API 回归，'新 prompt 下 faithfulness 不跌'未验证。"})


def template_delivery_observations():
    """Describe saved deliveries using the current production template matcher."""
    from app.agent.foundation.subgraphs.worldline.grounding import _is_abstention_paragraph

    observations = {}
    for row in inputs():
        answer = read_json(OUTPUT / "answers" / (row["case_id"] + ".json"))
        if answer["generation_status"] != "success":
            continue
        paragraphs = [p.strip() for p in answer["answer"].split("\n") if p.strip()]
        count = sum(_is_abstention_paragraph(p) for p in paragraphs)
        observations[row["case_id"]] = {"paragraphs": len(paragraphs), "template_paragraphs": count,
            "pure_template_delivery": bool(paragraphs) and count == len(paragraphs)}
    return observations


def main():
    configure_offline()
    logging.getLogger("app.llm.client").disabled = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "offline32", "replay18", "generate", "judge"))
    args = parser.parse_args()
    if args.mode in {"prepare", "offline32", "replay18"}:
        guard = Gate0Guard()
        sys.addaudithook(guard.audit)
        if args.mode == "prepare":
            prepare()
        elif args.mode == "offline32":
            inputs()
            asyncio.run(replay_success32())
        else:
            from evals.ragas_full_v5_saved_generation import replay
            results = asyncio.run(replay(OUTPUT, inputs()))
            write_json(OUTPUT / "generation_replay18.json", {"status": "passed", "cases": results,
                "template_delivery_observations": template_delivery_observations(),
                "real_api_calls": 0, "credentials_loaded": False, "denied_operations": guard.denied})
        if guard.denied:
            raise ValueError("h0_offline_forbidden_operation")
        print(json.dumps({"mode": args.mode, "status": "passed", "real_api_calls": 0}))
        return
    sys.addaudithook(full.smoke.secret_guard)
    rows = inputs()
    offline = read_json(OUTPUT / "offline_success32.json")
    if offline["status"] != "passed":
        raise ValueError("h0_offline_regression_not_passed")
    settings = full.smoke.load_approved_llm_settings()
    config = {"model": settings.generation_model, "base_url": str(settings.base_url),
              "timeout_seconds": settings.timeout_seconds, "max_output_tokens": settings.max_output_tokens,
              "temperature": 0, "secret_exported": False}
    if config != read_json(BASELINE / "generation_config.json"):
        raise ValueError("h0_generation_config_changed")
    with patch.object(full, "OUTPUT", OUTPUT), full.smoke_scope(rows):
        if args.mode == "generate":
            write_json(OUTPUT / "generation_config.json", config)
            asyncio.run(full.generate(rows, settings))
        else:
            answers = [read_json(OUTPUT / "answers" / (row["case_id"] + ".json")) for row in rows]
            full.judge(answers, settings)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        message = str(exc)
        safe = message if isinstance(exc, ValueError) and message.startswith(("h0_", "step4_", "gate1_")) else type(exc).__name__
        print(json.dumps({"stage": "stopped", "safe_error": safe}), flush=True)
        sys.exit(1)
