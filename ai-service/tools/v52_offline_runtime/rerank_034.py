"""Three preregistered rerank experiments over immutable seven-leg retrieval."""

import asyncio
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import sys
from unittest.mock import patch

from pydantic import Field

from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
from app.rag.reranking import input_builder, models, service
from app.rag.reranking.config import RerankConfig
from app.rag.reranking.runtime import QwenRerankScorer
from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
from app.rag.retrieval.legs import RetrievalLeg
from app.rag.retrieval.models import RetrievalResult, RetrievalStatus
from app.rag.retrieval.selection import select_candidates
from evals.ragas_gate0 import Gate0Guard
from tools.v52_offline_runtime import h4_metadata_034 as frozen
from tools.v52_offline_runtime.run_baseline import anonymous_local_models


base, batch = frozen.base, frozen.batch
OUTPUT = base.ROOT / "workspace/moodflow-review/rerank-034-20261019"
ROUNDS = {
    "top16": {"candidate_limit": 40, "top_k": 16, "threshold": None},
    "pool60": {"candidate_limit": 60, "top_k": 12, "threshold": None},
    "cutoff05": {"candidate_limit": 40, "top_k": 12, "threshold": 0.5},
}


class ExperimentConfig(RerankConfig):
    top_k: int = Field(default=12, ge=10, le=16)


class ExperimentInput(models.RerankInput):
    candidate_index: int = Field(ge=0, le=59)


class ExperimentCandidate(models.RerankedCandidate):
    retrieval_rank: int = Field(ge=1, le=60)
    rerank_rank: int = Field(ge=1, le=16)


class ExperimentResult(models.RerankResult):
    output_top_k: int = Field(ge=10, le=16)
    candidates: list[ExperimentCandidate] = Field(default_factory=list, max_length=16)


@contextmanager
def experiment_contracts():
    with (patch.object(input_builder, "RerankInput", ExperimentInput),
          patch.object(service, "RerankedCandidate", ExperimentCandidate),
          patch.object(service, "RerankResult", ExperimentResult)):
        yield


def write(path, value):
    base.write_json(path, value)


def protected_hashes():
    pins = frozen.protected_hashes()
    artifact = frozen.OUTPUT / "artifact-sha256.json"
    for name, expected in base.read_json(artifact)["files"].items():
        if base.sha256(base.ROOT / name) != expected:
            raise base.BaselineError("rerank_prior_artifact_drift:" + name)
        pins[name] = expected
    pins[artifact.relative_to(base.ROOT).as_posix()] = base.sha256(artifact)
    paths = list((base.AI_ROOT / "app/rag/reranking").rglob("*.py"))
    paths += list((base.AI_ROOT / "app/rag/reranking/config").glob("*.yaml"))
    paths += [Path(__file__), Path(__file__).with_name("verify_rerank_034.py"),
              Path(__file__).with_name("test_rerank_034.py")]
    pins.update({p.relative_to(base.ROOT).as_posix(): base.sha256(p) for p in paths})
    return pins


def load_source():
    inputs, bundles, _, _ = batch.load_source()
    controls, provenance = frozen.controls(bundles)
    projection = batch.repair.overlay_projection(inputs[3])
    config, rerank = inputs[4:6]
    if (config.fusion.rrf_k, config.fusion.per_leg_top_k, config.fusion.overfetch_multiplier,
            config.selection.broad_document_cap, config.selection.source_cap,
            rerank.maximum_candidates, rerank.top_k, rerank.timeout_seconds) != (60, 80, 2, 4, 5, 40, 12, 30):
        raise base.BaselineError("rerank_frozen_parameters_drift")
    return controls, provenance, projection, config, rerank


def reconstruct(control, projection):
    legs = []
    for saved in control["leg_results"]:
        if saved["error_code"] is not None or saved["limit"] != 80 or saved["weight"] != 1:
            raise base.BaselineError("rerank_frozen_leg_invalid")
        leg = RetrievalLeg(variant_index=saved["variant_index"], variant_kind=QueryVariantKind(saved["variant_kind"]),
                           channel=SearchChannel(saved["channel"]), text=saved["text"], weight=saved["weight"])
        hits = [RawSearchHit(h["point_id"], h["raw_score"], projection.children[h["child_id"]]) for h in saved["hits"]]
        legs.append(LegSearchResult(leg=leg, hits=hits))
    ranks = weighted_rrf(legs, k=60)
    for field, count in (("rrf_top40", 40), ("selection_input_rrf", 80)):
        if [h.point_id for h in ranks[:count]] != [h["point_id"] for h in control[field]]:
            raise base.BaselineError("rerank_frozen_rrf_order_drift")
    return ranks


def candidate_row(candidate):
    return {"point_id": candidate.point_id, "parent_id": candidate.parent_id,
            "child_id": candidate.primary_child_id, "doc_id": candidate.doc_id,
            "source_id": candidate.source_id, "fusion_score": candidate.fusion_score,
            "matched_child_ids": candidate.matched_child_ids}


def prepare_pool(control, projection, config, candidate_limit):
    ranks = reconstruct(control, projection)
    plan_data = dict(control["plan"], candidate_top_k=candidate_limit)
    plan = SearchPlan.model_validate(plan_data)
    candidates, warnings = select_candidates(ranks[:80], plan, config)
    if candidate_limit == 40:
        keys = ("point_id", "parent_id", "child_id", "doc_id", "source_id", "fusion_score", "matched_child_ids")
        if [candidate_row(c) for c in candidates] != [
                {key: hit[key] for key in keys} for hit in control["selection"]]:
            raise base.BaselineError("rerank_frozen_selection_drift:" + control["case_id"])
        if warnings != control["selection_warnings"]:
            raise base.BaselineError("rerank_selection_warning_drift")
    return plan, candidates, warnings


def metrics(gold, ranks, final, top_k):
    relevant = set(gold)
    if not relevant or len(relevant) != len(gold) or top_k not in (12, 16):
        raise ValueError("rerank_metric_inputs_invalid")
    if len(final) > top_k or len({h["parent_id"] for h in final}) != len(final):
        raise ValueError("rerank_final_population_invalid")
    hit = relevant & {h["parent_id"] for h in final}
    first = next((rank for rank, item in enumerate(final, 1) if item["parent_id"] in relevant), None)
    first12 = final[:12]
    hit12 = relevant & {h["parent_id"] for h in first12}
    return {"recall_at_40": len(relevant & {h["parent_id"] for h in ranks[:40]}) / len(relevant),
            "recall_at_k": len(hit) / len(relevant), "precision_at_k": len(hit) / top_k,
            "mrr": 1 / first if first else 0, "first_hit_rank": first,
            "fixed_recall_at_12": len(hit12) / len(relevant), "fixed_precision_at_12": len(hit12) / 12,
            "returned_precision": len(hit) / len(final) if final else 0, "returned_count": len(final)}


class RecordingScorer:
    def __init__(self, scorer):
        self.scorer, self.inputs, self.scores, self.calls = scorer, [], [], 0

    def score(self, inputs):
        self.calls += 1
        if self.calls != 1:
            raise ValueError("rerank_extra_model_call")
        self.inputs = list(inputs)
        self.scores = self.scorer.score(inputs)
        return self.scores


def execute_case(control, projection, config, rerank, spec, scorer):
    plan, candidates, warnings = prepare_pool(control, projection, config, spec["candidate_limit"])
    plan = plan.model_copy(update={"final_top_k": spec["top_k"]})
    experiment = ExperimentConfig.model_validate({**rerank.model_dump(), "maximum_candidates": spec["candidate_limit"], "top_k": spec["top_k"]})
    pool = RetrievalResult(status=RetrievalStatus.SUCCESS if candidates else RetrievalStatus.EMPTY,
                           plan_id=plan.plan_id, index_profile=plan.index_profile, candidates=candidates)
    recording = RecordingScorer(scorer)
    with experiment_contracts():
        result = asyncio.run(service.QwenCandidateReranker(experiment, scorer=recording).rerank(plan, pool))
    if result.fallback_used or result.status.value != ("success" if candidates else "empty"):
        raise base.BaselineError("rerank_inference_not_success:" + ",".join(result.warnings))
    if recording.calls != bool(candidates):
        raise base.BaselineError("rerank_model_call_count_invalid")
    raw = [{**candidate_row(item.candidate), "rerank_score": item.rerank_score,
            "retrieval_rank": item.retrieval_rank} for item in result.candidates]
    final = [hit for hit in raw if spec["threshold"] is None or hit["rerank_score"] >= spec["threshold"]]
    scored = [{**candidate_row(candidate), "candidate_index": index, "score": score,
               "query_sha256": hashlib.sha256(item.query.encode()).hexdigest(),
               "document_sha256": hashlib.sha256(item.document.encode()).hexdigest()}
              for index, (candidate, item, score) in enumerate(zip(candidates, recording.inputs, recording.scores, strict=True))]
    gold = set(control["relevant_parent_ids"])
    old_hit = gold & {h["parent_id"] for h in control["rerank_top12"]}
    new_hit = gold & {h["parent_id"] for h in final}
    killed = [h["parent_id"] for h in scored if spec["threshold"] is not None and h["score"] < spec["threshold"] and h["parent_id"] in gold]
    return {"case_id": control["case_id"], "question": control["question"], "spec": spec,
            "plan": plan.model_dump(mode="json"), "effective_qdrant_filter": control["effective_qdrant_filter"],
            "selection_warnings": warnings, "scored_candidates": scored, "raw_top_k": raw, "final_top_k": final,
            "model_calls": recording.calls, "rerank_fallback_used": False,
            "rerank_timings": result.timings.model_dump(mode="json"),
            "newly_lost_final_gold": sorted(old_hit - new_hit), "threshold_rejected_gold": sorted(killed),
            "metrics": metrics(control["relevant_parent_ids"], control["rrf_top40"], final, spec["top_k"])}


def run_round(name, source, scorer, protected, guard):
    folder = OUTPUT / name
    if folder.exists():
        raise base.BaselineError("rerank_round_already_attempted:" + name)
    controls, _, projection, config, rerank = source
    folder.mkdir()
    state = {"status": "running", "started_at": batch.h1.now(), "round": name, "spec": ROUNDS[name],
             "case_ids": list(controls), "expected_records": 34, "completed_records": 0,
             "new_retrieval_calls": 0, "generation_calls": 0, "baseline_rerun": False}
    write(folder / "run_manifest.json", state)
    error = None
    try:
        with (folder / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for control in controls.values():
                row = execute_case(control, projection, config, rerank, ROUNDS[name], scorer)
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                state.update(completed_records=state["completed_records"] + 1, last_case_id=row["case_id"])
                write(folder / "run_manifest.json", state)
                print(json.dumps({"round": name, "progress": state["completed_records"], "case_id": row["case_id"],
                                  "pool": len(row["scored_candidates"]), "metrics": row["metrics"]}), flush=True)
    except BaseException as exc:
        error = str(exc) if isinstance(exc, base.BaselineError) else type(exc).__name__
    unchanged = protected == protected_hashes()
    complete = state["completed_records"] == 34 and error is None and unchanged and not guard.denied
    state.update(status="complete" if complete else "incomplete", error_code=error,
                 finished_at=batch.h1.now(), protected_unchanged=unchanged, guard_denied=guard.denied)
    write(folder / "run_manifest.json", state)
    if not complete:
        raise base.BaselineError("rerank_round_incomplete:" + str(error))


def main():
    if sys.argv[1:] != ["run"] or OUTPUT.exists():
        raise base.BaselineError("rerank_only_three_new_rounds_allowed")
    base.h2.configure()
    import torch

    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    with anonymous_local_models():
        source = load_source()
        protected = protected_hashes()
        controls, provenance, projection, config, rerank = source
        pools = {cid: {str(limit): len(prepare_pool(row, projection, config, limit)[1]) for limit in (40, 60)}
                 for cid, row in controls.items()}
        OUTPUT.mkdir()
        write(OUTPUT / "preregistration.json", {
            "approved_by": "West", "rounds": ROUNDS, "population": "34 full24 partial10", "baseline_rerun": False,
            "success": "macro MRR delta>=0.02 OR macro recall_at_k delta>=0.02; zero new Gold loss",
            "gold_veto": "any newly lost baseline final Gold OR any threshold-rejected input Gold",
            "recall_at_40": "immutable first40 RRF Child cut, then unique Parent intersection; expected unchanged",
            "metric_names": "recall_at_k/precision_at_k use16 in top16,12 otherwise; also report fixed12",
            "precision_denominator": "configured top_k, including short/empty returns; returned precision separately",
            "frozen_upstream": "reuse exact seven frozen legs, RRF k60, first80 Children, existing selector caps4/5",
            "pool60": "only selection target and rerank input capacity40->60; upstream80 unchanged",
            "schema_adapter": "offline-only subclass bounds16/60; original input builder, service, scorer and validators",
            "exceptions": "065 A frozen;055/060 original ten-Parent filtered pool reused; no backfill",
        })
        write(OUTPUT / "preflight.json", {"protected_before": protected, "control_provenance": provenance,
              "case_ids": list(controls), "pool_sizes": pools, "retrieval_config": config.model_dump(mode="json"),
              "rerank_config": rerank.model_dump(mode="json")})
        scorer = QwenRerankScorer(rerank.model)
        scorer.prepare()
        write(OUTPUT / "runtime.json", {"device": scorer._device, "torch": torch.__version__,
              "model": rerank.model.model_dump(mode="json"), "network_calls": 0})
        for name in ROUNDS:
            run_round(name, source, scorer, protected, guard)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
