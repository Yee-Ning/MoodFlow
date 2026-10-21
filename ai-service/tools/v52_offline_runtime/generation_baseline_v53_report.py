"""Offline provenance checks, historical cohorts and raw Ragas verdict replay."""

from collections import Counter
import hashlib
import json
import math
import re
import statistics

from tools.v52_offline_runtime import generation_baseline_v53 as run


KEYS = ("faithfulness", "factual_correctness(mode=f1)")
LOW_FAITHFULNESS = 0.8


def distribution(records, key):
    finite = [(row["case_id"], row[key]) for row in records if row[key] is not None]
    if any(not math.isfinite(value) or not 0 <= value <= 1 for _, value in finite):
        raise ValueError("v53_metric_out_of_range")
    values = [value for _, value in finite]
    return {"mean": statistics.mean(values) if values else None, "p50": statistics.median(values) if values else None,
            "finite": len(values), "not_scorable": len(records) - len(values),
            "lowest5": [{"case_id": cid, "score": value} for cid, value in sorted(finite, key=lambda item: (item[1], item[0]))[:5]]}


def cohorts(current, gold, baseline, first_fix, second_fix):
    old = {row["case_id"]: row for row in baseline if row["generation_status"] != "success"}
    latest = {row["case_id"]: row for row in [*first_fix, *second_fix]}
    output = []
    for cid, row in old.items():
        new = current.get(cid)
        output.append({"case_id": cid, "historical_group": "H0_fixed10" if latest[cid]["generation_status"] == "success" else "H0_remaining8",
            "step4_label": row["answerability"], "current_label": gold[cid]["answerability"] if cid in gold else "absent_from_V53",
            "step4_code": (row.get("generation_failure") or {}).get("validation_code"),
            "H0_last_status": latest[cid]["generation_status"],
            "current_status": new["generation_status"] if new else "not_in_current34_not_generated",
            "current_failure": new.get("failure_category") if new else None})
    return output


def attribution(recall12, packed_recall):
    if recall12 == 0:
        return "retrieval_Gold_coverage_zero; faithfulness causality still needs raw unsupported-claim evidence"
    if packed_recall == 0:
        return "Gold seen in rerank but absent from actual packed contexts"
    return "Gold reached actual contexts; inspect generation-side unsupported claims; incomplete retrieval may coexist"


def meta_claim_candidate(item):
    return bool(re.search(r"world\s*line|root node|catalog facts|directory facts|世界线|图谱|根节点|目录事实",
                          item.get("statement", ""), re.IGNORECASE))


def analyze():
    from evals import ragas_full_v5_report as replay
    from evals.ragas_smoke_v5_report import load_calls

    gold = {row["case_id"]: row for row in run.selected_gold()}
    preparation = run.read(run.OUTPUT / "preparation.json")
    run.check_pins(preparation["protected_before"])
    answers = {cid: run.read(run.OUTPUT / "answers" / (cid + ".json")) for cid in gold}
    retrieval = {cid: run.read(run.OUTPUT / "retrieval" / (cid + ".json")) for cid in gold}
    successful = {cid for cid, answer in answers.items() if answer["generation_status"] == "success"}
    scores = run.read(run.OUTPUT / "scores.json")
    if {row["case_id"] for row in scores} != successful or len(scores) != len(successful):
        raise ValueError("v53_successful_metric_population_mismatch")
    generation_calls = load_calls(run.OUTPUT / "generation_calls")
    judge_calls = load_calls(run.OUTPUT / "judge_calls")
    packing = []
    for cid, answer in answers.items():
        mapping = {hit["child_id"]: hit["parent_id"] for hit in retrieval[cid]["rerank_top12"]}
        packed = {mapping[source["metadata"]["chunk_id"]] for source in answer["sent_sources"]}
        packed_recall = len(packed & set(gold[cid]["relevant_parent_ids"])) / len(gold[cid]["relevant_parent_ids"])
        packing.append({"case_id": cid, "generation_status": answer["generation_status"],
            "rerank_recall_at_12": retrieval[cid]["metrics"]["recall_at_12"], "packed_gold_recall": packed_recall,
            "actual_contexts": len(answer["contexts"])})
    for call in [*generation_calls, *judge_calls]:
        if call["case_id"] not in gold or call["request"]["temperature"] != 0:
            raise ValueError("v53_call_scope_or_temperature_changed")
        if call.get("status_code") == 200:
            usage = call.get("usage")
            if usage is None or usage.get("input_tokens", usage.get("prompt_tokens", 0)) + usage.get("output_tokens", usage.get("completion_tokens", 0)) != usage["total_tokens"]:
                raise ValueError("v53_usage_receipt_invalid")
    outcomes = {(row["case_id"], row["metric"]): row for row in
                (run.read(path) for path in (run.OUTPUT / "metric_outcomes").glob("*.json"))}
    scored, unsupported = [], {}
    for raw in scores:
        cid = raw["case_id"]
        row = answers[cid]
        if any(row[key] != gold[cid][key] for key in ("question", "ground_truth", "relevant_parent_ids", "answerability")):
            raise ValueError("v53_answer_gold_reference_drift")
        calls = [call for call in generation_calls if call["case_id"] == cid]
        sent = json.loads(calls[-1]["request"]["input"][0]["content"])
        if row["contexts"] != [source["excerpt"] for source in sent["sources"]]:
            raise ValueError("v53_scored_contexts_not_actual_sent_contexts")
        if set(sent) & {"ground_truth", "required_facts", "anchors", "answerability", "relevant_parent_ids"}:
            raise ValueError("v53_gold_leaked_to_generation")
        child_parent = {hit["child_id"]: hit["parent_id"] for hit in retrieval[cid]["rerank_top12"]}
        packed = {child_parent[source["metadata"]["chunk_id"]] for source in row["sent_sources"]}
        packed_recall = len(packed & set(gold[cid]["relevant_parent_ids"])) / len(gold[cid]["relevant_parent_ids"])
        row = {**row, **raw["scores"], "packed_gold_recall": packed_recall,
               "diagnostic_attribution": attribution(retrieval[cid]["metrics"]["recall_at_12"], packed_recall)}
        scored.append(row)
        unsupported[cid] = []
        for call in judge_calls:
            if call["case_id"] != cid or call["metric"] != "faithfulness":
                continue
            value = replay.decoded_reply(call)
            if isinstance(value, dict):
                unsupported[cid].extend(item for item in value.get("statements", []) if isinstance(item, dict) and item.get("verdict") == 0)
    metric_replay = replay.replay_scores(scored, judge_calls, outcomes)
    historical_root = run.ROOT / "workspace/moodflow-review"
    history = cohorts(answers, {row["case_id"]: row for row in run.rows(run.GOLD)},
        run.read(historical_root / "ragas-full-v5-gate1-20261017/generation_status.json"),
        run.read(historical_root / "rag-h0-abstention-gate1-20261017/generation_status.json"),
        run.read(historical_root / "rag-h0-round2-gate1-20261018/generation_status.json"))
    step4_status = {row["case_id"]: row for row in run.read(historical_root / "ragas-full-v5-gate1-20261017/generation_status.json")}
    step4_inputs = {row["case_id"]: row for row in run.read(historical_root / "ragas-full-v5-gate1-20261017/inputs.json")}
    failures_vs_step4 = [{"case_id": cid, "current_failure": answer.get("failure_category"),
        "step4_status": step4_status[cid]["generation_status"] if cid in step4_status else "not_in_old_step4",
        "same_question_as_step4": cid in step4_inputs and step4_inputs[cid]["question"] == answer["question"]}
        for cid, answer in answers.items() if answer["generation_status"] != "success"]
    old_gold = {row["case_id"]: row for row in run.rows(run.GOLD.with_name("rag_eval_gold_v5_2.frozen.jsonl"))}
    special = []
    for cid in ("rag-v0-036", "rag-v0-039"):
        answer = answers[cid]
        special.append({"case_id": cid, "generation_status": answer["generation_status"],
            "scores": next((row["scores"] for row in scores if row["case_id"] == cid), None),
            "required_facts_changed_from_v52": gold[cid]["required_facts"] != old_gold[cid]["required_facts"],
            "ground_truth_identical_to_v52": gold[cid]["ground_truth"] == old_gold[cid]["ground_truth"],
            "ground_truth_identical_to_old_v5_step4": gold[cid]["ground_truth"] == step4_inputs[cid]["ground_truth"],
            "reference_sha256": hashlib.sha256(answer["ground_truth"].encode()).hexdigest()})
    per_case = [{key: row[key] for key in ("case_id", "question", "answerability", *KEYS, "retrieval_metrics",
                 "packed_gold_recall", "diagnostic_attribution", "generation_attempts")} for row in scored]
    for row in per_case:
        items = unsupported[row["case_id"]]
        row["unsupported_claims"] = len(items)
        row["nonrag_meta_claim_candidates"] = sum(meta_claim_candidate(item) for item in items)
        row["example_unsupported_nonmeta"] = next((item for item in items if not meta_claim_candidate(item)), None)
    summary = {"attempted": 34, "status_counts": dict(Counter(row["generation_status"] for row in answers.values())),
        "failure_categories": dict(Counter(row.get("failure_category", "unclassified") for row in answers.values() if row["generation_status"] != "success")),
        "by_answerability": {label: dict(Counter(row["generation_status"] for row in answers.values() if row["answerability"] == label)) for label in ("full", "partial")},
        "distributions": {key: distribution(scored, key) for key in KEYS},
        "current_retrieval_macro": {key: statistics.mean(row["metrics"][key] for row in retrieval.values()) for key in ("recall_at_40", "recall_at_12", "mrr")},
        "low_faithfulness_threshold": LOW_FAITHFULNESS,
        "low_faithfulness": [row for row in per_case if row["faithfulness"] is not None and row["faithfulness"] < LOW_FAITHFULNESS],
        "per_case_scores": per_case, "historical18": history, "failures_vs_step4": failures_vs_step4, "special036039": special,
        "packing_diagnostics_all34": packing,
        "generation_usage": run.usage_totals(generation_calls), "judge_usage": run.usage_totals(judge_calls),
        "unsupported_claims": unsupported,
        "normalization_cases": [cid for cid, answer in answers.items() if any(event.get("event") == "worldline.narrative_normalization.v1" for event in answer["normalization_and_boundary_events"])],
        "boundary_template_cases": [cid for cid, answer in answers.items() if any(event.get("event") == "worldline.boundary_template_check.v1" for event in answer["normalization_and_boundary_events"])],
        "protected_files_unchanged": len(preparation["protected_before"]), "metric_verdict_replay_passed": True}
    run.check_pins(preparation["protected_before"])
    run.write(run.OUTPUT / "metric-verdict-replay.json", metric_replay)
    run.write(run.OUTPUT / "analysis.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key in {"status_counts", "failure_categories", "distributions", "current_retrieval_macro", "generation_usage", "judge_usage"}}, ensure_ascii=False))


if __name__ == "__main__":
    from evals.ragas_gate0 import Gate0Guard, configure_offline
    import sys

    configure_offline()
    sys.addaudithook(Gate0Guard().audit)
    analyze()
