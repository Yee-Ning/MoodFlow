"""Step 4 Gate 0: freeze all 50 inputs and mock exactly three production calls.

No live mode or credential loader is exposed. Each CLI process installs the
existing Gate 0 audit guard before importing any third-party package.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime
import importlib.metadata
import inspect
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from evals import ragas_smoke_v5 as smoke
from evals.ragas_gate0 import (
    AI_ROOT, GOLD, PROJECTION, REPO_ROOT, STEP2, Gate0Guard,
    configure_offline, prepare_sample, protected_hashes, read_jsonl, sha256, write_json,
)


OUTPUT = REPO_ROOT / "workspace/moodflow-review/ragas-full-v5-gate0-20261017"
LABELS = {"full": 18, "partial": 12, "insufficient": 13, "needs_input": 7}
DRY_CASE_IDS = ("rag-v0-001", "rag-v0-006", "rag-v0-048")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def production_hashes():
    return {p.relative_to(AI_ROOT).as_posix(): sha256(p)
            for p in sorted((AI_ROOT / "app").rglob("*.py"))}


def verify_sources():
    completion = read_json(smoke.OUTPUT / "completion.json")
    preparation = read_json(smoke.OUTPUT / "preparation.json")
    if protected_hashes() != completion["protected_after"]:
        raise ValueError("step4_frozen_hash_drift")
    for name, digest in completion["artifact_hashes"].items():
        if sha256(smoke.OUTPUT / name) != digest:
            raise ValueError("step4_smoke_artifact_drift:" + name)
    for name, digest in preparation["source_hashes"].items():
        if sha256(AI_ROOT / name) != digest:
            raise ValueError("step4_production_source_drift:" + name)
    return {"protected_hashes": protected_hashes(),
            "smoke_artifacts_verified": len(completion["artifact_hashes"]),
            "smoke_completion_sha256": sha256(smoke.OUTPUT / "completion.json"),
            "smoke_script_sha256": sha256(Path(smoke.__file__)),
            "smoke_production_sources_verified": len(preparation["source_hashes"])}


def build_inputs(gold, baseline, projection):
    ids = [r["case_id"] for r in gold]
    if (len(ids) != 50 or len(set(ids)) != 50
            or Counter(r["answerability"] for r in gold) != LABELS
            or len(baseline) != 50 or {r["case_id"] for r in baseline} != set(ids)):
        raise ValueError("step4_full_population_mismatch")
    parents = {r["parent_id"] for r in projection}
    if len(parents) != 413:
        raise ValueError("step4_projection_parent_population_mismatch")
    chunks = {r["chunk_id"]: r["payload"] for r in projection}
    rows = []
    for case in gold:
        sample, provenance = prepare_sample(gold, baseline, projection, case["case_id"])
        sample["answer"] = None
        rows.append({"case_id": case["case_id"], "answerability": case["answerability"], **sample,
                     "provenance": [{**p, "title": chunks[p["child_id"]]["source_title"],
                                     "source_name": chunks[p["child_id"]]["source_name"],
                                     "source_url": chunks[p["child_id"]].get("source_url"),
                                     "doc_id": chunks[p["child_id"]]["doc_id"]} for p in provenance]})
    return rows


def prepare():
    sources = verify_sources()
    rows = build_inputs(read_jsonl(GOLD), read_jsonl(STEP2 / "cases.jsonl"), read_jsonl(PROJECTION))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    inputs_path = OUTPUT / "inputs.json"
    if inputs_path.exists():
        raise ValueError("step4_inputs_already_frozen_do_not_overwrite")
    write_json(inputs_path, rows)
    write_json(OUTPUT / "preparation.json", {
        "status": "all_50_inputs_frozen_before_any_live_call", "case_ids": [r["case_id"] for r in rows],
        "labels": LABELS, "raw_child_contexts": sum(len(r["contexts"]) for r in rows),
        "inputs_sha256": sha256(inputs_path), "sources": sources,
        "production_before": production_hashes(),
        "generation_config_from_saved_smoke": read_json(smoke.OUTPUT / "generation_config.json"),
        "expected_source_counts_after_exact_text_dedup": {
            r["case_id"]: len(dict.fromkeys(r["contexts"])) for r in rows},
        "gate1_authorized": False, "live_calls": 0, "credentials_loaded": False,
        "fixture": "frozen registry artist:jay_chou; empty catalog facts; root-only graph; no edges",
        "boundary": "reuse smoke.generate_all; replace RagSource only; original prompt/schema/grounding/repair",
    })


def verify_inputs():
    preparation = read_json(OUTPUT / "preparation.json")
    if sha256(OUTPUT / "inputs.json") != preparation["inputs_sha256"]:
        raise ValueError("step4_frozen_inputs_modified")
    rows = read_json(OUTPUT / "inputs.json")
    expected = build_inputs(read_jsonl(GOLD), read_jsonl(STEP2 / "cases.jsonl"), read_jsonl(PROJECTION))
    if rows != expected:
        raise ValueError("step4_input_content_drift")
    verify_sources()
    if production_hashes() != preparation["production_before"]:
        raise ValueError("step4_production_changed")
    return rows


def environment_check():
    import ragas
    from ragas import EvaluationDataset, evaluate
    from ragas.llms.base import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, FactualCorrectness
    from ragas.metrics.base import MetricWithEmbeddings
    if ragas.__version__ != "0.4.3" or Path(sys.prefix).resolve() != (AI_ROOT / ".venv-ragas").resolve():
        raise ValueError("step4_isolated_ragas_version_or_environment_drift")
    if "include-system-site-packages = false" not in (Path(sys.prefix) / "pyvenv.cfg").read_text():
        raise ValueError("step4_ragas_not_isolated")
    locked = {}
    for line in (AI_ROOT / "evals/requirements-ragas.lock.txt").read_text().splitlines():
        if line.strip() and not line.startswith("#"):
            name, version = line.split("==")
            installed = importlib.metadata.version(name)
            if installed != version:
                raise ValueError("step4_dependency_drift:" + name)
            locked[name] = installed
    metrics = [Faithfulness(), FactualCorrectness()]
    if any(isinstance(m, MetricWithEmbeddings) for m in metrics):
        raise ValueError("step4_unexpected_embeddings")
    if metrics[1].mode != "f1" or "bypass_temperature" not in inspect.signature(LangchainLLMWrapper).parameters:
        raise ValueError("step4_metric_or_temperature_contract_drift")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    write_json(OUTPUT / "environment.json", {
        "status": "passed", "python": sys.version, "executable": sys.executable,
        "ragas_version": ragas.__version__, "locked_packages_verified": locked,
        "evaluate_signature": str(inspect.signature(evaluate)),
        "dataset_class": EvaluationDataset.__name__,
        "metrics": [{"class": type(m).__name__, "name": m.name,
                     "requires_embeddings": False} for m in metrics],
        "factual_correctness_mode": metrics[1].mode,
        "wrapper_bypass_temperature_supported": True, "smoke_module_importable": True,
        "real_api_calls": 0, "credentials_loaded": False,
    })


def check_dry_run(rows, results, calls):
    by_id = {r["case_id"]: r for r in rows}
    if (tuple(r["case_id"] for r in results) != DRY_CASE_IDS
            or tuple(c["case_id"] for c in calls) != DRY_CASE_IDS):
        raise ValueError("step4_mock_population_or_attempt_count_mismatch")
    checks = []
    for result, call in zip(results, calls, strict=True):
        row = by_id[result["case_id"]]
        body = call["request"]
        sent = json.loads(body["input"][0]["content"])
        if (not call["offline"] or call["status_code"] != 200 or body["temperature"] != 0
                or body["model"] != "deepseek-flash" or call["usage"]["total_tokens"] != 0
                or result["generation_status"] != "success" or not result["citation_ids"]
                or sent["user_query"] != row["question"]
                or [s["excerpt"] for s in sent["sources"]] != list(dict.fromkeys(row["contexts"]))):
            raise ValueError("step4_mock_production_protocol_mismatch")
        checks.append({"case_id": row["case_id"], "answerability": row["answerability"],
                       "raw_child_contexts": len(row["contexts"]), "actual_sources": len(sent["sources"]),
                       "attempts": 1, "schema_and_grounding": "passed_with_mock_cited_text",
                       "citation_ids": result["citation_ids"], "mock_answer": result["answer"]})
    return checks


def dry_run(guard):
    rows = verify_inputs()
    by_id = {r["case_id"]: r for r in rows}
    config = read_json(smoke.OUTPUT / "generation_config.json")
    # This public, constant marker is never an actual credential. The only
    # transport below is the smoke adapter's in-process httpx.MockTransport.
    settings = SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: "GATE0_MOCK_ONLY"),
                               base_url=config["base_url"], model=config["model"],
                               generation_model=config["model"], timeout_seconds=config["timeout_seconds"],
                               max_output_tokens=config["max_output_tokens"])
    directory = OUTPUT / "mock_generation_calls"
    if directory.exists():
        raise ValueError("step4_mock_trace_exists_do_not_overwrite")
    recorder = smoke.Recorder(directory, offline=True)
    with patch.object(smoke, "CASE_IDS", tuple(by_id)):
        results = asyncio.run(smoke.generate_all([by_id[k] for k in DRY_CASE_IDS], settings, recorder, offline=True))
    checks = check_dry_run(rows, results, recorder.entries)
    verify_inputs()
    write_json(OUTPUT / "dry_run.json", {
        "status": "passed_protocol_only", "case_ids": list(DRY_CASE_IDS), "cases": checks,
        "real_api_calls": 0, "mock_requests": len(recorder.entries), "tokens": 0,
        "secret_file_reads": 0, "credentials_loaded": False, "denied_operations": guard.denied,
        "local_asyncio_socketpair_connections": guard.local_socketpairs,
        "limitation": "Mock cited text validates assembly/schema/grounding only; no generation-quality score or bug fix.",
    })


def finalize():
    rows = verify_inputs()
    environment = read_json(OUTPUT / "environment.json")
    dry = read_json(OUTPUT / "dry_run.json")
    calls = [read_json(p) for p in sorted((OUTPUT / "mock_generation_calls").glob("[0-9][0-9][0-9].json"))]
    check_dry_run(rows, [{**c, "answer": c["mock_answer"], "generation_status": "success"}
                         for c in dry["cases"]], calls)
    if (environment["status"] != "passed" or dry["status"] != "passed_protocol_only"
            or dry["denied_operations"] or dry["real_api_calls"] != 0 or dry["tokens"] != 0
            or dry["secret_file_reads"] != 0 or dry["credentials_loaded"]):
        raise ValueError("step4_gate0_incomplete")
    write_json(OUTPUT / "run_manifest.json", {
        "step": 4, "gate": 0, "status": "passed_awaiting_west_gate1_approval",
        "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
        "case_count": len(rows), "labels": dict(Counter(r["answerability"] for r in rows)),
        "inputs_sha256": sha256(OUTPUT / "inputs.json"), "dry_run_case_count": len(dry["cases"]),
        "real_api_calls": 0, "tokens": 0, "credentials_loaded": False,
        "gate1_authorized": False, "gate1_started": False, "step5_started": False,
        "protected_after": protected_hashes(), "production_unchanged": True,
        "evaluation_source_hashes": {p.relative_to(AI_ROOT).as_posix(): sha256(p) for p in [
            Path(__file__), Path(smoke.__file__), AI_ROOT / "tests/test_ragas_full_v5_gate0.py"]},
        "artifact_hashes": {p.relative_to(OUTPUT).as_posix(): sha256(p)
                            for p in sorted(OUTPUT.rglob("*")) if p.is_file() and p.name != "run_manifest.json"},
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("environment", "prepare", "dry-run", "finalize"))
    args = parser.parse_args()
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    if args.mode == "dry-run":
        dry_run(guard)
    else:
        {"environment": environment_check, "prepare": prepare, "finalize": finalize}[args.mode]()
    if guard.denied:
        raise ValueError("step4_gate0_forbidden_operation_attempted")
    print(json.dumps({"step": 4, "gate": 0, "mode": args.mode, "status": "passed", "real_api_calls": 0}))


if __name__ == "__main__":
    main()
