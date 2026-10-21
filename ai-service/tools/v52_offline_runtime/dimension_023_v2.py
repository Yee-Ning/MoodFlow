"""Report-54: West-approved 23-case v2 subset, retrieval only, no generation."""

from collections import Counter
from copy import deepcopy
import hashlib
import json
import sys

from tools.v52_offline_runtime import dimension_034_v2 as v2


batch, base = v2.batch, v2.batch.base
SOURCE = v2.OUTPUT
OUTPUT = SOURCE.parent / "h1-dimension-prompt-023-v2-20261019"
CASE_IDS = tuple("rag-v0-" + i for i in "001 002 003 004 008 009 024 026 034 036 037 039 040 050 051 054 057 058 062 063 064 065 066".split())
EXCLUDED = frozenset("rag-v0-" + i for i in "006 007 023 049 052 053 055 056 059 060 061".split())
ENGLISH_GOLD = frozenset({"parent-22eb9a7aa7d6e6e1138330ec", "parent-473c367bbbf7c2e105fb6534", "parent-f0c08762a7823c85fdcf2a93"})
AUTHORIZATION = {
    "approved_by": "West", "scope": "report54 approved23 only;46 paired retrieval records; no generation",
    "case_ids": list(CASE_IDS), "excluded_case_ids": sorted(EXCLUDED),
    "065": "A in both arms:2004 three-tier year condition only",
    "055_060_exceptions_consumed": 0, "generation_calls": 0,
    "no_extrapolation_to_34": True,
}


def protected_hashes():
    pins = v2.protected_hashes()
    manifest = SOURCE / "artifact-sha256.json"
    source_pins = base.read_json(manifest)["files"]
    for name, digest in source_pins.items():
        if base.sha256(base.ROOT / name) != digest:
            raise base.BaselineError("subset_v2_source_drift:" + name)
    expected = {name for name in source_pins if name.startswith(SOURCE.relative_to(base.ROOT).as_posix() + "/")}
    expected.add(manifest.relative_to(base.ROOT).as_posix())
    actual = {p.relative_to(base.ROOT).as_posix() for p in SOURCE.rglob("*") if p.is_file()}
    if actual != expected:
        raise base.BaselineError("subset_v2_source_population_drift")
    return {**pins, **source_pins, manifest.relative_to(base.ROOT).as_posix(): base.sha256(manifest)}


def load_source():
    protected = protected_hashes()
    inputs, bundles, _, _ = batch.load_source()
    config, rerank = inputs[4], inputs[5]
    if (config.fusion.rrf_k, config.fusion.overfetch_multiplier, config.fusion.per_leg_top_k,
            config.fusion.dense_weight, config.fusion.lexical_weight, config.selection.broad_document_cap,
            config.selection.source_cap, rerank.maximum_candidates, rerank.top_k) != (60, 2, 80, 1, 1, 4, 5, 40, 12):
        raise base.BaselineError("subset_numeric_parameters_drift")
    review = base.read_json(SOURCE / "semantic-review.json")
    accepted = {cid for cid, item in review["cases"].items() if item["passed"]}
    if len(CASE_IDS) != 23 or len(set(CASE_IDS)) != 23 or set(CASE_IDS) != accepted or set(CASE_IDS) & EXCLUDED:
        raise base.BaselineError("subset_approved_population_drift")
    _, resolver = base.registry_components(inputs[-1])
    paired, provenance = {}, {}
    for cid in CASE_IDS:
        case, old, old_plan, condition = bundles[cid]
        path = SOURCE / "new" / (cid + ".json")
        checked, new = review["cases"][cid], base.read_json(path)
        if (checked.get("reviewer", review.get("reviewer")) != "Codex" or not checked.get("automatic_passed")
                or not checked.get("semantic_passed") or checked["new_sha256"] != base.sha256(path)):
            raise base.BaselineError("subset_review_missing_or_stale:" + cid)
        old_path = SOURCE / "old" / (cid + ".json")
        if base.read_json(old_path) != old:
            raise base.BaselineError("subset_old_not_frozen:" + cid)
        if base.validate_stage(new["expansion_raw"], old, "expansion", resolver)[0] != new["queries"]:
            raise base.BaselineError("subset_constraint_replay_drift:" + cid)
        allowed = {"queries", "expansion_raw", "expansion_checks", "expansion_call_number", "status"}
        if not set(old) <= set(new) or set(new) - set(old) - allowed or any(new[k] != old[k] for k in old if k not in allowed):
            raise base.BaselineError("subset_changed_beyond_expansion:" + cid)
        if old_plan["candidate_top_k"] != 40 or old_plan["final_top_k"] != 12 or not old_plan["needs_rerank"]:
            raise base.BaselineError("subset_plan_parameter_drift")
        plan = deepcopy(old_plan)
        for variant, text in zip(plan["queries"][:3], new["queries"], strict=True):
            variant["text"] = text
        plan["plan_id"] = "query-plan:" + hashlib.sha256(json.dumps(plan, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
        paired[cid] = {"old": (case, old, old_plan, deepcopy(condition)), "new": (case, new, plan, deepcopy(condition))}
        provenance[cid] = {arm: {"path": p.relative_to(base.ROOT).as_posix(), "sha256": base.sha256(p)}
                           for arm, p in (("old", old_path), ("new", path))}
    if Counter(arms["old"][0]["answerability"] for arms in paired.values()) != {"full": 14, "partial": 9}:
        raise base.BaselineError("subset_label_population_drift")
    return inputs, paired, provenance, protected


def run(guard):
    inputs, paired, provenance, protected = load_source()
    if OUTPUT.exists():
        raise base.BaselineError("subset_retrieval_already_attempted")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_bytes(b"* -text\n")
    projection = batch.repair.overlay_projection(inputs[3])
    config, binding = inputs[4], inputs[-1]
    state = {"status": "loading_pinned_local_models", "started_at": batch.h1.now(),
             "case_ids": list(CASE_IDS), "arms": ["old", "new"], "expected_records": 46,
             "completed_records": 0, "protected_before": protected, "authorization": AUTHORIZATION,
             "generation_calls": 0, "Qdrant_write_calls": 0, "055_060_exceptions_consumed": 0}
    base.write_json(OUTPUT / "run_manifest.json", state)
    base.write_json(OUTPUT / "authorization.json", AUTHORIZATION)
    base.write_json(OUTPUT / "input-provenance.json", provenance)
    base.write_json(OUTPUT / "analysis-protocol.json", {
        "created_at": batch.h1.now(), "population": "approved23 full14/partial9 only; no34-case inference",
        "metric_aggregation": "per-case macro mean; R@40 cuts Child ranks before Parent dedup",
        "pattern": "R@40 delta>0 and R@12 delta==0; denominator23 and denominatorR@40-improved",
        "english_definition": "Parent body >=40 ASCII letters and Latin/(Latin+Han)>=0.8; manually read before retrieval",
        "english_gold_parents": sorted(ENGLISH_GOLD), "english_gold_case_count": 2,
        "english_rank_unit": "best Child rank in full observed seven-leg RRF union; missing is censored",
        "route_decision": "Use paired R@40/MRR net deltas, per-case tradeoffs and R@12; descriptive only for these23",
    })
    base.write_json(OUTPUT / "paired-plans.json", {
        cid: {arm: {"plan": b[2], "condition": b[3]} for arm, b in arms.items()} for cid, arms in paired.items()})
    audit, rows, before, error, after_error = base.h2.RequestAudit(), [], None, None, None
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {"embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        with base.target_scope(), audit.installed():
            before = base.inventory(projection)
            if before["payload_sha256"] != batch.OVERLAY_SHA:
                raise base.BaselineError("subset_live_overlay_sha_drift")
            base.write_json(OUTPUT / "collection.before.json", before)
            with (OUTPUT / "paired-cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                for cid in CASE_IDS:
                    for arm in ("old", "new"):
                        case, query, plan, condition = paired[cid][arm]
                        audit.active, audit.expected_filter, audit.expected_lexical = cid, condition["effective_qdrant_filter"], query["queries"]
                        offset = len(audit.requests)
                        row = base.h2.collect_case({**case, "control_legs": base.v51.expected_legs(query)}, query, plan,
                                                   condition, projection, config, binding, audit)
                        row.update(arm=arm, qdrant_requests=audit.requests[offset:])
                        if not row["healthy"] or row["metrics"] != base.independently_score(case, row):
                            raise base.BaselineError("subset_unhealthy_or_scoring_mismatch:" + cid)
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
                if after["payload_sha256"] != batch.OVERLAY_SHA or before != after:
                    raise base.BaselineError("subset_collection_changed")
            except BaseException as exc:
                after_error = type(exc).__name__
        unchanged = protected == protected_hashes()
        complete = len(rows) == 46 and error is None and after_error is None and unchanged and not audit.safety_events and not guard.denied
        state.update(status="complete" if complete else "incomplete", finished_at=batch.h1.now(), completed_records=len(rows),
                     error_code=error, inventory_error=after_error, protected_unchanged=unchanged,
                     safety_events=audit.safety_events, guard_denied=guard.denied)
        base.write_json(OUTPUT / "run_manifest.json", state)
        base.write_json(OUTPUT / "qdrant-requests.json", audit.requests)
    print(json.dumps({"status": state["status"], "completed": len(rows), "error_code": error}), flush=True)
    return 0 if complete else 1


def main():
    base.h2.configure()
    if sys.argv[1:] != ["run"]:
        raise ValueError("subset_only_allows_single_frozen_retrieval_run")
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with batch.single.anonymous_local_models():
        return run(guard)


if __name__ == "__main__":
    raise SystemExit(main())
