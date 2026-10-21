"""One Parent-aware fusion experiment over frozen seven-leg Child rankings."""

import asyncio
from collections import defaultdict
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys

from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
from app.rag.retrieval.config import RetrievalConfig
from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
from app.rag.retrieval.legs import RetrievalLeg
from app.rag.retrieval.models import RetrievalResult, RetrievalStatus
from app.rag.retrieval.selection import select_candidates
from app.rag.reranking.config import RerankConfig
from app.rag.reranking.runtime import QwenRerankScorer
from app.rag.reranking.service import QwenCandidateReranker
from evals.ragas_gate0 import Gate0Guard, configure_offline
from tools.v52_offline_runtime import timeline_gate0 as frozen
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


ROOT = frozen.ROOT
OUTPUT = ROOT / "workspace/moodflow-review/timeline-gate1-frrf-20261019"
PREVIOUS = ROOT / "workspace/moodflow-review/rerank-034-20261019"
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_3.frozen.jsonl"
FREEZE = ROOT / "workspace/moodflow-review/gold-v5-3-review-20261019/freeze-manifest.json"
PATCH = ROOT / "workspace/moodflow-review/v5-2-payload-fix-20261019/approved-payload-patch.json"
ALPHA = 0.3
SENSITIVITY = (0.1, 0.5)
METRICS = ("recall_at_40", "recall_at_12", "precision_at_12", "mrr")


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def check_pins(pins):
    for name, expected in pins.items():
        if frozen.sha(ROOT / name) != expected:
            raise ValueError("frrf_protected_source_drift:" + name)


def parent_fusion(children, alpha):
    if not math.isfinite(alpha) or not 0 <= alpha <= 1:
        raise ValueError("frrf_alpha_invalid")
    if len({hit.point_id for hit in children}) != len(children):
        raise ValueError("frrf_duplicate_child")
    groups = defaultdict(list)
    for hit in children:
        parent = hit.payload.get("parent_id")
        if not isinstance(parent, str) or not parent:
            raise ValueError("frrf_missing_parent")
        if not math.isfinite(hit.fusion_score) or hit.fusion_score < 0:
            raise ValueError("frrf_child_score_invalid")
        groups[parent].append(hit)
    representatives, audit = [], {}
    for parent, hits in groups.items():
        ranked = sorted(hits, key=lambda hit: (-hit.fusion_score, hit.point_id))
        best, others = ranked[0], ranked[1:]
        tail = math.fsum(hit.fusion_score for hit in others)
        score = best.fusion_score + alpha * tail
        representatives.append(replace(best, fusion_score=score))
        audit[parent] = {"parent_id": parent, "best_point_id": best.point_id,
            "best_child_id": best.payload["chunk_id"], "best_child_score": best.fusion_score,
            "other_child_score_sum": tail, "parent_score": score, "observed_child_count": len(ranked),
            "children": [{"point_id": hit.point_id, "child_id": hit.payload["chunk_id"],
                          "child_score": hit.fusion_score} for hit in ranked]}
    representatives.sort(key=lambda hit: (-hit.fusion_score, hit.point_id))
    return representatives, [{**audit[hit.payload["parent_id"]], "parent_rank": rank}
                             for rank, hit in enumerate(representatives, 1)]


def reconstruct(control, payloads):
    if tuple(leg["leg_id"] for leg in control["leg_results"]) != frozen.EXPECTED_LEGS:
        raise ValueError("frrf_seven_legs_required")
    results = []
    for saved in control["leg_results"]:
        if saved["error_code"] is not None or saved["limit"] != 80 or saved["weight"] != 1:
            raise ValueError("frrf_frozen_leg_drift")
        leg = RetrievalLeg(variant_index=saved["variant_index"], variant_kind=QueryVariantKind(saved["variant_kind"]),
                           channel=SearchChannel(saved["channel"]), text=saved["text"], weight=saved["weight"])
        hits = []
        for saved_hit in saved["hits"]:
            payload = payloads[saved_hit["point_id"]]
            if payload["parent_id"] != saved_hit["parent_id"] or payload["chunk_id"] != saved_hit["child_id"]:
                raise ValueError("frrf_child_identity_drift")
            if not frozen.matches(payload, control["effective_qdrant_filter"]):
                raise ValueError("frrf_saved_hit_violates_H2")
            hits.append(RawSearchHit(saved_hit["point_id"], saved_hit["raw_score"], payload))
        results.append(LegSearchResult(leg=leg, hits=hits))
    children = weighted_rrf(results, k=60)
    for field, limit in (("rrf_top40", 40), ("selection_input_rrf", 80)):
        if [hit.point_id for hit in children[:limit]] != [hit["point_id"] for hit in control[field]]:
            raise ValueError("frrf_control_order_drift")
    return children


def recall(gold, ranking):
    relevant = set(gold)
    if not relevant or len(relevant) != len(gold):
        raise ValueError("frrf_gold_population_invalid")
    return len(relevant & {hit["parent_id"] for hit in ranking[:40]}) / len(relevant)


def metrics(gold, ranking, final):
    if len(final) > 12 or len({hit["parent_id"] for hit in final}) != len(final):
        raise ValueError("frrf_final_population_invalid")
    relevant = set(gold)
    first = next((rank for rank, hit in enumerate(final, 1) if hit["parent_id"] in relevant), None)
    hits = len(relevant & {hit["parent_id"] for hit in final})
    return {"recall_at_40": recall(gold, ranking), "recall_at_12": hits / len(relevant),
            "precision_at_12": hits / 12, "mrr": 1 / first if first else 0}


def candidate_row(candidate):
    return {"point_id": candidate.point_id, "parent_id": candidate.parent_id,
            "child_id": candidate.primary_child_id, "doc_id": candidate.doc_id,
            "source_id": candidate.source_id, "fusion_score": candidate.fusion_score,
            "matched_child_ids": candidate.matched_child_ids}


def prepare_case(control, payloads, config, alpha):
    children = reconstruct(control, payloads)
    plan = SearchPlan.model_validate(control["plan"])
    old_candidates, warnings = select_candidates(children[:80], plan, config)
    fields = ("point_id", "parent_id", "child_id", "doc_id", "source_id", "fusion_score", "matched_child_ids")
    if ([candidate_row(item) for item in old_candidates] != [
            {key: item[key] for key in fields} for item in control["selection"]]
            or warnings != control["selection_warnings"]):
        raise ValueError("frrf_frozen_selector_drift")
    parents, audit = parent_fusion(children, alpha)
    selected, warnings = select_candidates(parents[:80], plan, config)
    return plan, parents, audit, selected, warnings


def load_source():
    evidence = frozen.read(frozen.OUTPUT / "evidence.json")
    pins = dict(evidence["source_pins"])
    for directory in (PREVIOUS, frozen.OUTPUT, FREEZE.parent):
        pins.update({path.relative_to(ROOT).as_posix(): frozen.sha(path)
                     for path in directory.rglob("*") if path.is_file()})
    for path in (Path(__file__), Path(__file__).with_name("test_timeline_gate1_frrf.py"),
                 Path(__file__).with_name("verify_timeline_gate1_frrf.py"), GOLD, PATCH):
        pins[path.relative_to(ROOT).as_posix()] = frozen.sha(path)
    check_pins(pins)
    freeze = frozen.read(FREEZE)
    if freeze["status"] != "frozen" or freeze["frozen_by"] != "West" or freeze["sha256"] != frozen.sha(GOLD):
        raise ValueError("frrf_V53_not_frozen")
    preflight = frozen.read(PREVIOUS / "preflight.json")
    controls, cache = {}, {}
    for cid, source in preflight["control_provenance"].items():
        if frozen.sha(ROOT / source["path"]) != source["sha256"]:
            raise ValueError("frrf_control_source_drift")
        if source["path"] not in cache:
            cache[source["path"]] = {row["case_id"]: row for row in frozen.records(ROOT / source["path"])}
        controls[cid] = cache[source["path"]][cid]
    gold = {row["case_id"]: row for row in frozen.records(GOLD) if row["answerability"] in {"full", "partial"}}
    if set(gold) != set(controls) or len(gold) != 34:
        raise ValueError("frrf_34_cases_required")
    updates = {row["point_id"]: row["after"] for row in frozen.read(PATCH)}
    payloads = {}
    for row in frozen.records(frozen.PROJECTION):
        payload = row["payload"]
        if row["point_id"] in updates:
            payload["entity_ids"] = updates[row["point_id"]]
        payloads[row["point_id"]] = payload
    config = RetrievalConfig.model_validate(preflight["retrieval_config"])
    rerank = RerankConfig.model_validate(preflight["rerank_config"])
    for cid, control in controls.items():
        if (not control["healthy"] or control["question"] != gold[cid]["question"]
                or control["relevant_parent_ids"] != gold[cid]["relevant_parent_ids"]):
            raise ValueError("frrf_V53_denominator_or_control_drift")
    return controls, preflight, payloads, config, rerank, pins, evidence


class RecordingScorer:
    def __init__(self, scorer):
        self.scorer, self.inputs, self.scores = scorer, [], []

    def score(self, inputs):
        if self.inputs:
            raise ValueError("frrf_extra_rerank_call")
        self.inputs = list(inputs)
        self.scores = self.scorer.score(inputs)
        return self.scores


def run_case(control, payloads, config, rerank, scorer):
    plan, parents, audit, selected, warnings = prepare_case(control, payloads, config, ALPHA)
    recording = RecordingScorer(scorer)
    pool = RetrievalResult(status=RetrievalStatus.SUCCESS if selected else RetrievalStatus.EMPTY,
                           plan_id=plan.plan_id, index_profile=plan.index_profile, candidates=selected)
    result = asyncio.run(QwenCandidateReranker(rerank, scorer=recording).rerank(plan, pool))
    if result.fallback_used or result.status.value != ("success" if selected else "empty"):
        raise ValueError("frrf_rerank_failed:" + ",".join(result.warnings))
    final = [{**candidate_row(item.candidate), "rerank_score": item.rerank_score,
              "retrieval_rank": item.retrieval_rank} for item in result.candidates]
    scored = [{**candidate_row(candidate), "score": score,
               "query_sha256": hashlib.sha256(item.query.encode()).hexdigest(),
               "document_sha256": hashlib.sha256(item.document.encode()).hexdigest()}
              for candidate, item, score in zip(selected, recording.inputs, recording.scores, strict=True)]
    top40 = [{"point_id": hit.point_id, "parent_id": hit.payload["parent_id"],
              "child_id": hit.payload["chunk_id"], "fusion_score": hit.fusion_score} for hit in parents[:40]]
    gold = set(control["relevant_parent_ids"])
    lost = {stage: sorted((gold & {hit["parent_id"] for hit in control[old]})
                         - {hit["parent_id"] for hit in new})
            for stage, old, new in (("rrf40", "rrf_top40", top40), ("selection", "selection", scored),
                                    ("final12", "rerank_top12", final))}
    return {"case_id": control["case_id"], "question": control["question"], "alpha": ALPHA,
            "plan": control["plan"], "effective_qdrant_filter": control["effective_qdrant_filter"],
            "parent_ranking": audit, "rrf_top40": top40, "selection_warnings": warnings,
            "scored_candidates": scored, "rerank_top12": final, "lost_gold": lost,
            "metrics": metrics(control["relevant_parent_ids"], top40, final),
            "rerank_timings": result.timings.model_dump(mode="json"), "rerank_fallback_used": False,
            "model_calls": int(bool(selected))}


def summarize(controls, results):
    output = {}
    for label, ids in (("timeline5", set(frozen.CASE_IDS)), ("regression29", set(controls) - set(frozen.CASE_IDS)),
                       ("all34", set(controls))):
        old = {key: statistics.mean(controls[cid]["metrics"][key] for cid in ids) for key in METRICS}
        new = {key: statistics.mean(results[cid]["metrics"][key] for cid in ids) for key in METRICS}
        output[label] = {"n": len(ids), "baseline": old, "experiment": new,
                         "delta": {key: new[key] - old[key] for key in METRICS}}
    losses = {stage: [{"case_id": cid, "parent_ids": row["lost_gold"][stage]}
                      for cid, row in results.items() if row["lost_gold"][stage]]
              for stage in ("rrf40", "selection", "final12")}
    passed = output["timeline5"]["delta"]["recall_at_40"] >= 0.1 and output["all34"]["delta"]["recall_at_40"] >= 0
    return {"groups": output, "gold_losses": losses, "metric_thresholds_passed": passed,
            "zero_new_gold_loss": not any(losses.values()), "adopt": passed and not any(losses.values())}


def main():
    if sys.argv[1:] != ["run"] or OUTPUT.exists():
        raise ValueError("frrf_one_new_run_only")
    configure_offline()
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    with anonymous_local_models():
        controls, preflight, payloads, config, rerank, pins, evidence = load_source()
        OUTPUT.mkdir()
        write(OUTPUT / "preregistration.json", {"approved_by": "West", "alpha": ALPHA,
            "formula": "max(child_scores) + alpha * sum(other_child_scores)",
            "length_control": "discount only, not count-normalized; no unapproved formula change",
            "population": "34 full/partial cases, timeline5 plus regression29", "baseline_rerun": False,
            "metric": "unique Gold Parents in first40 units / frozen Gold Parent count; same scorer",
            "selection_input": "first80 Parent representatives, confirmed by user; unchanged 2x overfetch",
            "selector": "unchanged production selector/caps/40 target; best Child only per Parent",
            "sensitivity": {"alphas": SENSITIVITY, "scope": "fusion/R@40 only, no rerank or retuning"},
            "success": "timeline5 R@40 delta>=0.1 AND all34 delta>=0 AND zero new Gold loss at R40/selection/final12",
            "exceptions": "065 filter A;055/060 exact existing filtered inputs; no unfiltered backfill",
            "gold": GOLD.relative_to(ROOT).as_posix(), "gold_sha256": frozen.sha(GOLD),
            "new_retrieval_calls": 0, "new_generation_calls": 0})
        write(OUTPUT / "preflight.json", {"protected_before": pins, "case_ids": list(controls),
            "control_provenance": preflight["control_provenance"], "retrieval_config": preflight["retrieval_config"],
            "rerank_config": preflight["rerank_config"]})
        sensitivity = {}
        for alpha in SENSITIVITY:
            values = {}
            for cid, control in controls.items():
                _, parents, _, _, _ = prepare_case(control, payloads, config, alpha)
                values[cid] = recall(control["relevant_parent_ids"], [{"parent_id": hit.payload["parent_id"]} for hit in parents])
            sensitivity[str(alpha)] = values
        write(OUTPUT / "sensitivity.json", sensitivity)
        scorer = QwenRerankScorer(rerank.model)
        scorer.prepare()
        write(OUTPUT / "runtime.json", {"device": scorer._device, "torch": torch.__version__,
              "model": rerank.model.model_dump(mode="json"), "external_network_calls": 0})
        state = {"status": "running", "completed_records": 0, "expected_records": 34,
                 "alpha": ALPHA, "baseline_rerun": False}
        write(OUTPUT / "run_manifest.json", state)
        results = {}
        with (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for cid, control in controls.items():
                row = run_case(control, payloads, config, rerank, scorer)
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                results[cid] = row
                state.update(completed_records=len(results), last_case_id=cid)
                write(OUTPUT / "run_manifest.json", state)
                print(json.dumps({"case_id": cid, "progress": len(results), "metrics": row["metrics"]}), flush=True)
        check_pins(pins)
        if guard.denied:
            raise ValueError("frrf_guard_denied_operation")
        summary = summarize(controls, results)
        write(OUTPUT / "summary.json", summary)
        write(OUTPUT / "frrf-eight.json", [{"case_id": row["case_id"], "parent_id": row["parent_id"],
            "old_best_child_rank": row["RRF_best_child_rank"],
            **next(item for item in results[row["case_id"]]["parent_ranking"] if item["parent_id"] == row["parent_id"])}
            for row in evidence["gold_rows"] if row["stage"] == "rrf_below40"])
        state.update(status="complete", protected_unchanged=True, guard_denied=guard.denied,
                     new_retrieval_calls=0, new_generation_calls=0, rerank_model_calls=sum(row["model_calls"] for row in results.values()))
        write(OUTPUT / "run_manifest.json", state)
        print(json.dumps(summary, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
