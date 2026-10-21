"""Read frozen evidence for five cases; no search, model, tokenizer or DB calls."""

from collections import Counter, defaultdict
import hashlib
import json
import os
from pathlib import Path
import statistics
import sys


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/timeline-gate0-20261019"
REVIEW = ROOT / "workspace/moodflow-review"
PREVIOUS = REVIEW / "rerank-034-20261019"
PROJECTION = ROOT / "ai-service/data/reruns/jay-chou-release-freeze-20261019/run01/index/index_projection.jsonl"
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_2.frozen.jsonl"
NATIVE = REVIEW / "h2-parent-diagnosis-v5-20261018"
CASE_IDS = tuple("rag-v0-" + suffix for suffix in ("002", "036", "037", "039", "040"))
EXPECTED_LEGS = tuple("q" + str(index) + ":decomposed:" + channel for index in range(3)
                      for channel in ("dense", "lexical")) + ("q3:semantic:dense",)
ATTRIBUTION = {
    ("002", "67b3917fa13651becf333ca5"): {"established": ["F-RRF"], "hypotheses": []},
    ("002", "4da6252d2b838d7851185536"): {"established": [], "hypotheses": ["C"]},
    ("002", "5df9b68987eb9968104eaa3b"): {"established": ["F-RRF"], "hypotheses": ["C"]},
    ("002", "22eb9a7aa7d6e6e1138330ec"): {"established": ["F-RRF"], "hypotheses": ["D"]},
    ("002", "473c367bbbf7c2e105fb6534"): {"established": ["F-RRF"], "hypotheses": ["D"]},
    ("036", "4da6252d2b838d7851185536"): {"established": ["F-RRF"], "hypotheses": ["C"]},
    ("036", "bb32b222016ed83b4eccabaa"): {"established": ["F-RRF"], "hypotheses": ["A", "B"]},
    ("037", "bb32b222016ed83b4eccabaa"): {"established": ["F-RRF"], "hypotheses": ["A", "B"]},
    ("039", "a9a1ce0be198f68e5eae8ffc"): {"established": ["F-ENTITY"], "hypotheses": []},
    ("040", "705e51bf8082844c72dbf857"): {"established": [], "hypotheses": ["C"]},
    ("040", "a37f4aefb6f6c5f5eea8d894"): {"established": ["F-RRF"], "hypotheses": ["C"]},
    ("040", "a9a1ce0be198f68e5eae8ffc"): {"established": ["F-ENTITY"], "hypotheses": []},
    ("040", "bb32b222016ed83b4eccabaa"): {"established": [], "hypotheses": ["B"]},
}


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records(path):
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def matches(payload, condition):
    if condition is None:
        return True
    if "must" in condition:
        return all(matches(payload, child) for child in condition["must"])
    if "should" in condition:
        return any(matches(payload, child) for child in condition["should"])
    if "is_empty" in condition:
        return payload.get(condition["is_empty"]["key"]) in (None, [], "")
    value = payload.get(condition["key"])
    if "match" in condition:
        values = value if isinstance(value, list) else [value]
        return bool(set(values) & set(condition["match"]["any"]))
    bounds = condition["range"]
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and ("gte" not in bounds or value >= bounds["gte"])
            and ("lte" not in bounds or value <= bounds["lte"]))


def fuse(legs):
    if tuple(leg["leg_id"] for leg in legs) != EXPECTED_LEGS:
        raise ValueError("seven_frozen_legs_required")
    scores, metadata = defaultdict(float), {}
    for leg in legs:
        if leg["error_code"] is not None or leg["weight"] != 1 or leg["limit"] != 80:
            raise ValueError("frozen_leg_configuration_mismatch")
        seen = set()
        for rank, hit in enumerate(leg["hits"], 1):
            point = hit["point_id"]
            if point not in seen:
                scores[point] += 1 / (60 + rank)
                metadata.setdefault(point, hit)
                seen.add(point)
    return [{**metadata[point], "fusion_score": scores[point], "rank": rank}
            for rank, point in enumerate(sorted(scores, key=lambda point: (-scores[point], point)), 1)]


def gold_alignment(case):
    anchors = {anchor["parent_id"] for anchor in case["anchors"]}
    relevant = set(case["relevant_parent_ids"])
    return {"anchors_without_relevant_parent": sorted(anchors - relevant),
            "relevant_parents_without_anchor": sorted(relevant - anchors)}


def saved_overlap(query, children, saved):
    if query not in saved:
        return None
    query_ids = set(saved[query]["ids"])
    body = children[0]["payload"]["parent_context"]["content"]
    return {"query_terms": len(query_ids),
            "parent_shared_terms": len(query_ids & set(saved[body]["ids"])) if body in saved else None,
            "child_shared_terms": [{"child_id": child["chunk_id"],
                "shared": len(query_ids & set(saved[child["bm25_text"]]["ids"])) if child["bm25_text"] in saved else None}
                for child in children]}


def summary(rows):
    missed = [row for row in rows if row["stage"] != "hit_rrf40"]
    categories = {}
    for code in "ABCDEF":
        proven = sorted({row["parent_id"] for row in missed
                         if any(tag.split("-")[0] == code for tag in row["attribution"]["established"])})
        suspected = sorted({row["parent_id"] for row in missed if code in row["attribution"]["hypotheses"]})
        categories[code] = {"established_parent_ids": proven, "established_count": len(proven),
                            "hypothesis_parent_ids": suspected, "hypothesis_count": len(suspected)}
    return {"case_count": len({row["case_id"] for row in rows}), "gold_case_parent_pairs": len(rows),
            "unique_gold_parents": len({row["parent_id"] for row in rows}),
            "stage_pairs": dict(Counter(row["stage"] for row in rows)),
            "unique_R40_missed_parents": len({row["parent_id"] for row in missed}), "categories": categories}


class ReadOnlyAudit:
    def __init__(self, destination):
        self.destination = destination.resolve()

    def audit(self, event, args):
        if event.startswith("socket.") or event == "subprocess.Popen":
            raise RuntimeError("timeline_network_and_subprocess_forbidden")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            path = Path(os.fsdecode(args[0])).resolve()
            if any(part.lower() == ".env" or part.lower().startswith(".env.") for part in path.parts):
                raise RuntimeError("timeline_secret_read_forbidden")
            mode, flags = args[1:3]
            writing = isinstance(mode, str) and any(flag in mode for flag in "wax+")
            writing = writing or isinstance(flags, int) and bool(flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC))
            if writing and not path.is_relative_to(self.destination):
                raise RuntimeError("timeline_source_write_forbidden")


def analyze():
    preflight = read(PREVIOUS / "preflight.json")
    pins = dict(preflight["protected_before"])
    pins.update(read(PREVIOUS / "artifact-sha256.json")["files"])
    extra = [PREVIOUS / "artifact-sha256.json", NATIVE / "tokenizer_probe.json", NATIVE / "native_term_ids.json",
             NATIVE / "comparisons.json", NATIVE / "completion.json", Path(__file__),
             Path(__file__).with_name("test_timeline_gate0.py")]
    pins.update({path.relative_to(ROOT).as_posix(): sha(path) for path in extra})
    for name, expected in pins.items():
        if sha(ROOT / name) != expected:
            raise ValueError("protected_source_drift:" + name)
    gold = {row["case_id"]: row for row in records(GOLD) if row["case_id"] in CASE_IDS}
    saved_terms = read(NATIVE / "native_term_ids.json")
    wanted = {parent for case in gold.values() for parent in case["relevant_parent_ids"]}
    projection = records(PROJECTION)
    parents = defaultdict(list)
    for row in projection:
        if row["parent_id"] in wanted:
            parents[row["parent_id"]].append(row)
    controls, cache = {}, {}
    for cid in CASE_IDS:
        source = preflight["control_provenance"][cid]
        if source["path"] not in cache:
            cache[source["path"]] = {row["case_id"]: row for row in records(ROOT / source["path"])}
        controls[cid] = cache[source["path"]][cid]
    result, cases = [], {}
    for cid in CASE_IDS:
        control, case = controls[cid], gold[cid]
        assert control["question"] == case["question"] and control["relevant_parent_ids"] == case["relevant_parent_ids"]
        ranks = fuse(control["leg_results"])
        for field, count in (("rrf_top40", 40), ("selection_input_rrf", 80)):
            assert [hit["point_id"] for hit in ranks[:count]] == [hit["point_id"] for hit in control[field]]
            assert all(abs(hit["fusion_score"] - saved["fusion_score"]) < 1e-15
                       for hit, saved in zip(ranks[:count], control[field], strict=True))
        current_filter = control["effective_qdrant_filter"]
        year_parts = [part for part in current_filter["must"] if "should" in part]
        entity_parts = [part for part in current_filter["must"] if part.get("key") == "entity_ids"]
        cases[cid] = {"question": case["question"], "answerability": case["answerability"],
                      "ground_truth": case["ground_truth"], "required_facts": case["required_facts"],
                      "gold_alignment": gold_alignment(case), "effective_filter": current_filter,
                      "legs": [{key: leg[key] for key in ("leg_id", "text", "limit")} for leg in control["leg_results"]],
                      "metrics": control["metrics"], "RRF_union_size": len(ranks),
                      "before_year_rank_status": "no_year_filter" if not year_parts else "unavailable_same_release_unfiltered_trace"}
        for parent in case["relevant_parent_ids"]:
            children = parents[parent]
            eligibility = [{"child_id": child["chunk_id"],
                            "entity_pass": all(matches(child["payload"], part) for part in entity_parts),
                            "year_pass": all(matches(child["payload"], part) for part in year_parts),
                            "effective_pass": matches(child["payload"], current_filter)} for child in children]
            parent_ranks = [hit for hit in ranks if hit["parent_id"] == parent]
            best = parent_ranks[0] if parent_ranks else None
            if not any(child["effective_pass"] for child in eligibility):
                stage = "filter_excluded"
            elif not best:
                stage = "not_observed_any_top80"
            elif best["rank"] > 40:
                stage = "rrf_below40"
            else:
                stage = "hit_rrf40"
            key = (cid.removeprefix("rag-v0-"), parent.removeprefix("parent-"))
            attribution = ATTRIBUTION.get(key, {"established": [], "hypotheses": []})
            assert (stage == "hit_rrf40") == (key not in ATTRIBUTION)
            result.append({"case_id": cid, "parent_id": parent, "stage": stage, "attribution": attribution,
                "leg_ranks": [{"leg_id": leg["leg_id"], "all_child_ranks": [rank for rank, hit in enumerate(leg["hits"], 1) if hit["parent_id"] == parent],
                               "best": next((rank for rank, hit in enumerate(leg["hits"], 1) if hit["parent_id"] == parent), None)} for leg in control["leg_results"]],
                "RRF_best_child_rank": best["rank"] if best else None,
                "RRF_child_evidence": [{"child_id": hit["child_id"], "rank": hit["rank"], "score": hit["fusion_score"]} for hit in parent_ranks],
                "filter_eligibility": eligibility,
                "saved_native_overlap": [{"leg_id": leg["leg_id"], "overlap": saved_overlap(leg["text"], children, saved_terms)}
                                         for leg in control["leg_results"] if leg["channel"] == "lexical"],
                "before_year_RRF_rank": best["rank"] if best and not year_parts else None,
                "before_year_rank_status": cases[cid]["before_year_rank_status"],
                "final_rank": next((rank for rank, hit in enumerate(control["rerank_top12"], 1) if hit["parent_id"] == parent), None),
                "anchors": [{**{key: anchor[key] for key in ("claim", "exact_text", "child_ids")},
                    "parent_contains": anchor["exact_text"] in children[0]["payload"]["parent_context"]["content"],
                    "specified_child_contains": any(anchor["exact_text"] in child["payload"]["content"] for child in children if child["chunk_id"] in anchor["child_ids"])}
                    for anchor in case["anchors"] if anchor["parent_id"] == parent]})
        actual = sum(row["stage"] == "hit_rrf40" for row in result if row["case_id"] == cid) / len(case["relevant_parent_ids"])
        assert actual == control["metrics"]["recall_at_40"]
    assert len(result) == 20 and len(parents) == 13
    stats = summary(result)
    stats["macro_recall_at_40"] = statistics.mean(case["metrics"]["recall_at_40"] for case in cases.values())
    assert all(sha(ROOT / name) == digest for name, digest in pins.items())
    return {"scope": list(CASE_IDS), "source_pins": pins, "cases": cases, "gold_rows": result, "summary": stats,
            "parent_contents": {parent: {"source_id": rows[0]["payload"]["source_id"],
                "content": rows[0]["payload"]["parent_context"]["content"],
                "children": [{"child_id": row["chunk_id"], "content": row["payload"]["content"], "bm25_text": row["bm25_text"],
                              "metadata": {key: row["payload"].get(key) for key in ("entity_ids", "year", "min_mentioned_year", "max_mentioned_year")}} for row in rows]} for parent, rows in parents.items()},
            "new_retrieval_calls": 0, "model_calls": 0, "new_tokenizer_calls": 0, "Qdrant_requests": 0,
            "protected_unchanged": True}


def main():
    if sys.argv[1:] or OUTPUT.exists():
        raise ValueError("timeline_read_only_diagnosis_output_already_exists_or_invalid_args")
    guard = ReadOnlyAudit(OUTPUT)
    sys.addaudithook(guard.audit)
    evidence = analyze()
    OUTPUT.mkdir()
    (OUTPUT / "evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(evidence["summary"], ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
