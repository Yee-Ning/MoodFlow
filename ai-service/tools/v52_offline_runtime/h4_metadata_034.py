"""Three isolated H4 metadata signals; immutable repaired control, no generation.

Parent metadata comes only from the frozen projection and approved live overlay.
Sibling entity IDs are unioned before filtering: a Child is not a Parent.
Gold is consulted only after rules are fixed, for the loss veto and scoring.
"""

from collections import Counter, defaultdict
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import statistics
import sys
from unittest.mock import patch
from urllib.parse import urlparse

from tools.v52_offline_runtime import dimension_034 as batch
from tools.v52_offline_runtime import dimension_023_v2 as previous
from tools.v52_offline_runtime import verify_dimension_034 as independent


base = batch.base
OUTPUT = base.ROOT / "workspace/moodflow-review/h4-metadata-filter-034-20261019"
SIGNALS = ("source_id", "entity", "doc_type")
SOURCE_RULES = {
    "杰威尔": ("jvrmusic.com.tw",),
    "索尼": ("sonymusic.com.tw", "sonymusic.co.jp"),
    "环球音乐日本": ("universal-music.co.jp",),
    "马来亚大学": ("knova.um.edu.my",),
}
TYPE_RULES = {"采访": ("interview",), "访谈": ("interview",),
              "研究摘要": ("academic_paper",), "艺人简介": ("official_profile",),
              "公告": ("official_news",)}
SOURCE_STOP_COUNTS = {"rag-v0-053": 10, "rag-v0-054": 3, "rag-v0-060": 3, "rag-v0-064": 2}
PREREGISTRATION = {
    "approved_by": "West", "cases": 34, "baseline_rerun": False,
    "success": "macro R@40 delta >=0.02 AND zero newly excluded Gold Parents",
    "gold_loss_veto": "any lost Gold invalidates the entire signal; no live retrieval for a vetoed round",
    "single_variable": "one additional Parent metadata predicate, AND frozen effective H2 filter",
    "parent_semantics": "union sibling entity_ids; source_id/source_type must be consistent across siblings",
    "missing_metadata": "pass; known matching pass; known mismatch reject",
    "entity_scope": "frozen recognized entities explicitly named in original question; MATCH_ANY; skip artist:jay_chou",
    "entity_049": "Fantasy and the three songs are all explicitly named; retain all their frozen IDs",
    "source_scope": "publisher domains, all corresponding source IDs; YouTube in 052 is the object, not attribution",
    "doc_type_proxy": "West approved source_type for absent doc_type; interview/academic_paper/official_profile/official_news",
    "055_060": "only existing exact ten-Parent filtered-only exception; no smaller pool or other cases",
    "065": "A, original 2004 three-tier year condition only; broad artist is never re-added",
    "identity_cases": "reuse immutable control trace if no trigger or no eligible Child changes, never rerun control",
    "source_rules": SOURCE_RULES, "type_rules": TYPE_RULES,
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def matches(payload, condition):
    """Qdrant scalar/array MatchAny plus the unchanged H2 year/missing grammar."""
    if condition is None:
        return True
    if "must" in condition:
        return all(matches(payload, c) for c in condition["must"])
    if "should" in condition:
        return any(matches(payload, c) for c in condition["should"])
    if "is_empty" in condition:
        return payload.get(condition["is_empty"]["key"]) in (None, [], "")
    value = payload.get(condition["key"])
    if "match" in condition:
        values = value if isinstance(value, list) else [value]
        return bool(set(values) & set(condition["match"]["any"]))
    bounds = condition["range"]
    return type(value) is int and ("gte" not in bounds or value >= bounds["gte"]) and ("lte" not in bounds or value <= bounds["lte"])


def parent_metadata(projection):
    result = {}
    for p in projection.children.values():
        item = result.setdefault(p["parent_id"], {"source_id": p.get("source_id"), "source_type": p.get("source_type"),
            "entity_ids": set(), "child_ids": [], "source_url": p.get("source_url")})
        if any(item[k] != p.get(k) for k in ("source_id", "source_type", "source_url")):
            raise base.BaselineError("h4_inconsistent_sibling_source_metadata")
        item["entity_ids"].update(p.get("entity_ids") or [])
        item["child_ids"].append(p["chunk_id"])
    return {pid: {**m, "entity_ids": sorted(m["entity_ids"])} for pid, m in result.items()}


def select_signal(question, frozen_entities, signal, parents):
    """No case label, Gold IDs, answer, or retrieval score is an input."""
    if signal == "source_id":
        cues = [c for c in SOURCE_RULES if c in question]
        domains = {d for c in cues for d in SOURCE_RULES[c]}
        targets = sorted({m["source_id"] for m in parents.values()
            if (urlparse(m["source_url"] or "").hostname or "").removeprefix("www.") in domains})
        if cues and not targets:
            raise base.BaselineError("h4_named_source_mapping_empty")
        return {"field": "source_id", "cues": cues, "targets": targets}
    if signal == "entity":
        targets = [e for e in frozen_entities if e != "artist:jay_chou"]
        return {"field": "entity_ids", "cues": targets, "targets": targets}
    if signal == "doc_type":
        cues = [c for c in TYPE_RULES if c in question]
        return {"field": "source_type", "cues": cues, "targets": sorted({t for c in cues for t in TYPE_RULES[c]})}
    raise ValueError("unknown_h4_signal")


def signal_pass(metadata, rule):
    if not rule["targets"]:
        return True
    value = metadata.get(rule["field"])
    if value in (None, [], ""):
        return True
    values = value if isinstance(value, list) else [value]
    return bool(set(values) & set(rule["targets"]))


def build_variant(case, query, plan, condition, signal, projection, parents):
    rule = select_signal(case["question"], query["constraints"]["entity_ids"], signal, parents)
    allowed = sorted(pid for pid, meta in parents.items() if signal_pass(meta, rule))
    before = {cid for cid, p in projection.children.items() if matches(p, condition["effective_qdrant_filter"])}
    after = {cid for cid in before if projection.children[cid]["parent_id"] in set(allowed)}
    changed = before != after
    new_plan, new_condition = deepcopy(plan), deepcopy(condition)
    if changed:
        current = condition["effective_qdrant_filter"]
        new_condition["effective_qdrant_filter"] = {"must": ([current] if current else []) + [
            {"key": "parent_id", "match": {"any": allowed}}]}
        new_plan["plan_id"] = "query-plan:" + digest([plan, signal, rule, allowed])[:24]
        new_condition["eligible_children"] = len(after)
    before_parents = {projection.children[c]["parent_id"] for c in before}
    after_parents = {projection.children[c]["parent_id"] for c in after}
    # Only the veto, never the targeting rule, can see Gold.
    gold = set(case["relevant_parent_ids"])
    lost = sorted(gold & before_parents - after_parents)
    newly_killed = sorted(pid for pid in gold if not signal_pass(parents[pid], rule))
    check = {"case_id": case["case_id"], "rule": rule, "triggered": bool(rule["targets"]), "changed": changed,
        "eligible_children_before": len(before), "eligible_children_after": len(after),
        "eligible_parents_before": len(before_parents), "eligible_parents_after": len(after_parents),
        "gold_parent_count": len(gold), "newly_excluded_gold": lost,
        "signal_rejected_gold_including_preexisting": newly_killed,
        "gold_already_ineligible": sorted(gold - before_parents),
        "allowed_parent_count": len(allowed), "allowed_parent_sha256": digest(allowed)}
    return new_plan, new_condition, check


def protected_hashes():
    pins = previous.protected_hashes()
    for root in (previous.OUTPUT,):
        for p in root.rglob("*"):
            if p.is_file():
                pins[p.relative_to(base.ROOT).as_posix()] = base.sha256(p)
    return pins


def controls(bundles):
    paths = (batch.repair.FINAL / "combined-cases.jsonl", batch.repair.OUTPUT / "cases.jsonl")
    if base.sha256(paths[0]) != batch.repair.FINAL_SHA:
        raise base.BaselineError("h4_baseline_sha_drift")
    rows = {r["case_id"]: r for r in base.read_jsonl(paths[0])}
    fixes = {r["case_id"]: r for r in base.read_jsonl(paths[1])}
    for cid in batch.repair.FIX_IDS:
        rows[cid] = fixes[cid]
    if set(rows) != set(bundles) or len(rows) != 34:
        raise base.BaselineError("h4_control_population_drift")
    provenance = {}
    for cid, row in rows.items():
        case, query, plan, condition = bundles[cid]
        if (not row["healthy"] or row["question"] != case["question"]
                or row["relevant_parent_ids"] != case["relevant_parent_ids"]
                or row["effective_qdrant_filter"] != condition["effective_qdrant_filter"]
                or row["query_understanding"]["queries"] != query["queries"]
                or row["query_understanding"]["hyde"] != query["hyde"]):
            raise base.BaselineError("h4_control_inputs_not_frozen:" + cid)
        if independent.score(case["relevant_parent_ids"], independent.fuse(row["leg_results"]), row["rerank_top12"]) != row["metrics"]:
            raise base.BaselineError("h4_control_independent_score_mismatch")
        path = paths[1 if cid in batch.repair.FIX_IDS else 0]
        provenance[cid] = {"path": path.relative_to(base.ROOT).as_posix(), "sha256": base.sha256(path)}
    return rows, provenance


def prepare():
    if OUTPUT.exists():
        raise base.BaselineError("h4_prepare_already_attempted")
    inputs, bundles, _, _ = batch.load_source()
    projection = batch.repair.overlay_projection(inputs[3])
    parents = parent_metadata(projection)
    control, provenance = controls(bundles)
    protected = protected_hashes()
    rounds = {}
    for signal in SIGNALS:
        variants, checks = {}, []
        for cid, (case, query, plan, condition) in bundles.items():
            new_plan, new_condition, check = build_variant(case, query, plan, condition, signal, projection, parents)
            variants[cid] = {"plan": new_plan, "condition": new_condition}
            checks.append(check)
        rejected = [r for r in checks if r["signal_rejected_gold_including_preexisting"]]
        rounds[signal] = {"status": "gold_veto" if rejected else "preflight_passed", "checks": checks, "variants": variants,
            "gold_loss_pairs": [{"case_id": r["case_id"], "parent_id": p} for r in checks for p in r["signal_rejected_gold_including_preexisting"]],
            "new_gold_loss_pairs": [{"case_id": r["case_id"], "parent_id": p} for r in checks for p in r["newly_excluded_gold"]]}
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_bytes(b"* -text\n")
    base.write_json(OUTPUT / "preregistration.json", {**PREREGISTRATION, "recorded_at": batch.h1.now()})
    base.write_json(OUTPUT / "preflight.json", rounds)
    base.write_json(OUTPUT / "control-provenance.json", provenance)
    base.write_json(OUTPUT / "parent-metadata.json", parents)
    base.write_json(OUTPUT / "preparation.json", {"prepared_at": batch.h1.now(), "protected_before": protected,
        "frozen_198_count": len(batch.assert_frozen_198()), "control_rerun": False,
        "control_metrics": {m: statistics.mean(r["metrics"][m] for r in control.values()) for m in independent.METRICS},
        "child_count": len(projection.children), "parent_count": len(parents), "overlay_sha256": batch.OVERLAY_SHA})
    for signal, r in rounds.items():
        print(json.dumps({"signal": signal, "status": r["status"], "gold_loss": r["gold_loss_pairs"],
            "triggers": [{k: c[k] for k in ("case_id", "rule", "changed", "eligible_parents_after")} for c in r["checks"] if c["triggered"]]}, ensure_ascii=False), flush=True)


def seal():
    """Freeze reviewed preflight before the first retrieval, not after seeing scores."""
    if (OUTPUT / "seal.json").exists() or any(OUTPUT.glob("*/run_manifest.json")):
        raise base.BaselineError("h4_protocol_already_sealed_or_started")
    prep = base.read_json(OUTPUT / "preparation.json")
    if prep["protected_before"] != protected_hashes():
        raise base.BaselineError("h4_protected_drift_before_seal")
    # A pre-retrieval wording correction: 049 really names Fantasy in the frozen question.
    base.write_json(OUTPUT / "preregistration.json", {**PREREGISTRATION, "recorded_at": batch.h1.now(),
        "pre_retrieval_correction": "Removed draft wording that mistakenly called Fantasy inferred in049; actual049 filter never excluded it."})
    paths = [OUTPUT / n for n in ("preregistration.json", "preflight.json", "preparation.json", "parent-metadata.json", "control-provenance.json")]
    base.write_json(OUTPUT / "seal.json", {"sealed_at": batch.h1.now(),
        "sha256": {p.relative_to(base.ROOT).as_posix(): base.sha256(p) for p in paths},
        "code_sha256": base.sha256(Path(__file__))})
    print(json.dumps({"sealed": True, "retrieval_started": False}), flush=True)


def load_prepared():
    sealed = base.read_json(OUTPUT / "seal.json")
    if sealed["code_sha256"] != base.sha256(Path(__file__)):
        raise base.BaselineError("h4_runner_changed_after_seal")
    for name, sha in sealed["sha256"].items():
        if base.sha256(base.ROOT / name) != sha:
            raise base.BaselineError("h4_sealed_input_drift:" + name)
    prep = base.read_json(OUTPUT / "preparation.json")
    if prep["protected_before"] != protected_hashes():
        raise base.BaselineError("h4_protected_input_drift")
    inputs, bundles, _, _ = batch.load_source()
    config, rerank = inputs[4], inputs[5]
    if (config.fusion.rrf_k, config.fusion.overfetch_multiplier, config.fusion.per_leg_top_k,
            config.fusion.dense_weight, config.fusion.lexical_weight, config.selection.broad_document_cap,
            config.selection.source_cap, rerank.maximum_candidates, rerank.top_k) != (60, 2, 80, 1, 1, 4, 5, 40, 12):
        raise base.BaselineError("h4_numeric_parameter_drift")
    if any(p["candidate_top_k"] != 40 or p["final_top_k"] != 12 for _, _, p, _ in bundles.values()):
        raise base.BaselineError("h4_plan_numeric_drift")
    control, provenance = controls(bundles)
    if provenance != base.read_json(OUTPUT / "control-provenance.json"):
        raise base.BaselineError("h4_control_provenance_drift")
    projection = batch.repair.overlay_projection(inputs[3])
    parents = parent_metadata(projection)
    rounds = base.read_json(OUTPUT / "preflight.json")
    for signal in SIGNALS:
        for check in rounds[signal]["checks"]:
            cid = check["case_id"]
            c, q, p, f = bundles[cid]
            np, nf, nc = build_variant(c, q, p, f, signal, projection, parents)
            if nc != check or rounds[signal]["variants"][cid] != {"plan": np, "condition": nf}:
                raise base.BaselineError("h4_preflight_not_reproducible")
    return inputs, bundles, projection, control, rounds, prep


class SourceException:
    """Dormant unless a separate explicit West authorization is recorded."""

    def __init__(self, signal, directory):
        self.original = base.h2.intercept_fallback
        self.directory, self.events = directory, []
        self.authorized = False
        path = OUTPUT / "source-safety-authorization.json"
        if signal == "source_id" and path.exists():
            document = base.read_json(path)
            expected = {"approved_by": "West", "signal": "source_id", "exact_counts": SOURCE_STOP_COUNTS,
                        "unfiltered_backfill": False, "scope": "this H4 source_id round only"}
            if document != expected:
                raise base.BaselineError("h4_source_exception_authorization_scope_drift")
            self.authorized = True

    def __call__(self, plan, count, config, predicate, audit):
        if not self.authorized or audit.active not in SOURCE_STOP_COUNTS:
            return self.original(plan, count, config, predicate, audit)
        if (count != SOURCE_STOP_COUNTS[audit.active] or not predicate(plan, count, config)
                or config.selection.exploratory_entity_fallback_below != 12
                or plan.index_profile.collection_name != base.COLLECTION
                or any(e["case_id"] == audit.active for e in self.events)):
            raise base.h2.SafetyStop("h4_source_exception_count_or_scope_drift")
        event = {"case_id": audit.active, "selection_count": count, "threshold": 12,
            "approved_by": "West", "signal": "source_id", "unfiltered_request_sent": False,
            "event": "separately_authorized_source_round_filtered_rerank_only", "H2_default_changed": False}
        self.events.append(event)
        base.write_json(self.directory / (audit.active + ".exception-consumed.json"), event)
        return False


def run(signal, guard):
    if signal not in SIGNALS:
        raise ValueError("unknown_h4_signal")
    directory = OUTPUT / signal
    if directory.exists():
        raise base.BaselineError("h4_round_already_attempted")
    inputs, bundles, projection, control, rounds, prep = load_prepared()
    round_data = rounds[signal]
    if round_data["gold_loss_pairs"]:
        raise base.BaselineError("h4_gold_loss_veto_no_retrieval")
    once = SourceException(signal, directory)
    if signal == "source_id" and not once.authorized:
        raise base.BaselineError("h4_source_round_requires_separate_safety_authorization")
    directory.mkdir(exist_ok=False)
    state = {"status": "loading_pinned_models", "signal": signal, "started_at": batch.h1.now(),
        "completed_cases": 0, "live_cases": 0, "reused_control_cases": 0, "baseline_rerun": False,
        "Qdrant_write_calls": 0, "expansion_generation_calls": 0, "new_gold_loss_pairs": [],
        "existing_exception_scope_expanded": False}
    base.write_json(directory / "run_manifest.json", state)
    audit, before, error, after_error, rows = base.h2.RequestAudit(), None, None, None, []
    checks = {r["case_id"]: r for r in round_data["checks"]}
    try:
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        state["runtime"] = {"embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        with (base.target_scope(), audit.installed(), patch.object(base.h2, "matches", matches),
                patch.object(base.h2, "intercept_fallback", once)):
            before = base.inventory(projection)
            if before["payload_sha256"] != batch.OVERLAY_SHA:
                raise base.BaselineError("h4_live_overlay_drift")
            base.write_json(directory / "collection.before.json", before)
            with (directory / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                for cid, (case, query, old_plan, old_condition) in bundles.items():
                    variant = round_data["variants"][cid]
                    if not checks[cid]["changed"]:
                        row = {"case_id": cid, "signal": signal, "execution": "reuse_immutable_control", "metrics": control[cid]["metrics"]}
                        state["reused_control_cases"] += 1
                    else:
                        condition = variant["condition"]
                        audit.active, audit.expected_filter, audit.expected_lexical = cid, condition["effective_qdrant_filter"], query["queries"]
                        offset = len(audit.requests)
                        row = base.h2.collect_case({**case, "control_legs": base.v51.expected_legs(query)}, query,
                            variant["plan"], condition, projection, inputs[4], inputs[-1], audit)
                        row.update(signal=signal, execution="live_signal_retrieval", qdrant_requests=audit.requests[offset:])
                        if not row["healthy"] or row["metrics"] != base.independently_score(case, row):
                            raise base.BaselineError("h4_unhealthy_or_score_mismatch:" + cid)
                        state["live_cases"] += 1
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    rows.append(row)
                    state.update(status="running", completed_cases=len(rows), last_case_id=cid)
                    base.write_json(directory / "run_manifest.json", state)
                    print(json.dumps({"signal": signal, "case_id": cid, "execution": row["execution"], "completed": len(rows), "metrics": row["metrics"]}), flush=True)
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (base.v51.BaselineError, base.h2.H2Error, base.h2.SafetyStop)) else type(exc).__name__
    finally:
        audit.active = None
        if before is not None and not guard.denied:
            try:
                with base.target_scope(), audit.installed():
                    after = base.inventory(projection)
                base.write_json(directory / "collection.after.json", after)
                if before != after:
                    raise base.BaselineError("h4_collection_changed")
            except BaseException as exc:
                after_error = type(exc).__name__
        unchanged = prep["protected_before"] == protected_hashes()
        complete = len(rows) == 34 and error is None and after_error is None and unchanged and not audit.safety_events and not guard.denied
        state.update(status="complete" if complete else "incomplete", finished_at=batch.h1.now(),
            error_code=error, inventory_error=after_error, protected_unchanged=unchanged,
            safety_events=audit.safety_events, guard_denied=guard.denied)
        state["separate_source_exceptions"] = once.events
        base.write_json(directory / "run_manifest.json", state)
        base.write_json(directory / "qdrant-requests.json", audit.requests)
    print(json.dumps({"signal": signal, "status": state["status"], "completed": len(rows), "error_code": error}), flush=True)
    return 0 if complete else 1


def main():
    base.h2.configure()
    if sys.argv[1:] == ["prepare"]:
        prepare()
        return 0
    if sys.argv[1:] == ["seal"]:
        seal()
        return 0
    if len(sys.argv) != 3 or sys.argv[1] != "run" or sys.argv[2] not in SIGNALS:
        raise ValueError("h4_only_prepare_seal_or_one_signal_run")
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = base.h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    with batch.single.anonymous_local_models():
        return run(sys.argv[2], guard)


if __name__ == "__main__":
    raise SystemExit(main())
