"""Approved H0 round 2: exact 12 live cases, 38 saved successes offline."""

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

from evals import rag_h0_v5 as prior
from evals import ragas_full_v5 as full
from evals import ragas_full_v5_report as reporting
from evals.ragas_gate0 import Gate0Guard, REPO_ROOT, configure_offline, protected_hashes, sha256, write_json
from evals.ragas_smoke_v5_report import load_calls


OUTPUT = REPO_ROOT / "workspace/moodflow-review/rag-h0-round2-gate1-20261018"
ROUND1 = prior.OUTPUT
STEP4 = prior.BASELINE
CASE_IDS = tuple("rag-v0-" + value for value in (
    "001", "002", "003", "025", "026", "031", "043", "044", "045", "048", "057", "064"))
CHANGED = {"app/agent/structured_output/service.py", *[
    "app/agent/foundation/subgraphs/worldline/" + name + ".py"
    for name in ("generation", "grounding", "prompts", "stage_pack")]}
ADDED = {"app/agent/foundation/subgraphs/worldline/" + name + ".py"
         for name in ("narrative_boundary", "narrative_normalization")}
read_json = full.read_json
_baseline_terminal_detail = full.terminal_detail


def terminal_detail(error):
    detail = _baseline_terminal_detail(error)
    from app.agent.structured_output.service import NormalizationError
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, NormalizationError):
            detail.update(validation_code=error.code, failure_type="normalization_error")
        error = error.__cause__
    return detail


def verify_priors():
    for directory in (STEP4, ROUND1, REPO_ROOT / "workspace/moodflow-review/rag-h0-round2-gate0-20261017",
                      REPO_ROOT / "workspace/moodflow-review/rag-h0-root-fix-plan-20261018"):
        completion = read_json(directory / "completion.json")
        if any(sha256(directory / name) != digest for name, digest in completion["artifact_hashes"].items()):
            raise ValueError("h02_prior_artifacts_changed")
    if protected_hashes() != read_json(ROUND1 / "completion.json")["protected_after"]:
        raise ValueError("h02_frozen_input_drift")
    before = read_json(REPO_ROOT / "workspace/moodflow-review/rag-h0-root-fix-plan-20261018/verification.json")["production_before"]
    current = full.gate0.production_hashes()
    if (set(current) - set(before) != ADDED or set(before) - set(current)
            or {p for p in before if current[p] != before[p]} != CHANGED):
        raise ValueError("h02_unapproved_production_changes")
    failed = tuple(r["case_id"] for r in read_json(ROUND1 / "generation_status.json") if r["generation_status"] != "success")
    if failed != CASE_IDS:
        raise ValueError("h02_failure_population_drift")
    return read_json(STEP4 / "inputs.json")


def prepare():
    rows = [r for r in verify_priors() if r["case_id"] in CASE_IDS]
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "answers").mkdir()
    (OUTPUT / ".gitattributes").write_text("* text eol=crlf\n", encoding="utf-8")
    write_json(OUTPUT / "inputs.json", rows)
    write_json(OUTPUT / "preparation.json", {"status": "fixed_before_real_calls", "case_ids": list(CASE_IDS),
        "inputs_sha256": sha256(OUTPUT / "inputs.json"), "production_after_fix": full.gate0.production_hashes(),
        "protected_before": protected_hashes(), "approved_scope": "P0-1 + P0-4 + three A2 templates; C2 prompt only; no V2",
        "api_authorization": "West approved exactly 12 generation/judge cases in round 2 Gate 1.",
        "secret_authorization": "West separately authorized in-memory ai-service/.env LLM fields for this round's 12 cases.",
        "excluded_live_successes": 38})


def inputs():
    rows = [r for r in verify_priors() if r["case_id"] in CASE_IDS]
    prep = read_json(OUTPUT / "preparation.json")
    if (read_json(OUTPUT / "inputs.json") != rows or sha256(OUTPUT / "inputs.json") != prep["inputs_sha256"]
            or full.gate0.production_hashes() != prep["production_after_fix"]):
        raise ValueError("h02_fixed_input_or_code_changed")
    return rows


def delivery_state(result, sent=None):
    if result["generation_status"] != "success":
        failure = result["generation_failure"] or {}
        if failure.get("validation_code") == "normalization_failed":
            return "validation_failed"
        return "provider_failed" if failure.get("failure_type") in {"model_error", "truncation", "provider_error"} else "validation_failed"
    from app.agent.foundation.subgraphs.worldline.grounding import _is_abstention_paragraph
    from app.agent.foundation.subgraphs.worldline.narrative_boundary import boundary_context_from_input, is_boundary_paragraph
    context = boundary_context_from_input(sent) if sent is not None else None
    paragraphs = [p.strip() for p in result["answer"].split("\n") if p.strip()]
    disclosure = "当前未检索到补充音乐知识，以下内容仅基于音乐目录事实。"
    return "abstained" if paragraphs and all(
        _is_abstention_paragraph(p) or is_boundary_paragraph(p, context) or p == disclosure
        for p in paragraphs) else "answered"


def offline_settings():
    config = read_json(ROUND1 / "generation_config.json")
    return SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: "ROUND2_OFFLINE_ONLY"),
        base_url=config["base_url"], model=config["model"], generation_model=config["model"],
        timeout_seconds=config["timeout_seconds"], max_output_tokens=config["max_output_tokens"])


async def offline38():
    import httpx
    from app.agent.foundation.subgraphs.worldline.narrative_normalization import narrative_audit_scope
    rows = [r for r in verify_priors() if r["case_id"] not in CASE_IDS]
    round1_ids = {r["case_id"] for r in read_json(ROUND1 / "generation_status.json") if r["generation_status"] == "success"}
    originals, last = {}, {}
    for row in rows:
        directory = ROUND1 if row["case_id"] in round1_ids else STEP4
        originals[row["case_id"]] = read_json(directory / "answers" / (row["case_id"] + ".json"))
        last[row["case_id"]] = [c for c in load_calls(directory / "generation_calls") if c["case_id"] == row["case_id"]][-1]
    seen = set()
    transport = httpx.MockTransport

    class ReplayRecorder:
        async def request(self, request):
            case_id = full.smoke.SCOPE.get()["case_id"]
            body, saved = json.loads(request.content), last[case_id]
            if case_id in seen or {k: v for k, v in body.items() if k != "instructions"} != {
                    k: v for k, v in saved["request"].items() if k != "instructions"}:
                raise ValueError("h02_offline_non_prompt_request_changed")
            seen.add(case_id)
            request.extensions["saved_response"] = saved["response"]

        async def response(self, response):
            await response.aread()

    async def respond(request):
        return httpx.Response(200, json=request.extensions["saved_response"])

    with (narrative_audit_scope() as audits, patch.object(httpx, "MockTransport", lambda unused: transport(respond)),
          redirect_stdout(io.StringIO())):
        results = await full.smoke.generate_all(rows, offline_settings(), ReplayRecorder(), offline=True)
    for result in results:
        old = originals[result["case_id"]]
        if any(result[key] != old[key] for key in ("answer", "citation_ids", "generation_status")):
            raise ValueError("h02_offline_success_delivery_changed")
    replay = {}
    for directory, source_name in ((STEP4, "fifty_cases.jsonl"), (ROUND1, "eighteen_cases.jsonl")):
        selected_ids = {r["case_id"] for r in rows if (r["case_id"] in round1_ids) == (directory == ROUND1)}
        score_rows = [r for r in full.gate0.read_jsonl(directory / source_name) if r["case_id"] in selected_ids]
        outcomes = {(r["case_id"], r["metric"]): r for r in [read_json(p) for p in (directory / "metric_outcomes").glob("*.json")]}
        replay.update(reporting.replay_scores(score_rows, load_calls(directory / "judge_calls"), outcomes))
    if len(results) != 38 or len(replay) != 38 or len(seen) != 38:
        raise ValueError("h02_offline_population_incomplete")
    write_json(OUTPUT / "offline_success38.json", {"status": "passed", "real_api_calls": 0,
        "credentials_loaded": False, "case_ids": [r["case_id"] for r in rows],
        "step4_successes": 32, "round1_successes": 6, "old_answers_and_citations_exact": 38,
        "non_prompt_requests_exact": 38, "metric_cells_exact": 76, "metric_replay": replay,
        "normalization_and_boundary_audits": audits,
        "limitation": "38 题未做真实 API 回归，新 prompt 下 faithfulness 不跌未验证"})


async def generate(rows, settings):
    from app.agent.foundation.subgraphs.worldline.narrative_normalization import narrative_audit_scope
    (OUTPUT / "audits").mkdir(exist_ok=True)
    for row in rows:
        path = OUTPUT / "answers" / (row["case_id"] + ".json")
        if path.exists():
            if not (OUTPUT / "audits" / path.name).exists():
                raise ValueError("h02_interrupted_case_audit_missing")
            continue
        with narrative_audit_scope() as events, patch.object(full, "terminal_detail", terminal_detail):
            await full.generate([row], settings)
        calls = [c for c in load_calls(OUTPUT / "generation_calls") if c["case_id"] == row["case_id"]]
        result = read_json(path)
        sent = json.loads(calls[-1]["request"]["input"][0]["content"]) if calls else None
        result["delivery_state"] = delivery_state(result, sent)
        write_json(path, result)
        write_json(OUTPUT / "audits" / path.name, {"case_id": row["case_id"], "events": events})


def main():
    configure_offline()
    logging.getLogger("app.llm.client").disabled = True
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "offline38", "replay12", "generate", "judge"))
    args = parser.parse_args()
    if args.mode in {"prepare", "offline38", "replay12"}:
        guard = Gate0Guard()
        sys.addaudithook(guard.audit)
        if args.mode == "prepare":
            prepare()
        elif args.mode == "offline38":
            inputs()
            asyncio.run(offline38())
        else:
            from evals.ragas_full_v5_saved_generation import replay
            write_json(OUTPUT / "generation_replay12.json", {"status": "passed", "real_api_calls": 0,
                "cases": asyncio.run(replay(OUTPUT, inputs()))})
        if guard.denied:
            raise ValueError("h02_offline_forbidden_operation")
        print(json.dumps({"mode": args.mode, "status": "passed", "real_api_calls": 0}))
        return
    sys.addaudithook(full.smoke.secret_guard)
    rows = inputs()
    if read_json(OUTPUT / "offline_success38.json")["status"] != "passed":
        raise ValueError("h02_offline_regression_not_passed")
    settings = full.smoke.load_approved_llm_settings()
    config = {"model": settings.generation_model, "base_url": str(settings.base_url),
        "timeout_seconds": settings.timeout_seconds, "max_output_tokens": settings.max_output_tokens,
        "temperature": 0, "secret_exported": False}
    if config != read_json(ROUND1 / "generation_config.json"):
        raise ValueError("h02_generation_config_changed")
    with patch.object(full, "OUTPUT", OUTPUT), full.smoke_scope(rows):
        if args.mode == "generate":
            write_json(OUTPUT / "generation_config.json", config)
            asyncio.run(generate(rows, settings))
        else:
            full.judge([read_json(OUTPUT / "answers" / (r["case_id"] + ".json")) for r in rows], settings)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        safe = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(("h02_", "step4_", "gate1_")) else type(exc).__name__
        print(json.dumps({"stage": "stopped", "safe_error": safe}), flush=True)
        sys.exit(1)
