"""Finish V5.2 with West's single 060-only filtered-rerank exception."""

from __future__ import annotations

from collections import Counter
import json
import sys
from unittest.mock import patch

from tools.v52_offline_runtime import resume_055_once as prior
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


base = prior.base
SOURCE = prior.SOURCE.parent / "resume-055-once"
OUTPUT = SOURCE.parent / "final-baseline-060-once"
PINS = {
    "combined-cases.jsonl": "a975524b28d6793c823fd7fc13db726d5e7acf025fb44b7dda3e1fbd044cc111",
    "run_manifest.json": "d0320ce154969112f4490d9ea4b926b296f3bf342b48985bf9f7a88e81025fe3",
    "055-special.json": "e7b182239a7944c795ff556216d333e363a7ee1ab898297f990873d4ba74c384",
    "055-exception-consumed.json": "008fc0a1f13070dc1bd293d8cce573a958575b9b10e644cb089e29996662ae98",
}
EXCEPTION_CASE = "rag-v0-060"
EXPECTED_PLAN_ID = "query-plan:ade76be1fa57214a9999aaf7"
GOLD_060 = "parent-422dc677ed403a00004716be"
REMAINING = tuple(f"rag-v0-{n:03}" for n in range(60, 67))


def load_source():
    inputs, _, queries, plans, filters, _ = prior.load_source()
    for name, digest in PINS.items():
        if base.sha256(SOURCE / name) != digest:
            raise base.BaselineError("final_source_digest_mismatch:" + name)
    previous = base.read_jsonl(SOURCE / "combined-cases.jsonl")
    state = base.read_json(SOURCE / "run_manifest.json")
    cases, _, _, projection, *_ = inputs
    if (
        len(previous) != 27
        or [r["case_id"] for r in previous] != [c["case_id"] for c in cases[:27]]
        or not all(r["healthy"] for r in previous)
        or tuple(c["case_id"] for c in cases[27:]) != REMAINING
        or state["status"] != "incomplete"
        or state["safety_events"][0]["case_id"] != EXCEPTION_CASE
        or [e["case_id"] for e in state["exception_events"]] != ["rag-v0-055"]
    ):
        raise base.BaselineError("final_source_population_or_stop_drift")
    condition = filters[27]
    eligible = {
        p
        for p, child in projection.points.items()
        if base.h2.matches(
            projection.children[child], condition["effective_qdrant_filter"]
        )
    }
    if (
        len(eligible) != 8
        or len(
            {projection.children[projection.points[p]]["parent_id"] for p in eligible}
        )
        != 8
    ):
        raise base.BaselineError("060_original_eight_candidates_changed")
    return inputs, previous, queries, plans, filters, eligible


def protected_hashes():
    result = prior.protected_hashes()
    result.update(
        {
            p.relative_to(base.ROOT).as_posix(): base.sha256(p)
            for p in SOURCE.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        }
    )
    return result


class Once060:
    def __init__(self, directory):
        self.directory = directory
        self.original = base.h2.intercept_fallback
        self.events = []

    def __call__(self, plan, count, config, predicate, audit):
        if audit.active != EXCEPTION_CASE:
            return self.original(plan, count, config, predicate, audit)
        if (
            not predicate(plan, count, config)
            or count != 8
            or config.selection.exploratory_entity_fallback_below != 12
            or plan.plan_id != EXPECTED_PLAN_ID
            or plan.index_profile.collection_name != base.COLLECTION
        ):
            raise base.h2.SafetyStop("060_once_exception_scope_drift")
        path = self.directory / "060-exception-consumed.json"
        if self.events or path.exists():
            raise base.h2.SafetyStop("060_once_exception_already_consumed")
        event = {
            "case_id": EXCEPTION_CASE,
            "selection_count": 8,
            "threshold": 12,
            "plan_id": plan.plan_id,
            "consumed_at": base.h1.now(),
            "approved_by": "West",
            "event": "one_time_060_filtered_rerank_only",
            "unfiltered_request_sent": False,
            "not_a_change_to_H2": True,
            "not_authorized_for_other_cases_or_runs": True,
        }
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        self.events.append(event)
        return False


def case_060_report(row, projection, expected_points):
    if (
        row["case_id"] != EXCEPTION_CASE
        or not row["healthy"]
        or len(row["selection"]) != 8
        or {r["point_id"] for r in row["selection"]} != expected_points
        or len(row["rerank_top12"]) != 8
        or row["rerank_fallback_used"]
    ):
        raise base.BaselineError("060_filtered_eight_rerank_contract_drift")
    children = [
        {
            "child_id": child,
            "parent_id": payload["parent_id"],
            "entity_ids": payload.get("entity_ids", []),
        }
        for child, payload in projection.children.items()
        if payload["parent_id"] == GOLD_060
    ]
    if len(children) != 2 or any(
        "album:capricorn" in p["entity_ids"] for p in children
    ):
        raise base.BaselineError("060_known_payload_bug_changed")
    return {
        "case_id": EXCEPTION_CASE,
        "metrics": row["metrics"],
        "candidate_count": 8,
        "rerank_result": row["rerank_top12"],
        "reranker": "Qwen/Qwen3-Reranker-0.6B",
        "gold_parent_id": GOLD_060,
        "data_bug": {
            "status": "confirmed_pending_fix_not_changed",
            "missing_entity_id": "album:capricorn",
            "affected_children": children,
        },
        "manual_score_adjustment": False,
        "unfiltered_backfill": False,
    }


def run(guard):
    inputs, previous, queries, plans, filters, expected_points = load_source()
    cases, _, _, projection, config, _, binding = inputs
    if OUTPUT.exists():
        raise base.BaselineError("one_time_final_output_exists_no_repeat")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    protected = protected_hashes()
    state = {
        "status": "loading_pinned_local_models",
        "started_at": base.h1.now(),
        "case_ids": list(REMAINING),
        "source_pins": PINS,
        "reused_count": 27,
        "source_directory": SOURCE.relative_to(base.ROOT).as_posix(),
        "resumed_count": 0,
        "protected_before": protected,
        "gold_sha256_before": {
            n: base.sha256(base.GOLD.parent / n) for n in base.GOLD_SHAS
        },
        "authorization": "West approved single060-only filtered-rerank exception.061-066 retain original H2 stop rule.",
        "new_generation_calls": 0,
        "qdrant_write_calls": 0,
    }
    base.write_json(OUTPUT / "run_manifest.json", state)
    audit, once, resumed, error, before = (
        base.h2.RequestAudit(),
        Once060(OUTPUT),
        [],
        None,
        None,
    )
    original_raw = (SOURCE / "combined-cases.jsonl").read_bytes()
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {
            "embedding_device": encoder._encoder._device,
            "reranker_device": scorer._device,
        }
        with (
            base.target_scope(),
            audit.installed(),
            patch.object(base.h2, "intercept_fallback", once),
        ):
            before = base.inventory(projection)
            base.write_json(OUTPUT / "collection_before.json", before)
            with (
                (OUTPUT / "combined-cases.jsonl").open("xb") as combined,
                (OUTPUT / "resumed-cases.jsonl").open("xb") as stream,
            ):
                combined.write(original_raw)
                combined.flush()
                for case, query, plan, condition in zip(
                    cases[27:], queries[27:], plans[27:], filters[27:], strict=True
                ):
                    if not case["case_id"] == query["case_id"] == condition["case_id"]:
                        raise base.BaselineError("final_case_order_drift")
                    audit.active, audit.expected_filter, audit.expected_lexical = (
                        case["case_id"],
                        condition["effective_qdrant_filter"],
                        query["queries"],
                    )
                    offset = len(audit.requests)
                    row = base.h2.collect_case(
                        {**case, "control_legs": base.v51.expected_legs(query)},
                        query,
                        plan,
                        condition,
                        projection,
                        config,
                        binding,
                        audit,
                    )
                    row["qdrant_requests"] = audit.requests[offset:]
                    if not row["healthy"]:
                        raise base.BaselineError("final_unhealthy_case_stop")
                    if case["case_id"] == EXCEPTION_CASE:
                        base.write_json(
                            OUTPUT / "060-special.json",
                            case_060_report(row, projection, expected_points),
                        )
                    raw = (
                        json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                    ).encode("utf-8")
                    stream.write(raw)
                    stream.flush()
                    combined.write(raw)
                    combined.flush()
                    resumed.append(row)
                    state.update(
                        status="running",
                        resumed_count=len(resumed),
                        last_case_id=row["case_id"],
                    )
                    base.write_json(OUTPUT / "run_manifest.json", state)
                    print(
                        json.dumps(
                            {
                                "case_id": row["case_id"],
                                "combined_completed": 27 + len(resumed),
                                "metrics": row["metrics"],
                            }
                        ),
                        flush=True,
                    )
    except BaseException as exc:
        error = (
            str(exc)
            if isinstance(
                exc, (base.v51.BaselineError, base.h2.H2Error, base.h2.SafetyStop)
            )
            else type(exc).__name__
        )
    finally:
        audit.active = None
        inventory_error = None
        if before is not None and not guard.denied:
            try:
                with base.target_scope(), audit.installed():
                    after = base.inventory(projection)
                base.write_json(OUTPUT / "collection_after.json", after)
                if before["payload_sha256"] != after["payload_sha256"]:
                    raise base.BaselineError("final_collection_changed")
            except BaseException as exc:
                inventory_error = type(exc).__name__
        records = [*previous, *resumed]
        metrics = prior.summarize(cases, records)
        unchanged = protected == protected_hashes()
        complete = (
            len(resumed) == 7
            and error is None
            and inventory_error is None
            and unchanged
            and not guard.denied
            and not audit.safety_events
            and len(once.events) == 1
            and metrics["status"] == "complete"
        )
        state.update(
            status="complete" if complete else "incomplete",
            error_code=error,
            finished_at=base.h1.now(),
            inventory_error=inventory_error,
            protected_unchanged=unchanged,
            resumed_count=len(resumed),
            combined_count=len(records),
            exception_events=once.events,
            safety_events=audit.safety_events,
            guard_denied=guard.denied,
            gold_sha256_after={
                n: base.sha256(base.GOLD.parent / n) for n in base.GOLD_SHAS
            },
            original_27_raw_bytes_preserved=(OUTPUT / "combined-cases.jsonl")
            .read_bytes()
            .startswith(original_raw)
            if (OUTPUT / "combined-cases.jsonl").exists()
            else False,
            qdrant_request_counts=dict(
                Counter(f"{r['method']} {r['path']}" for r in audit.requests)
            ),
        )
        base.write_json(OUTPUT / "run_manifest.json", state)
        base.write_json(OUTPUT / "qdrant_requests.json", audit.requests)
        base.write_json(OUTPUT / "metrics.json", metrics)
    print(
        json.dumps(
            {
                "status": state["status"],
                "combined_completed": state["combined_count"],
                "error_code": error,
                "exception_used_for": [e["case_id"] for e in once.events],
                "safety_events": audit.safety_events,
            }
        ),
        flush=True,
    )
    return 0 if complete else 1


def main():
    if len(sys.argv) != 1:
        raise base.BaselineError("final_one_time_no_scope_overrides")
    base.h2.configure()
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
