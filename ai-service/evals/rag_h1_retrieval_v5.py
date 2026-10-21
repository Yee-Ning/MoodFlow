"""Read-only seven-leg H1 retrieval of West's frozen 25-case population.

Reuse RagSource.collect and the Step 2 observer/scoring implementation. Only
plan construction and the approved seven-leg builder are adapted in scope.
"""

from __future__ import annotations

import argparse
from collections import Counter
import importlib.util
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

import httpx

from evals import rag_h1_bundle_v5 as original
from evals import rag_h1_union_v5 as frozen_run
from evals import rag_source_baseline as baseline
from evals.rag_h1_bundle_adapter import bundle_plan, seven_legs
from evals.rag_source_baseline_report import stage_diagnosis
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = original.REPO_ROOT / "workspace/moodflow-review/h1-retrieval-25-v5-20261018"
FROZEN_SHA = "6119d48b4260a7f4b7bd268ec48c41ceec7068d5de2f9b68d438a4c37849373d"
EXCLUDED = {"rag-v0-005", "rag-v0-008", "rag-v0-009", "rag-v0-045", "rag-v0-054"}


class ReadOnlyGuard(Gate0Guard):
    def audit(self, event, args):
        if event == "socket.getaddrinfo":
            host = args[0].decode("ascii") if isinstance(args[0], bytes) else args[0]
            if host in {"localhost", "127.0.0.1", "::1"} and str(args[1]) == "6333":
                return
        if event == "socket.connect" and isinstance(args[1], tuple):
            address = args[1]
            if len(address) >= 2 and address[0] in {"127.0.0.1", "::1"} and address[1] == 6333:
                return
            # Inspect the original caller here; delegating adds a stack frame
            # before Gate0Guard's Windows asyncio socketpair exception.
            caller = sys._getframe(1)
            if (caller.f_globals.get("__name__") == "socket"
                    and caller.f_code.co_name == "_fallback_socketpair"
                    and caller.f_locals.get("csock") is args[0]
                    and address == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
                    and address[0] in {"127.0.0.1", "::1"}):
                self.local_socketpairs += 1
                return
        super().audit(event, args)


def code_hashes():
    return {**frozen_run.code_hashes(), "evals/rag_h1_retrieval_v5.py": original.sha256(Path(__file__)),
            "evals/rag_source_baseline.py": original.sha256(Path(baseline.__file__))}


def load_inputs():
    verifier_path = frozen_run.OUTPUT / "verify_frozen.py"
    spec = importlib.util.spec_from_file_location("h1_frozen_query_verifier", verifier_path)
    verifier = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(verifier)
    verifier.verify()
    if original.sha256(frozen_run.OUTPUT / "frozen_queries.json") != FROZEN_SHA:
        raise original.H1Error("h1_retrieval_frozen_query_hash_mismatch")
    queries = original.read_json(frozen_run.OUTPUT / "frozen_queries.json")
    cases, projection, config, rerank, binding = baseline.preflight()
    ids = [r["case_id"] for r in queries]
    if ids != [i for i in original.CASE_IDS if i not in EXCLUDED]:
        raise original.H1Error("h1_retrieval_fixed_population_drift")
    selected = [c for c in cases if c["case_id"] in ids]
    control = [r for r in baseline.read_jsonl(baseline.OUTPUT / "cases.jsonl") if r["case_id"] in ids]
    frozen_control = original.read_json(frozen_run.OUTPUT / "candidate_baseline.json")
    if ([c["case_id"] for c in selected] != ids or baseline.summarize(control) != frozen_control["metrics"]
            or any(q["question"] != c["question"] for q, c in zip(queries, selected, strict=True))
            or config.fusion.overfetch_multiplier != 2 or config.fusion.per_leg_top_k != 80
            or config.fusion.maximum_legs != 6):
        raise original.H1Error("h1_retrieval_control_or_parameters_drift")
    return queries, selected, control, projection, config, rerank, binding


def prepare():
    queries, cases, control, _, config, rerank, binding = load_inputs()
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    (OUTPUT / "inputs.json").write_bytes((frozen_run.OUTPUT / "frozen_queries.json").read_bytes())
    original.write_json(OUTPUT / "baseline25_metrics.json", baseline.summarize(control))
    original.write_json(OUTPUT / "plans.json", [bundle_plan(q["completed_query"], q["queries"], q["hyde"], binding.profile).model_dump(mode="json") for q in queries])
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "recorded_at": original.now(), "authorization": "West explicitly approved: 按 25 题冻结清单进检索。",
        "frozen_queries_sha256": FROZEN_SHA, "frozen_completion_sha256": original.sha256(frozen_run.OUTPUT / "completion.json"),
        "case_ids": [c["case_id"] for c in cases], "excluded_case_ids": sorted(EXCLUDED),
        "code_sha256": code_hashes(), "protected_before": original.protected_hashes(),
        "artifact_sha256": {n: original.sha256(OUTPUT / n) for n in ("inputs.json", "baseline25_metrics.json", "plans.json")},
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "protocol": original.read_json(frozen_run.OUTPUT / "freeze_manifest.json")["protocol"],
        "leg_latency_policy": "Keep production batch execution. Record shared seven-leg search wall time; independent per-leg latency is unavailable in a batch and is never invented.",
        "new_deepseek_calls": 0, "judge_calls": 0, "secret_reads_allowed": False,
        "baseline_rerun": False, "no_H2": True,
    })
    print(json.dumps({"prepared_zero_network": True, "cases": len(cases), "legs_per_case": 7}), flush=True)


def load_prepared():
    inputs = load_inputs()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["code_sha256"] != code_hashes() or prep["protected_before"] != original.protected_hashes()
            or prep["frozen_completion_sha256"] != original.sha256(frozen_run.OUTPUT / "completion.json")
            or any(original.sha256(OUTPUT / n) != digest for n, digest in prep["artifact_sha256"].items())):
        raise original.H1Error("h1_retrieval_pre_call_drift")
    return inputs


def serialize_legs(results, elapsed_ms, *, projection, relevant, limit):
    serialized = []
    for result in results:
        leg = result.leg
        hits = []
        for rank, hit in enumerate(result.hits, 1):
            child = hit.payload["chunk_id"]
            expected = projection.children[child]
            if any(hit.payload.get(k) != expected.get(k) for k in ("content", "retrieval_content", "source_content_sha256")):
                raise original.H1Error("h1_retrieval_live_child_text_drift")
            hits.append(projection.item(child=child, parent=hit.payload["parent_id"], point=hit.point_id,
                                        relevant=relevant, rank=rank, raw_score=hit.score))
        serialized.append({"leg_id": leg.retrieval_leg, "variant_index": leg.variant_index,
                           "variant_kind": leg.variant_kind.value, "channel": leg.channel.value,
                           "text": leg.text, "weight": leg.weight, "limit": limit, "hit_count": len(hits),
                           "unique_parent_count": len({h["parent_id"] for h in hits}), "error_code": result.error_code,
                           "shared_search_wall_ms": elapsed_ms, "independent_leg_latency_ms": None,
                           "latency_scope": "shared_7_leg_search_including_production_fallback_if_any", "hits": hits})
    return serialized


def collect_case(case, understanding, projection, binding, config, request_log):
    rag_source = baseline.rag_source
    service = baseline.retrieval_module
    actual_backend = baseline.QdrantHybridSearchBackend
    actual_rrf = service.weighted_rrf
    observed = {"search_calls": 0, "rrf_calls": 0}

    class ObservedBackend(actual_backend):
        async def search(self, supplied_binding, legs, query_filter, *, limit):
            if len(legs) != 7 or query_filter is not None or limit != 80:
                raise original.H1Error("h1_retrieval_seven_leg_search_contract_drift")
            observed["search_calls"] += 1
            started = perf_counter()
            results = await super().search(supplied_binding, legs, query_filter, limit=limit)
            elapsed = (perf_counter() - started) * 1000
            observed["leg_results"] = serialize_legs(results, elapsed, projection=projection,
                                                    relevant=set(case["relevant_parent_ids"]), limit=limit)
            return results

    def plan(query, profile, limit):
        if query != case["question"] or understanding["question"] != query:
            raise original.H1Error("h1_retrieval_literal_input_drift")
        return bundle_plan(understanding["completed_query"], understanding["queries"], understanding["hyde"], profile, limit=limit)

    def fusion(results, *, k):
        observed["rrf_calls"] += 1
        if k != 60 or len(results) != 7 or any(r.leg.weight != 1.0 for r in results):
            raise original.H1Error("h1_retrieval_fusion_contract_drift")
        return actual_rrf(results, k=k)

    started = perf_counter()
    with (patch.object(rag_source, "literal_search_plan", plan),
          patch.object(service, "build_retrieval_legs", seven_legs),
          patch.object(service, "weighted_rrf", fusion),
          patch.object(baseline, "QdrantHybridSearchBackend", ObservedBackend)):
        record = baseline.collect_case(case, projection, binding, config, request_log)
    record.update(observed, query_understanding=understanding, case_wall_ms=(perf_counter() - started) * 1000)
    record["healthy"] = bool(record["healthy"] and observed["search_calls"] == observed["rrf_calls"] == 1
                             and len(record.get("selection_input_rrf", [])) == 80
                             and 12 <= len(record.get("rerank_input", [])) == len(record.get("selection", [])) <= 40
                             and len(record.get("leg_results", [])) == 7
                             and all(leg["error_code"] is None for leg in record.get("leg_results", [])))
    if not record["healthy"]:
        record["metrics"] = None
    return record


def compare_case(experiment, control):
    stage_keys = ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12")
    changes = {}
    for stage in stage_keys:
        before = {r["parent_id"] for r in control[stage]}
        after = {r["parent_id"] for r in experiment[stage]}
        relevant = set(experiment["relevant_parent_ids"])
        changes[stage] = {"gained_parents": sorted(after - before), "lost_parents": sorted(before - after),
                          "gained_relevant_parents": sorted((after - before) & relevant),
                          "lost_relevant_parents": sorted((before - after) & relevant)}
    gained = changes["rrf_top40"]["gained_relevant_parents"]
    attribution = []
    for parent in gained:
        winning_children = [r for r in experiment["rrf_top40"] if r["parent_id"] == parent]
        legs = []
        for leg in experiment["leg_results"]:
            hits = [h for h in leg["hits"] if h["parent_id"] == parent]
            if hits:
                legs.append({"leg_id": leg["leg_id"], "text": leg["text"], "channel": leg["channel"],
                             "variant_index": leg["variant_index"], "hits": hits,
                             "rrf_top40_contributing_children": [h for h in hits if h["child_id"] in {c["child_id"] for c in winning_children}]})
        attribution.append({"parent_id": parent, "rrf_top40_children": winning_children,
                            "retrieving_legs": legs, "causal_claim": "candidate provenance only; no marginal leg ablation"})
    return {"case_id": experiment["case_id"], "question": experiment["question"],
            "question_group": experiment["question_group"], "answerability": experiment["answerability"],
            "baseline": control["metrics"], "experiment": experiment["metrics"],
            "delta": {k: experiment["metrics"][k] - control["metrics"][k] for k in baseline.METRICS},
            "parent_changes": changes, "gained_gold_parent_attribution": attribution,
            "baseline_stage_diagnosis": stage_diagnosis(control), "experiment_stage_diagnosis": stage_diagnosis(experiment)}


def summarize_comparison(records, controls):
    experiment = baseline.summarize(records)
    control = baseline.summarize(controls)
    timeline = experiment["by_question_group"]["timeline"]["recall_at_40"] >= 0.6
    overview = experiment["by_question_group"]["artist_overview"]["recall_at_40"] > control["by_question_group"]["artist_overview"]["recall_at_40"]
    return {"baseline": control, "experiment": experiment,
            "overall_delta": {k: experiment["all_scored"][k] - control["all_scored"][k] for k in baseline.METRICS},
            "hypothesis": {"timeline_threshold": 0.6, "timeline_pass": timeline, "artist_overview_pass": overview,
                           "conclusion": "成立" if timeline and overview else "部分成立" if timeline or overview else "不成立",
                           "definition": "Both preregistered conditions:成立; exactly one:部分成立; neither:不成立."}}


def run(guard):
    queries, cases, controls, projection, config, _, binding = load_prepared()
    if (OUTPUT / "run_manifest.json").exists() or (OUTPUT / "cases.jsonl").exists():
        raise original.H1Error("h1_retrieval_already_attempted_no_automatic_repeat")
    manifest = {"started_at": original.now(), "status": "loading_pinned_local_models", "completed_case_count": 0,
                "new_deepseek_calls": 0, "judge_calls": 0, "secret_reads": 0, "baseline_rerun": False}
    original.write_json(OUTPUT / "run_manifest.json", manifest)
    requests, records = [], []
    error_code = None
    try:
        print("Preparing pinned local embedding/reranking models; external network disabled", flush=True)
        encoder, _, scorer, _ = baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch
        manifest["runtime"] = {"torch": torch.__version__, "cuda": torch.cuda.is_available(),
                               "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                               "embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        original.write_json(OUTPUT / "run_manifest.json", manifest)
        sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

        def guarded_sync(client, request, *args, **kwargs):
            baseline.guard_request(request, requests)
            return sync_send(client, request, *args, **kwargs)

        async def guarded_async(client, request, *args, **kwargs):
            baseline.guard_request(request, [])  # The unchanged backend hook records it once.
            return await async_send(client, request, *args, **kwargs)

        with (patch.object(httpx.Client, "send", guarded_sync), patch.object(httpx.AsyncClient, "send", guarded_async),
              (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream):
            for index, (case, query) in enumerate(zip(cases, queries, strict=True), 1):
                offset = len(requests)
                row = collect_case(case, query, projection, binding, config, requests)
                row["qdrant_requests"] = requests[offset:]
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                records.append(row)
                manifest.update(completed_case_count=len(records), last_case_id=row["case_id"], status="running")
                original.write_json(OUTPUT / "run_manifest.json", manifest)
                print(json.dumps({"progress": f"{index}/25", "case_id": row["case_id"], "healthy": row["healthy"],
                                  "metrics": row["metrics"]}, ensure_ascii=False), flush=True)
                if not row["healthy"]:
                    raise original.H1Error("h1_retrieval_pipeline_unhealthy_stop_no_retry")
    except Exception as exc:
        error_code = str(exc) if isinstance(exc, original.H1Error) else type(exc).__name__
    finally:
        prep = original.read_json(OUTPUT / "pre_call_manifest.json")
        unchanged = prep["protected_before"] == original.protected_hashes()
        manifest.update(finished_at=original.now(), error_code=error_code, protected_unchanged=unchanged,
                        guard_denied=guard.denied, qdrant_request_counts=dict(Counter(f"{r['method']} {r['path']}" for r in requests)),
                        status="complete" if len(records) == 25 and error_code is None and unchanged and not guard.denied else "incomplete")
        original.write_json(OUTPUT / "run_manifest.json", manifest)
        original.write_json(OUTPUT / "qdrant_requests.json", requests)
    if manifest["status"] == "complete":
        original.write_json(OUTPUT / "metrics.json", summarize_comparison(records, controls))
        original.write_json(OUTPUT / "comparisons.json", [compare_case(r, c) for r, c in zip(records, controls, strict=True)])
    print(json.dumps({"status": manifest["status"], "completed": len(records), "error_code": error_code}), flush=True)
    return 0 if manifest["status"] == "complete" else 1


def verify():
    _, cases, controls, projection, config, _, _ = load_prepared()
    rows = baseline.read_jsonl(OUTPUT / "cases.jsonl")
    if ([r["case_id"] for r in rows] != [c["case_id"] for c in cases] or any(not r["healthy"] for r in rows)):
        raise original.H1Error("h1_retrieval_incomplete_population")
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
    from app.rag.retrieval.legs import RetrievalLeg
    from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
    from app.rag.retrieval.selection import select_candidates
    for row in rows:
        raw = []
        for leg in row["leg_results"]:
            reconstructed = RetrievalLeg(variant_index=leg["variant_index"], variant_kind=QueryVariantKind(leg["variant_kind"]),
                                         channel=SearchChannel(leg["channel"]), text=leg["text"], weight=leg["weight"])
            raw.append(LegSearchResult(leg=reconstructed, error_code=leg["error_code"], hits=[
                RawSearchHit(h["point_id"], h["raw_score"], projection.children[h["child_id"]]) for h in leg["hits"]]))
        fused = weighted_rrf(raw, k=config.fusion.rrf_k)
        for field, count in (("rrf_top40", 40), ("selection_input_rrf", 80)):
            if ([h.payload["chunk_id"] for h in fused[:count]] != [h["child_id"] for h in row[field]]
                    or any(not math.isclose(a.fusion_score, b["fusion_score"], abs_tol=1e-15)
                           for a, b in zip(fused[:count], row[field], strict=True))):
                raise original.H1Error("h1_retrieval_rrf_offline_replay_mismatch")
        selected, warnings = select_candidates(fused[:80], SearchPlan.model_validate(row["plan"]), config)
        if ([c.primary_child_id for c in selected] != [c["child_id"] for c in row["selection"]]
                or warnings != row["selection_warnings"]
                or any(c.matched_child_ids != r["matched_child_ids"] for c, r in zip(selected, row["selection"], strict=True))):
            raise original.H1Error("h1_retrieval_selection_offline_replay_mismatch")
        for field in ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12"):
            if any(projection.children[h["child_id"]]["parent_id"] != h["parent_id"] or h["parent_id"] not in projection.parents for h in row[field]):
                raise original.H1Error("h1_retrieval_parent_mapping_mismatch")
        scores = [r["rerank_score"] for r in row["rerank_top12"]]
        if (len(scores) != 12 or any(s is None or not math.isfinite(s) for s in scores)
                or scores != sorted(scores, reverse=True)
                or len({r["parent_id"] for r in row["rerank_top12"]}) != 12
                or not {r["child_id"] for r in row["rerank_top12"]} <= {r["child_id"] for r in row["selection"]}):
            raise original.H1Error("h1_retrieval_rerank_record_invalid")
        replay = baseline.score_case(row, row["rrf_top40"], row["selection"], row["rerank_top12"])
        if any(row[k] != v for k, v in replay.items()):
            raise original.H1Error("h1_retrieval_metric_offline_replay_mismatch")
    if (original.read_json(OUTPUT / "metrics.json") != summarize_comparison(rows, controls)
            or original.read_json(OUTPUT / "comparisons.json") != [compare_case(r, c) for r, c in zip(rows, controls, strict=True)]):
        raise original.H1Error("h1_retrieval_summary_or_attribution_replay_mismatch")
    result = {"verified": True, "case_count": 25, "legs": 175, "rrf_and_metrics_offline_replay": True,
              "attribution_recomputed": True, "all_parents_within_413": True, "protected_inputs_unchanged": True,
              "verification_network_calls": 0}
    print(json.dumps(result), flush=True)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    args = parser.parse_args()
    configure_offline()
    guard = ReadOnlyGuard() if args.command == "run" else Gate0Guard()
    sys.addaudithook(guard.audit)
    if args.command == "prepare":
        prepare()
    elif args.command == "verify":
        verify()
    else:
        return run(guard)
    if guard.denied:
        raise original.H1Error("h1_retrieval_offline_operation_denied")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
