"""Measure live frozen H2 retrieval and unchanged production narrative generation."""

import asyncio
from collections import Counter
import hashlib
import json
import logging
from pathlib import Path
import sys
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[3]
OUTPUT = ROOT / "workspace/moodflow-review/generation-baseline-v53-20261019"
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_3.frozen.jsonl"
GOLD_SHA = "af584127350a75ab3f892c721b98669b64ef2814ebeb2c728a0b4b5280a99c50"


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def failure_category(code):
    for category in ("paragraph_missing_citation", "additionalProperties", "unknown_citation", "narrative_length", "extra_forbidden"):
        if category in code:
            return category
    if "timeout" in code.lower():
        return "timeout"
    return "other:" + code


def accepted_status(citation_ids):
    return "success" if citation_ids else "refused"


def usage_totals(entries):
    total = Counter()
    for entry in entries:
        usage = entry.get("usage") or {}
        total["http_attempts"] += 1
        total["usage_missing"] += not bool(usage)
        total["input_tokens"] += usage.get("input_tokens", usage.get("prompt_tokens", 0)) or 0
        total["output_tokens"] += usage.get("output_tokens", usage.get("completion_tokens", 0)) or 0
        total["reported_total_tokens"] += usage.get("total_tokens", 0) or 0
        total["cache_hit_input_tokens"] += usage.get("prompt_cache_hit_tokens", 0) or 0
    return dict(total)


def selected_gold():
    if digest(GOLD) != GOLD_SHA:
        raise ValueError("v53_gold_hash_drift")
    selected = [row for row in rows(GOLD) if row["answerability"] in {"full", "partial"}]
    if len(selected) != 34 or Counter(row["answerability"] for row in selected) != {"full": 24, "partial": 10}:
        raise ValueError("v53_population_drift")
    return selected


def check_pins(pins):
    for name, expected in pins.items():
        if digest(ROOT / name) != expected:
            raise ValueError("v53_protected_drift:" + name)


def recorder_type(smoke):
    class Recorder(smoke.Recorder):
        async def request(self, request):
            scope = smoke.SCOPE.get()
            body = json.loads(request.content)
            if (not scope or request.method != "POST" or request.url.scheme != "https"
                    or request.url.host != "api.deepseek.com" or request.url.username or request.url.password
                    or request.url.path not in {"/responses", "/v1/responses", "/v1/chat/completions"}
                    or scope["case_id"] not in {row["case_id"] for row in selected_gold()}):
                raise ValueError("v53_provider_scope_violation")
            if type(body.get("temperature")) not in (int, float) or body["temperature"] != 0:
                raise ValueError("v53_actual_temperature_drift")
            if scope["stage"] == "judge" and body.get("model") != "deepseek-chat":
                raise ValueError("v53_judge_model_drift")
            if scope["stage"] == "generation" and sum(entry["case_id"] == scope["case_id"] for entry in self.entries) >= 2:
                raise ValueError("v53_extra_generation_attempt")
            index = len(self.entries)
            entry = {"call_number": index + 1, "stage": scope["stage"], "case_id": scope["case_id"],
                     "metric": scope.get("metric"), "path": request.url.path, "request": body, "offline": False}
            self.entries.append(entry)
            request.extensions["gate1_record_index"] = index
            write(self.directory / f"{index + 1:03}.json", entry)

        async def response(self, response):
            await super().response(response)
            if self.entries[-1]["stage"] == "judge" and len(self.entries) % 5 == 0:
                print(json.dumps({"stage": "judge_usage", **usage_totals(self.entries)}), flush=True)

    return Recorder


def install_guard(smoke, h2):
    class Guard(h2.ReadOnlyGuard):
        def audit(self, event, args):
            if event == "socket.connect" and len(args) >= 2:
                caller = sys._getframe(1)
                if (caller.f_globals.get("__name__") == "socket"
                        and caller.f_code.co_name == "_fallback_socketpair"
                        and caller.f_locals.get("csock") is args[0]
                        and args[1] == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
                        and args[1][0] in {"127.0.0.1", "::1"}):
                    self.local_socketpairs += 1
                    return
            if event == "open" and isinstance(args[0], (str, bytes)):
                if Path(args[0]).name.lower().startswith(".env"):
                    smoke.secret_guard(event, args)
                    return
            if event == "socket.getaddrinfo":
                host = args[0].decode("ascii") if isinstance(args[0], bytes) else args[0]
                if host == "api.deepseek.com" and str(args[1]) == "443":
                    return
            if event == "socket.connect" and isinstance(args[1], tuple) and args[1][1] == 443:
                return
            super().audit(event, args)

    guard = Guard()
    sys.addaudithook(guard.audit)
    return guard


async def generate_one(case, items, settings, recorder, smoke):
    import httpx
    from openai import AsyncOpenAI
    from app.llm import client
    from app.agent.foundation.context_sources import SourceRegistry
    from app.agent.foundation.subgraphs.worldline import generation, stage_pack
    from app.agent.foundation.subgraphs.worldline.models import MusicEntityFacts, MusicKnowledgeDocument, ResolvedEntity, WorldLineGraph, WorldLineNode
    from app.agent.foundation.subgraphs.worldline.narrative_normalization import narrative_audit_scope
    from evals.ragas_full_v5 import terminal_detail

    entity = ResolvedEntity(entity_id="artist:jay_chou", entity_type="artist", name="周杰伦", source="frozen_release_registry")
    graph = WorldLineGraph(root=entity.entity_id, nodes=[WorldLineNode(id=entity.entity_id, type="artist", name=entity.name)], edges=[])
    facts = MusicEntityFacts(entity=entity, facts=[])
    original_prepare = stage_pack.prepare_explorer_input

    class CurrentRequestSource:
        name = "rag"

        def collect(self, request, *, limit):
            if request.query != case["question"] or limit < 12:
                raise ValueError("v53_current_request_source_scope_drift")
            return items

    async def prepare(*args, **kwargs):
        return await original_prepare(*args, **kwargs, registry=SourceRegistry([CurrentRequestSource()]))

    def factory(**kwargs):
        return AsyncOpenAI(**kwargs, http_client=httpx.AsyncClient(trust_env=False,
                           event_hooks={"request": [recorder.request], "response": [recorder.response]}))

    token = smoke.SCOPE.set({"stage": "generation", "case_id": case["case_id"]})
    result = {key: case[key] for key in ("case_id", "question", "answerability", "ground_truth", "relevant_parent_ids")}
    try:
        with (patch.object(client, "get_settings", lambda: settings), patch.object(client, "AsyncOpenAI", factory),
              patch.object(stage_pack, "prepare_explorer_input", prepare), narrative_audit_scope() as audit):
            documents = [MusicKnowledgeDocument(id=item.metadata["chunk_id"], title=item.metadata["title"],
                         content=item.content, source=item.metadata["source_name"]) for item in items]
            try:
                answer = await generation.StructuredWorldLineGenerator().generate_narrative(
                    entity, facts, documents, graph, [], user_query=case["question"])
                result.update(answer=answer.narrative, citation_ids=answer.citation_ids,
                              generation_status=accepted_status(answer.citation_ids), generation_failure=None,
                              structural_validation_passed=True)
                if not answer.citation_ids:
                    result["failure_category"] = "refusal"
            except generation.WorldLineGenerationError as error:
                detail = terminal_detail(error)
                result.update(answer="", citation_ids=[], generation_status="failed_production_fallback",
                              generation_failure=detail, failure_category=failure_category(detail["validation_code"]))
            result["normalization_and_boundary_events"] = audit
    finally:
        smoke.SCOPE.reset(token)
    calls = [entry for entry in recorder.entries if entry["case_id"] == case["case_id"]]
    sent = json.loads(calls[-1]["request"]["input"][0]["content"]) if calls else {"sources": []}
    result.update(contexts=[source["excerpt"] for source in sent.get("sources", [])],
                  sent_sources=sent.get("sources", []), generation_attempts=len(calls),
                  retrieval_source_items=[{"content": item.content, "metadata": item.metadata} for item in items])
    return result


def online():
    from evals import ragas_smoke_v5 as smoke
    from tools.v52_offline_runtime import dimension_034 as frozen
    from tools.v52_offline_runtime.run_baseline import anonymous_local_models
    from app.agent.foundation import rag_source
    import torch

    if OUTPUT.exists():
        raise ValueError("v53_online_run_already_exists")
    frozen.base.h2.configure()
    print(json.dumps({"cuda_bootstrap": torch.cuda.is_available()}), flush=True)
    guard = install_guard(smoke, frozen.base.h2)
    gold = selected_gold()
    with anonymous_local_models():
        inputs, bundles, _, _ = frozen.load_source()
        base = frozen.base
        projection = frozen.repair.overlay_projection(inputs[3])
        config, binding = inputs[4], inputs[-1]
        pins = read(ROOT / "workspace/moodflow-review/timeline-gate1-frrf-20261019/preflight.json")["protected_before"]
        for directory in (ROOT / "ai-service/app",):
            for path in directory.rglob("*"):
                if path.is_file() and path.suffix in {".py", ".yaml", ".yml"}:
                    pins[path.relative_to(ROOT).as_posix()] = digest(path)
        for path in (Path(__file__), Path(__file__).with_name("test_generation_baseline_v53.py")):
            pins[path.relative_to(ROOT).as_posix()] = digest(path)
        pins[GOLD.relative_to(ROOT).as_posix()] = GOLD_SHA
        check_pins(pins)
        OUTPUT.mkdir()
        for name in ("answers", "retrieval", "exceptions"):
            (OUTPUT / name).mkdir()
        write(OUTPUT / "preparation.json", {"status": "prepared_before_live_calls", "cases": 34,
            "gold_sha256": GOLD_SHA, "protected_before": pins,
            "case_ids": [case["case_id"] for case in gold], "bundles": {cid: {"query": bundle[1], "plan": bundle[2], "filter": bundle[3]} for cid, bundle in bundles.items()},
            "secret_authorization": "Direct user approval in this conversation: 授权本轮受控读取; this34-case online run and Ragas judge only, no credential export",
            "pipeline": "fresh Qdrant retrieval -> production Child RRF -> selection -> local rerank -> actual RagSource items -> production pack -> unchanged narrative generator",
            "production_changes": False, "insufficient_or_needs_input_generated": False,
            "judge": {"model": "deepseek-chat", "temperature": 0, "bypass_temperature": True, "ragas": "0.4.3"}})
        settings = smoke.load_approved_llm_settings()
        write(OUTPUT / "generation_config.json", {"model": settings.generation_model, "base_url": settings.base_url,
              "timeout_seconds": settings.timeout_seconds, "max_output_tokens": settings.max_output_tokens,
              "temperature": "unchanged production structured-output request", "secret_exported": False})
        recorder = recorder_type(smoke)(OUTPUT / "generation_calls")
        encoder, _, scorer, _ = base.baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        audit = base.h2.RequestAudit()
        once = frozen.repair.FilteredRepairOnce(OUTPUT / "exceptions")
        with base.target_scope(), audit.installed():
            write(OUTPUT / "collection.before.json", base.inventory(projection))
        original_collect = rag_source.RagSource.collect
        state = {"status": "running", "expected": 34, "completed": 0}
        write(OUTPUT / "generation_state.json", state)
        for case in gold:
            captured = []

            def capture(self, request, *, limit):
                result = original_collect(self, request, limit=limit)
                captured.append(result)
                return result

            _, query, plan, condition = bundles[case["case_id"]]
            audit.active, audit.expected_filter, audit.expected_lexical = case["case_id"], condition["effective_qdrant_filter"], query["queries"]
            with (base.target_scope(), audit.installed(), patch.object(base.h2, "intercept_fallback", once),
                  patch.object(rag_source.RagSource, "collect", capture)):
                retrieval = base.h2.collect_case({**case, "control_legs": base.v51.expected_legs(query)}, query,
                             plan, condition, projection, config, binding, audit)
            write(OUTPUT / "retrieval" / (case["case_id"] + ".json"), retrieval)
            if len(captured) != 1:
                raise ValueError("v53_single_fresh_retrieval_capture_failed")
            answer = asyncio.run(generate_one(case, captured[0], settings, recorder, smoke))
            if guard.denied:
                state.update(status="incomplete_harness_guard", guard_denied=guard.denied)
                write(OUTPUT / "generation_state.json", state)
                raise ValueError("v53_guard_denied_operation")
            answer["retrieval_metrics"] = retrieval["metrics"]
            answer["retrieval_healthy"] = retrieval["healthy"]
            write(OUTPUT / "answers" / (case["case_id"] + ".json"), answer)
            state.update(completed=state["completed"] + 1, last_case_id=case["case_id"], generation_usage=usage_totals(recorder.entries))
            write(OUTPUT / "generation_state.json", state)
            print(json.dumps({"stage": "online_generation", "case_id": case["case_id"], "completed": state["completed"],
                  "status": answer["generation_status"], "failure": answer.get("failure_category"), "contexts": len(answer["contexts"])}), flush=True)
        audit.active = None
        with base.target_scope(), audit.installed():
            write(OUTPUT / "collection.after.json", base.inventory(projection))
        check_pins(pins)
        if guard.denied or audit.safety_events:
            raise ValueError("v53_guard_or_safety_violation")
        state.update(status="complete", protected_unchanged=True, guard_denied=guard.denied)
        write(OUTPUT / "generation_state.json", state)
        write(OUTPUT / "qdrant_requests.json", audit.requests)
        write(OUTPUT / "generation_usage.json", usage_totals(recorder.entries))


def judge():
    from evals import ragas_smoke_v5 as smoke
    from evals import ragas_full_v5 as full
    from evals import rag_h2_metadata_v5 as h2

    h2.configure()
    install_guard(smoke, h2)
    gold = selected_gold()
    if read(OUTPUT / "generation_state.json")["status"] != "complete":
        raise ValueError("v53_incomplete_generation_cannot_judge")
    check_pins(read(OUTPUT / "preparation.json")["protected_before"])
    answers = [read(OUTPUT / "answers" / (case["case_id"] + ".json")) for case in gold]
    successful = [row for row in answers if row["generation_status"] == "success"]
    write(OUTPUT / "judge_population.json", {"all_attempted": len(answers), "judge_rows": len(successful),
        "case_ids": [row["case_id"] for row in successful], "excluded_failures": [row["case_id"] for row in answers if row["generation_status"] != "success"]})
    settings = smoke.load_approved_llm_settings()
    with (patch.object(smoke, "OUTPUT", OUTPUT), patch.object(full, "OUTPUT", OUTPUT),
          patch.object(smoke, "CASE_IDS", tuple(case["case_id"] for case in gold)),
          patch.object(smoke, "Recorder", recorder_type(smoke))):
        full.judge(successful, settings)
    calls = [read(path) for path in sorted((OUTPUT / "judge_calls").glob("[0-9][0-9][0-9].json"))]
    write(OUTPUT / "judge_usage.json", usage_totals(calls))
    check_pins(read(OUTPUT / "preparation.json")["protected_before"])


def main():
    logging.getLogger("app.llm.client").disabled = True
    if sys.argv[1:] == ["run"]:
        online()
    elif sys.argv[1:] == ["judge"]:
        judge()
    else:
        raise ValueError("v53_only_run_or_judge")


if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        code = str(error) if isinstance(error, ValueError) and str(error).startswith("v53_") else type(error).__name__
        print(json.dumps({"status": "stopped", "safe_error_code": code}), flush=True)
        raise SystemExit(1) from None
