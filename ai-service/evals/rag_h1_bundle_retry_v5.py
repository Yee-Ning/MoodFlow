"""West-approved one-time 005 expansion retry; preserve every original artifact.

Reuses the frozen prompts, request construction and validators without edits.
If the retry passes, generate its HyDE and then the fixed list's positions 6-30.
No retrieval command is provided; the six-focus review remains mandatory.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from evals import rag_h1_bundle_v5 as original
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = original.OUTPUT / "manual-retry-005-20261018"
SUCCESS_IDS = original.CASE_IDS[:4]
RETRY_ID = "rag-v0-005"
MAX_NEW_CALLS = 52


def retry_code_hashes():
    return {**original.code_hashes(), "evals/rag_h1_bundle_retry_v5.py": original.sha256(Path(__file__))}


def verify_originals():
    rows = original.load_inputs()
    partial = original.read_json(original.OUTPUT / "partial_manifest.json")
    if any(original.sha256(original.OUTPUT / name) != digest for name, digest in partial["artifact_hashes"].items()):
        raise original.H1Error("h1_retry_original_artifact_drift")
    if (partial["completed_case_ids"] != list(SUCCESS_IDS) or partial["failed_case_ids"] != [RETRY_ID]
            or partial["real_http_calls"] != 9 or partial["retrieval_allowed"]):
        raise original.H1Error("h1_retry_prior_state_mismatch")
    calls = [original.read_json(p) for p in sorted((original.OUTPUT / "calls").glob("*.json"))]
    records = {p.stem: original.read_json(p) for p in (original.OUTPUT / "query_understanding").glob("*.json")}
    if len(calls) != 9 or set(records) != {*SUCCESS_IDS, RETRY_ID}:
        raise original.H1Error("h1_retry_original_population_mismatch")
    for case_id in SUCCESS_IDS:
        if records[case_id]["status"] != "validated_pending_West_semantic_review":
            raise original.H1Error("h1_retry_prior_success_status_changed")
    failed = records[RETRY_ID]
    if (failed["error_code"] != "h1_added_registry_entity" or failed["attempts"] != {"expansion": 1, "hyde": 0}
            or failed["expansion_raw"] != calls[-1]["response"]["choices"][0]["message"]["content"]):
        raise original.H1Error("h1_retry_prior_failure_changed")
    return rows, records, calls


def allowed_scopes(rows):
    if tuple(r["case_id"] for r in rows) != original.CASE_IDS:
        raise original.H1Error("h1_retry_fixed_population_changed")
    return [(r["case_id"], stage) for r in rows[4:] for stage in ("expansion", "hyde")]


def prepare():
    rows, _, _ = verify_originals()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "status": "prepared_zero_api",
        "authorization": "West approved exactly one manual 005 expansion retry; if valid continue its HyDE and positions 6-30; stop on any failure.",
        "secret_authorization": "West separately approved this invocation's in-memory LLM_API_KEY and necessary connection fields.",
        "original_partial_manifest_sha256": original.sha256(original.OUTPUT / "partial_manifest.json"),
        "original_inputs_sha256": original.sha256(original.OUTPUT / "inputs.json"),
        "original_prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "code_sha256": retry_code_hashes(), "prior_completed_case_ids": list(SUCCESS_IDS),
        "ordered_allowed_scopes": allowed_scopes(rows), "maximum_new_http_calls": MAX_NEW_CALLS,
        "manual_retry": {"case_id": RETRY_ID, "stage": "expansion", "attempt": 2, "allowed_http_calls": 1},
        "max_retries": 0, "temperature": 0, "retrieval_allowed": False,
    })
    print(json.dumps({"status": "prepared_zero_api", "prior_successes_reused": 4,
                      "next_after_005": original.CASE_IDS[5], "max_new_http_calls": MAX_NEW_CALLS}), flush=True)


def load_prepared():
    rows, records, calls = verify_originals()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["original_partial_manifest_sha256"] != original.sha256(original.OUTPUT / "partial_manifest.json")
            or prep["original_inputs_sha256"] != original.sha256(original.OUTPUT / "inputs.json")
            or prep["original_prompts_sha256"] != original.sha256(original.OUTPUT / "prompts.json")
            or prep["code_sha256"] != retry_code_hashes()
            or prep["ordered_allowed_scopes"] != [list(scope) for scope in allowed_scopes(rows)]):
        raise original.H1Error("h1_retry_pre_call_drift")
    return rows, records, calls


class RetryRecorder(original.Recorder):
    def __init__(self, directory, rows):
        super().__init__(directory)
        self.allowed = allowed_scopes(rows)

    async def request(self, request):
        if self.scope is None or len(self.entries) >= MAX_NEW_CALLS:
            raise original.H1Error("h1_retry_unapproved_scope_or_budget")
        row, stage = self.scope
        if (row["case_id"], stage) != self.allowed[len(self.entries)]:
            raise original.H1Error("h1_retry_out_of_order_or_duplicate_request")
        await super().request(request)
        entry = self.entries[-1]
        entry.update(global_call_number=9 + entry["call_number"],
                     attempt=2 if (row["case_id"], stage) == (RETRY_ID, "expansion") else 1,
                     manual_retry=(row["case_id"], stage) == (RETRY_ID, "expansion"))
        self.save(entry)


async def process_cases(rows, prior_records, resolver, call_stage):
    """Sequential fail-closed state machine, independently testable without API."""
    results = [prior_records[case_id] for case_id in SUCCESS_IDS]
    directory = OUTPUT / "query_understanding"
    directory.mkdir(exist_ok=False)
    for row in rows[4:]:
        item = {**row, "status": "started", "attempts": {"expansion": 0, "hyde": 0}}
        if row["case_id"] == RETRY_ID:
            item["previous_failed_attempt"] = {
                "path": (original.OUTPUT / "query_understanding" / (RETRY_ID + ".json")).as_posix(),
                "sha256": original.sha256(original.OUTPUT / "query_understanding" / (RETRY_ID + ".json")),
            }
        path = directory / (row["case_id"] + ".json")
        original.write_json(path, item)
        try:
            for stage in ("expansion", "hyde"):
                item["active_stage"] = stage
                item["attempts"][stage] = 2 if row["case_id"] == RETRY_ID and stage == "expansion" else 1
                raw, call_number = await call_stage(row, stage)
                item[stage + "_raw"], item[stage + "_call_number"] = raw, call_number
                original.write_json(path, item)
                if stage == "expansion":
                    item["queries"], item["expansion_checks"] = original.validate_expansion(raw, row, resolver)
                else:
                    item["hyde"], item["hyde_checks"] = original.validate_hyde(raw, row, resolver)
                original.write_json(path, item)
            if item["hyde"].casefold() in {q.casefold() for q in item["queries"]}:
                raise original.H1Error("h1_hyde_duplicates_subquery")
            item["status"] = "validated_pending_West_semantic_review"
            original.write_json(path, item)
            results.append(item)
            print(json.dumps({"progress": f"{len(results)}/30", "case_id": row["case_id"]}, ensure_ascii=False), flush=True)
        except Exception as exc:
            item.update(status="failed_paused_no_retry", error_type=type(exc).__name__,
                        error_code=str(exc) if isinstance(exc, original.H1Error) else "provider_or_runtime_error_no_secret_text")
            original.write_json(path, item)
            raise
    return results


def write_comparison(prior_records):
    path = OUTPUT / "query_understanding" / (RETRY_ID + ".json")
    if not path.exists():
        return
    second = original.read_json(path)
    first = prior_records[RETRY_ID]
    original.write_json(OUTPUT / "005_attempt_comparison.json", {
        "question": first["completed_query"],
        "first": {"raw": first["expansion_raw"], "validation": first["error_code"],
                  "record_sha256": original.sha256(original.OUTPUT / "query_understanding" / (RETRY_ID + ".json"))},
        "second": {"raw": second.get("expansion_raw"),
                   "validation": "passed" if "queries" in second else second.get("error_code", "not_completed"),
                   "status": second["status"], "record_sha256": original.sha256(path)},
        "same_failure": "queries" not in second and second.get("error_code") == first["error_code"],
    })


def freeze(results, old_calls, new_calls):
    if tuple(r["case_id"] for r in results) != original.CASE_IDS or len(new_calls) != MAX_NEW_CALLS:
        raise original.H1Error("h1_retry_freeze_population_incomplete")
    original.write_json(OUTPUT / "frozen_query_understanding.json", results)
    references = {}
    for row in results:
        directory = original.OUTPUT if row["case_id"] in SUCCESS_IDS else OUTPUT
        path = directory / "query_understanding" / (row["case_id"] + ".json")
        references[row["case_id"]] = {"path": path.as_posix(), "sha256": original.sha256(path)}
    original.write_json(OUTPUT / "freeze_manifest.json", {
        "frozen_at": original.now(), "case_ids": list(original.CASE_IDS), "case_count": 30,
        "frozen_queries_sha256": original.sha256(OUTPUT / "frozen_query_understanding.json"),
        "original_inputs_sha256": original.sha256(original.OUTPUT / "inputs.json"),
        "original_prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "code_sha256": retry_code_hashes(), "per_case": references,
        "all_calls_usage": original.usage_summary([*old_calls, *new_calls]),
        "retrieval_allowed": False, "reason": "Await West confirmation of six complete focus chains.",
    })


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    from evals import rag_source_baseline as baseline
    rows, old_records, old_calls = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_retry_already_attempted_no_automatic_resume")
    key, timeout = original.approved_settings(allow_env_read)
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    recorder = RetryRecorder(OUTPUT / "calls", rows)
    state = {"status": "running_authorized_manual_retry", "started_at": original.now(),
             "max_retries": 0, "completed_cases": 4, "qdrant_calls": 0, "judge_calls": 0}
    original.write_json(OUTPUT / "generation_state.json", state)
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
                    return raw, 9 + len(recorder.entries)
                results = await process_cases(rows, old_records, resolver, call_stage)
        freeze(results, old_calls, recorder.entries)
        state["status"] = "frozen_pending_West_six_focus_review"
    except Exception as exc:
        state.update(status="failed_paused_no_retry", error_type=type(exc).__name__,
                     error_code=str(exc) if isinstance(exc, original.H1Error) else "provider_or_runtime_error_no_secret_text")
    finally:
        write_comparison(old_records)
        completed = [original.read_json(p) for p in (OUTPUT / "query_understanding").glob("*.json")]
        state.update(finished_at=original.now(),
                     completed_cases=4 + sum(r["status"] == "validated_pending_West_semantic_review" for r in completed),
                     new_usage=original.usage_summary(recorder.entries),
                     cumulative_usage=original.usage_summary([*old_calls, *recorder.entries]))
        try:
            verify_originals()
            state["original_artifacts_unchanged"] = True
        except Exception:
            state.update(status="failed_original_artifact_drift", original_artifacts_unchanged=False)
        original.write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "frozen_pending_West_six_focus_review" else 1


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
            raise original.H1Error("h1_retry_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
