"""Independent stdlib-only F-RRF score, selection, sensitivity and loss replay."""

from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import statistics

from tools.v52_offline_runtime import verify_dimension_034 as replay


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/timeline-gate1-frrf-20261019"
TIMELINE = {"rag-v0-" + suffix for suffix in ("002", "036", "037", "039", "040")}
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_3.frozen.jsonl"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def aggregate(legs, alpha):
    groups = defaultdict(list)
    for child in replay.fuse(legs):
        groups[child["parent_id"]].append(child)
    parents = []
    for children in groups.values():
        best = children[0]
        score = best["fusion_score"] + alpha * sum(child["fusion_score"] for child in children[1:])
        parents.append({**best, "fusion_score": score, "child_score": best["fusion_score"],
                        "observed_child_count": len(children)})
    return sorted(parents, key=lambda parent: (-parent["fusion_score"], parent["point_id"]))


def select(parents, payloads, plan, config):
    valid = []
    for hit in parents[:80]:
        payload = payloads[hit["point_id"]]
        text = "\n".join([payload.get("content", ""), payload.get("retrieval_content", ""), payload.get("breadcrumb", ""),
                          *payload.get("topics", []), *payload.get("keywords", [])]).casefold()
        if any(term.strip().casefold() in text for term in plan["post_exclusions"] if term.strip()):
            continue
        section = payload.get("breadcrumb", "").rsplit(">", 1)[-1].strip().casefold()
        if "timeline" in plan["query_types"] and section in {"外部連結", "外部链接", "external links"}:
            continue
        valid.append(hit)
    broad = {"timeline", "comparison", "influence_style", "exploratory"}
    cap = config["broad_document_cap"] if broad & set(plan["query_types"]) else config["default_document_cap"]
    chosen, overflow, docs, sources = [], [], Counter(), Counter()
    for hit in valid:
        if docs[hit["doc_id"]] >= cap or sources[hit["source_id"]] >= config["source_cap"]:
            overflow.append(hit)
            continue
        chosen.append(hit)
        docs[hit["doc_id"]] += 1
        sources[hit["source_id"]] += 1
        if len(chosen) == 40:
            break
    return (chosen + overflow[:max(0, 40 - len(chosen))])[:40]


def main():
    preflight = read(OUTPUT / "preflight.json")
    pins = preflight["protected_before"]
    assert all(digest(ROOT / name) == expected for name, expected in pins.items())
    controls, cache = {}, {}
    for cid, source in preflight["control_provenance"].items():
        assert digest(ROOT / source["path"]) == source["sha256"]
        if source["path"] not in cache:
            cache[source["path"]] = {row["case_id"]: row for row in rows(ROOT / source["path"])}
        controls[cid] = cache[source["path"]][cid]
    gold = {row["case_id"]: row for row in rows(GOLD) if row["answerability"] in {"full", "partial"}}
    results = {row["case_id"]: row for row in rows(OUTPUT / "cases.jsonl")}
    assert len(results) == len(controls) == 34 and set(results) == set(gold) == set(controls)
    payloads = {row["point_id"]: row["payload"] for row in rows(replay.PROJECTION)}
    sensitivity = read(OUTPUT / "sensitivity.json")
    comparisons, losses = [], defaultdict(list)
    for cid, result in results.items():
        control = controls[cid]
        relevant = set(gold[cid]["relevant_parent_ids"])
        assert control["relevant_parent_ids"] == gold[cid]["relevant_parent_ids"]
        assert result["plan"] == control["plan"] and result["effective_qdrant_filter"] == control["effective_qdrant_filter"]
        fused = aggregate(control["leg_results"], 0.3)
        assert [row["point_id"] for row in fused] == [row["best_point_id"] for row in result["parent_ranking"]]
        for actual, saved in zip(fused, result["parent_ranking"], strict=True):
            assert math.isclose(actual["fusion_score"], saved["parent_score"], abs_tol=1e-15)
            assert actual["observed_child_count"] == saved["observed_child_count"]
        assert [row["point_id"] for row in fused[:40]] == [row["point_id"] for row in result["rrf_top40"]]
        selected = select(fused, payloads, control["plan"], preflight["retrieval_config"]["selection"])
        scored = result["scored_candidates"]
        assert [row["point_id"] for row in selected] == [row["point_id"] for row in scored]
        assert all(math.isfinite(row["score"]) for row in scored)
        for item in scored:
            payload = payloads[item["point_id"]]
            document = "\n".join(part for part in (
                "Evidence: " + payload["content"].strip(),
                "Title: " + (payload.get("source_title") or payload.get("breadcrumb") or payload.get("source_name") or payload["source_id"]).strip(),
                "Breadcrumb: " + payload.get("breadcrumb", "").strip() if payload.get("breadcrumb", "").strip() else "") if part)
            assert item["query_sha256"] == hashlib.sha256(control["plan"]["standalone_query"].encode()).hexdigest()
            assert item["document_sha256"] == hashlib.sha256(document.encode()).hexdigest()
        ranked = sorted(scored, key=lambda row: (-row["score"], -row["fusion_score"], row["point_id"]))[:12]
        assert [row["point_id"] for row in ranked] == [row["point_id"] for row in result["rerank_top12"]]
        old = replay.score(list(relevant), replay.fuse(control["leg_results"]), control["rerank_top12"])
        new = replay.score(list(relevant), fused, ranked)
        assert all(old[key] == control["metrics"][key] for key in replay.METRICS)
        assert all(new[key] == result["metrics"][key] for key in replay.METRICS)
        for stage, old_rows, new_rows in (("rrf40", control["rrf_top40"], fused[:40]),
                ("selection", control["selection"], scored), ("final12", control["rerank_top12"], ranked)):
            missing = sorted((relevant & {row["parent_id"] for row in old_rows}) - {row["parent_id"] for row in new_rows})
            assert missing == result["lost_gold"][stage]
            if missing:
                losses[stage].append({"case_id": cid, "parent_ids": missing})
        for alpha in (0.1, 0.5):
            value = len(relevant & {row["parent_id"] for row in aggregate(control["leg_results"], alpha)[:40]}) / len(relevant)
            assert value == sensitivity[str(alpha)][cid]
        comparisons.append({"case_id": cid, "old": old, "new": new,
                            "delta": {key: new[key] - old[key] for key in replay.METRICS}})
    summary = read(OUTPUT / "summary.json")
    for label, ids in (("timeline5", TIMELINE), ("regression29", set(controls) - TIMELINE), ("all34", set(controls))):
        for key in replay.METRICS:
            old, new = (statistics.mean(row[group][key] for row in comparisons if row["case_id"] in ids) for group in ("old", "new"))
            assert old == summary["groups"][label]["baseline"][key]
            assert new == summary["groups"][label]["experiment"][key]
    assert summary["gold_losses"] == {stage: losses[stage] for stage in ("rrf40", "selection", "final12")}
    expected = (summary["groups"]["timeline5"]["delta"]["recall_at_40"] >= 0.1
                and summary["groups"]["all34"]["delta"]["recall_at_40"] >= 0 and not any(losses.values()))
    assert summary["adopt"] == expected
    assert all(digest(ROOT / name) == expected for name, expected in pins.items())
    output = {"status": "passed", "cases": 34, "protected_files": len(pins), "all_protected_unchanged": True,
              "independent_fusion_selector_metric_loss_sensitivity_replay": True, "comparisons": comparisons}
    destination = OUTPUT / "independent-verification.json"
    if destination.exists():
        raise ValueError("frrf_verification_already_exists")
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in output.items() if key != "comparisons"}))


if __name__ == "__main__":
    main()
