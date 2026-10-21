"""Globally apply West's object-set coverage clarification; replay 049 once.

Historical files/checker hashes remain immutable. Only 049's first HyDE may
call the provider, within the already authorized eight-call batch budget.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from evals import rag_h1_bundle_v5 as original
from evals import rag_h1_disposition_checks_v5 as checks
from evals import rag_h1_disposition_v5 as prior
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = prior.OUTPUT / "global-set-coverage-20261018"
ALL_IDS = frozenset(original.CASE_IDS)


def validate_expansion(raw, row, entity_resolver=None):
    # Apply precisely the same checked coverage algorithm to every approved
    # input; no case-specific object-preservation exceptions remain.
    with patch.object(checks, "SET_COVERAGE_IDS", ALL_IDS):
        return checks.validate_expansion(raw, row, entity_resolver)


def code_hashes():
    return {**prior.code_hashes(), "evals/rag_h1_union_v5.py": original.sha256(Path(__file__))}


def history():
    _, _, old_calls = prior.load_prepared()
    calls = [*old_calls, *[original.read_json(p) for p in sorted((prior.OUTPUT / "calls").glob("*.json"))]]
    outcomes = original.read_json(prior.OUTPUT / "dispositions.json")
    if (len(calls) != 59 or len(outcomes) != 29
            or next(o for o in outcomes if o["case_id"] == "rag-v0-054")["disposition"] != "excluded"
            or original.read_json(prior.OUTPUT / "054_decision.json")["decision"] != "exclude"):
        raise original.H1Error("h1_union_history_scope_drift")
    row = next(o["record"] for o in outcomes if o["case_id"] == "rag-v0-049")
    response = next(c["response"]["choices"][0]["message"]["content"] for c in calls
                    if c.get("global_call_number") == 58)
    if row["expansion_raw"] != response or row.get("hyde_raw"):
        raise original.H1Error("h1_union_049_saved_response_drift")
    return outcomes, calls


def prepare():
    outcomes, _ = history()
    resolver = prior.resolver()
    replays = []
    for outcome in outcomes:
        if outcome["disposition"] == "passed" or outcome["case_id"] == "rag-v0-049":
            row = outcome["record"]
            queries, validation = validate_expansion(row["expansion_raw"], row, resolver)
            if row.get("queries") and row["queries"] != queries:
                raise original.H1Error("h1_union_saved_query_drift")
            if outcome["disposition"] == "passed" and checks.validate_hyde(row["hyde_raw"], row, resolver)[0] != row["hyde"]:
                raise original.H1Error("h1_union_saved_hyde_drift")
            replays.append({"case_id": row["case_id"], "queries": queries, "checks": validation,
                            "original_raw_sha256": original.text_hash(row["expansion_raw"]),
                            "hyde_needed": row["case_id"] == "rag-v0-049", "api_calls": 0})
    if len(replays) != 25:
        raise original.H1Error("h1_union_replay_count_drift")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    original.write_json(OUTPUT / "coverage_decision.json", {
        "recorded_at": original.now(), "authorized_by": "West explicit clarification in this conversation",
        "verbatim_reply": "统一适用集合覆盖（建议）", "all_original_case_ids": list(original.CASE_IDS),
        "changed": "Only explicit object coverage is over the three-query set for every case.",
        "unchanged": "Years, people/new entities, negatives, document cues, prompts, requests and HyDE checks.",
        "049": "Replay saved HTTP 58 expansion, then first HyDE only. No new expansion call.",
        "054": "Remain explicitly excluded after the source-loss diagnosis; no resolver fix or extra call.",
        "shared_batch_http_budget": 8, "already_spent": 6, "maximum_additional_http": 1,
    })
    original.write_json(OUTPUT / "offline_revalidation.json", {"api_calls": 0, "records": replays})
    original.write_json(OUTPUT / "inputs.json", [o["record"] for o in outcomes
                                                 if o["disposition"] == "passed" or o["case_id"] == "rag-v0-049"])
    parent_files = {p.name: original.sha256(p) for p in sorted(prior.OUTPUT.iterdir()) if p.is_file()}
    parent_files.update({p.relative_to(prior.OUTPUT).as_posix(): original.sha256(p)
                         for directory in (prior.OUTPUT / "calls", prior.OUTPUT / "query_understanding")
                         for p in sorted(directory.glob("*.json"))})
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "code_sha256": code_hashes(), "prior_artifact_sha256": parent_files,
        "artifact_sha256": {n: original.sha256(OUTPUT / n) for n in ("inputs.json", "coverage_decision.json", "offline_revalidation.json")},
        "allowed_scope": ["rag-v0-049", "hyde"], "temperature": 0, "max_retries": 0,
        "max_new_http": 1, "shared_batch_http_spent": 6, "shared_batch_max_http": 8,
        "secret_authorization": "Same current-turn West authorization for at most 8 HTTP including first HyDE for 049; no expanded secret scope.",
        "retrieval_allowed": False,
    })
    print(json.dumps({"zero_api_replays": 25, "next_call": "049 first HyDE only", "max_http": 1}), flush=True)


def load_prepared():
    outcomes, calls = history()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["code_sha256"] != code_hashes()
            or any(original.sha256(prior.OUTPUT / n) != digest for n, digest in prep["prior_artifact_sha256"].items())
            or any(original.sha256(OUTPUT / n) != digest for n, digest in prep["artifact_sha256"].items())):
        raise original.H1Error("h1_union_pre_call_drift")
    return outcomes, calls


class Recorder(original.Recorder):
    async def request(self, request):
        if (self.scope is None or self.scope[0]["case_id"] != "rag-v0-049" or self.scope[1] != "hyde"
                or self.entries):
            raise original.H1Error("h1_union_only_049_first_hyde_once")
        await super().request(request)
        self.entries[-1].update(global_call_number=60, attempt=1)
        self.save(self.entries[-1])


def apply_response(outcomes, raw, resolver):
    outcomes = deepcopy(outcomes)
    for item in outcomes:
        if item["disposition"] == "passed" or item["case_id"] == "rag-v0-049":
            row = item["record"]
            row["queries"], row["expansion_checks"] = validate_expansion(row["expansion_raw"], row, resolver)
    item = next(o for o in outcomes if o["case_id"] == "rag-v0-049")
    row = item["record"]
    row.update(hyde_raw=raw, hyde_call_number=60, attempts={"expansion": 2, "hyde": 1})
    item.update(active_stage="hyde", prior_scope_verdict="Excluded by the previous case-limited checker; superseded by West's global coverage clarification.")
    try:
        row["hyde"], row["hyde_checks"] = checks.validate_hyde(raw, row, resolver)
        if row["hyde"].casefold() in {q.casefold() for q in row["queries"]}:
            raise original.H1Error("h1_hyde_duplicates_subquery")
        item.update(disposition="passed", reason="Saved second expansion passes globally consistent set coverage; first HyDE passed, no expansion recalled")
        item.pop("error_code", None)
        row.pop("error_code", None)
        row.pop("error_type", None)
        row.update(status="validated_pending_final_retrieval_approval", batch_status="passed_rules_pending_final_retrieval_approval")
    except Exception as exc:
        item.update(disposition="pending_West", error_code=prior.batch.error_code(exc), reason="First HyDE failed; no automatic retry or exclusion")
    return outcomes


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    outcomes, old_calls = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_union_already_attempted_no_resume")
    resolver = prior.resolver()
    row = next(o["record"] for o in outcomes if o["case_id"] == "rag-v0-049")
    validate_expansion(row["expansion_raw"], row, resolver)
    key, timeout = original.approved_settings(allow_env_read)
    recorder = Recorder(OUTPUT / "calls")
    recorder.scope = (row, "hyde")
    state = {"started_at": original.now(), "status": "running_049_first_hyde", "max_retries": 0, "qdrant_calls": 0, "judge_calls": 0}
    original.write_json(OUTPUT / "generation_state.json", state)
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                    event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=original.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                response = await client.chat.completions.create(**original.request_body(row, "hyde"))
        if not response.choices or response.choices[0].finish_reason != "stop" or not isinstance(response.choices[0].message.content, str):
            raise original.H1Error("h1_response_not_finished_or_missing_text")
        outcomes = apply_response(outcomes, response.choices[0].message.content, resolver)
        original.write_json(OUTPUT / "dispositions.json", outcomes)
        with patch.object(prior, "OUTPUT", OUTPUT):
            summary = prior.snapshot(outcomes, old_calls, recorder.entries)
        state.update(status=summary["status"], candidate_count=summary["candidate_count"], pending_case_ids=summary["pending_case_ids"])
    except Exception as exc:
        state.update(status="paused_049_pending_West", error_type=type(exc).__name__, error_code=prior.batch.error_code(exc))
    finally:
        state.update(finished_at=original.now(), additional_usage=original.usage_summary(recorder.entries),
                     shared_batch_usage=original.usage_summary([*old_calls[-6:], *recorder.entries]))
        load_prepared()
        state["history_and_protected_inputs_unchanged"] = True
        original.write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "frozen_pending_final_retrieval_approval" else 1


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
            raise original.H1Error("h1_union_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
