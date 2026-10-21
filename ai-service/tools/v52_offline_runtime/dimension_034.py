"""Report-52: 34 single-call expansions, reviewed before paired old/new H2.

Only the expansion system prompt changes. Original per-case request bodies,
HyDE, filters, models and numeric settings remain frozen. The two explicitly
approved 055/060 filtered-only exceptions are separately consumed per arm.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
from time import perf_counter
import sys
from unittest.mock import patch

from tools.v52_offline_runtime import dimension_002 as single


base, h1, repair = single.base, single.h1, single.repair
OUTPUT = single.OUTPUT.parent / "h1-dimension-prompt-034-20261019"
OVERLAY_SHA = "4cac0258fafc191c0301609a0359ee3dd4bd581ae5579010aa2ed40f5ac7ca72"
ARMS = ("old", "new")
AUTHORIZATION = {
    "approved_by": "West",
    "scope": "34 freshly generated expansions; paired old/new retrieval",
    "055_060": "one filtered-only rerank per case and arm, no unfiltered backfill",
    "065": "previously approved A year-only filter in both arms",
}


def assert_frozen_198():
    pins = base.read_json(single.OUTPUT / "verification.json")["protected_file_sha256"]
    if len(pins) != 198:
        raise base.BaselineError("batch_expected_198_pins")
    for name, sha in pins.items():
        if base.sha256(base.ROOT / name) != sha:
            raise base.BaselineError("batch_frozen_198_drift:" + name)
    return pins


def original_requests(queries):
    roots = [
        base.ROOT / "workspace/moodflow-review/h1-query-bundle-gate1-20261018",
        base.ROOT / "workspace/moodflow-review/v5-1-h2-baseline-20261018",
        repair.prior.SOURCE.parent,
    ]
    calls = []
    for root in roots:
        for path in sorted(root.rglob("calls/*.json")):
            entry = base.read_json(path)
            if entry.get("stage") == "expansion" and entry.get("status_code") == 200:
                calls.append((path, entry))
    found = {}
    for query in queries:
        matches = [(p, c) for p, c in calls if c.get("case_id") == query["case_id"]
                   and c.get("response", {}).get("choices", [{}])[0].get("message", {}).get("content") == query["expansion_raw"]]
        variants = {json.dumps(c["request"], ensure_ascii=False, sort_keys=True) for _, c in matches}
        if len(variants) != 1:
            raise base.BaselineError("batch_original_request_missing_or_ambiguous:" + query["case_id"])
        path, entry = matches[0]
        request = entry["request"]
        if (request["messages"][0]["content"] != h1.EXPANSION_PROMPT
                or request["model"] != h1.MODEL or request["temperature"] != 0
                or type(request["temperature"]) is not int or request["max_tokens"] != 4096):
            raise base.BaselineError("batch_original_generation_protocol_drift")
        found[query["case_id"]] = {
            "request": request, "source": path.relative_to(base.ROOT).as_posix(),
            "source_sha256": base.sha256(path),
        }
    return found


def generation_body(original):
    body = deepcopy(original["request"])
    body["messages"][0]["content"] = single.EXPANSION_PROMPT
    return body


def load_source():
    pins = assert_frozen_198()
    inputs, _, queries, plans, filters, _ = repair.prior.load_source()
    if base.sha256(repair.FINAL / "combined-cases.jsonl") != repair.FINAL_SHA:
        raise base.BaselineError("batch_baseline_digest_drift")
    cases = inputs[0]
    if len(cases) != 34 or Counter(c["answerability"] for c in cases) != {"full": 24, "partial": 10}:
        raise base.BaselineError("batch_population_drift")
    requests = original_requests(queries)
    bundles = {}
    for case, query, plan, condition in zip(cases, queries, plans, filters, strict=True):
        cid = case["case_id"]
        if cid != query["case_id"] or cid != condition["case_id"]:
            raise base.BaselineError("batch_source_order_drift")
        if cid == "rag-v0-065":
            plan, condition = repair.variant_065(plan, condition)
        bundles[cid] = (case, query, plan, condition)
    return inputs, bundles, requests, pins


def protected_hashes():
    return {**single.protected_hashes(), **assert_frozen_198()}


class Recorder(h1.Recorder):
    def __init__(self, directory, requests):
        super().__init__(directory)
        self.requests_by_case = requests

    async def request(self, request):
        cid = self.scope
        if (cid not in self.requests_by_case or request.method != "POST"
                or request.url.scheme != "https" or request.url.host != "api.deepseek.com"
                or request.url.port not in {None, 443} or request.url.path != "/v1/chat/completions"
                or request.url.username or request.url.password):
            raise h1.H1Error("batch_generation_scope_forbidden")
        body = json.loads(request.content)
        if body != generation_body(self.requests_by_case[cid]) or type(body["temperature"]) is not int:
            raise h1.H1Error("batch_request_changed_beyond_system_prompt")
        if any(e["case_id"] == cid for e in self.entries):
            raise h1.H1Error("batch_duplicate_case_generation")
        entry = {"case_id": cid, "stage": "expansion", "call_number": len(self.entries) + 1,
                 "request": body, "request_sha256": hashlib.sha256(request.content).hexdigest(),
                 "started_at": h1.now()}
        self.entries.append(entry)
        request.extensions["h1_record_index"] = len(self.entries) - 1
        request.extensions["h1_started"] = perf_counter()
        self.save(entry)


async def generate():
    inputs, bundles, requests, pins = load_source()
    if OUTPUT.exists():
        raise base.BaselineError("batch_generation_already_attempted")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    for name in ("old", "new", "raw"):
        (OUTPUT / name).mkdir()
    protected = protected_hashes()
    base.write_json(OUTPUT / "authorization.json", AUTHORIZATION)
    base.write_json(OUTPUT / "original-requests.json", requests)
    base.write_json(OUTPUT / "prompts.json", {"old": h1.EXPANSION_PROMPT, "new": single.EXPANSION_PROMPT})
    state = {"status": "generating", "started_at": h1.now(), "case_ids": list(bundles),
             "completed_cases": [], "constraint_failed_cases": [], "maximum_calls": 34,
             "SDK_retries": 0, "new_HyDE_calls": 0, "Qdrant_calls": 0,
             "protected_before": protected, "frozen_198": pins}
    base.write_json(OUTPUT / "generation-state.json", state)
    recorder, error = Recorder(OUTPUT / "calls", requests), None
    try:
        import httpx
        from openai import AsyncOpenAI

        key, timeout = h1.approved_settings(True)
        _, resolver = base.registry_components(inputs[-1])
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeout,
            event_hooks={"request": [recorder.request], "response": [recorder.response]},
        ) as http:
            async with AsyncOpenAI(api_key=key, base_url=h1.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                for cid, (_, old, _, _) in bundles.items():
                    recorder.scope = cid
                    base.write_json(OUTPUT / "old" / (cid + ".json"), old)
                    response = await client.chat.completions.create(**generation_body(requests[cid]))
                    if not response.choices or response.choices[0].finish_reason != "stop":
                        raise base.BaselineError("batch_provider_response_incomplete:" + cid)
                    raw = response.choices[0].message.content
                    if not isinstance(raw, str):
                        raise base.BaselineError("batch_provider_response_not_text:" + cid)
                    base.write_json(OUTPUT / "raw" / (cid + ".json"), {"raw": raw, "case_id": cid})
                    new = deepcopy(old)
                    new.update(expansion_raw=raw, expansion_checks=None, expansion_call_number=len(recorder.entries),
                               status="constraint_passed_pending_semantic_review")
                    try:
                        new["queries"], new["expansion_checks"] = base.validate_stage(raw, old, "expansion", resolver)
                    except (h1.H1Error, base.v51.BaselineError) as exc:
                        new.update(status="constraint_failed_no_retry", constraint_error=str(exc))
                        # Retain the actual output for review; never substitute old queries.
                        try:
                            new["queries"] = json.loads(raw).get("queries")
                        except (ValueError, AttributeError):
                            new["queries"] = None
                        state["constraint_failed_cases"].append(cid)
                    base.write_json(OUTPUT / "new" / (cid + ".json"), new)
                    state["completed_cases"].append(cid)
                    base.write_json(OUTPUT / "generation-state.json", state)
                    print(json.dumps({"case_id": cid, "completed": len(state["completed_cases"]),
                                      "status": new["status"], "queries": new["queries"]}, ensure_ascii=False), flush=True)
        state["status"] = "generated_pending_semantic_review" if not state["constraint_failed_cases"] else "generated_with_constraint_failures"
    except Exception as exc:
        error = str(exc) if isinstance(exc, (h1.H1Error, base.BaselineError)) else type(exc).__name__
        state["status"] = "generation_incomplete_no_retry"
    finally:
        unchanged = protected == protected_hashes()
        state.update(finished_at=h1.now(), error_code=error, protected_unchanged=unchanged,
                     usage=h1.usage_summary(recorder.entries))
        base.write_json(OUTPUT / "generation-state.json", state)
    return 0 if len(state["completed_cases"]) == 34 and not state["constraint_failed_cases"] and unchanged and error is None else 1


def reviewed_bundles(bundles, binding):
    state = base.read_json(OUTPUT / "generation-state.json")
    review = base.read_json(OUTPUT / "semantic-review.json")
    if (state["status"] != "generated_pending_semantic_review" or len(state["completed_cases"]) != 34
            or review.get("reviewer") != "Codex" or review.get("all_passed") is not True
            or set(review.get("cases", {})) != set(bundles)):
        raise base.BaselineError("batch_semantic_gate_not_passed")
    _, resolver = base.registry_components(binding)
    output = {}
    for cid, (case, old, original_plan, condition) in bundles.items():
        path = OUTPUT / "new" / (cid + ".json")
        checked = review["cases"][cid]
        if checked.get("passed") is not True or checked.get("new_sha256") != base.sha256(path):
            raise base.BaselineError("batch_semantic_review_stale:" + cid)
        new = base.read_json(path)
        if base.validate_stage(new["expansion_raw"], old, "expansion", resolver)[0] != new["queries"]:
            raise base.BaselineError("batch_constraint_recheck_drift:" + cid)
        for key in old:
            if key not in {"queries", "expansion_raw", "expansion_checks", "expansion_call_number", "status"} and new[key] != old[key]:
                raise base.BaselineError("batch_changed_beyond_expansion:" + cid + ":" + key)
        plan = deepcopy(original_plan)
        for variant, text in zip(plan["queries"][:3], new["queries"], strict=True):
            variant["text"] = text
        plan["plan_id"] = "query-plan:" + hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        output[cid] = {"old": (case, old, original_plan, deepcopy(condition)),
                       "new": (case, new, plan, deepcopy(condition))}
    return output


class BatchFilteredOnce:
    def __init__(self, directory, paired):
        self.directory, self.paired, self.arm = directory, paired, None
        self.original, self.events = base.h2.intercept_fallback, []

    def __call__(self, plan, count, config, predicate, audit):
        cid = audit.active
        if cid not in repair.EXCEPTIONS:
            return self.original(plan, count, config, predicate, audit)
        if (self.arm not in ARMS or count != 10 or config.selection.exploratory_entity_fallback_below != 12
                or plan.plan_id != self.paired[cid][self.arm][2]["plan_id"]
                or plan.index_profile.collection_name != base.COLLECTION or not predicate(plan, count, config)):
            raise base.h2.SafetyStop("batch_filtered_once_scope_drift")
        path = self.directory / f"{self.arm}-{cid}.json"
        if path.exists():
            raise base.h2.SafetyStop("batch_filtered_once_already_consumed")
        self.directory.mkdir(exist_ok=True)
        event = {"arm": self.arm, "case_id": cid, "plan_id": plan.plan_id,
                 "selection_count": 10, "threshold": 12, "approved_by": "West",
                 "consumed_at": h1.now(), "unfiltered_backfill_sent": False,
                 "event": "report52_filtered_only_once"}
        with path.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        self.events.append(event)
        return False


def run(guard):
    inputs, bundles, _, _ = load_source()
    paired = reviewed_bundles(bundles, inputs[-1])
    if (OUTPUT / "run_manifest.json").exists():
        raise base.BaselineError("batch_retrieval_already_attempted")
    projection = repair.overlay_projection(inputs[3])
    config, binding = inputs[4], inputs[-1]
    protected = protected_hashes()
    state = {"status": "loading_pinned_local_models", "started_at": h1.now(),
             "case_ids": list(paired), "arms": list(ARMS), "expected_records": 68,
             "completed_records": 0, "protected_before": protected, "authorization": AUTHORIZATION,
             "Qdrant_write_calls": 0, "new_HyDE_calls": 0}
    base.write_json(OUTPUT / "run_manifest.json", state)
    base.write_json(OUTPUT / "paired-plans.json", {
        cid: {arm: {"plan": b[2], "condition": b[3]} for arm, b in arms.items()}
        for cid, arms in paired.items()
    })
    audit, rows, before, error, after_error = base.h2.RequestAudit(), [], None, None, None
    once = BatchFilteredOnce(OUTPUT / "exceptions", paired)
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {"embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        with base.target_scope(), audit.installed(), patch.object(base.h2, "intercept_fallback", once):
            before = base.inventory(projection)
            if before["payload_sha256"] != OVERLAY_SHA:
                raise base.BaselineError("batch_live_overlay_sha_drift")
            base.write_json(OUTPUT / "collection.before.json", before)
            with (OUTPUT / "paired-cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                for cid, arms in paired.items():
                    for arm in ARMS:
                        case, query, plan, condition = arms[arm]
                        once.arm = arm
                        audit.active, audit.expected_filter, audit.expected_lexical = cid, condition["effective_qdrant_filter"], query["queries"]
                        offset = len(audit.requests)
                        row = base.h2.collect_case(
                            {**case, "control_legs": base.v51.expected_legs(query)}, query, plan,
                            condition, projection, config, binding, audit,
                        )
                        row.update(arm=arm, qdrant_requests=audit.requests[offset:])
                        if not row["healthy"] or row["metrics"] != base.independently_score(case, row):
                            raise base.BaselineError("batch_unhealthy_or_scoring_mismatch:" + cid)
                        stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                        stream.flush()
                        rows.append(row)
                        state.update(status="running", completed_records=len(rows), last_case_id=cid, last_arm=arm)
                        base.write_json(OUTPUT / "run_manifest.json", state)
                        print(json.dumps({"case_id": cid, "arm": arm, "completed": len(rows), "metrics": row["metrics"]}), flush=True)
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (base.v51.BaselineError, base.h2.H2Error, base.h2.SafetyStop)) else type(exc).__name__
    finally:
        audit.active = None
        if before is not None and not guard.denied:
            try:
                with base.target_scope(), audit.installed():
                    after = base.inventory(projection)
                base.write_json(OUTPUT / "collection.after.json", after)
                if after["payload_sha256"] != OVERLAY_SHA:
                    raise base.BaselineError("batch_collection_changed")
            except BaseException as exc:
                after_error = type(exc).__name__
        unchanged = protected == protected_hashes()
        complete = len(rows) == 68 and error is None and after_error is None and unchanged and not audit.safety_events and not guard.denied and len(once.events) == 4
        state.update(status="complete" if complete else "incomplete", finished_at=h1.now(),
                     completed_records=len(rows), error_code=error, inventory_error=after_error,
                     protected_unchanged=unchanged, safety_events=audit.safety_events,
                     guard_denied=guard.denied, filtered_only_exceptions=once.events)
        base.write_json(OUTPUT / "run_manifest.json", state)
        base.write_json(OUTPUT / "qdrant-requests.json", audit.requests)
    print(json.dumps({"status": state["status"], "completed": len(rows), "error_code": error}), flush=True)
    return 0 if complete else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "run"))
    args = parser.parse_args()
    base.h2.configure()
    if args.command == "generate":
        sys.addaudithook(h1.secret_guard)
        return asyncio.run(generate())
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with single.anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
