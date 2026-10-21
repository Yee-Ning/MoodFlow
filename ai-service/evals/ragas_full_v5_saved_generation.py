"""Replay saved provider responses through the unchanged production generator.

All transports are in-process mocks under Gate0Guard; no credentials or API.
Request JSON and final delivery must exactly match the paid run.
"""

from __future__ import annotations

import asyncio
from contextlib import redirect_stdout
import io
import json
from types import SimpleNamespace
from unittest.mock import patch

from evals import ragas_full_v5 as run
from evals.ragas_gate0 import Gate0Guard, configure_offline, write_json
from evals.ragas_smoke_v5_report import load_calls


async def replay(directory, rows):
    import httpx
    from app.agent.foundation.subgraphs.worldline.generation import WorldLineGenerationError
    original_transport = httpx.MockTransport
    calls = load_calls(directory / "generation_calls")
    by_case = {r["case_id"]: [c for c in calls if c["case_id"] == r["case_id"]] for r in rows}
    consumed = {r["case_id"]: 0 for r in rows}
    config = run.read_json(directory / "generation_config.json")
    settings = SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: "OFFLINE_REPLAY_ONLY"),
        base_url=config["base_url"], model=config["model"], generation_model=config["model"],
        timeout_seconds=config["timeout_seconds"], max_output_tokens=config["max_output_tokens"])

    class ReplayRecorder:
        async def request(self, request):
            case_id = run.smoke.SCOPE.get()["case_id"]
            index = consumed[case_id]
            if index >= len(by_case[case_id]):
                raise ValueError("step4_replay_would_request_extra_attempt")
            saved = by_case[case_id][index]
            if json.loads(request.content) != saved["request"] or saved.get("status_code") != 200:
                raise ValueError("step4_replay_request_or_provider_status_mismatch")
            request.extensions["saved_response"] = saved["response"]
            consumed[case_id] += 1

        async def response(self, response):
            await response.aread()

    async def saved_response(request):
        return httpx.Response(200, json=request.extensions["saved_response"])

    results = []
    with patch.object(httpx, "MockTransport", lambda unused: original_transport(saved_response)):
        for row in rows:
            with redirect_stdout(io.StringIO()):
                try:
                    actual = (await run.smoke.generate_all([row], settings, ReplayRecorder(), offline=True))[0]
                except WorldLineGenerationError as exc:
                    actual = run.smoke.failed_generation_result(row, run.terminal_detail(exc))
            expected = run.read_json(directory / "answers" / (row["case_id"] + ".json"))
            # Early successful smoke artifacts predate explicit status fields;
            # the smoke report uses the same historical success default.
            expected.setdefault("generation_status", "success")
            expected.setdefault("generation_failure", None)
            if any(actual[k] != expected[k] for k in ("answer", "citation_ids", "generation_status")):
                raise ValueError("step4_replay_delivered_result_mismatch:" + row["case_id"])
            actual_failure, expected_failure = actual["generation_failure"], expected["generation_failure"]
            if (actual_failure is None) != (expected_failure is None):
                raise ValueError("step4_replay_failure_presence_mismatch")
            if actual_failure and any(actual_failure[k] != expected_failure[k] for k in ("failure_type", "validation_code", "attempts")):
                raise ValueError("step4_replay_failure_detail_mismatch")
            if consumed[row["case_id"]] != len(by_case[row["case_id"]]):
                raise ValueError("step4_replay_unused_paid_generation_attempt")
            results.append({"case_id": row["case_id"], "generation_status": actual["generation_status"],
                "saved_attempts": consumed[row["case_id"]], "all_request_json_exact": True,
                "delivered_answer_and_status_exact": True, "failure_detail_exact": True})
    return results


def main():
    import sys
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    rows = run.inputs()
    results = asyncio.run(replay(run.OUTPUT, rows))
    if guard.denied:
        raise ValueError("step4_replay_forbidden_operation")
    write_json(run.OUTPUT / "generation_replay.json", {"status": "passed", "cases": results,
        "real_api_calls": 0, "credentials_loaded": False, "denied_operations": guard.denied,
        "local_asyncio_socketpair_connections": guard.local_socketpairs})
    print(json.dumps({"generation_replay_cases": len(results), "real_api_calls": 0, "status": "passed"}))


if __name__ == "__main__":
    main()
