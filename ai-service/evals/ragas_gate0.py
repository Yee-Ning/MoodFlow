"""Step 3 Gate 0 only: one frozen question, saved retrieval, no API/secret access.

Run with .venv-ragas/Scripts/python.exe -m evals.ragas_gate0.
There is deliberately no Gate 1, answer generation, ChatOpenAI, or credential path.
"""

from __future__ import annotations

from contextlib import redirect_stdout
from dataclasses import dataclass, field
from datetime import datetime
import hashlib
import importlib.metadata
import inspect
import io
import json
import math
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


AI_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = AI_ROOT.parent
GOLD = AI_ROOT / "evals/datasets/rag_eval_gold_v5.frozen.jsonl"
GOLD_V4 = AI_ROOT / "evals/datasets/rag_eval_gold_v4.frozen.jsonl"
STEP2 = REPO_ROOT / "workspace/moodflow-review/rag-source-baseline-v5-20261017"
RELEASE_ROOT = AI_ROOT / "data/reruns/jay-chou-b-20261016-182958/full-20261016-213514"
PROJECTION = RELEASE_ROOT / "data/indexing/v1/knowledge-v2-e40ab37ffcc7bcf0/v4/index_projection.jsonl"
OUTPUT = REPO_ROOT / "workspace/moodflow-review/ragas-smoke-v5-gate0-20261017"
GOLD_SHA = "b708332729177a095cb0993381bc9b3ffaef7808846e61bbdb8cbd0877afbfad"
PROJECTION_SHA = "80b1de8e0ea7f72cf44bdc6266888cee85ff73ae56748bd98ea9af289d4bcf7b"
STEP2_SHA = "4565a6a4440b76f62387ba77bbce610db9c6eafa0e5dea5743b40fae1fd8b58c"
CASE_ID = "rag-v0-001"
PLACEHOLDER = "__GATE0_PLACEHOLDER_NOT_GENERATED__"
COLUMN_MAP = {"contexts": "step2_contexts", "answer": "placeholder_answer"}


class Gate0ForbiddenOperation(RuntimeError):
    pass


class Gate0JudgeDisabled(RuntimeError):
    pass


@dataclass
class Gate0Guard:
    """Process-wide audit guard, installed before importing third-party packages."""

    denied: list[str] = field(default_factory=list)
    local_socketpairs: int = 0

    def audit(self, event, args):
        if event == "socket.connect" and len(args) >= 2:
            caller = sys._getframe(1)
            # CPython/Windows implements asyncio's self-pipe as a loopback
            # socketpair. Permit only this exact stdlib IPC operation, never
            # arbitrary localhost HTTP/Qdrant/API connections.
            if (caller.f_globals.get("__name__") == "socket"
                    and caller.f_code.co_name == "_fallback_socketpair"
                    and caller.f_locals.get("csock") is args[0]
                    and args[1] == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
                    and args[1][0] in {"127.0.0.1", "::1"}):
                self.local_socketpairs += 1
                return
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto", "subprocess.Popen"}:
            self.denied.append(event)
            raise Gate0ForbiddenOperation("gate0_network_or_subprocess_disabled")
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            parts = Path(os.fsdecode(args[0])).parts
            if any(p.lower() in {"secret", "secrets"} or p.lower() == ".env"
                   or p.lower().startswith(".env.") for p in parts):
                self.denied.append("secret_file_open")
                raise Gate0ForbiddenOperation("gate0_secret_file_access_disabled")


def configure_offline():
    # Set only non-secret controls. Never read/print credential environment values.
    for name, value in {"RAGAS_DO_NOT_TRACK": "true", "HF_HUB_OFFLINE": "1",
                        "HF_DATASETS_OFFLINE": "1", "HF_HUB_DISABLE_TELEMETRY": "1",
                        "LANGCHAIN_TRACING_V2": "false", "LANGSMITH_TRACING": "false"}.items():
        os.environ[name] = value


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def protected_hashes():
    paths = [GOLD, GOLD_V4, PROJECTION, STEP2 / "cases.jsonl", STEP2 / "run_manifest.json",
             AI_ROOT / "requirements.txt", AI_ROOT / ".venv/pyvenv.cfg"]
    return {path.relative_to(REPO_ROOT).as_posix(): sha256(path) for path in paths}


def prepare_sample(gold_rows, baseline_rows, projection_rows, case_id=CASE_ID):
    gold_by_id = {row["case_id"]: row for row in gold_rows}
    baseline_by_id = {row["case_id"]: row for row in baseline_rows}
    if len(gold_by_id) != len(gold_rows) or len(baseline_by_id) != len(baseline_rows):
        raise ValueError("gate0_duplicate_case_id")
    gold, baseline = gold_by_id[case_id], baseline_by_id[case_id]
    if gold["question"] != baseline["question"] or not baseline["healthy"]:
        raise ValueError("gate0_baseline_question_or_health_mismatch")
    by_child = {row["chunk_id"]: row for row in projection_rows}
    if len(by_child) != len(projection_rows):
        raise ValueError("gate0_duplicate_projection_child")
    ranking = baseline["rerank_top12"]
    if len(ranking) != 12 or len({r["parent_id"] for r in ranking}) != 12:
        raise ValueError("gate0_top12_population_mismatch")
    contexts, provenance = [], []
    for rank, hit in enumerate(ranking, 1):
        row = by_child[hit["child_id"]]
        payload = row["payload"]
        if (hit["rank"] != rank or row["parent_id"] != hit["parent_id"]
                or str(row["point_id"]) != hit["point_id"]
                or payload["parent_id"] != hit["parent_id"]):
            raise ValueError("gate0_baseline_projection_identity_mismatch")
        content = payload["content"]
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if not content.strip() or payload.get("source_content_sha256") != digest:
            raise ValueError("gate0_child_content_hash_mismatch")
        contexts.append(content)
        provenance.append({"rank": rank, "child_id": hit["child_id"], "parent_id": hit["parent_id"],
                           "source_id": hit["source_id"], "rerank_score": hit["rerank_score"],
                           "content_field": "projection.payload.content", "content_sha256": digest,
                           "content_characters": len(content)})
    return {"question": gold["question"], "ground_truth": gold["ground_truth"],
            "contexts": contexts, "answer": PLACEHOLDER}, provenance


def load_sample():
    if sha256(GOLD) != GOLD_SHA or sha256(PROJECTION) != PROJECTION_SHA or sha256(STEP2 / "cases.jsonl") != STEP2_SHA:
        raise ValueError("gate0_frozen_input_hash_mismatch")
    manifest = json.loads((STEP2 / "run_manifest.json").read_text(encoding="utf-8"))
    if manifest["status"] != "complete" or manifest["gold_sha256"] != GOLD_SHA:
        raise ValueError("gate0_step2_not_complete")
    return prepare_sample(read_jsonl(GOLD), read_jsonl(STEP2 / "cases.jsonl"), read_jsonl(PROJECTION))


def normalize_scores(scores):
    return {name: {"value": None if math.isnan(float(value)) else float(value),
                   "status": "expected_nan_judge_disabled" if math.isnan(float(value)) else "finite"}
            for name, value in scores.items()}


def capture_help(objects):
    stream = io.StringIO()
    with redirect_stdout(stream):
        for name, obj in objects.items():
            print(f"\n=== INSTALLED HELP: {name} ===\n")
            help(obj)
    return stream.getvalue().rstrip() + "\n"


def dry_run(sample):
    """Use actual legacy metrics with a fail-closed local sentinel, never a judge."""
    configure_offline()
    import ragas
    from datasets import Dataset
    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.llms.base import BaseRagasLLM
    from ragas.metrics import AnswerCorrectness, FactualCorrectness, Faithfulness
    from ragas.metrics.base import MetricWithEmbeddings
    from ragas.run_config import RunConfig

    if ragas.__version__ != "0.4.3":
        raise ValueError("gate0_ragas_version_drift")

    class NoJudge(BaseRagasLLM):
        def __init__(self):
            super().__init__()
            self.blocked = 0

        def refuse(self):
            self.blocked += 1
            raise Gate0JudgeDisabled("intentional_gate0_no_judge")

        def generate_text(self, *args, **kwargs):
            self.refuse()

        async def agenerate_text(self, *args, **kwargs):
            self.refuse()

        async def generate(self, *args, **kwargs):
            # Fail before any provider, credentials, response, retry or inference.
            self.refuse()

        def is_finished(self, response):
            return False

    metric_classes = (Faithfulness, FactualCorrectness)
    contract = {"ragas_version": ragas.__version__, "evaluate_signature": str(inspect.signature(evaluate)),
                "column_map_applies_to": "datasets.Dataset (v1 names), converted internally to EvaluationDataset (v2 names)",
                "metrics": {}}
    for cls in (*metric_classes, AnswerCorrectness):
        metric = cls()
        contract["metrics"][metric.name] = {
            "class": cls.__name__, "required_columns": {k: sorted(v) for k, v in metric.required_columns.items()},
            "requires_embeddings": isinstance(metric, MetricWithEmbeddings),
            "constructor": str(inspect.signature(cls)), "source": inspect.getsourcefile(cls),
            "selected_for_gate1": cls in metric_classes}
    if any(contract["metrics"][cls().name]["requires_embeddings"] for cls in metric_classes):
        raise ValueError("gate0_unexpected_embedding_dependency")
    contract["metric_name_note"] = "factual_correctness and answer_correctness are distinct metrics, not aliases."
    contract["wrapper_temperature_note"] = (
        "Installed LangchainLLMWrapper.agenerate_text replaces model.temperature with metric temperature "
        "unless bypass_temperature=True. Gate 1 must preserve the user's actual outbound temperature=0; "
        "ChatOpenAI(temperature=0) alone does not ensure it. No live wrapper is instantiated in Gate 0.")
    contract["wrapper_constructor"] = str(inspect.signature(LangchainLLMWrapper))
    contract["wrapper_async_source"] = inspect.getsource(LangchainLLMWrapper.agenerate_text)

    legacy_row = {"question": sample["question"], "ground_truth": sample["ground_truth"],
                  "step2_contexts": sample["contexts"], "placeholder_answer": sample["answer"]}
    expected = {"user_input": sample["question"], "reference": sample["ground_truth"],
                "retrieved_contexts": sample["contexts"], "response": sample["answer"]}
    judge_disabled = NoJudge()
    run_config = RunConfig(max_retries=1, max_workers=1, timeout=10)
    dataset = Dataset.from_list([legacy_row])
    first = evaluate(dataset, metrics=[cls() for cls in metric_classes], llm=judge_disabled,
                     column_map=COLUMN_MAP, raise_exceptions=False,
                     run_config=run_config, show_progress=False)
    if not isinstance(first.dataset, EvaluationDataset) or len(first.scores) != 1:
        raise ValueError("gate0_evaluate_dataset_conversion_failed")
    mapped = first.dataset.to_list()[0]
    if any(mapped[key] != value for key, value in expected.items()):
        raise ValueError("gate0_column_map_content_mismatch")
    # EvaluationDataset already has v2 names. column_map is deliberately not
    # used on this branch because Ragas only applies it to datasets.Dataset.
    second = evaluate(EvaluationDataset.from_list([expected]), metrics=[cls() for cls in metric_classes],
                      llm=judge_disabled, raise_exceptions=False,
                      run_config=run_config, show_progress=False)
    if len(second.scores) != 1 or second.dataset.to_list() != first.dataset.to_list():
        raise ValueError("gate0_native_evaluation_dataset_mismatch")
    if not all(math.isnan(float(score)) for result in (first, second) for score in result.scores[0].values()):
        raise ValueError("gate0_expected_nan_not_preserved")
    # Each pass: one faithfulness statement extraction plus two concurrent
    # factual-correctness F1 claim decompositions; all stop at the sentinel.
    if judge_disabled.blocked != 6:
        raise ValueError("gate0_expected_local_sentinel_invocation_mismatch")
    result = {"dataset_rows": 1, "evaluated_case_ids": [CASE_ID],
              "legacy_column_map": "passed_exact_content_comparison",
              "evaluation_dataset": "passed_exact_content_comparison",
              "raise_exceptions": False, "scores_are_quality_measurements": False,
              "scores": normalize_scores(first.scores[0]),
              "evaluation_dataset_scores": normalize_scores(second.scores[0]),
              "local_no_judge_sentinel_rejections": judge_disabled.blocked,
              "expected_failure": "Gate0JudgeDisabled: intentional_gate0_no_judge",
              "nan_serialization": "JSON null + explicit reason; never fill NaN with 0 or average it as a valid score",
              "real_judge_calls": 0, "generation_calls": 0, "embedding_calls": 0}
    api_help = capture_help({"ragas.evaluate": evaluate, "EvaluationDataset": EvaluationDataset,
                             "Faithfulness": Faithfulness, "FactualCorrectness": FactualCorrectness,
                             "AnswerCorrectness": AnswerCorrectness, "LangchainLLMWrapper": LangchainLLMWrapper})
    return result, api_help, contract


def main():
    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    if Path(sys.prefix).resolve() != (AI_ROOT / ".venv-ragas").resolve():
        raise ValueError("gate0_requires_isolated_venv_ragas")
    environment_config = (Path(sys.prefix) / "pyvenv.cfg").read_text(encoding="utf-8")
    if "include-system-site-packages = false" not in environment_config:
        raise ValueError("gate0_environment_not_isolated")
    before = protected_hashes()
    sample, provenance = load_sample()
    if OUTPUT.exists():
        raise ValueError("gate0_output_already_exists")
    result, api_help, api_contract = dry_run(sample)
    if guard.denied:
        raise ValueError("gate0_unexpected_denied_operation")
    after = protected_hashes()
    if before != after:
        raise ValueError("gate0_protected_inputs_changed")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "four_columns.jsonl").write_text(json.dumps(sample, ensure_ascii=False) + "\n", encoding="utf-8")
    (OUTPUT / "installed_api_help.txt").write_text(api_help, encoding="utf-8")
    write_json(OUTPUT / "contexts_provenance.json", {"case_id": CASE_ID, "top12": provenance})
    write_json(OUTPUT / "api_contract.json", api_contract)
    write_json(OUTPUT / "dry_run.json", result)
    manifest = {"gate": 0, "status": "passed_dataflow_only", "timestamp": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
                "case_id": CASE_ID, "case_count": 1, "answer": "placeholder_not_generated",
                "real_judge_calls": 0, "generation_calls": 0, "embedding_calls": 0,
                "retrieval_calls": 0, "network_denials": len(guard.denied), "secret_file_reads": 0,
                "local_asyncio_socketpair_connections": guard.local_socketpairs,
                "credentials_loaded": False, "column_map": COLUMN_MAP, "protected_before": before,
                "protected_after": after, "protected_unchanged": before == after,
                "python": sys.version.split()[0], "environment": str(Path(sys.prefix)),
                "include_system_site_packages": False, "gate1_authorized": False,
                "packages": {name: importlib.metadata.version(name) for name in
                             ("ragas", "langchain-openai", "datasets", "langchain-community", "openai", "pandas", "numpy")}}
    write_json(OUTPUT / "run_manifest.json", manifest)
    print(json.dumps({"gate0": manifest["status"], "real_judge_calls": 0,
                      "case_id": CASE_ID, "result": result}, ensure_ascii=False))


if __name__ == "__main__":
    main()
