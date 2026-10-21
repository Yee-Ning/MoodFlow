"""West-approved four-point payload repair verification, isolated from H2 defaults.

This tool cannot write Qdrant. It verifies the exact approved live overlay,
reuses frozen expansion/model settings, and runs only three cases plus five
predeclared regression cases. It never changes the frozen release projection.
"""

from __future__ import annotations

from copy import deepcopy
import json
import sys
from unittest.mock import patch

from tools.v52_offline_runtime import resume_055_once as prior
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


base = prior.base
OUTPUT = prior.SOURCE.parent.parent / "v5-2-payload-fix-20261019"
FINAL = prior.SOURCE.parent / "final-baseline-060-once"
FINAL_SHA = "e2fbeb4e898acf3991c29cdc817eca1bc31b0fad100441c4578f41974bda0115"
PATCH_POINTS = {
    "child-ff53fe46bd60d3394422527f": "5ae5e5cc-2bda-52e5-82c3-8be325010964",
    "child-28193c8e5516053a76c2db0f": "1be6f9a1-10f1-5853-bc6e-2677db42d151",
    "child-7c634105b6e82e43d9ee8e65": "434463e5-a992-5f86-8783-f9e212fdcc46",
    "child-111b7a5bdedb0c2e732b88d5": "4104da15-68bd-5094-9dad-c091efabcfbe",
}
FIX_IDS = ("rag-v0-055", "rag-v0-060", "rag-v0-065")
REGRESSION_IDS = tuple("rag-v0-" + n for n in ("054", "002", "036", "061", "066"))
RUN_IDS = FIX_IDS + REGRESSION_IDS
EXCEPTIONS = {
    "rag-v0-055": "query-plan:144e8aaeefffae82c271efe4",
    "rag-v0-060": "query-plan:ade76be1fa57214a9999aaf7",
}
METRICS = ("recall_at_40", "recall_at_12", "precision_at_12", "mrr")


def overlay_projection(original):
    result = deepcopy(original)
    for child, point in PATCH_POINTS.items():
        if result.points.get(point) != child:
            raise base.BaselineError("repair_child_point_identity_drift")
        ids = result.children[child]["entity_ids"]
        if "album:capricorn" in ids:
            raise base.BaselineError("frozen_original_already_contains_album")
        result.children[child]["entity_ids"] = [*ids, "album:capricorn"]
    return result


def variant_065(plan, condition):
    plan, condition = deepcopy(plan), deepcopy(condition)
    if (
        condition["case_id"] != "rag-v0-065"
        or condition["entity_ids"] != ["artist:jay_chou"]
        or condition["year_range"] != [2004, 2004]
        or condition["effective_qdrant_filter"]["must"][0]
        != {"key": "entity_ids", "match": {"any": ["artist:jay_chou"]}}
    ):
        raise base.BaselineError("065_original_filter_drift")
    root = plan["metadata_filter"]["root"]
    if len(root["children"]) != 2 or root["children"][0]["field"] != "entity_ids":
        raise base.BaselineError("065_original_ast_drift")
    root["children"] = root["children"][1:]
    condition["entity_ids"] = []
    condition["effective_qdrant_filter"]["must"] = condition[
        "effective_qdrant_filter"
    ]["must"][1:]
    condition["eligible_children"] = 901
    if "intermediate_ast" in condition:
        condition["intermediate_ast"] = deepcopy(plan["metadata_filter"])
    return plan, condition


def load_source():
    inputs, _, queries, plans, filters, _ = prior.load_source()
    if base.sha256(FINAL / "combined-cases.jsonl") != FINAL_SHA:
        raise base.BaselineError("repair_final_baseline_digest_drift")
    previous = {r["case_id"]: r for r in base.read_jsonl(FINAL / "combined-cases.jsonl")}
    if len(previous) != 34 or not all(r["healthy"] for r in previous.values()):
        raise base.BaselineError("repair_baseline_population_drift")
    cases = inputs[0]
    bundles = {}
    for case, query, plan, condition in zip(cases, queries, plans, filters, strict=True):
        case_id = case["case_id"]
        if case_id != query["case_id"] or case_id != condition["case_id"]:
            raise base.BaselineError("repair_frozen_case_order_drift")
        if case_id not in RUN_IDS:
            continue
        if case_id == "rag-v0-065":
            plan, condition = variant_065(plan, condition)
        bundles[case_id] = (case, query, plan, condition)
    if set(bundles) != set(RUN_IDS):
        raise base.BaselineError("repair_eight_case_scope_drift")
    return inputs, bundles, previous


class FilteredRepairOnce:
    """Only the two repaired ten-Parent pools may pass the stop, once each."""

    def __init__(self, output):
        self.output = output
        self.original = base.h2.intercept_fallback
        self.events = []

    def __call__(self, plan, count, config, predicate, audit):
        if audit.active not in EXCEPTIONS:
            return self.original(plan, count, config, predicate, audit)
        if (
            not predicate(plan, count, config)
            or count != 10
            or config.selection.exploratory_entity_fallback_below != 12
            or plan.plan_id != EXCEPTIONS[audit.active]
            or plan.index_profile.collection_name != base.COLLECTION
        ):
            raise base.h2.SafetyStop("repair_filtered_once_scope_drift")
        path = self.output / (audit.active + ".repair-exception-consumed.json")
        if path.exists():
            raise base.h2.SafetyStop("repair_filtered_once_already_consumed")
        event = {
            "case_id": audit.active,
            "selection_count": count,
            "threshold": 12,
            "plan_id": plan.plan_id,
            "approved_by": "West",
            "consumed_at": base.h1.now(),
            "event": "repair_validation_filtered_rerank_only_once",
            "unfiltered_request_sent": False,
            "H2_default_changed": False,
        }
        with path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, indent=2) + "\n")
        self.events.append(event)
        return False


def comparisons(records, previous):
    return [
        {
            "case_id": row["case_id"],
            "group": "fix" if row["case_id"] in FIX_IDS else "regression",
            "before": {k: previous[row["case_id"]]["metrics"][k] for k in METRICS},
            "after": {k: row["metrics"][k] for k in METRICS},
            "scores_unchanged": all(
                row["metrics"][k] == previous[row["case_id"]]["metrics"][k]
                for k in METRICS
            ),
        }
        for row in records
    ]


def run(guard):
    inputs, bundles, previous = load_source()
    _, _, _, original, config, _, binding = inputs
    projection = overlay_projection(original)
    if (OUTPUT / "run_manifest.json").exists():
        raise base.BaselineError("repair_verification_already_attempted_no_repeat")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    protected = prior.protected_hashes()
    state = {
        "status": "loading_pinned_local_models",
        "started_at": base.h1.now(),
        "case_ids": list(RUN_IDS),
        "065_choice": "A: original three-tier year filter only",
        "frozen_baseline_replaced": False,
        "protected_before": protected,
        "qdrant_write_calls": 0,
        "new_expansion_calls": 0,
    }
    base.write_json(OUTPUT / "run_manifest.json", state)
    base.write_json(OUTPUT / "evaluation-filter-variants.json", {
        cid: {"plan": bundle[2], "condition": bundle[3]}
        for cid, bundle in bundles.items()
    })
    audit, once, records, error, after_error = (
        base.h2.RequestAudit(), FilteredRepairOnce(OUTPUT), [], None, None
    )
    before = None
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {
            "embedding_device": encoder._encoder._device,
            "reranker_device": scorer._device,
        }
        with base.target_scope(), audit.installed(), patch.object(
            base.h2, "intercept_fallback", once
        ):
            before = base.inventory(projection)
            base.write_json(OUTPUT / "target.after-patch.before-retrieval.json", before)
            with (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                for cid in RUN_IDS:
                    case, query, plan, condition = bundles[cid]
                    audit.active = cid
                    audit.expected_filter = condition["effective_qdrant_filter"]
                    audit.expected_lexical = query["queries"]
                    offset = len(audit.requests)
                    row = base.h2.collect_case(
                        {**case, "control_legs": base.v51.expected_legs(query)},
                        query, plan, condition, projection, config, binding, audit,
                    )
                    row["qdrant_requests"] = audit.requests[offset:]
                    if not row["healthy"]:
                        raise base.BaselineError("repair_unhealthy_case:" + cid)
                    if row["metrics"] != base.independently_score(case, row):
                        raise base.BaselineError("repair_independent_scoring_mismatch")
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    records.append(row)
                    state.update(status="running", completed=len(records), last_case_id=cid)
                    base.write_json(OUTPUT / "run_manifest.json", state)
                    print(json.dumps({"case_id": cid, "completed": len(records),
                                      "metrics": row["metrics"]}), flush=True)
    except BaseException as exc:
        error = str(exc) if isinstance(
            exc, (base.v51.BaselineError, base.h2.H2Error, base.h2.SafetyStop)
        ) else type(exc).__name__
    finally:
        audit.active = None
        if before is not None and not guard.denied:
            try:
                with base.target_scope(), audit.installed():
                    after = base.inventory(projection)
                base.write_json(OUTPUT / "target.after-retrieval.json", after)
                if before["payload_sha256"] != after["payload_sha256"]:
                    raise base.BaselineError("repair_payload_changed_during_retrieval")
            except BaseException as exc:
                after_error = type(exc).__name__
        comparison = comparisons(records, previous)
        complete = (
            len(records) == 8 and error is None and after_error is None
            and not guard.denied and not audit.safety_events
            and {e["case_id"] for e in once.events} == set(EXCEPTIONS)
            and protected == prior.protected_hashes()
        )
        state.update(
            status="complete" if complete else "incomplete",
            finished_at=base.h1.now(), completed=len(records), error_code=error,
            inventory_error=after_error, exception_events=once.events,
            safety_events=audit.safety_events, guard_denied=guard.denied,
            protected_unchanged=protected == prior.protected_hashes(),
            gold_sha256_after={n: base.sha256(base.GOLD.parent / n) for n in base.GOLD_SHAS},
        )
        base.write_json(OUTPUT / "run_manifest.json", state)
        base.write_json(OUTPUT / "qdrant-retrieval-requests.json", audit.requests)
        base.write_json(OUTPUT / "score-comparison.json", comparison)
    print(json.dumps({"status": state["status"], "completed": len(records),
                      "error_code": error, "inventory_error": after_error}), flush=True)
    return 0 if complete else 1


def main():
    if len(sys.argv) != 1:
        raise base.BaselineError("repair_verification_no_scope_overrides")
    base.h2.configure()
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
