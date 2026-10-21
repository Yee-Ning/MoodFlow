"""Opt-in H1 dimension prompt: one 002 expansion, review, then paired H2 run.

Frozen H1/H2 modules and old artifacts stay unchanged. The only experimental
input change is the appended expansion system prompt; HyDE is reused exactly.
"""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import sys
from unittest.mock import patch

from tools.v52_offline_runtime import verify_payload_fix as repair
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


base, h1 = repair.base, repair.base.h1
CASE_ID = "rag-v0-002"
OUTPUT = repair.OUTPUT.parent / "h1-dimension-prompt-002-20261019"
OLD_CALL = base.ROOT / "workspace/moodflow-review/h1-query-bundle-gate1-20261018/calls/003.json"
STRATEGY = """拆分策略（按优先级）：
1. 按维度拆：把问题拆成 2-3 个不同维度/角度
   例：「音乐风格特点」→ 乐器使用维度、曲风融合维度、代表作品维度
2. 按时间拆：如问题涉及时期，按早期/中期/近期拆
3. 按对象拆：如问题涉及多人/多作品，每人/每作品一个子查询
4. 避免同义改写：三个子查询不能只是换说法，必须覆盖不同信息面"""
EXPANSION_PROMPT = h1.EXPANSION_PROMPT + "\n\n" + STRATEGY


def load_source():
    inputs, bundles, previous = repair.load_source()
    case, old, plan, condition = bundles[CASE_ID]
    old_call = base.read_json(OLD_CALL)
    if (
        old_call["case_id"] != CASE_ID or old_call["stage"] != "expansion"
        or old_call["request"] != h1.request_body(old, "expansion")
    ):
        raise base.BaselineError("dimension_original_request_drift")
    return inputs, case, old, plan, condition, previous[CASE_ID]


def generation_body(old):
    body = h1.request_body(old, "expansion")
    body["messages"][0]["content"] = EXPANSION_PROMPT
    return body


class Recorder(h1.Recorder):
    async def request(self, request):
        if (
            self.scope is None or self.scope[0]["case_id"] != CASE_ID
            or self.scope[1] != "expansion" or self.entries
        ):
            raise h1.H1Error("dimension_only_one_002_expansion_call")
        with patch.object(h1, "EXPANSION_PROMPT", EXPANSION_PROMPT):
            await super().request(request)


def protected_hashes():
    hashes = repair.prior.protected_hashes()
    paths = [OLD_CALL]
    paths += [p for p in repair.FINAL.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    paths += [p for p in repair.OUTPUT.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    hashes.update({p.relative_to(base.ROOT).as_posix(): base.sha256(p) for p in paths})
    return hashes


async def generate():
    inputs, _, old, _, _, _ = load_source()
    if OUTPUT.exists():
        raise base.BaselineError("dimension_generation_already_attempted")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    protected = protected_hashes()
    base.write_json(OUTPUT / "prompts.json", {
        "old": h1.EXPANSION_PROMPT, "new": EXPANSION_PROMPT,
        "only_change": "append West-approved STRATEGY", "HyDE_changed": False,
    })
    base.write_json(OUTPUT / "old-expansion.json", old)
    state = {"status": "generating", "case_ids": [CASE_ID],
             "started_at": h1.now(), "protected_before": protected,
             "SDK_retries": 0, "maximum_physical_calls": 1,
             "full34_authorized": False, "new_HyDE_calls": 0, "Qdrant_calls": 0}
    base.write_json(OUTPUT / "generation-state.json", state)
    error = None
    recorder = Recorder(OUTPUT / "calls")
    try:
        import httpx
        from openai import AsyncOpenAI

        # Reuse the established connection loader; credentials go only to SDK
        # memory, never to reports, request headers in logs, or tool output.
        key, timeout = h1.approved_settings(True)
        _, resolver = base.registry_components(inputs[-1])
        recorder.scope = (old, "expansion")
        async with httpx.AsyncClient(
            trust_env=False, follow_redirects=False, timeout=timeout,
            event_hooks={"request": [recorder.request], "response": [recorder.response]},
        ) as http:
            async with AsyncOpenAI(api_key=key, base_url=h1.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                response = await client.chat.completions.create(**generation_body(old))
        if not response.choices or response.choices[0].finish_reason != "stop":
            raise base.BaselineError("dimension_provider_response_incomplete")
        raw = response.choices[0].message.content
        if not isinstance(raw, str):
            raise base.BaselineError("dimension_response_not_text")
        base.write_json(OUTPUT / "raw-expansion.json", {"case_id": CASE_ID, "raw": raw})
        queries, checks = base.validate_stage(raw, old, "expansion", resolver)
        new = deepcopy(old)
        new.update(queries=queries, expansion_raw=raw, expansion_checks=checks,
                   expansion_call_number=1, status="constraint_passed_pending_read_review")
        base.write_json(OUTPUT / "new-expansion.json", new)
        state["status"] = "constraint_passed_pending_read_review"
        print(json.dumps({"case_id": CASE_ID, "queries": queries,
                          "constraints": "passed", "HyDE_reused": True}, ensure_ascii=False), flush=True)
    except Exception as exc:
        error = str(exc) if isinstance(exc, (h1.H1Error, base.BaselineError)) else type(exc).__name__
        state["status"] = "failed_no_automatic_retry"
    finally:
        unchanged = protected == protected_hashes()
        state.update(error_code=error, finished_at=h1.now(), protected_unchanged=unchanged,
                     usage=h1.usage_summary(recorder.entries),
                     prompt_sha256=hashlib.sha256(EXPANSION_PROMPT.encode()).hexdigest())
        base.write_json(OUTPUT / "generation-state.json", state)
    return 0 if error is None and unchanged else 1


def reviewed_bundle(old_plan, old, condition):
    new = base.read_json(OUTPUT / "new-expansion.json")
    review = base.read_json(OUTPUT / "expansion-review.json")
    if (
        review.get("reviewer") != "Codex" or review.get("passed") is not True
        or review.get("case_id") != CASE_ID or len(review.get("dimensions", [])) != 3
        or review.get("new_expansion_sha256") != base.sha256(OUTPUT / "new-expansion.json")
    ):
        raise base.BaselineError("dimension_semantic_review_missing_or_stale")
    for key in old:
        if key not in {"queries", "expansion_raw", "expansion_checks", "expansion_call_number", "status"} and new[key] != old[key]:
            raise base.BaselineError("dimension_changed_beyond_expansion:" + key)
    plan = deepcopy(old_plan)
    for variant, text in zip(plan["queries"][:3], new["queries"], strict=True):
        variant["text"] = text
    # New plan identity labels changed queries; all execution fields stay fixed.
    plan["plan_id"] = "query-plan:" + hashlib.sha256(
        json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:24]
    return new, plan, deepcopy(condition)


def run(guard):
    inputs, case, old, original_plan, condition, previous = load_source()
    generation = base.read_json(OUTPUT / "generation-state.json")
    if generation["status"] != "constraint_passed_pending_read_review":
        raise base.BaselineError("dimension_generation_not_passed")
    new, plan, condition = reviewed_bundle(original_plan, old, condition)
    _, resolver = base.registry_components(inputs[-1])
    if base.validate_stage(new["expansion_raw"], old, "expansion", resolver)[0] != new["queries"]:
        raise base.BaselineError("dimension_expansion_validation_drift")
    if (OUTPUT / "run_manifest.json").exists():
        raise base.BaselineError("dimension_retrieval_already_attempted")
    projection = repair.overlay_projection(inputs[3])
    config, binding = inputs[4], inputs[-1]
    protected = protected_hashes()
    state = {"status": "loading_pinned_local_models", "started_at": h1.now(),
             "case_ids": [CASE_ID], "protected_before": protected,
             "new_HyDE_calls": 0, "Qdrant_writes": 0, "full34_authorized": False}
    base.write_json(OUTPUT / "run_manifest.json", state)
    base.write_json(OUTPUT / "evaluation-plan.json", {"plan": plan, "condition": condition})
    audit, row, before, error, after_error = base.h2.RequestAudit(), None, None, None, None
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {"embedding_device": encoder._encoder._device,
                            "reranker_device": scorer._device}
        with base.target_scope(), audit.installed():
            before = base.inventory(projection)
            base.write_json(OUTPUT / "collection.before.json", before)
            audit.active, audit.expected_filter, audit.expected_lexical = (
                CASE_ID, condition["effective_qdrant_filter"], new["queries"]
            )
            offset = len(audit.requests)
            row = base.h2.collect_case(
                {**case, "control_legs": base.v51.expected_legs(new)}, new, plan,
                condition, projection, config, binding, audit,
            )
            row["qdrant_requests"] = audit.requests[offset:]
            if not row["healthy"] or row["metrics"] != base.independently_score(case, row):
                raise base.BaselineError("dimension_unhealthy_or_scoring_mismatch")
            with (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (base.v51.BaselineError, base.h2.H2Error, base.h2.SafetyStop)) else type(exc).__name__
    finally:
        audit.active = None
        if before is not None and not guard.denied:
            try:
                with base.target_scope(), audit.installed():
                    after = base.inventory(projection)
                base.write_json(OUTPUT / "collection.after.json", after)
                if before["payload_sha256"] != after["payload_sha256"]:
                    raise base.BaselineError("dimension_collection_changed")
            except BaseException as exc:
                after_error = type(exc).__name__
        unchanged = protected == protected_hashes()
        complete = row is not None and row["healthy"] and error is None and after_error is None and unchanged and not audit.safety_events and not guard.denied
        state.update(status="complete" if complete else "incomplete", finished_at=h1.now(),
                     error_code=error, inventory_error=after_error, protected_unchanged=unchanged,
                     guard_denied=guard.denied, safety_events=audit.safety_events)
        base.write_json(OUTPUT / "run_manifest.json", state)
        base.write_json(OUTPUT / "qdrant-requests.json", audit.requests)
        if row is not None and row.get("metrics") is not None:
            base.write_json(OUTPUT / "comparison.json", {
                "old_queries": old["queries"], "new_queries": new["queries"],
                "old_metrics": previous["metrics"], "new_metrics": row["metrics"],
                "HyDE_identical": old["hyde"] == new["hyde"],
                "filter_identical": condition["effective_qdrant_filter"] == previous["effective_qdrant_filter"],
                "full34_run": False,
            })
    print(json.dumps({"status": state["status"], "error_code": error,
                      "metrics": row.get("metrics") if row else None}), flush=True)
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
    with anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
