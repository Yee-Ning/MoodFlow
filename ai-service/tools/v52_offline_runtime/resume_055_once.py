"""West's single-use 055 exception; H2 itself remains unchanged."""

from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import json
import sys
from unittest.mock import patch

from evals import rag_v52_h2_baseline as base
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


SOURCE = base.ROOT / "workspace/moodflow-review/v5-2-h2-baseline-20261019/run04"
OUTPUT = SOURCE.parent / "resume-055-once"
SOURCE_PINS = {
    "frozen_queries.json": "ee980f7ca5ade77aa3120fbb7dbec6fe755918c145c34891daad5e04ad4a2e02",
    "plans.json": "40c34d0bca3fcd1c67ded91422eeab1c55c47401065983684823c93ea3f80bc0",
    "filters.json": "312aaa9e2c83bbb1c82b89b8fc2332dfa3d37a383c5403e4c8e2ebabe8e94acc",
    "retrieval/cases.jsonl": "72dd9e968f2a24aec845385128d22aa15c2892edb95d78c0391bb6aa5561003a",
    "retrieval/run_manifest.json": "b847dae127ee98c9d79ec6aa94c44129ffeb076375f7f3ac00a0fdb07d35c01f",
}
EXCEPTION_CASE = "rag-v0-055"
EXPECTED_PLAN_ID = "query-plan:144e8aaeefffae82c271efe4"
GOLD_055 = "parent-ed25a65ce1ff591f3e99f841"
REMAINING = tuple(f"rag-v0-{n:03}" for n in range(55, 67))


def load_source():
    for name, digest in SOURCE_PINS.items():
        if base.sha256(SOURCE / name) != digest:
            raise base.BaselineError("resume_source_digest_mismatch:" + name)
    with (
        patch.object(base, "OUTPUT", SOURCE),
        patch.object(base, "EXPLICIT_RESTRICTIONS", True),
    ):
        inputs = base.load_frozen()
    cases, _, _, projection, *_ = inputs
    previous = base.read_jsonl(SOURCE / "retrieval/cases.jsonl")
    state = base.read_json(SOURCE / "retrieval/run_manifest.json")
    if (
        len(previous) != 22
        or [r["case_id"] for r in previous] != [c["case_id"] for c in cases[:22]]
        or not all(r["healthy"] for r in previous)
        or tuple(c["case_id"] for c in cases[22:]) != REMAINING
        or state["status"] != "incomplete"
        or state["safety_events"][0]["case_id"] != EXCEPTION_CASE
    ):
        raise base.BaselineError("resume_source_population_or_stop_drift")
    queries, plans, filters = [
        base.read_json(SOURCE / n)
        for n in ("frozen_queries.json", "plans.json", "filters.json")
    ]
    condition = next(f for f in filters if f["case_id"] == EXCEPTION_CASE)
    eligible = {
        p
        for p in projection.points
        if base.h2.matches(
            projection.children[projection.points[p]],
            condition["effective_qdrant_filter"],
        )
    }
    if (
        len(eligible) != 8
        or len(
            {projection.children[projection.points[p]]["parent_id"] for p in eligible}
        )
        != 8
    ):
        raise base.BaselineError("055_original_eight_candidates_changed")
    return inputs, previous, queries, plans, filters, eligible


def protected_hashes():
    result = base.protected_hashes()
    paths = [
        p for p in SOURCE.rglob("*") if p.is_file() and "__pycache__" not in p.parts
    ]
    paths += list(Path(__file__).parent.glob("*.py"))
    result.update({p.relative_to(base.ROOT).as_posix(): base.sha256(p) for p in paths})
    return result


class Once055:
    """Persist consumption before allowing filtered-only rerank for this one case."""

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
            raise base.h2.SafetyStop("055_once_exception_scope_drift")
        path = self.directory / "055-exception-consumed.json"
        if self.events or path.exists():
            raise base.h2.SafetyStop("055_once_exception_already_consumed")
        event = {
            "case_id": EXCEPTION_CASE,
            "selection_count": count,
            "threshold": 12,
            "plan_id": plan.plan_id,
            "consumed_at": base.h1.now(),
            "approved_by": "West",
            "event": "one_time_055_filtered_rerank_only",
            "unfiltered_request_sent": False,
            "not_a_change_to_H2": True,
            "not_authorized_for_other_cases_or_runs": True,
        }
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        self.events.append(event)
        return False  # Deny the unfiltered search; let original rerank use the existing pool.


def case_055_report(row, projection, expected_points):
    if (
        row["case_id"] != EXCEPTION_CASE
        or not row["healthy"]
        or len(row["selection"]) != 8
        or {r["point_id"] for r in row["selection"]} != expected_points
        or len(row["rerank_top12"]) != 8
        or row["rerank_fallback_used"]
    ):
        raise base.BaselineError("055_filtered_eight_rerank_contract_drift")
    children = [
        {
            "child_id": child,
            "parent_id": payload["parent_id"],
            "entity_ids": payload.get("entity_ids", []),
        }
        for child, payload in projection.children.items()
        if payload["parent_id"] == GOLD_055
    ]
    if len(children) != 2 or any(
        "album:capricorn" in p["entity_ids"] for p in children
    ):
        raise base.BaselineError("055_known_payload_bug_changed")
    return {
        "case_id": EXCEPTION_CASE,
        "metrics": row["metrics"],
        "candidate_count": 8,
        "rerank_result": row["rerank_top12"],
        "reranker": "Qwen/Qwen3-Reranker-0.6B",
        "gold_parent_id": GOLD_055,
        "data_bug": {
            "status": "confirmed_pending_fix_not_changed",
            "missing_entity_id": "album:capricorn",
            "affected_children": children,
        },
        "manual_score_adjustment": False,
        "unfiltered_backfill": False,
    }


def summarize(cases, records):
    by_id = {c["case_id"]: c for c in cases}
    if len({r["case_id"] for r in records}) != len(records):
        raise base.BaselineError("combined_duplicate_case")
    for row in records:
        if not row["healthy"] or row["metrics"] != base.independently_score(
            by_id[row["case_id"]], row
        ):
            raise base.BaselineError("resume_independent_metric_mismatch")
    complete = [r["case_id"] for r in records] == [c["case_id"] for c in cases]
    return {
        "status": "complete" if complete else "incomplete_intercepted",
        "requested_case_count": 34,
        "completed_case_count": len(records),
        "overall34": base.mean(records) if complete else None,
        "completed_only": base.mean(records),
        "by_answerability": {
            label: base.mean([r for r in records if r["answerability"] == label])
            for label in ("full", "partial")
        },
        "timeline5": base.mean(
            [r for r in records if r["case_id"] in base.TIMELINE_IDS]
        ),
        "new7": [
            {
                "case_id": case_id,
                "metrics": next(
                    (r["metrics"] for r in records if r["case_id"] == case_id), None
                ),
            }
            for case_id in base.NEW_IDS
        ],
        "uncompleted_case_ids": [
            c["case_id"]
            for c in cases
            if c["case_id"] not in {r["case_id"] for r in records}
        ],
    }


def run(guard):
    inputs, previous, queries, plans, filters, expected_points = load_source()
    cases, _, _, projection, config, _, binding = inputs
    if OUTPUT.exists():
        raise base.BaselineError("one_time_resume_output_exists_no_repeat")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    protected = protected_hashes()
    state = {
        "status": "loading_pinned_local_models",
        "started_at": base.h1.now(),
        "case_ids": list(REMAINING),
        "source_pins": SOURCE_PINS,
        "reused_count": 22,
        "source_directory": SOURCE.relative_to(base.ROOT).as_posix(),
        "resumed_count": 0,
        "protected_before": protected,
        "gold_sha256_before": {
            n: base.sha256(base.GOLD.parent / n) for n in base.GOLD_SHAS
        },
        "authorization": "West approved a single 055-only filtered-rerank exception. All other triggers must stop.",
        "new_generation_calls": 0,
        "qdrant_write_calls": 0,
    }
    base.write_json(OUTPUT / "run_manifest.json", state)
    audit, once, resumed, error, before = (
        base.h2.RequestAudit(),
        Once055(OUTPUT),
        [],
        None,
        None,
    )
    original_raw = (SOURCE / "retrieval/cases.jsonl").read_bytes()
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
                    cases[22:], queries[22:], plans[22:], filters[22:], strict=True
                ):
                    if not case["case_id"] == query["case_id"] == condition["case_id"]:
                        raise base.BaselineError("resume_case_order_drift")
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
                        raise base.BaselineError("resume_unhealthy_case_stop")
                    if case["case_id"] == EXCEPTION_CASE:
                        base.write_json(
                            OUTPUT / "055-special.json",
                            case_055_report(row, projection, expected_points),
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
                                "combined_completed": 22 + len(resumed),
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
                    raise base.BaselineError("resume_collection_changed")
            except BaseException as exc:
                inventory_error = type(exc).__name__
        records = [*previous, *resumed]
        metrics = summarize(cases, records)
        unchanged = protected == protected_hashes()
        complete = (
            len(resumed) == 12
            and error is None
            and inventory_error is None
            and unchanged
            and not guard.denied
            and not audit.safety_events
            and len(once.events) == 1
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
            original_22_raw_bytes_preserved=(OUTPUT / "combined-cases.jsonl")
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
    global OUTPUT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    if OUTPUT != SOURCE.parent / "resume-055-once":
        raise base.BaselineError("one_time_resume_output_scope_forbidden")
    base.h2.configure()
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
