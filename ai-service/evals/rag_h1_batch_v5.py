"""H1: collect case failures, stop only on three consecutive identical codes.

Existing 007 output and 008 failure are reused without model calls. The 29-case
population, prompts and corrected title validator remain fixed. No retrieval or
automatic failure disposition is implemented here.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from evals import rag_h1_bundle_v5 as original
from evals import rag_h1_title_resume_v5 as prior
from evals.rag_h1_title_format_v5 import validate_text_constraints
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = prior.OUTPUT / "batch-collect-20261018"
CASE_IDS = prior.prior.CASE_IDS
REUSED_IDS = CASE_IDS[:6]
MAX_NEW_CALLS = 46


def code_hashes():
    return {**prior.code_hashes(), "evals/rag_h1_batch_v5.py": original.sha256(Path(__file__))}


def verify_history():
    rows, records, calls, _ = prior.load_prepared()
    completion = original.read_json(prior.OUTPUT / "completion.json")
    if (any(original.sha256(prior.OUTPUT / name) != digest for name, digest in completion["artifact_hashes"].items())
            or completion["code_sha256"] != prior.code_hashes()):
        raise original.H1Error("h1_batch_history_drift")
    latest_calls = [original.read_json(p) for p in sorted((prior.OUTPUT / "calls").glob("*.json"))]
    if [(c["case_id"], c["stage"]) for c in latest_calls] != [
        ("rag-v0-007", "hyde"), ("rag-v0-008", "expansion")]:
        raise original.H1Error("h1_batch_prior_call_scope_mismatch")
    selected = {case_id: records[case_id] for case_id in original.CASE_IDS[:4]}
    selected.update({case_id: original.read_json(prior.OUTPUT / "query_understanding" / (case_id + ".json"))
                     for case_id in ("rag-v0-007", "rag-v0-008")})
    if (tuple(selected) != REUSED_IDS or selected["rag-v0-007"]["status"] != "validated_pending_West_semantic_review"
            or selected["rag-v0-008"]["error_code"] != "h1_added_registry_entity"):
        raise original.H1Error("h1_batch_prior_case_state_mismatch")
    return rows, selected, [*calls, *latest_calls]


def scopes(rows):
    if tuple(r["case_id"] for r in rows) != CASE_IDS:
        raise original.H1Error("h1_batch_fixed_population_drift")
    return [(r["case_id"], stage) for r in rows[6:] for stage in ("expansion", "hyde")]


def error_code(exc):
    if isinstance(exc, original.H1Error):
        return str(exc)
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return f"h1_provider_http_{status}"
    if type(exc).__name__ in {"APIConnectionError", "APITimeoutError", "ConnectError", "ReadTimeout", "ConnectTimeout"}:
        return "h1_provider_" + type(exc).__name__
    return "h1_runtime_" + type(exc).__name__


def diagnose(item, resolver):
    code = item["error_code"]
    raw = item.get(item.get("active_stage", "expansion") + "_raw")
    diagnosis = {"code": code, "preliminary": True, "disposition": "West_pending",
                 "raw_text_available_in_case": raw is not None,
                 "note": "Rule-level diagnosis only; no LLM judge or automatic repair."}
    if code.startswith("h1_provider_"):
        return {**diagnosis, "category": "provider_failure", "note": "HTTP/transport failure; no model constraint verdict."}
    if code.startswith("h1_runtime_"):
        return {**diagnosis, "category": "runtime_or_checker_problem_pending_review"}
    texts = []
    if item.get("active_stage") == "expansion" and isinstance(raw, str):
        try:
            texts = [original.literal(q) for q in original.ExpansionOutput.model_validate_json(raw).queries]
        except Exception:
            pass
    elif isinstance(raw, str):
        texts = [" ".join(raw.split())]
    original_ids = {e["knowledge_entity_id"] for e in item["entities"] if e["knowledge_entity_id"]}
    evidence = []
    for text in texts:
        try:
            validate_text_constraints(text, item, hyde=item.get("active_stage") == "hyde", entity_resolver=resolver)
            result = "passed"
        except original.H1Error as exc:
            result = str(exc)
        added = [{"id": e.knowledge_entity_id, "name": e.canonical_name, "type": e.entity_type.value}
                 for e in resolver.resolve(text, catalog_references=[])
                 if e.knowledge_entity_id and e.knowledge_entity_id not in original_ids
                 and e.entity_type and e.entity_type.value in {"artist", "song", "album", "person"}]
        evidence.append({"text": text, "validator_result": result, "added_entities": added})
    diagnosis["subqueries"] = evidence
    if code == "h1_title_constraint_drift":
        import re
        titles = re.findall(r"《([^》]+)》", item["completed_query"])
        if evidence and titles and all(all(title in e["text"] for title in titles) for e in evidence):
            return {**diagnosis, "category": "checker_problem_suspected",
                    "note": "Title text retained; punctuation/entity resolution requires human review; rejection remains in effect."}
    if code == "h1_added_registry_entity":
        return {**diagnosis, "category": "model_constraint_violation_suspected",
                "note": "Registry detects concrete entities absent from the original query; alias/context false positives still require review."}
    if code in {"h1_expansion_json_schema_invalid", "h1_expansion_blank_or_duplicate", "h1_response_not_finished",
                "h1_missing_response_text", "h1_hyde_blank_long_or_fenced", "h1_hyde_duplicates_subquery"}:
        return {**diagnosis, "category": "model_output_contract_failure"}
    return {**diagnosis, "category": "model_constraint_or_checker_problem_pending_review"}


def prepared_records(rows, records, resolver):
    result = []
    for row in rows[:6]:
        item = deepcopy(records[row["case_id"]])
        directory = original.OUTPUT if row["case_id"] in original.CASE_IDS[:4] else prior.OUTPUT
        source = directory / "query_understanding" / (row["case_id"] + ".json")
        item["reused_source"] = {"path": source.as_posix(), "sha256": original.sha256(source)}
        if item["status"] == "validated_pending_West_semantic_review":
            item["batch_status"] = "passed_rules_pending_semantic_review"
        else:
            item["batch_status"] = "failed_pending_disposition"
            item["diagnosis"] = diagnose(item, resolver)
        result.append(item)
    return result


def prepare():
    from evals import rag_source_baseline as baseline
    rows, records, _ = verify_history()
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    reused = prepared_records(rows, records, resolver)
    with patch.object(original, "validate_text_constraints", validate_text_constraints):
        for item in reused:
            if item["batch_status"].startswith("passed_"):
                if (original.validate_expansion(item["expansion_raw"], item, resolver)[0] != item["queries"]
                        or original.validate_hyde(item["hyde_raw"], item, resolver)[0] != item["hyde"]):
                    raise original.H1Error("h1_batch_reused_output_drift")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    for name in ("inputs.json", "baseline29.json"):
        (OUTPUT / name).write_bytes((prior.OUTPUT / name).read_bytes())
    original.write_json(OUTPUT / "reused_cases.json", reused)
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "status": "prepared_zero_api",
        "api_authorization": "West approved case-failure collection and continuation, no retries, stop on three consecutive identical case failure codes.",
        "secret_authorization": "West separately approved this invocation's in-memory key/necessary connection reads for remaining 23 cases.",
        "case_ids": list(CASE_IDS), "reused_case_ids": list(REUSED_IDS),
        "max_new_http_calls": MAX_NEW_CALLS, "remaining_cases": 23, "max_retries": 0, "temperature": 0,
        "allowed_scopes": scopes(rows), "initial_failure_streak": {"code": "h1_added_registry_entity", "case_ids": ["rag-v0-008"]},
        "case_failure_policy": "Record failed stage; do not call HyDE after invalid expansion; move to next case without excluding or replacing it.",
        "safety_valve": "Pause after 3 consecutive cases with exactly the same primary failure code; successful case resets the streak.",
        "code_sha256": code_hashes(), "prior_completion_sha256": original.sha256(prior.OUTPUT / "completion.json"),
        "original_prompts_sha256": original.sha256(original.OUTPUT / "prompts.json"),
        "artifact_sha256": {name: original.sha256(OUTPUT / name) for name in ("inputs.json", "baseline29.json", "reused_cases.json")},
        "retrieval_allowed": False, "freeze_allowed": False,
    })
    print(json.dumps({"prepared": True, "case_count": 29, "reused_passed": 5,
                      "reused_failed": 1, "next_case": rows[6]["case_id"], "new_cases": 23}), flush=True)


def load_prepared():
    rows, _, calls = verify_history()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["code_sha256"] != code_hashes()
            or prep["prior_completion_sha256"] != original.sha256(prior.OUTPUT / "completion.json")
            or prep["original_prompts_sha256"] != original.sha256(original.OUTPUT / "prompts.json")
            or any(original.sha256(OUTPUT / name) != digest for name, digest in prep["artifact_sha256"].items())
            or original.read_json(OUTPUT / "inputs.json") != rows
            or prep["allowed_scopes"] != [list(scope) for scope in scopes(rows)]):
        raise original.H1Error("h1_batch_pre_call_drift")
    return rows, original.read_json(OUTPUT / "reused_cases.json"), calls


class Recorder(original.Recorder):
    def __init__(self, directory, rows):
        super().__init__(directory)
        self.allowed = set(scopes(rows))

    async def request(self, request):
        if self.scope is None or len(self.entries) >= MAX_NEW_CALLS:
            raise original.H1Error("h1_batch_unapproved_scope_or_budget")
        row, stage = self.scope
        if (row["case_id"], stage) not in self.allowed:
            raise original.H1Error("h1_batch_reused_or_excluded_request")
        await super().request(request)
        self.entries[-1].update(global_call_number=13 + len(self.entries), attempt=1)
        self.save(self.entries[-1])


def write_tables(rows, results, *, status, streak):
    passed = [r for r in results if r["batch_status"].startswith("passed_")]
    failed = [r for r in results if r["batch_status"] == "failed_pending_disposition"]
    visited = {r["case_id"] for r in results}
    pending = [r["case_id"] for r in rows if r["case_id"] not in visited]
    summary = {"status": status, "fixed_case_count": 29, "processed_cases": len(results),
               "passed_cases": len(passed), "failed_cases": len(failed),
               "passed_case_ids": [r["case_id"] for r in passed], "failed_case_ids": [r["case_id"] for r in failed],
               "failure_codes": dict(Counter(r["error_code"] for r in failed)),
               "not_attempted_case_ids": pending, "failure_streak": streak,
               "freeze_allowed": False, "retrieval_allowed": False,
               "semantic_review": "Pending, including 007 HyDE direct-answer concern; rule passes are not semantic approval."}
    original.write_json(OUTPUT / "batch_results.json", results)
    original.write_json(OUTPUT / "passed_cases.json", passed)
    original.write_json(OUTPUT / "failed_cases.json", failed)
    original.write_json(OUTPUT / "batch_summary.json", summary)
    return summary


async def process_cases(rows, reused, resolver, call_stage):
    results = deepcopy(reused)
    streak = {"code": reused[-1]["error_code"], "case_ids": [reused[-1]["case_id"]]}
    (OUTPUT / "query_understanding").mkdir(exist_ok=False)
    for row in rows[6:]:
        item = {**row, "batch_status": "started", "attempts": {"expansion": 0, "hyde": 0}}
        path = OUTPUT / "query_understanding" / (row["case_id"] + ".json")
        original.write_json(path, item)
        try:
            for stage in ("expansion", "hyde"):
                item["active_stage"] = stage
                item["attempts"][stage] += 1
                raw, number = await call_stage(row, stage)
                item[stage + "_raw"], item[stage + "_call_number"] = raw, number
                original.write_json(path, item)
                with patch.object(original, "validate_text_constraints", validate_text_constraints):
                    if stage == "expansion":
                        item["queries"], item["expansion_checks"] = original.validate_expansion(raw, row, resolver)
                    else:
                        item["hyde"], item["hyde_checks"] = original.validate_hyde(raw, row, resolver)
                original.write_json(path, item)
            if item["hyde"].casefold() in {q.casefold() for q in item["queries"]}:
                raise original.H1Error("h1_hyde_duplicates_subquery")
            item.update(status="validated_pending_West_semantic_review", batch_status="passed_rules_pending_semantic_review")
            streak = {"code": None, "case_ids": []}
        except Exception as exc:
            item.update(status="failed_no_retry", batch_status="failed_pending_disposition",
                        error_type=type(exc).__name__, error_code=error_code(exc))
            item["diagnosis"] = diagnose(item, resolver)
            code = item["error_code"]
            streak = {"code": code, "case_ids": [*streak["case_ids"], row["case_id"]]
                      if streak["code"] == code else [row["case_id"]]}
        original.write_json(path, item)
        results.append(item)
        tripped = len(streak["case_ids"]) >= 3
        summary = write_tables(rows, results,
                               status="safety_valve_paused" if tripped else "collecting", streak=streak)
        print(json.dumps({"progress": f"{len(results)}/29", "case_id": row["case_id"],
                          "outcome": item["batch_status"], "code": item.get("error_code"),
                          "same_code_streak": len(streak["case_ids"])}, ensure_ascii=False), flush=True)
        if tripped:
            return results, summary
    return results, write_tables(rows, results, status="completed_batch_pending_West_dispositions", streak=streak)


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    from evals import rag_source_baseline as baseline
    rows, reused, old_calls = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_batch_already_attempted_no_automatic_resume")
    _, _, _, _, binding = baseline.preflight()
    _, resolver = original.registry_components(binding)
    key, timeout = original.approved_settings(allow_env_read)
    recorder = Recorder(OUTPUT / "calls", rows)
    state = {"status": "running_batch_collection", "started_at": original.now(), "qdrant_calls": 0, "judge_calls": 0,
             "max_retries": 0, "fixed_case_count": 29}
    original.write_json(OUTPUT / "generation_state.json", state)
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                    event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=original.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                async def call_stage(row, stage):
                    recorder.scope = (row, stage)
                    response = await client.chat.completions.create(**original.request_body(row, stage))
                    if not response.choices or response.choices[0].finish_reason != "stop":
                        raise original.H1Error("h1_response_not_finished")
                    raw = response.choices[0].message.content
                    if not isinstance(raw, str):
                        raise original.H1Error("h1_missing_response_text")
                    return raw, 13 + len(recorder.entries)
                _, summary = await process_cases(rows, reused, resolver, call_stage)
        state.update(summary)
    except Exception as exc:
        state.update(status="fatal_runtime_paused", error_type=type(exc).__name__, error_code=error_code(exc))
    finally:
        state.update(finished_at=original.now(), new_usage=original.usage_summary(recorder.entries),
                     cumulative_usage=original.usage_summary([*old_calls, *recorder.entries]))
        try:
            verify_history()
            state["historical_artifacts_unchanged"] = True
        except Exception:
            state.update(status="fatal_history_drift", historical_artifacts_unchanged=False)
        original.write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "completed_batch_pending_West_dispositions" else 1


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
            raise original.H1Error("h1_batch_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
