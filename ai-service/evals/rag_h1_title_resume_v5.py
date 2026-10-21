"""Approved title-only fix; saved 007 expansion replay -> HyDE -> remaining 24.

No old artifacts or validators are overwritten. No retrieval entry exists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from unittest.mock import patch

from evals import rag_h1_bundle_29_v5 as prior
from evals import rag_h1_bundle_v5 as original
from evals.rag_h1_title_format_v5 import validate_text_constraints
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = prior.OUTPUT / "title-format-fix-20261018"
MAX_NEW_CALLS = 49
HISTORICAL_CODE_HASHES = prior.code_hashes


def code_hashes():
    return {**HISTORICAL_CODE_HASHES(), **{name: original.sha256(original.AI_ROOT / name) for name in (
        "evals/rag_h1_title_format_v5.py", "evals/rag_h1_title_resume_v5.py")}}


def verify_history():
    rows, records, calls = prior.load_prepared()
    completion = original.read_json(prior.OUTPUT / "completion.json")
    if (any(original.sha256(prior.OUTPUT / name) != digest for name, digest in completion["artifact_hashes"].items())
            or completion["code_sha256"] != prior.code_hashes()):
        raise original.H1Error("h1_title_prior_history_drift")
    saved_calls = [original.read_json(p) for p in sorted((prior.OUTPUT / "calls").glob("*.json"))]
    saved = original.read_json(prior.OUTPUT / "query_understanding/rag-v0-007.json")
    if (len(saved_calls) != 1 or saved["error_code"] != "h1_title_constraint_drift"
            or saved["attempts"] != {"expansion": 1, "hyde": 0}
            or saved_calls[0]["case_id"] != "rag-v0-007" or saved_calls[0]["stage"] != "expansion"
            or saved["expansion_raw"] != saved_calls[0]["response"]["choices"][0]["message"]["content"]):
        raise original.H1Error("h1_title_saved_007_mismatch")
    return rows, records, [*calls, *saved_calls], saved


def scopes(rows):
    if tuple(r["case_id"] for r in rows) != prior.CASE_IDS:
        raise original.H1Error("h1_title_population_drift")
    return [("rag-v0-007", "hyde"), *[(r["case_id"], stage) for r in rows[5:] for stage in ("expansion", "hyde")]]


def replay(saved, row, resolver):
    with patch.object(original, "validate_text_constraints", validate_text_constraints):
        return original.validate_expansion(saved["expansion_raw"], row, resolver)


def prepare():
    from evals import rag_source_baseline as baseline
    rows, old_records, _, saved = verify_history()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    queries, checks = replay(saved, rows[4], resolver)
    revalidated = []
    with patch.object(original, "validate_text_constraints", validate_text_constraints):
        for case_id in prior.SUCCESS_IDS:
            item = old_records[case_id]
            row = next(r for r in rows if r["case_id"] == case_id)
            if (original.validate_expansion(item["expansion_raw"], row, resolver)[0] != item["queries"]
                    or original.validate_hyde(item["hyde_raw"], row, resolver)[0] != item["hyde"]):
                raise original.H1Error("h1_title_old_success_changed")
            revalidated.append(case_id)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    for name in ("inputs.json", "baseline29.json"):
        (OUTPUT / name).write_bytes((prior.OUTPUT / name).read_bytes())
    original.write_json(OUTPUT / "007_offline_revalidation.json", {
        "status": "passed", "case_id": "rag-v0-007", "queries": queries, "checks": checks,
        "saved_raw_sha256": original.text_hash(saved["expansion_raw"]),
        "source_record_sha256": original.sha256(prior.OUTPUT / "query_understanding/rag-v0-007.json"),
        "old_four_successes_revalidated_unchanged": revalidated, "real_api_calls": 0,
    })
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "status": "prepared_saved_007_revalidated_zero_api",
        "api_authorization": "West approved title-format correction, saved 007 offline replay, 007 HyDE and remaining 24 fixed cases; stop on failure.",
        "secret_authorization": "West separately authorized this invocation's in-memory LLM_API_KEY/necessary connection reads.",
        "case_ids": list(prior.CASE_IDS), "ordered_allowed_scopes": scopes(rows),
        "maximum_new_http_calls": MAX_NEW_CALLS, "max_retries": 0, "temperature": 0,
        "code_sha256": code_hashes(),
        "prior_completion_sha256": original.sha256(prior.OUTPUT / "completion.json"),
        "inputs29_sha256": original.sha256(prior.OUTPUT / "inputs.json"),
        "baseline29_sha256": original.sha256(prior.OUTPUT / "baseline29.json"),
        "prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "replay_sha256": original.sha256(OUTPUT / "007_offline_revalidation.json"),
        "retrieval_allowed": False,
    })
    print(json.dumps({"status": "prepared_saved_007_revalidated_zero_api", "queries": queries,
                      "old_four_unchanged": True, "next_http": "007 HyDE", "max_new_http_calls": 49}, ensure_ascii=False), flush=True)


def load_prepared():
    rows, records, calls, saved = verify_history()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["code_sha256"] != code_hashes()
            or prep["prior_completion_sha256"] != original.sha256(prior.OUTPUT / "completion.json")
            or prep["inputs29_sha256"] != original.sha256(prior.OUTPUT / "inputs.json")
            or prep["inputs29_sha256"] != original.sha256(OUTPUT / "inputs.json")
            or prep["baseline29_sha256"] != original.sha256(prior.OUTPUT / "baseline29.json")
            or prep["baseline29_sha256"] != original.sha256(OUTPUT / "baseline29.json")
            or prep["prompts_sha256"] != original.sha256(original.OUTPUT / "prompts.json")
            or prep["replay_sha256"] != original.sha256(OUTPUT / "007_offline_revalidation.json")
            or prep["ordered_allowed_scopes"] != [list(scope) for scope in scopes(rows)]):
        raise original.H1Error("h1_title_pre_call_drift")
    return rows, records, calls, saved


class Recorder(original.Recorder):
    def __init__(self, directory, rows):
        super().__init__(directory)
        self.allowed = scopes(rows)

    async def request(self, request):
        if self.scope is None or len(self.entries) >= MAX_NEW_CALLS:
            raise original.H1Error("h1_title_unapproved_scope_or_budget")
        row, stage = self.scope
        if (row["case_id"], stage) != self.allowed[len(self.entries)]:
            raise original.H1Error("h1_title_out_of_order_repeat_or_excluded_request")
        await super().request(request)
        self.entries[-1].update(global_call_number=11 + len(self.entries), attempt=1)
        self.save(self.entries[-1])


async def process_cases(rows, records, saved, resolver, call_stage):
    async def replay_or_call(row, stage):
        if (row["case_id"], stage) == ("rag-v0-007", "expansion"):
            return saved["expansion_raw"], 11
        return await call_stage(row, stage)
    with patch.object(prior, "OUTPUT", OUTPUT), patch.object(original, "validate_text_constraints", validate_text_constraints):
        return await prior.process_cases(rows, records, resolver, replay_or_call)


def freeze(results, old_calls, new_calls):
    with (patch.object(prior, "OUTPUT", OUTPUT), patch.object(prior, "MAX_NEW_CALLS", MAX_NEW_CALLS),
          patch.object(prior, "code_hashes", code_hashes)):
        prior.freeze(results, old_calls, new_calls)


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    from evals import rag_source_baseline as baseline
    rows, records, old_calls, saved = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_title_already_attempted_no_automatic_resume")
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    replay(saved, rows[4], resolver)
    key, timeout = original.approved_settings(allow_env_read)
    recorder = Recorder(OUTPUT / "calls", rows)
    state = {"status": "running_title_format_fixed_29", "started_at": original.now(),
             "completed_cases": 4, "case_count": 29, "max_retries": 0, "qdrant_calls": 0, "judge_calls": 0}
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
                                      "new_http_calls": len(recorder.entries)}, ensure_ascii=False), file=progress_stream, flush=True)
                    return raw, 11 + len(recorder.entries)
                results = await process_cases(rows, records, saved, resolver, call_stage)
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
            raise original.H1Error("h1_title_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
