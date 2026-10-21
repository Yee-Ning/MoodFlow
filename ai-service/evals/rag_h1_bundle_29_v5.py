"""West-approved H1 population revision: exclude 005, reuse four successes.

Freeze the matched historical baseline and 29 inputs before any new calls.
Uses the original prompts, request builder and validators without modification.
No retrieval command exists; West's six-focus review is still required.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
from unittest.mock import patch

from evals import rag_h1_bundle_v5 as original
from evals import rag_h1_bundle_retry_v5 as retry
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = original.OUTPUT / "run-29-exclude-005-20261018"
CASE_IDS = tuple(case_id for case_id in original.CASE_IDS if case_id != "rag-v0-005")
SUCCESS_IDS = original.CASE_IDS[:4]
MAX_NEW_CALLS = 50


def code_hashes():
    return {**retry.retry_code_hashes(), "evals/rag_h1_bundle_29_v5.py": original.sha256(Path(__file__))}


def verify_history():
    rows, records, calls = retry.verify_originals()
    completion = original.read_json(retry.OUTPUT / "completion.json")
    if (any(original.sha256(retry.OUTPUT / name) != digest for name, digest in completion["artifact_hashes"].items())
            or completion["code_sha256"] != retry.retry_code_hashes()):
        raise original.H1Error("h1_29_retry_history_drift")
    additional = [original.read_json(p) for p in sorted((retry.OUTPUT / "calls").glob("*.json"))]
    if (len(additional) != 1 or additional[0]["case_id"] != "rag-v0-005"
            or additional[0]["stage"] != "expansion" or not completion["same_failure"]):
        raise original.H1Error("h1_29_retry_history_scope_mismatch")
    return rows, records, [*calls, *additional]


def select_inputs(rows):
    if tuple(r["case_id"] for r in rows) != original.CASE_IDS:
        raise original.H1Error("h1_29_original_population_drift")
    selected = [r for r in rows if r["case_id"] != "rag-v0-005"]
    if tuple(r["case_id"] for r in selected) != CASE_IDS:
        raise original.H1Error("h1_29_selected_population_drift")
    return selected


def control29():
    from evals import rag_source_baseline as baseline
    rows = [r for r in baseline.read_jsonl(baseline.OUTPUT / "cases.jsonl") if r["case_id"] in CASE_IDS]
    if tuple(r["case_id"] for r in rows) != CASE_IDS or not all(r["scored"] and r["healthy"] for r in rows):
        raise original.H1Error("h1_29_baseline_population_or_health_mismatch")
    for row in rows:
        replay = baseline.score_case(row, row["rrf_top40"], row["selection"], row["rerank_top12"])
        if any(replay[key] != row[key] for key in replay):
            raise original.H1Error("h1_29_baseline_metric_replay_mismatch")
    summary = baseline.summarize(rows)
    old_summary = original.read_json(baseline.OUTPUT / "metrics.json")
    if any(summary["by_question_group"][group] != old_summary["by_question_group"][group]
           for group in ("timeline", "artist_overview")):
        raise original.H1Error("h1_29_focus_baseline_changed")
    return summary


def scopes(rows):
    if tuple(r["case_id"] for r in rows) != CASE_IDS:
        raise original.H1Error("h1_29_call_population_drift")
    return [(row["case_id"], stage) for row in rows[4:] for stage in ("expansion", "hyde")]


def prepare():
    all_rows, _, _ = verify_history()
    rows = select_inputs(all_rows)
    control = control29()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    original.write_json(OUTPUT / "inputs.json", rows)
    original.write_json(OUTPUT / "baseline29.json", {
        "source": "Step 2 saved cases/metrics only; no retrieval rerun",
        "case_ids": list(CASE_IDS), "excluded_case_id": "rag-v0-005", "metrics": control,
        "focus_groups_unchanged": True, "real_api_calls": 0, "qdrant_calls": 0,
    })
    original.write_json(OUTPUT / "exclusion.json", {
        "case_id": "rag-v0-005", "authorized_by": "West explicit 29-case decision in chat",
        "reason": "Systematic h1_added_registry_entity rejection on both unchanged-prompt expansion attempts; not selected by retrieval scores.",
        "original_30_case_ids": list(original.CASE_IDS), "selected_29_case_ids": list(CASE_IDS),
        "failed_records": [{"path": path.as_posix(), "sha256": original.sha256(path)} for path in (
            original.OUTPUT / "query_understanding/rag-v0-005.json",
            retry.OUTPUT / "query_understanding/rag-v0-005.json")],
        "hypothesis": {"timeline_recall_at_40_gte": 0.6,
                       "artist_overview_recall_at_40_gt": control["by_question_group"]["artist_overview"]["recall_at_40"]},
    })
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "status": "prepared_zero_api_fixed_29",
        "api_authorization": "West approved excluding 005 and continuing fixed positions 6-30; stop on any new failure.",
        "secret_authorization": "West separately authorized this invocation's in-memory key and necessary connection reads for remaining 25 cases.",
        "case_ids": list(CASE_IDS), "reused_successes": list(SUCCESS_IDS),
        "ordered_allowed_scopes": scopes(rows), "maximum_new_http_calls": MAX_NEW_CALLS,
        "max_retries": 0, "temperature": 0, "code_sha256": code_hashes(),
        "artifact_sha256": {name: original.sha256(OUTPUT / name)
                            for name in ("inputs.json", "baseline29.json", "exclusion.json")},
        "original_prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "original_partial_manifest_sha256": original.sha256(original.OUTPUT / "partial_manifest.json"),
        "retry_completion_sha256": original.sha256(retry.OUTPUT / "completion.json"),
        "retrieval_allowed": False,
    })
    print(json.dumps({"status": "prepared_zero_api_fixed_29", "case_count": 29,
                      "reuse": 4, "remaining": 25, "next_case": rows[4]["case_id"]}), flush=True)


def load_prepared():
    all_rows, records, calls = verify_history()
    rows = select_inputs(all_rows)
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (any(original.sha256(OUTPUT / name) != digest for name, digest in prep["artifact_sha256"].items())
            or prep["original_prompts_sha256"] != original.sha256(original.OUTPUT / "prompts.json")
            or prep["original_partial_manifest_sha256"] != original.sha256(original.OUTPUT / "partial_manifest.json")
            or prep["retry_completion_sha256"] != original.sha256(retry.OUTPUT / "completion.json")
            or prep["code_sha256"] != code_hashes()
            or original.read_json(OUTPUT / "inputs.json") != rows
            or original.read_json(OUTPUT / "baseline29.json")["metrics"] != control29()
            or prep["ordered_allowed_scopes"] != [list(scope) for scope in scopes(rows)]):
        raise original.H1Error("h1_29_pre_call_drift")
    return rows, records, calls


class Bundle29Recorder(original.Recorder):
    def __init__(self, directory, rows):
        super().__init__(directory)
        self.allowed = scopes(rows)

    async def request(self, request):
        if self.scope is None or len(self.entries) >= MAX_NEW_CALLS:
            raise original.H1Error("h1_29_unapproved_scope_or_budget")
        row, stage = self.scope
        if (row["case_id"], stage) != self.allowed[len(self.entries)]:
            raise original.H1Error("h1_29_out_of_order_excluded_or_duplicate_request")
        await super().request(request)
        self.entries[-1].update(global_call_number=10 + len(self.entries), attempt=1, selected_population=29)
        self.save(self.entries[-1])


async def process_cases(rows, prior_records, resolver, call_stage):
    # Reuse the verified fail-closed orchestration and exact original validators.
    # Suppress its historical /30 progress label; this entry reports /29 itself.
    with patch.object(retry, "OUTPUT", OUTPUT), redirect_stdout(io.StringIO()):
        return await retry.process_cases(rows, prior_records, resolver, call_stage)


def freeze(results, old_calls, new_calls):
    if (tuple(r["case_id"] for r in results) != CASE_IDS or len(new_calls) != MAX_NEW_CALLS
            or any(r["status"] != "validated_pending_West_semantic_review" for r in results)):
        raise original.H1Error("h1_29_freeze_population_incomplete")
    original.write_json(OUTPUT / "frozen_query_understanding.json", results)
    references = {}
    for row in results:
        directory = original.OUTPUT if row["case_id"] in SUCCESS_IDS else OUTPUT
        path = directory / "query_understanding" / (row["case_id"] + ".json")
        references[row["case_id"]] = {"path": path.as_posix(), "sha256": original.sha256(path)}
    original.write_json(OUTPUT / "freeze_manifest.json", {
        "frozen_at": original.now(), "case_count": 29, "case_ids": list(CASE_IDS),
        "frozen_queries_sha256": original.sha256(OUTPUT / "frozen_query_understanding.json"),
        "inputs_sha256": original.sha256(OUTPUT / "inputs.json"),
        "baseline29_sha256": original.sha256(OUTPUT / "baseline29.json"),
        "original_prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "code_sha256": code_hashes(), "per_case": references,
        "all_calls_usage_including_two_excluded_005_failures": original.usage_summary([*old_calls, *new_calls]),
        "retrieval_allowed": False, "reason": "Await West confirmation of six complete focus chains.",
    })


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    from evals import rag_source_baseline as baseline
    rows, old_records, old_calls = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_29_already_attempted_no_automatic_resume")
    key, timeout = original.approved_settings(allow_env_read)
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    recorder = Bundle29Recorder(OUTPUT / "calls", rows)
    state = {"status": "running_fixed_29", "started_at": original.now(), "case_count": 29,
             "max_retries": 0, "completed_cases": 4, "qdrant_calls": 0, "judge_calls": 0}
    original.write_json(OUTPUT / "generation_state.json", state)
    progress_stream = sys.stdout
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                    event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=original.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                async def call_stage(row, stage):
                    recorder.scope = (row, stage)
                    response = await client.chat.completions.create(**original.request_body(row, stage))
                    if response.choices[0].finish_reason != "stop":
                        raise original.H1Error("h1_response_not_finished")
                    raw = response.choices[0].message.content
                    if not isinstance(raw, str):
                        raise original.H1Error("h1_missing_response_text")
                    print(json.dumps({"case_id": row["case_id"], "stage_http_completed": stage,
                                      "new_http_calls": len(recorder.entries)}, ensure_ascii=False),
                          file=progress_stream, flush=True)
                    return raw, 10 + len(recorder.entries)
                results = await process_cases(rows, old_records, resolver, call_stage)
        freeze(results, old_calls, recorder.entries)
        state["status"] = "frozen_29_pending_West_six_focus_review"
    except Exception as exc:
        state.update(status="failed_paused_no_retry", error_type=type(exc).__name__,
                     error_code=str(exc) if isinstance(exc, original.H1Error) else "provider_or_runtime_error_no_secret_text")
    finally:
        completed = [original.read_json(p) for p in (OUTPUT / "query_understanding").glob("*.json")]
        state.update(finished_at=original.now(),
                     completed_cases=4 + sum(r["status"] == "validated_pending_West_semantic_review" for r in completed),
                     new_usage=original.usage_summary(recorder.entries),
                     cumulative_usage=original.usage_summary([*old_calls, *recorder.entries]))
        try:
            verify_history()
            state["historical_artifacts_unchanged"] = True
        except Exception:
            state.update(status="failed_history_drift", historical_artifacts_unchanged=False)
        original.write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "frozen_29_pending_West_six_focus_review" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "generate"))
    parser.add_argument("--authorized-env-read", action="store_true")
    args = parser.parse_args()
    configure_offline()
    if args.command == "prepare":
        guard = Gate0Guard()
        sys.addaudithook(guard.audit)
        prepare()
        if guard.denied:
            raise original.H1Error("h1_29_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
