"""Step 4 Gate 1, explicitly approved by West: fixed 50 cases, existing bugs.

Generation uses .venv, judging .venv-ragas. Only evaluation code is adapted.
The .env LLM-field access for these 50 cases was separately authorized in chat.
"""

from __future__ import annotations

import argparse
import asyncio
from contextlib import contextmanager
import json
import logging
import math
import sys
from unittest.mock import patch

from evals import ragas_full_v5_gate0 as gate0
from evals import ragas_smoke_v5 as smoke
from evals.ragas_gate0 import AI_ROOT, configure_offline, sha256, write_json


OUTPUT = AI_ROOT.parent / "workspace/moodflow-review/ragas-full-v5-gate1-20261017"
BASE_RECORDER = smoke.Recorder


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def inputs():
    rows = gate0.verify_inputs()
    if (OUTPUT / "inputs.json").read_bytes() != (gate0.OUTPUT / "inputs.json").read_bytes():
        raise ValueError("step4_gate1_frozen_input_bytes_changed")
    preparation = read_json(OUTPUT / "preparation.json")
    if sha256(OUTPUT / "inputs.json") != preparation["inputs_sha256"]:
        raise ValueError("step4_gate1_input_hash_changed")
    return rows


def prepare():
    rows = gate0.verify_inputs()
    manifest = read_json(gate0.OUTPUT / "run_manifest.json")
    if manifest["status"] != "passed_awaiting_west_gate1_approval":
        raise ValueError("step4_gate0_not_passed")
    for name, digest in manifest["artifact_hashes"].items():
        if sha256(gate0.OUTPUT / name) != digest:
            raise ValueError("step4_gate0_artifact_changed:" + name)
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "inputs.json").write_bytes((gate0.OUTPUT / "inputs.json").read_bytes())
    (OUTPUT / "answers").mkdir()
    write_json(OUTPUT / "preparation.json", {
        "step": 4, "gate": 1, "status": "prepared_before_live_calls",
        "case_ids": [r["case_id"] for r in rows], "labels": gate0.LABELS,
        "inputs_sha256": sha256(OUTPUT / "inputs.json"),
        "gate0_manifest_sha256": sha256(gate0.OUTPUT / "run_manifest.json"),
        "protected_before": gate0.protected_hashes(),
        "production_before": gate0.production_hashes(),
        "api_authorization": "West explicitly approved Step 4 Gate 1 in chat.",
        "secret_authorization": "West separately authorized in-memory .env LLM-field access for these 50 cases.",
        "generation": "reuse smoke.generate_all and its production fixture without production changes",
        "judge_batches": 1, "judge_rows": 50,
        "judge_metrics": ["Faithfulness()", "FactualCorrectness() default F1"],
    })


@contextmanager
def smoke_scope(rows):
    with (patch.object(smoke, "OUTPUT", OUTPUT),
          patch.object(smoke, "CASE_IDS", tuple(r["case_id"] for r in rows)),
          patch.object(smoke, "Recorder", FullRecorder)):
        yield


class FullRecorder(BASE_RECORDER):
    async def request(self, request):
        body = json.loads(request.content)
        if type(body.get("temperature")) not in (int, float) or body["temperature"] != 0:
            raise ValueError("step4_actual_temperature_must_be_numeric_zero")
        scope = smoke.SCOPE.get()
        if scope and scope["stage"] == "generation":
            previous = sum(c["stage"] == "generation" and c["case_id"] == scope["case_id"]
                           for c in self.entries)
            if previous >= 2:
                raise ValueError("step4_no_more_than_two_generation_attempts")
        await super().request(request)

    async def response(self, response):
        await super().response(response)
        entry = self.entries[response.request.extensions["gate1_record_index"]]
        if entry["stage"] == "judge" and len(self.entries) % 5 == 0:
            print(json.dumps({"stage": "judge_progress", "http_attempts": len(self.entries),
                              "case_id": entry["case_id"], "metric": entry["metric"]}), flush=True)


def terminal_detail(error):
    """Record the actual safe validation code, without altering production errors.

    The graph's public diagnostic allowlist omits schema/parse codes. Preserve
    that original value, and expose the underlying structured code separately.
    """
    from app.agent.foundation.subgraphs.worldline.graph import _narrative_failure_detail
    from app.agent.structured_output.errors import ValidationFailure
    detail = _narrative_failure_detail(error)
    detail["production_validation_code"] = detail["validation_code"]
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, ValidationFailure):
            detail["validation_code"] = error.code
        # An evaluation invariant is a harness error, never a production failure.
        if isinstance(error, ValueError) and str(error).startswith(("gate1_", "step4_")):
            raise RuntimeError("step4_evaluation_invariant_failed") from None
        error = error.__cause__
    return detail


async def generate(rows, settings):
    from app.agent.foundation.subgraphs.worldline.generation import WorldLineGenerationError
    recorder = FullRecorder(OUTPUT / "generation_calls", resume=True)
    for row in rows:
        path = OUTPUT / "answers" / (row["case_id"] + ".json")
        previous = [c for c in recorder.entries if c["case_id"] == row["case_id"]]
        if path.exists():
            continue
        if previous:
            # Never send a fresh request for a case with interrupted saved calls.
            raise ValueError("step4_interrupted_case_requires_offline_review:" + row["case_id"])
        try:
            result = (await smoke.generate_all([row], settings, recorder))[0]
        except WorldLineGenerationError as exc:
            result = smoke.failed_generation_result(row, terminal_detail(exc))
        calls = [c for c in recorder.entries if c["case_id"] == row["case_id"]]
        result["generation_attempts"] = len(calls)
        result["actual_source_counts"] = [len(json.loads(c["request"]["input"][0]["content"])["sources"])
                                          for c in calls]
        write_json(path, result)
        print(json.dumps({"stage": "generation_saved", "case_id": row["case_id"],
                          "generation_status": result["generation_status"], "attempts": len(calls),
                          "completed_cases": sum(1 for _ in (OUTPUT / "answers").glob("*.json"))}), flush=True)


def judge(rows, settings):
    from ragas.metrics import Faithfulness, FactualCorrectness
    outcomes = OUTPUT / "metric_outcomes"
    outcomes.mkdir(exist_ok=False)

    def checkpoint(original, name):
        async def run(metric, sample, *args, **kwargs):
            scope = smoke.SCOPE.get()
            record = {"case_id": scope["case_id"], "metric": name}
            try:
                score = await original(metric, sample, *args, **kwargs)
                record.update(value=None if math.isnan(float(score)) else float(score),
                              status="not_scorable_nan" if math.isnan(float(score)) else "finite")
                return score
            except BaseException as exc:
                record.update(value=None, status="metric_exception", error_type=type(exc).__name__)
                raise
            finally:
                write_json(outcomes / (scope["case_id"] + "." + name + ".json"), record)
        return run

    with (patch.object(Faithfulness, "single_turn_ascore", checkpoint(Faithfulness.single_turn_ascore, "faithfulness")),
          patch.object(FactualCorrectness, "single_turn_ascore", checkpoint(FactualCorrectness.single_turn_ascore, "factual_correctness"))):
        smoke.judge_all(rows, settings)


def main():
    configure_offline()
    # Provider error bodies can echo credentials. Keep only safe category
    # diagnostics and the recorder's status/usage, never SDK exception text.
    logging.getLogger("app.llm.client").disabled = True
    sys.addaudithook(smoke.secret_guard)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("prepare", "generate", "judge"))
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
        return
    rows = inputs()
    settings = smoke.load_approved_llm_settings()
    expected = read_json(gate0.OUTPUT / "preparation.json")["generation_config_from_saved_smoke"]
    actual = {"model": settings.generation_model, "base_url": str(settings.base_url),
              "timeout_seconds": settings.timeout_seconds, "max_output_tokens": settings.max_output_tokens,
              "temperature": 0, "secret_exported": False}
    if actual != expected:
        raise ValueError("step4_generation_configuration_changed_from_smoke")
    with smoke_scope(rows):
        if args.mode == "generate":
            write_json(OUTPUT / "generation_config.json", actual)
            asyncio.run(generate(rows, settings))
        else:
            answers = [read_json(OUTPUT / "answers" / (r["case_id"] + ".json")) for r in rows]
            judge(answers, settings)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        code = str(exc) if isinstance(exc, ValueError) and str(exc).startswith(("step4_", "gate1_")) else type(exc).__name__
        print(json.dumps({"stage": "stopped", "safe_error_code": code}), flush=True)
        sys.exit(1)
