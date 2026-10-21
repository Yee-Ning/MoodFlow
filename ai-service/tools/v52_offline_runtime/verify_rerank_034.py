"""Stdlib-only independent replay of all three rerank experiments."""

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import statistics

from tools.v52_offline_runtime import verify_dimension_034 as replay


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/rerank-034-20261019"
SPECS = {"top16": (40, 16, None), "pool60": (60, 12, None), "cutoff05": (40, 12, .5)}
KEYS = ("recall_at_40", "recall_at_k", "precision_at_k", "mrr", "fixed_recall_at_12", "fixed_precision_at_12", "returned_precision", "returned_count")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write(name, data):
    (OUTPUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def select(ranks, payloads, plan, target):
    parents = {}
    for hit in ranks[:80]:
        payload = payloads[hit["point_id"]]
        text = "\n".join([payload.get("content", ""), payload.get("retrieval_content", ""), payload.get("breadcrumb", ""),
                           *payload.get("topics", []), *payload.get("keywords", [])]).casefold()
        if any(term.strip().casefold() in text for term in plan["post_exclusions"] if term.strip()):
            continue
        section = payload.get("breadcrumb", "").rsplit(">", 1)[-1].strip().casefold()
        if "timeline" in plan["query_types"] and section in {"外部連結", "外部链接", "external links"}:
            continue
        parents.setdefault(hit["parent_id"], hit)
    selected, overflow, docs, sources = [], [], Counter(), Counter()
    for hit in sorted(parents.values(), key=lambda item: (-item["fusion_score"], item["point_id"])):
        if docs[hit["doc_id"]] >= 4 or sources[hit["source_id"]] >= 5:
            overflow.append(hit)
            continue
        selected.append(hit)
        docs[hit["doc_id"]] += 1
        sources[hit["source_id"]] += 1
        if len(selected) == target:
            break
    return (selected + overflow[:max(0, target - len(selected))])[:target]


def score(gold, ranks, final, top_k):
    relevant = set(gold)
    hits = len(relevant.intersection(item["parent_id"] for item in final))
    hits12 = len(relevant.intersection(item["parent_id"] for item in final[:12]))
    first = next((rank for rank, item in enumerate(final, 1) if item["parent_id"] in relevant), None)
    return {"recall_at_40": len(relevant.intersection(item["parent_id"] for item in ranks[:40])) / len(relevant),
            "recall_at_k": hits / len(relevant), "precision_at_k": hits / top_k,
            "mrr": 1 / first if first else 0, "first_hit_rank": first,
            "fixed_recall_at_12": hits12 / len(relevant), "fixed_precision_at_12": hits12 / 12,
            "returned_precision": hits / len(final) if final else 0, "returned_count": len(final)}


def average(values):
    return {key: statistics.mean(row[key] for row in values) for key in KEYS}


def decision(old, new, lost, killed):
    met = new["mrr"] - old["mrr"] >= .02 or new["recall_at_k"] - old["recall_at_k"] >= .02
    return {"metric_threshold_met": met, "gold_gate_passed": not lost and not killed,
            "adopt": met and not lost and not killed}


def main():
    preflight = read(OUTPUT / "preflight.json")
    for name, expected in preflight["protected_before"].items():
        assert digest(ROOT / name) == expected, name
    gold = {row["case_id"]: row for row in rows(replay.GOLD) if row["answerability"] in {"full", "partial"}}
    assert len(gold) == 34 and set(gold) == set(preflight["case_ids"])
    controls, cache = {}, {}
    for cid, source in preflight["control_provenance"].items():
        path = ROOT / source["path"]
        assert digest(path) == source["sha256"]
        if source["path"] not in cache:
            cache[source["path"]] = {row["case_id"]: row for row in rows(path)}
        controls[cid] = cache[source["path"]][cid]
    updates = {row["point_id"]: row["after"] for row in read(replay.PATCH)}
    payloads = {}
    for row in rows(replay.PROJECTION):
        payload = row["payload"]
        if row["point_id"] in updates:
            payload["entity_ids"] = updates[row["point_id"]]
        payloads[row["point_id"]] = payload
    baseline, fused = {}, {}
    for cid, control in controls.items():
        assert control["healthy"] and control["relevant_parent_ids"] == gold[cid]["relevant_parent_ids"]
        assert control["question"] == gold[cid]["question"]
        fused[cid] = replay.fuse(control["leg_results"])
        assert [h["point_id"] for h in fused[cid][:40]] == [h["point_id"] for h in control["rrf_top40"]]
        assert [h["point_id"] for h in fused[cid][:80]] == [h["point_id"] for h in control["selection_input_rrf"]]
        assert [h["point_id"] for h in select(fused[cid], payloads, control["plan"], 40)] == [h["point_id"] for h in control["selection"]]
        baseline[cid] = score(gold[cid]["relevant_parent_ids"], fused[cid], control["rerank_top12"], 12)
        assert baseline[cid]["recall_at_40"] == control["metrics"]["recall_at_40"]
        assert baseline[cid]["recall_at_k"] == control["metrics"]["recall_at_12"]
        assert baseline[cid]["precision_at_k"] == control["metrics"]["precision_at_12"]
        assert baseline[cid]["mrr"] == control["metrics"]["mrr"]
    old_mean = average(list(baseline.values()))
    assert all(abs(old_mean[key] - value) < 1e-6 for key, value in (
        ("recall_at_40", .826331), ("recall_at_k", .798880), ("mrr", .680147)))
    summary, details, losses, pools = {"baseline": old_mean}, [], [], []
    all_round_scores = {}
    for name, (target, top_k, cutoff) in SPECS.items():
        state = read(OUTPUT / name / "run_manifest.json")
        assert state["status"] == "complete" and state["completed_records"] == 34
        assert state["protected_unchanged"] and not state["guard_denied"] and state["new_retrieval_calls"] == 0
        experiment = rows(OUTPUT / name / "cases.jsonl")
        assert [row["case_id"] for row in experiment] == preflight["case_ids"]
        scores, lost_pairs, killed_pairs = {}, [], []
        for row in experiment:
            cid = row["case_id"]
            control, relevant = controls[cid], set(gold[cid]["relevant_parent_ids"])
            assert row["spec"] == {"candidate_limit": target, "top_k": top_k, "threshold": cutoff}
            assert row["question"] == control["question"] and row["effective_qdrant_filter"] == control["effective_qdrant_filter"]
            assert row["plan"] == {**control["plan"], "candidate_top_k": target, "final_top_k": top_k}
            expected = select(fused[cid], payloads, control["plan"], target)
            scored = row["scored_candidates"]
            assert len(scored) <= target and [h["point_id"] for h in scored] == [h["point_id"] for h in expected]
            assert row["model_calls"] == bool(scored) and not row["rerank_fallback_used"]
            assert len(set(h["parent_id"] for h in scored)) == len(scored)
            for index, (hit, wanted) in enumerate(zip(scored, expected, strict=True)):
                payload = payloads[hit["point_id"]]
                assert replay.matches(payload, control["effective_qdrant_filter"])
                assert hit["candidate_index"] == index and hit["child_id"] == payload["chunk_id"]
                assert hit["parent_id"] == payload["parent_id"] and math.isfinite(hit["score"]) and 0 <= hit["score"] <= 1
                assert abs(hit["fusion_score"] - wanted["fusion_score"]) < 1e-15
                title = (payload.get("source_title") or payload.get("breadcrumb") or payload.get("source_name") or payload["source_id"]).strip()
                document = "\n".join(part for part in ["Evidence: " + payload["content"].strip(), "Title: " + title,
                    "Breadcrumb: " + payload["breadcrumb"].strip() if payload.get("breadcrumb", "").strip() else ""] if part)
                assert hit["document_sha256"] == hashlib.sha256(document.encode()).hexdigest()
                assert hit["query_sha256"] == hashlib.sha256(control["question"].encode()).hexdigest()
            ordered = sorted(scored, key=lambda hit: (-hit["score"], -hit["fusion_score"], hit["point_id"]))
            raw = ordered[:top_k]
            assert [h["point_id"] for h in row["raw_top_k"]] == [h["point_id"] for h in raw]
            assert [h["rerank_score"] for h in row["raw_top_k"]] == [h["score"] for h in raw]
            assert [h["retrieval_rank"] for h in row["raw_top_k"]] == [h["candidate_index"] + 1 for h in raw]
            final = [hit for hit in row["raw_top_k"] if cutoff is None or hit["rerank_score"] >= cutoff]
            assert row["final_top_k"] == final
            scores[cid] = score(list(relevant), fused[cid], final, top_k)
            assert scores[cid] == row["metrics"]
            assert scores[cid]["recall_at_40"] == baseline[cid]["recall_at_40"]
            old_hit = relevant.intersection(h["parent_id"] for h in control["rerank_top12"])
            new_hit = relevant.intersection(h["parent_id"] for h in final)
            lost = sorted(old_hit - new_hit)
            killed = sorted(h["parent_id"] for h in scored if cutoff is not None and h["score"] < cutoff and h["parent_id"] in relevant)
            assert lost == row["newly_lost_final_gold"] and killed == row["threshold_rejected_gold"]
            lost_pairs.extend((cid, pid) for pid in lost)
            killed_pairs.extend((cid, pid) for pid in killed)
            losses.append({"round": name, "case_id": cid, "lost_baseline_final_gold": lost,
                           "threshold_rejected_input_gold": [{"parent_id": pid, "score": next(h["score"] for h in scored if h["parent_id"] == pid)} for pid in killed]})
            details.append({"round": name, "case_id": cid, "question": control["question"],
                            "old": baseline[cid], "new": scores[cid],
                            "delta": {key: scores[cid][key] - baseline[cid][key] for key in KEYS}})
            pools.append({"round": name, "case_id": cid, "old_pool": len(control["selection"]), "new_pool": len(scored),
                          "old_gold_in_pool": sorted(relevant.intersection(h["parent_id"] for h in control["selection"])),
                          "new_gold_in_pool": sorted(relevant.intersection(h["parent_id"] for h in scored))})
        new_mean = average(list(scores.values()))
        summary[name] = {"n": 34, "metrics": new_mean, "delta": {key: new_mean[key] - old_mean[key] for key in KEYS},
                         "newly_lost_final_gold": lost_pairs, "threshold_rejected_gold": killed_pairs,
                         "pool_sizes": dict(Counter(row["new_pool"] for row in pools if row["round"] == name)),
                         **decision(old_mean, new_mean, lost_pairs, killed_pairs)}
        all_round_scores[name] = scores
    write("summary.json", summary)
    write("per-case-comparison.json", details)
    write("gold-loss-audit.json", losses)
    write("candidate-pool-audit.json", pools)
    write("independent-replay.json", {"status": "passed", "control_cases": 34, "experiment_cases": 102,
          "protected_files": len(preflight["protected_before"]), "R40_changed_cases": 0,
          "network_or_model_calls_in_verifier": 0,
          "zero_R40_cases": {cid: {"baseline": baseline[cid], **{name: values[cid] for name, values in all_round_scores.items()}}
                             for cid in baseline if baseline[cid]["recall_at_40"] == 0},
          "full_R40_cases": [cid for cid in baseline if baseline[cid]["recall_at_40"] == 1]})
    print(json.dumps({"status": "passed", "summary": summary}), flush=True)


if __name__ == "__main__":
    main()
