"""Independent stdlib H4 audit: control provenance, Parent veto, RRF and scores."""

from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import statistics

from tools.v52_offline_runtime import verify_dimension_034 as replay


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/h4-metadata-filter-034-20261019"
METRICS = replay.METRICS
SIGNALS = ("source_id", "entity", "doc_type")


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def records(path):
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def eligible(payload, expression):
    if expression is None:
        return True
    if "must" in expression:
        return all(eligible(payload, e) for e in expression["must"])
    if "should" in expression:
        return any(eligible(payload, e) for e in expression["should"])
    if "is_empty" in expression:
        return payload.get(expression["is_empty"]["key"]) in (None, "", [])
    value = payload.get(expression["key"])
    if "match" in expression:
        actual = value if isinstance(value, list) else [value]
        return any(v in expression["match"]["any"] for v in actual)
    bounds = expression["range"]
    return type(value) is int and bounds.get("gte", value) <= value <= bounds.get("lte", value)


def verify_trace(row, payloads, condition):
    assert row["healthy"] and row["metrics"] is not None
    assert len(row["leg_results"]) == 7 and row["search_calls"] == row["rrf_calls"] == 1
    expected = [t for q in row["query_understanding"]["queries"] for t in (q, q)] + [row["query_understanding"]["hyde"]]
    assert [l["text"] for l in row["leg_results"]] == expected
    assert [l["channel"] for l in row["leg_results"]] == ["dense", "lexical"] * 3 + ["dense"]
    for leg in row["leg_results"]:
        for hit in leg["hits"]:
            p = payloads[hit["point_id"]]
            assert p["parent_id"] == hit["parent_id"] and p["chunk_id"] == hit["child_id"]
            assert eligible(p, condition)
    ranks = replay.fuse(row["leg_results"])
    for key, cutoff in (("rrf_top40", 40), ("selection_input_rrf", 80)):
        assert [h["point_id"] for h in row[key]] == [h["point_id"] for h in ranks[:cutoff]]
        assert all(abs(h["fusion_score"] - ranks[i]["fusion_score"]) < 1e-13 for i, h in enumerate(row[key]))
    selection = replay.selected(ranks, payloads, row["plan"])
    assert [h["point_id"] for h in selection] == [h["point_id"] for h in row["selection"]]
    reranked = row["rerank_top12"]
    assert len(reranked) == min(12, len(selection))
    assert {h["point_id"] for h in reranked} <= {h["point_id"] for h in selection}
    assert all(h["rerank_score"] is not None for h in reranked)
    assert [h["rerank_score"] for h in reranked] == sorted([h["rerank_score"] for h in reranked], reverse=True)
    score = replay.score(row["relevant_parent_ids"], ranks, reranked)
    assert score == row["metrics"]
    return score


def summarize(old, new):
    assert len(old) == len(new) == 34 and set(old) == set(new)
    old_mean = {m: statistics.mean(old[c][m] for c in old) for m in METRICS}
    new_mean = {m: statistics.mean(new[c][m] for c in new) for m in METRICS}
    return {"n": 34, "control": old_mean, "experiment": new_mean,
        "delta": {m: new_mean[m] - old_mean[m] for m in METRICS},
        "directions": {m: dict(Counter("up" if new[c][m] > old[c][m] else "down" if new[c][m] < old[c][m] else "same" for c in old)) for m in METRICS}}


def main():
    prep, sealed, preflight = (read(OUTPUT / n) for n in ("preparation.json", "seal.json", "preflight.json"))
    for name, sha in {**prep["protected_before"], **sealed["sha256"]}.items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == sha, name
    payloads, parents = {}, {}
    patch = {r["point_id"]: r["after"] for r in read(replay.PATCH)}
    for record in records(replay.PROJECTION):
        p = record["payload"]
        if record["point_id"] in patch:
            p["entity_ids"] = patch[record["point_id"]]
        payloads[record["point_id"]] = p
        parent = parents.setdefault(p["parent_id"], {"entity_ids": set(), "source_id": p.get("source_id"), "source_type": p.get("source_type")})
        parent["entity_ids"].update(p.get("entity_ids") or [])
        assert parent["source_id"] == p.get("source_id") and parent["source_type"] == p.get("source_type")
    assert len(payloads) == 1270 and len(parents) == 689
    provenance = read(OUTPUT / "control-provenance.json")
    controls = {}
    for name in {p["path"] for p in provenance.values()}:
        assert all(hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == p["sha256"] for p in provenance.values() if p["path"] == name)
        for row in records(ROOT / name):
            if row["case_id"] in provenance and provenance[row["case_id"]]["path"] == name:
                controls[row["case_id"]] = row
    assert len(controls) == 34
    old = {cid: verify_trace(row, payloads, row["effective_qdrant_filter"]) for cid, row in controls.items()}
    assert {m: statistics.mean(v[m] for v in old.values()) for m in METRICS} == prep["control_metrics"]
    gold = {r["case_id"]: {k: r[k] for k in ("question", "relevant_parent_ids", "answerability")}
            for r in records(replay.GOLD) if r["case_id"] in controls}
    results, details = {}, []
    for signal in SIGNALS:
        folder = OUTPUT / signal
        if not (folder / "run_manifest.json").exists():
            continue
        state = read(folder / "run_manifest.json")
        assert state["status"] == "complete" and state["completed_cases"] == 34
        assert not state["safety_events"] and not state["guard_denied"] and state["protected_unchanged"]
        assert state["Qdrant_write_calls"] == state["expansion_generation_calls"] == 0 and not state["baseline_rerun"]
        before, after = read(folder / "collection.before.json"), read(folder / "collection.after.json")
        assert before == after and before["payload_sha256"] == prep["overlay_sha256"]
        checks = {r["case_id"]: r for r in preflight[signal]["checks"]}
        new, loss, live, reused = {}, [], 0, 0
        for cid, check in checks.items():
            rule = check["rule"]
            # Rebuild allowed Parent IDs from metadata, with no Gold-dependent rescue.
            allowed = []
            for pid, metadata in parents.items():
                actual = metadata[rule["field"]]
                values = list(actual) if isinstance(actual, set) else actual if isinstance(actual, list) else [actual]
                if not rule["targets"] or actual in (None, [], "") or not values or set(values) & set(rule["targets"]):
                    allowed.append(pid)
            allowed.sort()
            assert len(allowed) == check["allowed_parent_count"]
            material = json.dumps(allowed, ensure_ascii=False, sort_keys=True).encode()
            assert hashlib.sha256(material).hexdigest() == check["allowed_parent_sha256"]
            loss.extend({"case_id": cid, "parent_id": pid} for pid in gold[cid]["relevant_parent_ids"] if pid not in allowed)
            expected = controls[cid]["effective_qdrant_filter"]
            if check["changed"]:
                expected = {"must": ([expected] if expected else []) + [{"key": "parent_id", "match": {"any": allowed}}]}
            assert preflight[signal]["variants"][cid]["condition"]["effective_qdrant_filter"] == expected
        assert not loss and not preflight[signal]["gold_loss_pairs"]
        for row in records(folder / "cases.jsonl"):
            cid = row["case_id"]
            assert cid in controls and cid not in new and row["signal"] == signal
            if row["execution"] == "reuse_immutable_control":
                assert not checks[cid]["changed"] and row["metrics"] == old[cid]
                new[cid] = old[cid]
                reused += 1
            else:
                live += 1
                assert row["execution"] == "live_signal_retrieval" and checks[cid]["changed"]
                assert all(row[k] == gold[cid][k] for k in ("question", "relevant_parent_ids", "answerability"))
                assert row["query_understanding"] == controls[cid]["query_understanding"]
                variant = preflight[signal]["variants"][cid]
                assert row["plan"] == variant["plan"] and row["effective_qdrant_filter"] == variant["condition"]["effective_qdrant_filter"]
                old_plan = controls[cid]["plan"]
                assert {k: v for k, v in row["plan"].items() if k != "plan_id"} == {k: v for k, v in old_plan.items() if k != "plan_id"}
                new[cid] = verify_trace(row, payloads, row["effective_qdrant_filter"])
            details.append({"signal": signal, "case_id": cid, "question": gold[cid]["question"], "execution": row["execution"],
                "old": old[cid], "new": new[cid], "delta": {m: new[cid][m] - old[cid][m] for m in METRICS}})
        assert len(new) == 34 and live == state["live_cases"] and reused == state["reused_control_cases"]
        requests = read(folder / "qdrant-requests.json")
        batches = [r for r in requests if r["path"].endswith("/query/batch")]
        assert len(batches) == live and all(len(r["queries"]) == 7 and r["status_code"] == 200 for r in batches)
        assert all(r["method"] == "GET" or r["method"] == "POST" and r["path"].endswith(("/scroll", "/query/batch")) for r in requests)
        assert all(r["path"] == "/" or r["path"].startswith("/collections/music_knowledge_recovery_20261017_ce001e1a586e2c20_v1_v2_v4") for r in requests)
        for r in batches:
            assert all(q["limit"] == 80 and q["filter"] == preflight[signal]["variants"][r["case_id"]]["condition"]["effective_qdrant_filter"] for q in r["queries"])
        result = summarize(old, new)
        result.update(gold_loss_pairs=loss, live_cases=live, reused_control_cases=reused,
            passed=result["delta"]["recall_at_40"] >= .02 and not loss,
            exceptions=state["separate_source_exceptions"])
        results[signal] = result
    write(OUTPUT / "summary.json", results)
    write(OUTPUT / "per-case-comparison.json", details)
    write(OUTPUT / "independent-replay.json", {"status": "passed", "control_records_verified": 34,
        "complete_rounds": list(results), "logical_experiment_records": len(details),
        "network_calls": 0, "baseline_rerun": False, "protected_files_verified": len(prep["protected_before"]),
        "gold_loss_checks": "all689 Parents from frozen metadata+4point overlay; independent rebuild per signal",
        "RRF_selection_metrics_recomputed": True})
    print(json.dumps(results, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
