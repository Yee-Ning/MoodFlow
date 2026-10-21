"""H3: H2's frozen pipeline plus exactly three OpenCC s2t BM25 legs.

Evaluation scope only. The plan, dense inputs, filter, selection and scorer are
reused unchanged; the ten-leg builder is patched only inside this experiment.
"""

from __future__ import annotations

import argparse
from collections import Counter
from functools import lru_cache
import importlib.metadata
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from evals import rag_h2_metadata_v5 as h2
from evals.ragas_gate0 import Gate0Guard


ROOT = h2.ROOT
CONTROL = h2.OUTPUT / "attempt-02"
OUTPUT = ROOT / "workspace/moodflow-review/h3-traditional-bm25-v5-20261018"
OPENCC_PATH = Path.home() / ".cache/moodflow-h3-opencc"
TARGET = "parent-bb32b222016ed83b4eccabaa"
STAGES = h2.STAGES
sha256, read_json, read_jsonl, write_json = h2.sha256, h2.read_json, h2.read_jsonl, h2.write_json
stack, dumped_filter, matches = h2.stack, h2.dumped_filter, h2.matches
SafetyStop = h2.SafetyStop


class H3Error(RuntimeError):
    pass


@lru_cache
def converter():
    sys.path.insert(0, str(OPENCC_PATH))
    from opencc import OpenCC

    if importlib.metadata.version("opencc-python-reimplemented") != "0.1.7":
        raise H3Error("h3_opencc_version_drift")
    return OpenCC("s2t")


def conversions(queries):
    cc = converter()
    if cc.convert("周杰伦 东风破 专辑 音乐 风格 演变") != "周杰倫 東風破 專輯 音樂 風格 演變":
        raise H3Error("h3_key_conversion_failed")
    return [{"case_id": row["case_id"], "subquery_index": i,
             "original": text, "traditional": cc.convert(text)}
            for row in queries for i, text in enumerate(row["queries"])]


def ten_legs(plan, dense_vectors, config):
    from app.rag.query_understanding.models import SearchChannel
    from app.rag.retrieval.legs import RetrievalLeg

    h1, _ = stack()
    legs = h1.seven_legs(plan, dense_vectors, config)
    for i, variant in enumerate(plan.queries[:3]):
        legs.append(RetrievalLeg(variant_index=4 + i, variant_kind=variant.kind,
            channel=SearchChannel.LEXICAL, text=converter().convert(variant.text), weight=1.0))
    return legs


def protected_hashes():
    result = h2.protected_hashes()
    directories = [h2.OUTPUT, ROOT / "ai-service/evals/datasets"]
    for directory in directories:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                result[path.relative_to(ROOT).as_posix()] = sha256(path)
    return result


def dependency_hashes():
    converter()
    return {path.relative_to(OPENCC_PATH).as_posix(): sha256(path)
            for path in sorted((OPENCC_PATH / "opencc").rglob("*"))
            if path.is_file() and "__pycache__" not in path.parts}


def load_inputs():
    # The historical H1 CLI verifier hardcodes its original Git branch. This
    # experiment reads its frozen data directly and verifies all runtime/data
    # contracts, without pretending to run on that branch or changing it.
    h1, baseline = stack()
    all_cases, projection, config, rerank, binding = baseline.preflight()
    queries = read_json(CONTROL / "inputs.json")
    ids = [row["case_id"] for row in queries]
    cases = [row for row in all_cases if row["case_id"] in ids]
    if (sha256(CONTROL / "inputs.json") != h1.FROZEN_SHA
            or sha256(h1.OUTPUT / "inputs.json") != h1.FROZEN_SHA
            or ids != [row["case_id"] for row in cases] or len(set(ids)) != 25):
        raise H3Error("h3_frozen_25_query_population_drift")
    controls = read_jsonl(CONTROL / "cases.jsonl")
    plans, filters = read_json(CONTROL / "plans.json"), read_json(CONTROL / "filters.json")
    manifest = read_json(CONTROL / "pre_call_manifest.json")
    if (read_json(CONTROL / "run_manifest.json")["status"] != "complete"
            or not read_json(CONTROL / "verification.json")["verified"]
            or config.model_dump(mode="json") != manifest["retrieval_config"]
            or rerank.model_dump(mode="json") != manifest["rerank_config"]
            or len(controls) != 25):
        raise H3Error("h3_h2_control_or_config_drift")
    for name, digest in manifest["artifact_sha256"].items():
        if sha256(CONTROL / name) != digest:
            raise H3Error("h3_h2_prepared_input_drift:" + name)
    for name, digest in read_json(h1.OUTPUT / "pre_call_manifest.json")["code_sha256"].items():
        if sha256(baseline.AI_ROOT / name) != digest:
            raise H3Error("h3_h1_pipeline_code_drift:" + name)
    for query, case, control, plan, condition in zip(queries, cases, controls, plans, filters, strict=True):
        if (query["case_id"] != control["case_id"] or case["case_id"] != control["case_id"]
                or case["question"] != control["question"] or not control["healthy"]
                or case["relevant_parent_ids"] != control["relevant_parent_ids"]
                or control["plan"] != plan or condition["effective_qdrant_filter"] != control["effective_qdrant_filter"]):
            raise H3Error("h3_h2_population_or_plan_drift")
        if any(control[key] != value for key, value in baseline.score_case(
                case, control["rrf_top40"], control["selection"], control["rerank_top12"]).items()):
            raise H3Error("h3_h2_control_scoring_drift")
    if next(row for row in controls if row["case_id"] == "rag-v0-036")["metrics"]["recall_at_40"] != 0.5:
        raise H3Error("h3_036_preregistered_baseline_drift")
    return queries, cases, controls, plans, filters, projection, config, rerank, binding


class RequestAudit(h2.RequestAudit):
    def validate(self, request):
        if request.method == "POST" and request.url.path.endswith("/points/query/batch"):
            base = f"/collections/{h2.COLLECTION}/points/query/batch"
            if (request.url.scheme != "http" or request.url.host not in {"localhost", "127.0.0.1", "::1"}
                    or request.url.port != 6333 or request.url.username or request.url.password
                    or request.url.path != base or self.active is None):
                raise SafetyStop("h3_query_destination_or_case_forbidden")
            searches = json.loads(request.content)["searches"]
            if (len(searches) != 10
                    or [item["using"] for item in searches] != ["dense", "bm25", "dense", "bm25", "dense", "bm25", "dense", "bm25", "bm25", "bm25"]
                    or any(item.get("filter") != self.expected_filter or item["limit"] != 80 or item.get("with_vector") is not False for item in searches)
                    or any(item.get("params", {}).get("hnsw_ef") != 128 for item in searches if item["using"] == "dense")
                    or [h2.query_document(item["query"]) for item in searches if item["using"] == "bm25"] != self.expected_documents):
                raise SafetyStop("h3_ten_leg_http_contract_drift")
            return
        super().validate(request)


def prepare(guard):
    queries, cases, controls, plans, filters, projection, config, rerank, _ = load_inputs()
    if (OUTPUT / "pre_call_manifest.json").exists():
        raise H3Error("h3_already_prepared_no_overwrite")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    converted = conversions(queries)
    before = protected_hashes()
    audit = RequestAudit()
    with audit.installed():
        snapshot = h2.inventory(audit, projection)
    if before != protected_hashes() or guard.denied:
        raise H3Error("h3_prepare_protected_drift")
    for name in ("inputs.json", "plans.json", "filters.json"):
        (OUTPUT / name).write_bytes((CONTROL / name).read_bytes())
    write_json(OUTPUT / "conversions.json", converted)
    write_json(OUTPUT / "collection_before.json", snapshot)
    write_json(OUTPUT / "preflight_requests.json", audit.requests)
    _, baseline = stack()
    write_json(OUTPUT / "pre_call_manifest.json", {
        "recorded_at": baseline.now(), "authorization": "West explicitly requested H3 in this chat.",
        "control_directory": CONTROL.relative_to(ROOT).as_posix(), "case_ids": [row["case_id"] for row in cases],
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "protected_before": before, "adapter_sha256": sha256(Path(__file__)),
        "artifact_sha256": {name: sha256(OUTPUT / name) for name in ("inputs.json", "plans.json", "filters.json", "conversions.json")},
        "opencc": {"distribution": "opencc-python-reimplemented", "version": "0.1.7", "mode": "s2t",
                   "isolated_runtime": str(OPENCC_PATH), "files_sha256": dependency_hashes()},
        "protocol": {"only_variable": "three additional traditional-query BM25 legs", "legs": 10,
                     "new_leg_weights": [1.0, 1.0, 1.0], "rrf_k": 60, "candidate": 40, "per_leg": 80,
                     "selection_input_max": 80, "doc_cap": 4, "source_cap": 5, "rerank_top_k": 12,
                     "filter": "byte-identical H2 three-tier filters", "dense_added": 0},
        "preregistration": {"036_recall_at_40_strictly_above": 0.5, "target_parent": TARGET,
                            "no_regression": "all 25 cases: each Step2 metric must not decline; recall-only also reported"},
        "baseline_rerun": False, "gold_version_for_scoring": "v5 (same as H2; never mix V5.1)",
        "conversion_review": "pending Codex manual review before run; not a West signature"})
    print(json.dumps({"prepared": True, "cases": len(controls), "conversions": len(converted)}), flush=True)


def load_prepared():
    inputs = load_inputs()
    prep = read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["protected_before"] != protected_hashes() or prep["adapter_sha256"] != sha256(Path(__file__))
            or prep["opencc"]["files_sha256"] != dependency_hashes()
            or any(sha256(OUTPUT / name) != digest for name, digest in prep["artifact_sha256"].items())
            or read_json(OUTPUT / "conversions.json") != conversions(inputs[0])):
        raise H3Error("h3_prepared_input_or_code_drift")
    return inputs


def run(guard):
    inputs = load_prepared()
    queries, cases, controls, plans, filters, projection, config, _rerank, binding = inputs
    _, baseline = stack()
    review = read_json(OUTPUT / "conversion_review.json")
    if (review["conversions_sha256"] != sha256(OUTPUT / "conversions.json")
            or review["reviewer"] != "Codex" or review["reviewed_count"] != 75 or not review["passed"]):
        raise H3Error("h3_conversion_review_missing_or_stale")
    if (OUTPUT / "run_manifest.json").exists() or (OUTPUT / "cases.jsonl").exists():
        raise H3Error("h3_already_attempted_no_repeat")
    state = {"started_at": baseline.now(), "status": "loading_pinned_local_models", "completed_case_count": 0,
             "baseline_rerun": False, "qdrant_write_calls": 0, "new_llm_calls": 0, "secret_reads": 0}
    write_json(OUTPUT / "run_manifest.json", state)
    audit, records, error = RequestAudit(), [], None
    try:
        encoder, _, scorer, _ = baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch

        state["runtime"] = {"torch": torch.__version__, "cuda": torch.cuda.is_available(),
                            "embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        write_json(OUTPUT / "run_manifest.json", state)
        with audit.installed(), (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for index, (query, case, control, plan, condition) in enumerate(zip(queries, cases, controls, plans, filters, strict=True), 1):
                audit.active = case["case_id"]
                audit.expected_filter = condition["effective_qdrant_filter"]
                texts = [*query["queries"], *[converter().convert(text) for text in query["queries"]]]
                audit.expected_documents = [{"text": text, "model": config.bm25.model_name, "options": config.bm25.query_options()} for text in texts]
                offset = len(audit.requests)
                extra = [{"text": text, "channel": "lexical"} for text in texts[3:]]
                row = collect_case({**case, "control_legs": [*control["leg_results"], *extra]}, query,
                                   plan, condition, projection, config, binding, audit)
                row["qdrant_requests"] = audit.requests[offset:]
                row["first_seven_vs_h2"] = compare_old_legs(control, row)
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                records.append(row)
                state.update(status="running", completed_case_count=len(records), last_case_id=row["case_id"])
                write_json(OUTPUT / "run_manifest.json", state)
                print(json.dumps({"progress": f"{index}/25", "case_id": row["case_id"], "healthy": row["healthy"],
                                  "metrics": row["metrics"], "original_seven_unchanged": row["first_seven_vs_h2"]["identical"]}), flush=True)
                if not row["healthy"]:
                    raise H3Error("h3_unhealthy_case_stop")
            audit.active = None
            write_json(OUTPUT / "collection_after.json", h2.inventory(audit, projection))
    except SafetyStop as exc:
        error = str(exc)
    except Exception as exc:
        error = str(exc) if isinstance(exc, (H3Error, h2.H2Error)) else type(exc).__name__
    finally:
        unchanged = protected_hashes() == read_json(OUTPUT / "pre_call_manifest.json")["protected_before"]
        complete = len(records) == 25 and error is None and unchanged and not guard.denied and not audit.safety_events
        state.update(status="complete" if complete else "incomplete", finished_at=baseline.now(), error_code=error,
                     protected_unchanged=unchanged, guard_denied=guard.denied, safety_events=audit.safety_events,
                     qdrant_request_counts=dict(Counter(f"{row['method']} {row['path']}" for row in audit.requests)))
        write_json(OUTPUT / "run_manifest.json", state)
        write_json(OUTPUT / "qdrant_requests.json", audit.requests)
    print(json.dumps({"status": state["status"], "completed": len(records), "error_code": error}), flush=True)
    return 0 if complete else 1


def compare_old_legs(control, row):
    details = []
    for before, after in zip(control["leg_results"], row["leg_results"][:7], strict=True):
        old = [(hit["point_id"], hit["raw_score"]) for hit in before["hits"]]
        new = [(hit["point_id"], hit["raw_score"]) for hit in after["hits"]]
        details.append({"leg_id": before["leg_id"], "same_order_and_raw_scores": old == new,
                        "same_membership": {item[0] for item in old} == {item[0] for item in new}})
    return {"identical": all(item["same_order_and_raw_scores"] for item in details), "legs": details}


def verify():
    _, baseline = stack()
    from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
    from app.rag.retrieval.legs import RetrievalLeg
    from app.rag.retrieval.selection import select_candidates

    queries, cases, controls, plans, filters, projection, config, _, _ = load_prepared()
    state, rows = read_json(OUTPUT / "run_manifest.json"), read_jsonl(OUTPUT / "cases.jsonl")
    if state["status"] != "complete" or len(rows) != 25 or state["safety_events"] or state["guard_denied"]:
        raise H3Error("h3_incomplete_run")
    for row, query, case, control, plan, condition in zip(rows, queries, cases, controls, plans, filters, strict=True):
        if (row["case_id"] != case["case_id"] or not row["healthy"] or row["plan"] != plan
                or row["relevant_parent_ids"] != case["relevant_parent_ids"] or row["answerability"] != case["answerability"]
                or row["search_calls"] != 1 or row["rrf_calls"] != 1 or len(row["leg_results"]) != 10):
            raise H3Error("h3_population_or_plan_or_count_drift")
        expected = [(leg["text"], leg["channel"], leg["weight"]) for leg in control["leg_results"]]
        expected += [(converter().convert(text), "lexical", 1.0) for text in query["queries"]]
        if [(leg["text"], leg["channel"], leg["weight"]) for leg in row["leg_results"]] != expected:
            raise H3Error("h3_leg_spec_drift")
        raw = []
        for leg in row["leg_results"]:
            if leg["error_code"] is not None or len(leg["hits"]) > 80:
                raise H3Error("h3_failed_or_oversize_leg")
            reconstruction = RetrievalLeg(variant_index=leg["variant_index"], variant_kind=QueryVariantKind(leg["variant_kind"]),
                channel=SearchChannel(leg["channel"]), text=leg["text"], weight=leg["weight"])
            raw.append(LegSearchResult(leg=reconstruction, hits=[RawSearchHit(hit["point_id"], hit["raw_score"],
                         projection.children[hit["child_id"]]) for hit in leg["hits"]]))
            if any(not matches(projection.children[hit["child_id"]], condition["effective_qdrant_filter"]) for hit in leg["hits"]):
                raise H3Error("h3_filter_violation")
        fused = weighted_rrf(raw, k=60)
        for field, count in (("rrf_top40", 40), ("selection_input_rrf", 80)):
            saved = row[field]
            if ([hit.payload["chunk_id"] for hit in fused[:count]] != [hit["child_id"] for hit in saved]
                    or any(not math.isclose(hit.fusion_score, item["fusion_score"], rel_tol=0, abs_tol=1e-15)
                           for hit, item in zip(fused[:count], saved, strict=True))):
                raise H3Error("h3_rrf_replay_mismatch")
        selected, warnings = select_candidates(fused[:80], SearchPlan.model_validate(plan), config)
        if ([candidate.primary_child_id for candidate in selected] != [item["child_id"] for item in row["selection"]]
                or warnings != row["selection_warnings"] or len(row["rerank_top12"]) != min(12, len(selected))):
            raise H3Error("h3_selection_replay_mismatch")
        scores = [item["rerank_score"] for item in row["rerank_top12"]]
        if (any(value is None or not math.isfinite(value) for value in scores) or scores != sorted(scores, reverse=True)
                or not {hit["child_id"] for hit in row["rerank_top12"]} <= {hit["child_id"] for hit in row["selection"]}):
            raise H3Error("h3_rerank_provenance_or_scores_invalid")
        if any(row[key] != value for key, value in baseline.score_case(case, row["rrf_top40"], row["selection"], row["rerank_top12"]).items()):
            raise H3Error("h3_metric_replay_mismatch")
        if row["first_seven_vs_h2"] != compare_old_legs(control, row):
            raise H3Error("h3_original_leg_comparison_invalid")
        for field in STAGES:
            for hit in row[field]:
                projection.item(child=hit["child_id"], parent=hit["parent_id"], point=hit["point_id"], relevant=set(case["relevant_parent_ids"]))
    requests = read_json(OUTPUT / "qdrant_requests.json")
    batches = [request for request in requests if request["path"].endswith("/query/batch")]
    if len(batches) != 25 or any(len(request["queries"]) != 10 or request["status_code"] != 200 for request in batches):
        raise H3Error("h3_request_count_or_response_mismatch")
    for request, row, condition in zip(batches, rows, filters, strict=True):
        if request["case_id"] != row["case_id"] or any(query["filter"] != condition["effective_qdrant_filter"] for query in request["queries"]):
            raise H3Error("h3_wire_filter_mismatch")
    result = {"verified": True, "cases": 25, "legs": 250, "rrf_selection_metrics_replayed": True,
              "protected_unchanged": True, "verification_retrieval_calls": 0,
              "original_seven_identical_cases": sum(row["first_seven_vs_h2"]["identical"] for row in rows)}
    write_json(OUTPUT / "verification.json", result)
    print(json.dumps(result), flush=True)
    return result


def collect_case(case, query, plan_data, filter_data, projection, config, binding, audit):
    h1, baseline = stack()
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.retrieval import qdrant as qdrant_module
    from qdrant_client import models

    service, source = baseline.retrieval_module, baseline.rag_source
    actual_backend, actual_rrf = baseline.QdrantHybridSearchBackend, service.weighted_rrf
    actual_collect, actual_predicate = source.RagSource.collect, service._needs_exploratory_entity_fallback
    plan = SearchPlan.model_validate(plan_data)
    compiled = models.Filter.model_validate(filter_data["effective_qdrant_filter"]) if filter_data["effective_qdrant_filter"] is not None else None
    observed = {"search_calls": 0, "rrf_calls": 0, "fallback_checks": []}

    class ObservedBackend(actual_backend):
        async def search(self, supplied_binding, legs, query_filter, *, limit):
            observed["search_calls"] += 1
            if (observed["search_calls"] != 1 or len(legs) != 10 or limit != 80
                    or dumped_filter(query_filter) != filter_data["effective_qdrant_filter"]):
                raise SafetyStop("h3_unexpected_extra_search_or_filter_drift")
            expected = [(item["text"], item["channel"]) for item in case["control_legs"]]
            if [(leg.text, leg.channel.value) for leg in legs] != expected:
                raise SafetyStop("h3_frozen_leg_drift")
            started = perf_counter()
            results = await super().search(supplied_binding, legs, query_filter, limit=limit)
            if any(not matches(projection.children[hit.payload["chunk_id"]], filter_data["effective_qdrant_filter"]) for result in results for hit in result.hits):
                raise SafetyStop("h3_returned_child_violates_filter")
            observed["leg_results"] = h1.serialize_legs(results, (perf_counter() - started) * 1000,
                projection=projection, relevant=set(case["relevant_parent_ids"]), limit=limit)
            for item in observed["leg_results"]:
                item["latency_scope"] = "shared_10_leg_search"
            return results

    def compile_filter(intermediate):
        if intermediate != plan.metadata_filter:
            raise SafetyStop("h3_intermediate_ast_drift")
        return compiled

    def literal(query_text, profile, limit):
        if query_text != case["question"] or profile != binding.profile or limit != 40:
            raise SafetyStop("h3_original_query_or_binding_drift")
        return plan

    def fusion(results, *, k):
        observed["rrf_calls"] += 1
        if observed["rrf_calls"] != 1 or k != 60 or len(results) != 10 or any(result.leg.weight != 1 for result in results):
            raise SafetyStop("h3_fusion_contract_drift")
        return actual_rrf(results, k=k)

    def fallback(plan, count, supplied_config):
        observed["fallback_checks"].append({"candidate_count": count, "threshold": supplied_config.selection.exploratory_entity_fallback_below})
        return h2.intercept_fallback(plan, count, supplied_config, actual_predicate, audit)

    def collect(self, request, *, limit):
        result = actual_collect(self, request, limit=limit)
        observed["returned_source_child_ids"] = [item.metadata["chunk_id"] for item in result]
        return result

    def no_credentials(name, default=None):
        if name == config.qdrant.api_key_env:
            return None
        raise SafetyStop("h3_unexpected_environment_read")

    started = perf_counter()
    with (patch.object(source, "literal_search_plan", literal), patch.object(source.RagSource, "collect", collect),
          patch.object(service, "build_retrieval_legs", ten_legs), patch.object(service, "weighted_rrf", fusion),
          patch.object(service, "compile_qdrant_filter", compile_filter), patch.object(service, "_needs_exploratory_entity_fallback", fallback),
          patch.object(baseline, "QdrantHybridSearchBackend", ObservedBackend),
          patch.object(baseline, "guard_request", lambda request, _log: audit.validate(request)),
          patch.object(baseline, "os", SimpleNamespace(getenv=no_credentials)),
          patch.object(qdrant_module, "os", SimpleNamespace(getenv=no_credentials))):
        record = baseline.collect_case(case, projection, binding, config, [])
    record.update(observed, query_understanding=query, effective_qdrant_filter=filter_data["effective_qdrant_filter"],
                  intermediate_ast_requires_three_tier_compiler=filter_data["intermediate_ast_requires_three_tier_compiler"],
                  case_wall_ms=(perf_counter() - started) * 1000)
    selected = record.get("selection", [])
    expected_status = "success" if selected else "empty"
    record["healthy"] = (record.get("retrieval_status") == expected_status and record.get("rerank_status") == expected_status
        and not record["source_status"] and not record.get("rerank_fallback_used")
        and observed["search_calls"] == observed["rrf_calls"] == len(observed["fallback_checks"]) == 1
        and len(record.get("leg_results", [])) == 10 and all(leg["error_code"] is None for leg in record.get("leg_results", []))
        and len(record["rrf_top40"]) <= 40 and len(record["selection_input_rrf"]) <= 80
        and len(selected) <= 40 and len(record["rerank_top12"]) == min(12, len(selected))
        and len({item["parent_id"] for item in selected}) == len(selected)
        and observed["returned_source_child_ids"] == [item["child_id"] for item in record["rerank_top12"]])
    record.update(baseline.score_case(case, record["rrf_top40"], selected, record["rerank_top12"]))
    if not record["healthy"]:
        record["metrics"] = None
    return record


def main():
    global OUTPUT, OPENCC_PATH
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--opencc-path", type=Path, default=OPENCC_PATH)
    args = parser.parse_args()
    OUTPUT, OPENCC_PATH = args.output.resolve(), args.opencc_path.resolve()
    h2.configure()
    guard = Gate0Guard() if args.command == "verify" else h2.ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    if args.command == "run":
        # Match H2: bootstrap CUDA before importing the evaluation/model stack.
        import torch
        print(json.dumps({"cuda_bootstrap": torch.cuda.is_available(), "torch": torch.__version__}), flush=True)
        return run(guard)
    if args.command == "prepare":
        prepare(guard)
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
