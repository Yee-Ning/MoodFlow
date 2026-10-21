"""Continue unattempted cases without repeating any paid generation response."""

import asyncio
import json
import logging
from unittest.mock import patch

from tools.v52_offline_runtime import generation_baseline_v53 as run


def completed_cases(gold, directory, entries):
    completed = {case["case_id"] for case in gold if (directory / (case["case_id"] + ".json")).exists()}
    if any(call["case_id"] not in completed for call in entries):
        raise ValueError("v53_incomplete_paid_case_requires_offline_recovery")
    return completed


def main():
    from evals import ragas_smoke_v5 as smoke
    from tools.v52_offline_runtime import dimension_034 as frozen
    from tools.v52_offline_runtime.run_baseline import anonymous_local_models
    from app.agent.foundation import rag_source
    import torch

    logging.getLogger("app.llm.client").disabled = True
    frozen.base.h2.configure()
    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = run.install_guard(smoke, frozen.base.h2)
    preparation = run.read(run.OUTPUT / "preparation.json")
    run.check_pins(preparation["protected_before"])
    gold = run.selected_gold()
    with anonymous_local_models():
        inputs, bundles, _, _ = frozen.load_source()
        base = frozen.base
        projection = frozen.repair.overlay_projection(inputs[3])
        config, binding = inputs[4], inputs[-1]
        recorder = run.recorder_type(smoke)(run.OUTPUT / "generation_calls", resume=True)
        completed = completed_cases(gold, run.OUTPUT / "answers", recorder.entries)
        run.write(run.OUTPUT / "resume-preparation.json", {"already_completed_never_resent": sorted(completed),
            "remaining": [case["case_id"] for case in gold if case["case_id"] not in completed],
            "harness_only": True, "production_changes": False})
        settings = smoke.load_approved_llm_settings()
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        audit = base.h2.RequestAudit()
        once = frozen.repair.FilteredRepairOnce(run.OUTPUT / "exceptions")
        original_collect = rag_source.RagSource.collect
        state = run.read(run.OUTPUT / "generation_state.json")
        old_requests = run.read(run.OUTPUT / "qdrant_requests.json") if (run.OUTPUT / "qdrant_requests.json").exists() else []
        for case in gold:
            cid = case["case_id"]
            if cid in completed:
                continue
            captured = []

            def capture(self, request, *, limit):
                result = original_collect(self, request, limit=limit)
                captured.append(result)
                return result

            _, query, plan, condition = bundles[cid]
            audit.active, audit.expected_filter, audit.expected_lexical = cid, condition["effective_qdrant_filter"], query["queries"]
            try:
                with (base.target_scope(), audit.installed(), patch.object(base.h2, "intercept_fallback", once),
                      patch.object(rag_source.RagSource, "collect", capture)):
                    retrieval = base.h2.collect_case({**case, "control_legs": base.v51.expected_legs(query)}, query,
                                 plan, condition, projection, config, binding, audit)
            except BaseException as error:
                safe_code = str(error) if isinstance(error, base.h2.SafetyStop) else type(error).__name__
                run.write(run.OUTPUT / "resume-stop.json", {"case_id": cid, "safe_code": safe_code,
                    "audit_safety_events": audit.safety_events, "guard_denied": guard.denied,
                    "trace_functions": [frame.name for frame in __import__("traceback").extract_tb(error.__traceback__)]})
                print(json.dumps({"stage": "retrieval_stop", "case_id": cid, "safe_code": safe_code}), flush=True)
                raise SystemExit(1) from None
            run.write(run.OUTPUT / "retrieval" / (cid + ".json"), retrieval)
            if len(captured) != 1:
                raise ValueError("v53_single_fresh_retrieval_capture_failed")
            answer = asyncio.run(run.generate_one(case, captured[0], settings, recorder, smoke))
            if guard.denied:
                raise ValueError("v53_guard_denied_operation")
            answer.update(retrieval_metrics=retrieval["metrics"], retrieval_healthy=retrieval["healthy"])
            run.write(run.OUTPUT / "answers" / (cid + ".json"), answer)
            completed.add(cid)
            state.update(completed=len(completed), last_case_id=cid, generation_usage=run.usage_totals(recorder.entries))
            run.write(run.OUTPUT / "generation_state.json", state)
            run.write(run.OUTPUT / "qdrant_requests.json", old_requests + audit.requests)
            print(json.dumps({"stage": "online_generation", "case_id": cid, "completed": len(completed),
                  "status": answer["generation_status"], "failure": answer.get("failure_category"), "contexts": len(answer["contexts"])}), flush=True)
        audit.active = None
        with base.target_scope(), audit.installed():
            run.write(run.OUTPUT / "collection.after.json", base.inventory(projection))
        run.check_pins(preparation["protected_before"])
        state.update(status="complete", protected_unchanged=True, guard_denied=guard.denied)
        run.write(run.OUTPUT / "generation_state.json", state)
        run.write(run.OUTPUT / "qdrant_requests.json", old_requests + audit.requests)
        run.write(run.OUTPUT / "generation_usage.json", run.usage_totals(recorder.entries))


if __name__ == "__main__":
    main()
