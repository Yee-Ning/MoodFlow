"""Independent stdlib-only replay and English-Parent paired analysis.

No production retrieval/scoring imports, network, models, or source writes.
Missing ranks are censored observations, never invented infinity/zero ranks.
"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/h1-dimension-prompt-034-20261019"
PROJECTION = ROOT / "ai-service/data/reruns/jay-chou-release-freeze-20261019/run01/index/index_projection.jsonl"
PATCH = ROOT / "workspace/moodflow-review/v5-2-payload-fix-20261019/approved-payload-patch.json"
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_2.frozen.jsonl"
METRICS = ("recall_at_40", "recall_at_12", "precision_at_12", "mrr")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(name, data):
    (OUTPUT / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def matches(payload, condition):
    if condition is None:
        return True
    if "must" in condition:
        return all(matches(payload, c) for c in condition["must"])
    if "should" in condition:
        return any(matches(payload, c) for c in condition["should"])
    if "is_empty" in condition:
        return payload.get(condition["is_empty"]["key"]) in (None, [])
    value = payload.get(condition["key"])
    if "match" in condition:
        return isinstance(value, list) and bool(set(value) & set(condition["match"]["any"]))
    return isinstance(value, int) and ("gte" not in condition["range"] or value >= condition["range"]["gte"]) and ("lte" not in condition["range"] or value <= condition["range"]["lte"])


def fuse(legs):
    scores, metadata = {}, {}
    for leg in legs:
        assert leg["error_code"] is None and leg["limit"] == 80 and leg["weight"] == 1
        seen = set()
        for rank, hit in enumerate(leg["hits"], 1):
            point = hit["point_id"]
            if point in seen:
                continue
            seen.add(point)
            scores[point] = scores.get(point, 0) + 1 / (60 + rank)
            metadata.setdefault(point, hit)
    return [{**metadata[p], "fusion_score": scores[p], "rrf_rank": i}
            for i, p in enumerate(sorted(scores, key=lambda p: (-scores[p], p)), 1)]


def selected(ranks, payloads, plan):
    parents = {}
    for hit in ranks[:80]:
        p = payloads[hit["point_id"]]
        text = "\n".join([p.get("content", ""), p.get("retrieval_content", ""), p.get("breadcrumb", ""),
                           *p.get("topics", []), *p.get("keywords", [])]).casefold()
        if any(term.strip().casefold() in text for term in plan["post_exclusions"] if term.strip()):
            continue
        if "timeline" in plan["query_types"] and p.get("breadcrumb", "").rsplit(">", 1)[-1].strip().casefold() in {"外部連結", "外部链接", "external links"}:
            continue
        parents.setdefault(hit["parent_id"], hit)
    result, overflow, docs, sources = [], [], Counter(), Counter()
    for hit in sorted(parents.values(), key=lambda h: (-h["fusion_score"], h["point_id"])):
        if docs[hit["doc_id"]] >= 4 or sources[hit["source_id"]] >= 5:
            overflow.append(hit)
            continue
        result.append(hit)
        docs[hit["doc_id"]] += 1
        sources[hit["source_id"]] += 1
        if len(result) == 40:
            break
    if len(result) < 40:
        result.extend(overflow[:40 - len(result)])
    return result


def score(gold, ranks, reranked):
    relevant = set(gold)
    first = next((i for i, h in enumerate(reranked[:12], 1) if h["parent_id"] in relevant), None)
    return {
        "recall_at_40": len(relevant & {h["parent_id"] for h in ranks[:40]}) / len(relevant),
        "recall_at_12": len(relevant & {h["parent_id"] for h in reranked[:12]}) / len(relevant),
        "precision_at_12": len(relevant & {h["parent_id"] for h in reranked[:12]}) / 12,
        "mrr": 1 / first if first else 0,
        "first_hit_child_rank": first,
        "unique_rerank_parent_count": len({h["parent_id"] for h in reranked[:12]}),
    }


def english(text):
    latin = sum(c.isascii() and c.isalpha() for c in text)
    han = sum("\u4e00" <= c <= "\u9fff" for c in text)
    return {"is_english": latin >= 40 and latin / max(1, latin + han) >= .8,
            "latin_letters": latin, "han_characters": han,
            "latin_share": latin / max(1, latin + han)}


def rank_direction(old, new):
    if old is None and new is None:
        return "absent_both"
    if old is None:
        return "newly_observed"
    if new is None:
        return "no_longer_observed"
    return "improved" if new < old else "worsened" if new > old else "unchanged"


def main():
    state = read(OUTPUT / "run_manifest.json")
    assert state["status"] == "complete" and state["completed_records"] == 68
    gold = {r["case_id"]: {k: r[k] for k in ("case_id", "question", "answerability", "relevant_parent_ids")}
            for line in GOLD.read_text(encoding="utf-8").splitlines() if line.strip() for r in [json.loads(line)]}
    updates = {r["point_id"]: r["after"] for r in read(PATCH)}
    payloads, parent_meta = {}, {}
    with PROJECTION.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            payload = row["payload"]
            if row["point_id"] in updates:
                payload["entity_ids"] = updates[row["point_id"]]
            payloads[row["point_id"]] = payload
            parent_meta.setdefault(row["parent_id"], {
                **english(payload["parent_context"]["content"]), "source_id": payload["source_id"],
                "title": payload["source_title"], "language_metadata": payload["language"],
                "excerpt": payload["parent_context"]["content"][:200],
            })
    plans = read(OUTPUT / "paired-plans.json")
    review = read(OUTPUT / "semantic-review.json")
    by_case, verification = {}, []
    with (OUTPUT / "paired-cases.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            cid, arm = row["case_id"], row["arm"]
            assert arm in {"old", "new"} and arm not in by_case.get(cid, {})
            assert row["healthy"] and row["question"] == gold[cid]["question"] and row["relevant_parent_ids"] == gold[cid]["relevant_parent_ids"]
            assert len(row["leg_results"]) == 7 and row["search_calls"] == row["rrf_calls"] == 1
            expected = [text for q in row["query_understanding"]["queries"] for text in (q, q)] + [row["query_understanding"]["hyde"]]
            assert [l["text"] for l in row["leg_results"]] == expected
            for leg in row["leg_results"]:
                for hit in leg["hits"]:
                    p = payloads[hit["point_id"]]
                    assert p["chunk_id"] == hit["child_id"] and p["parent_id"] == hit["parent_id"]
                    assert matches(p, row["effective_qdrant_filter"])
            ranks = fuse(row["leg_results"])
            for name, n in (("rrf_top40", 40), ("selection_input_rrf", 80)):
                assert [h["point_id"] for h in row[name]] == [h["point_id"] for h in ranks[:n]]
                assert all(abs(h["fusion_score"] - ranks[i]["fusion_score"]) < 1e-13 for i, h in enumerate(row[name]))
            selection = selected(ranks, payloads, plans[cid][arm]["plan"])
            assert [h["point_id"] for h in selection] == [h["point_id"] for h in row["selection"]]
            assert len(row["rerank_top12"]) == min(12, len(selection))
            assert all(h["point_id"] in {s["point_id"] for s in selection} for h in row["rerank_top12"])
            assert score(row["relevant_parent_ids"], ranks, row["rerank_top12"]) == row["metrics"]
            best = {}
            for h in ranks:
                best.setdefault(h["parent_id"], h["rrf_rank"])
            by_case.setdefault(cid, {})[arm] = {"metrics": row["metrics"], "best_parent_ranks": best,
                "filter": row["effective_qdrant_filter"], "hyde": row["query_understanding"]["hyde"]}
            verification.append({"case_id": cid, "arm": arm, "RRF_selection_scores_verified": True, "union": len(ranks)})
    assert len(by_case) == 34 and len(verification) == 68
    comparisons, english_pairs = [], []
    for cid, arms in by_case.items():
        assert set(arms) == {"old", "new"} and arms["old"]["filter"] == arms["new"]["filter"] and arms["old"]["hyde"] == arms["new"]["hyde"]
        comparisons.append({"case_id": cid, "answerability": gold[cid]["answerability"], "question": gold[cid]["question"],
            "old": arms["old"]["metrics"], "new": arms["new"]["metrics"],
            "delta": {m: arms["new"]["metrics"][m] - arms["old"]["metrics"][m] for m in METRICS}})
        for parent, meta in parent_meta.items():
            if not meta["is_english"]:
                continue
            old, new = (arms[a]["best_parent_ranks"].get(parent) for a in ("old", "new"))
            english_pairs.append({"case_id": cid, "parent_id": parent, "gold_relevant": parent in gold[cid]["relevant_parent_ids"],
                "old_rank": old, "new_rank": new, "direction": rank_direction(old, new),
                "delta_if_observed_both": new - old if old is not None and new is not None else None})
    def group(rows):
        return {"n": len(rows), **{a: {m: statistics.mean(r[a][m] for r in rows) for m in METRICS} for a in ("old", "new")},
                "metric_directions": {m: dict(Counter("up" if r["delta"][m] > 0 else "down" if r["delta"][m] < 0 else "same" for r in rows)) for m in METRICS}}
    def english_summary(rows):
        deltas = [r["delta_if_observed_both"] for r in rows if r["delta_if_observed_both"] is not None]
        return {"pairs": len(rows), "directions": dict(Counter(r["direction"] for r in rows)),
                "median_delta_both_observed": statistics.median(deltas) if deltas else None,
                "top40": {a: sum(r[a + "_rank"] is not None and r[a + "_rank"] <= 40 for r in rows) for a in ("old", "new")},
                "top80": {a: sum(r[a + "_rank"] is not None and r[a + "_rank"] <= 80 for r in rows) for a in ("old", "new")}}
    summary = {"overall": group(comparisons),
        "by_answerability": {a: group([r for r in comparisons if r["answerability"] == a]) for a in ("full", "partial")},
        "recall40_up_recall12_same": [r["case_id"] for r in comparisons if r["delta"]["recall_at_40"] > 0 and r["delta"]["recall_at_12"] == 0],
        "query1_classification": dict(Counter(review["cases"][cid]["query1_class"] for cid in by_case)),
        "query1_exact_unchanged": [cid for cid in by_case if read(OUTPUT / "new" / (cid + ".json"))["queries"][0] == read(OUTPUT / "old" / (cid + ".json"))["queries"][0]],
        "english": {"definition": "Parent body: >=40 ASCII alphabetic letters and Latin/(Latin+Han)>=0.8; metadata alone is not authoritative",
            "english_parent_count": sum(m["is_english"] for m in parent_meta.values()),
            "all_english_pairs": english_summary(english_pairs),
            "gold_english_pairs": english_summary([r for r in english_pairs if r["gold_relevant"]])}}
    write("independent-replay.json", {"status": "passed", "records": verification, "network_calls": 0})
    write("paired-comparison.json", comparisons)
    write("english-parent-classification.json", {p: m for p, m in parent_meta.items() if m["is_english"]})
    write("english-rank-pairs.json", english_pairs)
    write("summary.json", summary)
    print(json.dumps({"verified_records": 68, "overall": summary["overall"], "english": summary["english"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
