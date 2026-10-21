"""Approved eight-case Gate 1; prepare -> production generation -> Ragas -> report.

Generation uses .venv; judging uses .venv-ragas. No retrieval is performed.
The sole secret-file read is explicitly authorized for this run by West.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextvars import ContextVar
import io
import json
import math
import os
import re
import sys
from types import SimpleNamespace
from unittest.mock import patch

from evals.ragas_gate0 import (
    AI_ROOT, GOLD, GOLD_SHA, PROJECTION, PROJECTION_SHA, RELEASE_ROOT,
    STEP2, STEP2_SHA, configure_offline, prepare_sample, protected_hashes,
    read_jsonl, sha256, write_json,
)


OUTPUT = AI_ROOT.parent / "workspace/moodflow-review/ragas-smoke-v5-gate1-20261017"
CASE_IDS = ("rag-v0-001", "rag-v0-024", "rag-v0-049", "rag-v0-054",
            "rag-v0-005", "rag-v0-036", "rag-v0-006", "rag-v0-065")
LABELS = {"full": 4, "partial": 2, "insufficient": 2}
SCOPE = ContextVar("ragas_gate1_call_scope", default=None)
APPROVED_ENV_READ = ContextVar("ragas_gate1_authorized_env_read", default=False)
ENV_FIELDS = {"LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL", "LLM_GENERATION_MODEL",
              "LLM_TIMEOUT_SECONDS", "LLM_MAX_OUTPUT_TOKENS"}


def secret_guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        from pathlib import Path
        path = Path(os.fsdecode(args[0]))
        if any(p.lower() in {"secret", "secrets"} or p.lower() == ".env" or p.lower().startswith(".env.")
               for p in path.parts):
            if path.resolve() != (AI_ROOT / ".env").resolve() or not APPROVED_ENV_READ.get():
                raise ValueError("gate1_secret_file_outside_explicit_authorization")


def load_approved_llm_settings():
    """Read only the authorized LLM fields; never print or persist the key."""
    from dotenv import dotenv_values
    selected = {k: os.environ[k] for k in ENV_FIELDS if k in os.environ}
    if any(k not in selected for k in ENV_FIELDS):
        # West's explicit approval is recorded in this chat. Do not generalize
        # this loader into an authorization to inspect any other secret path.
        token = APPROVED_ENV_READ.set(True)
        try:
            for line in (AI_ROOT / ".env").open(encoding="utf-8-sig"):
                match = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=", line)
                if match and match.group(1) in ENV_FIELDS and match.group(1) not in selected:
                    selected.update(dotenv_values(stream=io.StringIO(line), interpolate=False))
        finally:
            APPROVED_ENV_READ.reset(token)
    if not selected.get("LLM_API_KEY"):
        raise ValueError("approved_llm_key_unavailable")
    from urllib.parse import urlsplit
    url = urlsplit(selected["LLM_BASE_URL"])
    if url.scheme != "https" or url.hostname != "api.deepseek.com" or url.query or url.username:
        raise ValueError("generation_provider_is_not_approved_deepseek")
    return SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: selected["LLM_API_KEY"]),
                           base_url=selected["LLM_BASE_URL"], model=selected["LLM_MODEL"],
                           generation_model=selected.get("LLM_GENERATION_MODEL", "deepseek-flash"),
                           timeout_seconds=float(selected["LLM_TIMEOUT_SECONDS"]),
                           max_output_tokens=int(selected.get("LLM_MAX_OUTPUT_TOKENS", 4096)))


def prepare():
    if (sha256(GOLD), sha256(PROJECTION), sha256(STEP2 / "cases.jsonl")) != (GOLD_SHA, PROJECTION_SHA, STEP2_SHA):
        raise ValueError("gate1_frozen_input_hash_mismatch")
    gold, baseline, projection = read_jsonl(GOLD), read_jsonl(STEP2 / "cases.jsonl"), read_jsonl(PROJECTION)
    by_id, chunks = {r["case_id"]: r for r in gold}, {r["chunk_id"]: r["payload"] for r in projection}
    rows = []
    for case_id in CASE_IDS:
        sample, provenance = prepare_sample(gold, baseline, projection, case_id)
        sample["answer"] = None
        rows.append({"case_id": case_id, "answerability": by_id[case_id]["answerability"], **sample,
                     "provenance": [{**p, "title": chunks[p["child_id"]]["source_title"],
                                     "source_name": chunks[p["child_id"]]["source_name"],
                                     "source_url": chunks[p["child_id"]].get("source_url"),
                                     "doc_id": chunks[p["child_id"]]["doc_id"]} for p in provenance]})
    if dict(Counter(r["answerability"] for r in rows)) != LABELS:
        raise ValueError("gate1_eight_case_label_mismatch")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    write_json(OUTPUT / "inputs.json", rows)
    write_json(OUTPUT / "preparation.json", {"status": "prepared", "case_ids": list(CASE_IDS),
               "labels": LABELS, "contexts": 96, "protected_before": protected_hashes(),
               "secret_authorization": "West explicitly approved this run's in-memory .env LLM-field access in chat.",
               "production_generation": "StructuredWorldLineGenerator.generate_narrative -> ProviderWorldLineRuntime -> create_generation_response",
               "generation_fixture": "frozen registry artist root only; no catalog facts, graph edges, entity lookup or branch generation",
               "replay_boundary": "Only the RagSource provider is replaced by saved top-12 raw Child text; production prompt, pack, rendering, schema, grounding, repair and provider call are retained.",
               "source_hashes": {str(p.relative_to(AI_ROOT)): sha256(p) for p in [
                   AI_ROOT / "app/llm/client.py", AI_ROOT / "app/agent/foundation/subgraphs/worldline/generation.py",
                   AI_ROOT / "app/agent/foundation/subgraphs/worldline/prompts.py",
                   AI_ROOT / "app/agent/foundation/subgraphs/worldline/stage_pack.py"]}})


class Recorder:
    def __init__(self, directory, *, offline=False, resume=False):
        self.directory, self.offline = directory, offline
        self.entries = ([json.loads(p.read_text(encoding="utf-8"))
                         for p in sorted(directory.glob("[0-9][0-9][0-9].json"))] if resume else [])
        directory.mkdir(parents=True, exist_ok=True)

    async def request(self, request):
        scope = SCOPE.get()
        if (scope is None or scope["case_id"] not in CASE_IDS or request.method != "POST"
                or request.url.scheme != "https" or request.url.host != "api.deepseek.com"
                or request.url.path not in {"/responses", "/v1/responses", "/v1/chat/completions"}):
            raise ValueError("gate1_unapproved_provider_request")
        body = json.loads(request.content)
        if body.get("temperature") != 0:
            raise ValueError("gate1_actual_outbound_temperature_not_zero")
        if scope["stage"] == "judge" and body.get("model") != "deepseek-chat":
            raise ValueError("gate1_judge_model_mismatch")
        if scope["stage"] == "generation":
            sent = json.loads(body["input"][0]["content"])
            row = scope["row"]
            expected_unique = list(dict.fromkeys(row["contexts"]))
            if ([s["excerpt"] for s in sent["sources"]] != expected_unique
                    or sent["user_query"] != row["question"]):
                if self.offline:
                    write_json(self.directory / "packing_mismatch.json", {"case_id": row["case_id"],
                        "expected_child_ids": [p["child_id"] for p in row["provenance"]],
                        "sent_sources": sent["sources"]})
                raise ValueError("gate1_generation_dropped_reordered_or_changed_unique_contexts")
            if not body["instructions"].startswith(scope["system_prompt"]):
                raise ValueError("gate1_generation_system_prompt_changed")
        index = len(self.entries) + 1
        entry = {"call_number": index, "stage": scope["stage"], "case_id": scope["case_id"],
                 "metric": scope.get("metric"), "path": request.url.path,
                 "request": body, "offline": self.offline}
        request.extensions["gate1_record_index"] = index - 1
        self.entries.append(entry)
        write_json(self.directory / f"{index:03}.json", entry)

    async def response(self, response):
        await response.aread()
        index = response.request.extensions["gate1_record_index"]
        entry = self.entries[index]
        entry["status_code"] = response.status_code
        if response.status_code == 200:
            body = response.json()
            entry["response"] = body
            entry["usage"] = body.get("usage")
        else:
            # Provider errors can echo credential fragments: never save their text.
            entry["error"] = "provider_http_error_body_not_exported"
        write_json(self.directory / f"{index + 1:03}.json", entry)


def failed_generation_result(row, detail):
    from app.agent.foundation.subgraphs.worldline.graph import NARRATIVE_UNAVAILABLE_MESSAGE, _failed_result
    failed = _failed_result({"task_id": "ragas-smoke:" + row["case_id"]},
                            NARRATIVE_UNAVAILABLE_MESSAGE, "narrative_generation_failed")
    return {**row, "answer": failed["answer"], "citation_ids": [],
            "generation_status": "failed_production_fallback", "generation_failure": detail}


def recover_saved_generation_failure(row, calls):
    """Finalize an interrupted evaluator's already-exhausted production failure.

    Revalidate both saved responses; never call the model again for this case.
    """
    from app.agent.foundation.subgraphs.worldline.grounding import (
        WorldLineGroundingError, WorldLineGroundingValidator, inline_citation_ids,
    )
    from app.agent.foundation.subgraphs.worldline.models import EvidenceSource, NarrativeDraft, NarrativeOutput
    from app.agent.structured_output.errors import ValidationFailure
    from app.agent.structured_output.validator import validate_object
    if len(calls) != 2 or any(c.get("status_code") != 200 for c in calls):
        raise ValueError("gate1_resume_failure_not_two_completed_attempts")
    codes, failure_types = [], []
    for call in calls:
        response = call["response"]
        text = next(part["text"] for message in response["output"] if message["type"] == "message"
                    for part in message["content"] if part["type"] == "output_text")
        value = json.loads(text)
        # The production repair only removes the obsolete citation_ids field
        # when knowledge exists. It never drops arbitrary extra model fields.
        value.pop("citation_ids", None)
        try:
            draft = validate_object(NarrativeDraft, value)
        except ValidationFailure as exc:
            codes.append(exc.code)
            failure_types.append(exc.failure_type.value)
            continue
        narrative = draft.narrative.strip()
        sent = json.loads(call["request"]["input"][0]["content"])
        sources = [EvidenceSource.model_validate({**s, "excerpt": s["excerpt"][:2000]}) for s in sent["sources"]]
        try:
            WorldLineGroundingValidator().validate(NarrativeOutput(
                narrative=narrative, citation_ids=inline_citation_ids(narrative)), sources)
        except WorldLineGroundingError as exc:
            codes.append(exc.code)
            failure_types.append("semantic_validation_error")
        else:
            raise ValueError("gate1_resume_saved_response_passed_validation")
    return failed_generation_result(row, {"failure_type": failure_types[-1], "attempts": 2,
                                          "validation_code": codes[-1], "saved_attempt_validation_codes": codes})


async def generate_all(rows, settings, recorder, *, offline=False):
    import httpx
    from openai import AsyncOpenAI
    import yaml
    from app.llm import client as production_client
    from app.agent.foundation.context_sources import SourceRegistry, source_item
    from app.agent.foundation.rag_source import SourceItems
    from app.agent.foundation.subgraphs.worldline import generation, stage_pack
    from app.agent.foundation.subgraphs.worldline.models import (
        MusicEntityFacts, MusicKnowledgeDocument, ResolvedEntity, WorldLineGraph, WorldLineNode,
    )
    from app.agent.foundation.subgraphs.worldline.prompts import NARRATIVE_SYSTEM_PROMPT

    registry = yaml.safe_load((RELEASE_ROOT / "config_snapshot/entity_registry/v2.yaml").read_text(encoding="utf-8"))
    root = next(e for e in registry["entities"] if e["entity_id"] == "artist:jay_chou")
    entity = ResolvedEntity(entity_id=root["entity_id"], entity_type=root["entity_type"],
                            name=root["canonical_name"], source="frozen_release_registry")
    graph = WorldLineGraph(root=entity.entity_id, nodes=[WorldLineNode(
        id=entity.entity_id, type="artist", name=entity.name)], edges=[])
    facts = MusicEntityFacts(entity=entity, facts=[])
    original_prepare = stage_pack.prepare_explorer_input
    baseline_by_id = {r["case_id"]: r for r in read_jsonl(STEP2 / "cases.jsonl")}

    class ReplaySource:
        name = "rag"

        def collect(self, request, *, limit):
            row = SCOPE.get()["row"]
            if request.query != row["question"] or limit < 12:
                raise ValueError("gate1_replay_source_scope_mismatch")
            items = []
            for text, evidence in zip(row["contexts"], row["provenance"], strict=True):
                hit = baseline_by_id[row["case_id"]]["rerank_top12"][evidence["rank"] - 1]
                item = source_item("evidence:rag:" + evidence["parent_id"], "rag_evidence", text,
                    source="rag", intents=["worldline"], metadata={
                        "evidence_id": evidence["parent_id"], "chunk_id": evidence["child_id"],
                        "parent_id": evidence["parent_id"], "doc_id": evidence["doc_id"],
                        "source_id": evidence["source_id"], "source_name": evidence["source_name"],
                        "source_url": evidence["source_url"], "title": evidence["title"],
                        "citation": evidence["source_id"], "fusion_score": hit["fusion_score"],
                        "rerank_score": hit["rerank_score"], "score": hit["rerank_score"], "score_basis": "rerank",
                        "retrieval_rank": hit["retrieval_rank"], "rerank_rank": hit["rank"]},
                    references=[evidence["doc_id"], evidence["source_id"]])
                items.append(item.model_copy(update={"relevance": evidence["rerank_score"]}))
            return SourceItems(items, audit={})

    async def replay_prepare(*args, **kwargs):
        return await original_prepare(*args, **kwargs, registry=SourceRegistry([ReplaySource()]))

    def provider_factory(**kwargs):
        transport = None
        if offline:
            async def mock_response(request):
                sent = json.loads(json.loads(request.content)["input"][0]["content"])
                narrative = "离线结构验证。 [" + sent["sources"][0]["id"] + "]"
                return httpx.Response(200, json={"id": "resp_offline", "object": "response", "created_at": 0,
                    "model": "offline", "status": "completed", "output": [{"id": "msg_offline", "type": "message",
                    "role": "assistant", "status": "completed", "content": [{"type": "output_text",
                    "text": json.dumps({"narrative": narrative}, ensure_ascii=False), "annotations": []}]}],
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}})
            transport = httpx.MockTransport(mock_response)
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(trust_env=False, transport=transport,
                           event_hooks={"request": [recorder.request], "response": [recorder.response]}))

    results = []
    with (patch.object(production_client, "get_settings", lambda: settings),
          patch.object(production_client, "AsyncOpenAI", provider_factory),
          patch.object(stage_pack, "prepare_explorer_input", replay_prepare)):
        for row in rows:
            token = SCOPE.set({"stage": "generation", "case_id": row["case_id"],
                               "row": row, "system_prompt": NARRATIVE_SYSTEM_PROMPT})
            try:
                documents = [MusicKnowledgeDocument(id=p["child_id"], title=p["title"], content=text,
                    source=p["source_name"]) for text, p in zip(row["contexts"], row["provenance"], strict=True)]
                try:
                    output = await generation.StructuredWorldLineGenerator().generate_narrative(
                        entity, facts, documents, graph, [], user_query=row["question"])
                    result = {**row, "answer": output.narrative, "citation_ids": output.citation_ids,
                              "generation_status": "success", "generation_failure": None}
                except generation.WorldLineGenerationError as exc:
                    from app.agent.foundation.subgraphs.worldline.graph import _narrative_failure_detail
                    detail = _narrative_failure_detail(exc)
                    if detail["validation_code"] == "unclassified":
                        raise
                    result = failed_generation_result(row, detail)
                results.append(result)
                if not offline:
                    write_json(OUTPUT / "answers" / (row["case_id"] + ".json"), result)
                print(json.dumps({"stage": "offline_generation" if offline else "generation",
                                  "case_id": row["case_id"], "answer_characters": len(result["answer"]),
                                  "generation_status": result["generation_status"]}), flush=True)
            finally:
                SCOPE.reset(token)
    return results


def judge_all(rows, settings):
    import httpx
    import ragas
    from langchain_openai import ChatOpenAI
    from ragas import EvaluationDataset, evaluate
    from ragas.llms import LangchainLLMWrapper
    from ragas.metrics import Faithfulness, FactualCorrectness
    from ragas.run_config import RunConfig
    if ragas.__version__ != "0.4.3":
        raise ValueError("gate1_ragas_version_mismatch")
    if (OUTPUT / "scores.json").exists():
        raise ValueError("gate1_judge_already_completed")
    if (OUTPUT / "judge_calls").exists():
        raise ValueError("gate1_judge_attempts_already_exist_review_before_repeating")
    recorder = Recorder(OUTPUT / "judge_calls")
    by_question = {r["question"]: r["case_id"] for r in rows}

    def observe(original, metric_name):
        async def run(metric, sample, *args, **kwargs):
            token = SCOPE.set({"stage": "judge", "case_id": by_question[sample.user_input], "metric": metric_name})
            try:
                return await original(metric, sample, *args, **kwargs)
            finally:
                SCOPE.reset(token)
        return run

    async_client = httpx.AsyncClient(trust_env=False,
        event_hooks={"request": [recorder.request], "response": [recorder.response]})
    model = ChatOpenAI(api_key=settings.api_key.get_secret_value(), model="deepseek-chat",
        base_url="https://api.deepseek.com/v1", temperature=0, max_retries=0,
        timeout=settings.timeout_seconds, http_async_client=async_client)
    judge = LangchainLLMWrapper(model, bypass_temperature=True)
    dataset = EvaluationDataset.from_list([{"user_input": r["question"], "reference": r["ground_truth"],
        "retrieved_contexts": r["contexts"], "response": r["answer"]} for r in rows])
    with (patch.object(Faithfulness, "single_turn_ascore", observe(Faithfulness.single_turn_ascore, "faithfulness")),
          patch.object(FactualCorrectness, "single_turn_ascore", observe(FactualCorrectness.single_turn_ascore, "factual_correctness"))):
        result = evaluate(dataset, metrics=[Faithfulness(), FactualCorrectness()], llm=judge,
            raise_exceptions=False, show_progress=False,
            run_config=RunConfig(timeout=180, max_workers=1, max_retries=1))
    scored = []
    for row, scores in zip(rows, result.scores, strict=True):
        scored.append({"case_id": row["case_id"], "scores": {k: None if math.isnan(float(v)) else float(v)
                                                              for k, v in scores.items()},
                       "insufficient_faithfulness_note": "Raw score retained; inspect refusal separately, do not automatically treat honest abstention as an error."
                       if row["answerability"] == "insufficient" else None})
    write_json(OUTPUT / "scores.json", scored)
    print(json.dumps({"stage": "judge", "rows": len(scored), "http_attempts": len(recorder.entries)}), flush=True)


def main():
    configure_offline()  # Disable telemetry/tracing; provider HTTP remains explicit.
    sys.addaudithook(secret_guard)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["prepare", "dry-run-generate", "generate", "resume-generation", "judge"])
    args = parser.parse_args()
    if args.mode == "prepare":
        prepare()
        print(json.dumps({"status": "prepared", "case_ids": CASE_IDS, "labels": LABELS}))
        return
    preparation = json.loads((OUTPUT / "preparation.json").read_text(encoding="utf-8"))
    if preparation["protected_before"] != protected_hashes():
        raise ValueError("gate1_protected_inputs_changed")
    rows = json.loads((OUTPUT / "inputs.json").read_text(encoding="utf-8"))
    if tuple(r["case_id"] for r in rows) != CASE_IDS or Counter(r["answerability"] for r in rows) != LABELS:
        raise ValueError("gate1_population_drift")
    if args.mode == "dry-run-generate":
        settings = SimpleNamespace(api_key=SimpleNamespace(get_secret_value=lambda: "offline-not-a-credential"),
            base_url="https://api.deepseek.com", model="deepseek-flash", generation_model="deepseek-flash",
            timeout_seconds=60, max_output_tokens=4096)
        recorder = Recorder(OUTPUT / "offline_generation_calls", offline=True)
        asyncio.run(generate_all(rows, settings, recorder, offline=True))
    elif args.mode in {"generate", "resume-generation"}:
        settings = load_approved_llm_settings()
        directory = OUTPUT / "answers"
        resume = args.mode == "resume-generation"
        if not resume:
            directory.mkdir(exist_ok=False)
        recorder = Recorder(OUTPUT / "generation_calls", resume=resume)
        if resume:
            for row in rows:
                path = directory / (row["case_id"] + ".json")
                calls = [c for c in recorder.entries if c["case_id"] == row["case_id"]]
                if not path.exists() and calls:
                    write_json(path, recover_saved_generation_failure(row, calls))
            rows = [row for row in rows if not (directory / (row["case_id"] + ".json")).exists()]
        write_json(OUTPUT / "generation_config.json", {"model": settings.generation_model,
            "base_url": str(settings.base_url), "timeout_seconds": settings.timeout_seconds,
            "max_output_tokens": settings.max_output_tokens, "temperature": 0, "secret_exported": False})
        asyncio.run(generate_all(rows, settings, recorder))
    elif args.mode == "judge":
        settings = load_approved_llm_settings()
        rows = [json.loads((OUTPUT / "answers" / (case_id + ".json")).read_text(encoding="utf-8")) for case_id in CASE_IDS]
        judge_all(rows, settings)


if __name__ == "__main__":
    main()
