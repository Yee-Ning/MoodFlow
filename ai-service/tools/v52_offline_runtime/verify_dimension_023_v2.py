"""Independent standard-library replay for the approved23, never production imports."""

from collections import Counter
import hashlib
import json
from pathlib import Path
import statistics

from tools.v52_offline_runtime import verify_dimension_034 as replay


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/h1-dimension-prompt-023-v2-20261019"
SOURCE = OUTPUT.parent / "h1-dimension-prompt-034-v2-20261019"
CASE_IDS = tuple("rag-v0-" + i for i in "001 002 003 004 008 009 024 026 034 036 037 039 040 050 051 054 057 058 062 063 064 065 066".split())
ENGLISH_GOLD = {"parent-22eb9a7aa7d6e6e1138330ec", "parent-473c367bbbf7c2e105fb6534", "parent-f0c08762a7823c85fdcf2a93"}
METRICS = replay.METRICS


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(name, value):
    (OUTPUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def validate_population(rows):
    expected = [(cid, arm) for cid in CASE_IDS for arm in ("old", "new")]
    assert [(r["case_id"], r["arm"]) for r in rows] == expected


def direction(value):
    return "up" if value > 0 else "down" if value < 0 else "same"


def aggregate(rows):
    return {"n": len(rows), **{arm: {m: statistics.mean(r[arm][m] for r in rows) for m in METRICS} for arm in ("old", "new")},
            "delta": {m: statistics.mean(r["delta"][m] for r in rows) for m in METRICS},
            "directions": {m: dict(Counter(direction(r["delta"][m]) for r in rows)) for m in METRICS}}


def main():
    state = read(OUTPUT / "run_manifest.json")
    assert state["status"] == "complete" and state["completed_records"] == 46
    assert state["case_ids"] == list(CASE_IDS) and state["protected_unchanged"]
    assert state["generation_calls"] == state["Qdrant_write_calls"] == state["055_060_exceptions_consumed"] == 0
    assert not state["safety_events"] and not state["guard_denied"]
    for name, digest in state["protected_before"].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest, name
    gold = {r["case_id"]: {k: r[k] for k in ("case_id", "question", "answerability", "relevant_parent_ids")}
            for line in replay.GOLD.read_text(encoding="utf-8").splitlines() if line.strip() for r in [json.loads(line)] if r["case_id"] in CASE_IDS}
    updates = {r["point_id"]: r["after"] for r in read(replay.PATCH)}
    payloads, parents = {}, {}
    with replay.PROJECTION.open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            p = row["payload"]
            if row["point_id"] in updates:
                p["entity_ids"] = updates[row["point_id"]]
            payloads[row["point_id"]] = p
            parents.setdefault(row["parent_id"], {**replay.english(p["parent_context"]["content"]),
                "source_id": p["source_id"], "title": p["source_title"], "excerpt": p["parent_context"]["content"][:200]})
    english_pairs = [(cid, pid) for cid in CASE_IDS for pid in gold[cid]["relevant_parent_ids"] if parents[pid]["is_english"]]
    assert {pid for _, pid in english_pairs} == ENGLISH_GOLD and len(english_pairs) == 3
    assert set(read(OUTPUT / "analysis-protocol.json")["english_gold_parents"]) == ENGLISH_GOLD
    plans, inputs = read(OUTPUT / "paired-plans.json"), read(OUTPUT / "input-provenance.json")
    by_case, checked, order = {}, [], []
    with (OUTPUT / "paired-cases.jsonl").open(encoding="utf-8") as stream:
        for line in stream:
            row = json.loads(line)
            cid, arm = row["case_id"], row["arm"]
            order.append({"case_id": cid, "arm": arm})
            assert cid in CASE_IDS and arm in {"old", "new"} and arm not in by_case.get(cid, {})
            query_path = SOURCE / arm / (cid + ".json")
            query = read(query_path)
            assert row["query_understanding"] == query
            assert hashlib.sha256(query_path.read_bytes()).hexdigest() == inputs[cid][arm]["sha256"]
            assert row["healthy"] and row["question"] == gold[cid]["question"] and row["relevant_parent_ids"] == gold[cid]["relevant_parent_ids"]
            assert len(row["leg_results"]) == 7 and row["search_calls"] == row["rrf_calls"] == 1
            assert [l["text"] for l in row["leg_results"]] == [t for q in query["queries"] for t in (q, q)] + [query["hyde"]]
            assert [l["channel"] for l in row["leg_results"]] == ["dense", "lexical"] * 3 + ["dense"]
            assert row["effective_qdrant_filter"] == plans[cid][arm]["condition"]["effective_qdrant_filter"]
            for leg in row["leg_results"]:
                for hit in leg["hits"]:
                    p = payloads[hit["point_id"]]
                    assert all(p[k] == hit[h] for k, h in (("chunk_id", "child_id"), ("parent_id", "parent_id"), ("doc_id", "doc_id"), ("source_id", "source_id")))
                    assert replay.matches(p, row["effective_qdrant_filter"])
            ranks = replay.fuse(row["leg_results"])
            for name, n in (("rrf_top40", 40), ("selection_input_rrf", 80)):
                assert [h["point_id"] for h in row[name]] == [h["point_id"] for h in ranks[:n]]
                assert all(abs(h["fusion_score"] - ranks[i]["fusion_score"]) < 1e-13 for i, h in enumerate(row[name]))
            selection = replay.selected(ranks, payloads, plans[cid][arm]["plan"])
            assert [h["point_id"] for h in selection] == [h["point_id"] for h in row["selection"]]
            assert len(row["rerank_top12"]) == min(12, len(selection))
            assert len({h["parent_id"] for h in row["rerank_top12"]}) == len(row["rerank_top12"])
            assert all(h["point_id"] in {s["point_id"] for s in selection} for h in row["rerank_top12"])
            assert replay.score(gold[cid]["relevant_parent_ids"], ranks, row["rerank_top12"]) == row["metrics"]
            best = {}
            for hit in ranks:
                best.setdefault(hit["parent_id"], hit)
            language = {}
            for ecid, pid in english_pairs:
                if ecid != cid:
                    continue
                hit = best.get(pid)
                language[pid] = {"rrf_rank": hit["rrf_rank"] if hit else None, "child_id": hit["child_id"] if hit else None,
                    "rerank_rank": next((i for i, h in enumerate(row["rerank_top12"], 1) if h["parent_id"] == pid), None),
                    "leg_best_ranks": [next((i for i, h in enumerate(leg["hits"], 1) if h["parent_id"] == pid), None) for leg in row["leg_results"]]}
            by_case.setdefault(cid, {})[arm] = {"metrics": row["metrics"], "english": language,
                "filter": row["effective_qdrant_filter"], "hyde": query["hyde"]}
            checked.append({"case_id": cid, "arm": arm, "union": len(ranks), "RRF_selection_metrics_verified": True})
    validate_population(order)
    comparisons, languages = [], []
    for cid in CASE_IDS:
        arms = by_case[cid]
        assert arms["old"]["filter"] == arms["new"]["filter"] and arms["old"]["hyde"] == arms["new"]["hyde"]
        if cid == "rag-v0-065":
            assert "entity_ids" not in json.dumps(arms["old"]["filter"])
        delta = {m: arms["new"]["metrics"][m] - arms["old"]["metrics"][m] for m in METRICS}
        comparisons.append({"case_id": cid, "question": gold[cid]["question"], "answerability": gold[cid]["answerability"],
            "old": arms["old"]["metrics"], "new": arms["new"]["metrics"], "delta": delta,
            "directions": {m: direction(d) for m, d in delta.items()}})
        for ecid, pid in english_pairs:
            if ecid == cid:
                old, new = arms["old"]["english"][pid], arms["new"]["english"][pid]
                languages.append({"case_id": cid, "parent_id": pid, "title": parents[pid]["title"], "old": old, "new": new,
                    "direction": replay.rank_direction(old["rrf_rank"], new["rrf_rank"]),
                    "delta_if_both_observed": new["rrf_rank"] - old["rrf_rank"] if old["rrf_rank"] is not None and new["rrf_rank"] is not None else None})
    summary = {"population": "approved23 only, full14/partial9; no34-case extrapolation", "overall": aggregate(comparisons),
        "groups": {a: aggregate([r for r in comparisons if r["answerability"] == a]) for a in ("full", "partial")},
        "timeline": aggregate([r for r in comparisons if r["case_id"][-3:] in {"002", "036", "037", "039", "040"}]),
        "recall40_improved": [r["case_id"] for r in comparisons if r["delta"]["recall_at_40"] > 0],
        "recall40_up_recall12_same": [r["case_id"] for r in comparisons if r["delta"]["recall_at_40"] > 0 and r["delta"]["recall_at_12"] == 0],
        "english_gold": {"pairs": 3, "cases": 2, "directions": dict(Counter(r["direction"] for r in languages)),
            "top40": {arm: sum(r[arm]["rrf_rank"] is not None and r[arm]["rrf_rank"] <= 40 for r in languages) for arm in ("old", "new")},
            "top80": {arm: sum(r[arm]["rrf_rank"] is not None and r[arm]["rrf_rank"] <= 80 for r in languages) for arm in ("old", "new")}}}
    write("independent-replay.json", {"status": "passed", "records": checked, "network_calls": 0, "production_imports": False})
    write("paired-comparison.json", comparisons)
    write("english-gold-comparison.json", languages)
    write("english-parent-classification.json", {pid: parents[pid] for pid in ENGLISH_GOLD})
    write("summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
