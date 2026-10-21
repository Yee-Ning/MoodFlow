"""Fresh Gold V5.1 baseline using the unchanged H2 seven-leg pipeline."""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter

from evals import rag_h2_metadata_v5 as h2
from evals import rag_h1_bundle_v5 as h1
from evals import rag_h1_union_v5 as union
from evals import rag_h1_disposition_checks_v5 as checks
from evals.ragas_gate0 import Gate0Guard


ROOT = h2.ROOT
OUTPUT = ROOT / "workspace/moodflow-review/v5-1-h2-baseline-20261018"
CONTROL = h2.OUTPUT / "attempt-02"
GOLD = ROOT / "ai-service/evals/datasets/rag_eval_gold_v5_1.frozen.jsonl"
GOLD_SHA = "792e9bb7b151c604d310aba2b6ea624e65609ad34e2b92049e0d296dc4dbae8e"
NEW_IDS = ("rag-v0-008", "rag-v0-009", "rag-v0-054")
LABEL_COUNTS = {"full": 17, "partial": 10, "insufficient": 16, "needs_input": 7}
MAX_GENERATION_ATTEMPTS = 3
SEED_ATTEMPT = None
CLARIFY_CONSTRAINTS = False
CONSTRAINT_REMINDER = "约束复核：只依据原问题；不得新增任何具体年份、人名、作品名或答案事实。原题没有年份就不要写任何年份。保留原题的对象和具名来源等限定；HyDE只写通用音乐知识，不直接回答问题。"
sha256, read_json, read_jsonl, write_json = h2.sha256, h2.read_json, h2.read_jsonl, h2.write_json


class BaselineError(RuntimeError):
    pass


def protected_hashes():
    result = h2.protected_hashes()
    for directory in (h2.OUTPUT, union.OUTPUT, GOLD.parent, ROOT / "workspace/moodflow-review/h3-traditional-bm25-v5-20261018"):
        for path in sorted(directory.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                result[path.relative_to(ROOT).as_posix()] = sha256(path)
    return result


def load_inputs():
    _h1_retrieval, baseline = h2.stack()
    _, projection, config, rerank, binding = baseline.preflight()
    if sha256(GOLD) != GOLD_SHA:
        raise BaselineError("v51_gold_sha256_mismatch_stop")
    all_cases = read_jsonl(GOLD)
    if (len(all_cases) != 50 or len({row["case_id"] for row in all_cases}) != 50
            or Counter(row["answerability"] for row in all_cases) != LABEL_COUNTS):
        raise BaselineError("v51_gold_population_mismatch")
    cases = [row for row in all_cases if row["answerability"] in {"full", "partial"}]
    if len(cases) != 27 or any(not row["relevant_parent_ids"] or not set(row["relevant_parent_ids"]) <= projection.parents for row in cases):
        raise BaselineError("v51_scorable_parent_population_mismatch")
    h2_queries, controls = read_json(CONTROL / "inputs.json"), read_jsonl(CONTROL / "cases.jsonl")
    manifest = read_json(CONTROL / "pre_call_manifest.json")
    if (config.model_dump(mode="json") != manifest["retrieval_config"]
            or rerank.model_dump(mode="json") != manifest["rerank_config"]
            or read_json(CONTROL / "run_manifest.json")["status"] != "complete"
            or len(controls) != 25 or not all(row["healthy"] for row in controls)):
        raise BaselineError("v51_h2_pipeline_or_control_drift")
    for name, digest in manifest["artifact_sha256"].items():
        if sha256(CONTROL / name) != digest:
            raise BaselineError("v51_h2_prepared_input_drift:" + name)
    for name, digest in read_json(h2.H1 / "pre_call_manifest.json")["code_sha256"].items():
        if sha256(baseline.AI_ROOT / name) != digest:
            raise BaselineError("v51_frozen_pipeline_code_drift:" + name)
    ids = {row["case_id"] for row in cases}
    old_ids = {row["case_id"] for row in h2_queries}
    if ids - old_ids != set(NEW_IDS) or old_ids - ids != {"rag-v0-031"}:
        raise BaselineError("v51_fixed_population_difference_drift")
    return cases, h2_queries, controls, projection, config, rerank, binding


def revalidate(record, row, resolver):
    queries, validation = union.validate_expansion(record["expansion_raw"], row, resolver)
    hyde, hyde_validation = checks.validate_hyde(record["hyde_raw"], row, resolver)
    if queries != record["queries"] or hyde != record["hyde"]:
        raise BaselineError("v51_reused_query_text_drift")
    return {**row, "queries": queries, "hyde": hyde, "expansion_raw": record["expansion_raw"],
            "hyde_raw": record["hyde_raw"], "expansion_checks": validation, "hyde_checks": hyde_validation}


def prepare():
    cases, old_queries, _, projection, config, rerank, binding = load_inputs()
    if OUTPUT.exists():
        raise BaselineError("v51_output_exists_no_overwrite")
    before = protected_hashes()
    old_by_id = {row["case_id"]: row for row in old_queries}
    records = {item["case_id"]: item["record"] for item in read_json(union.OUTPUT / "dispositions.json")}
    # Keep H2's exact frozen context/entity snapshot for common questions.
    # Re-resolving aliases may change their incidental list order across Python
    # processes; there is no reason to introduce that into an unchanged plan.
    new_resolved = {row["case_id"]: row for row in asyncio.run(h1.resolve_inputs(
        [case for case in cases if case["case_id"] in NEW_IDS], binding))}
    rows = []
    for case in cases:
        if case["case_id"] in old_by_id:
            old = old_by_id[case["case_id"]]
            if old["question"] != case["question"]:
                raise BaselineError("v51_common_question_drift")
            row = {**records[case["case_id"]], **old, "answerability_for_reporting_only": case["answerability"]}
        else:
            row = new_resolved[case["case_id"]]
        rows.append(row)
    _, resolver = h1.registry_components(binding)
    reused = []
    for row in rows:
        if row["case_id"] in old_by_id:
            old = old_by_id[row["case_id"]]
            for field in ("question", "completed_query", "context", "entities"):
                if row[field] != old[field]:
                    raise BaselineError("v51_h2_common_input_drift:" + field)
            record = revalidate(records[row["case_id"]], row, resolver)
            if record["queries"] != old["queries"] or record["hyde"] != old["hyde"]:
                raise BaselineError("v51_h2_common_expansion_drift")
            record["provenance"] = {"kind": "reused_frozen_H1_H2", "path": (CONTROL / "inputs.json").relative_to(ROOT).as_posix(),
                                    "sha256": sha256(CONTROL / "inputs.json")}
            reused.append(record)
    if len(reused) != 24 or before != protected_hashes():
        raise BaselineError("v51_prepare_reuse_or_hash_mismatch")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    write_json(OUTPUT / "inputs.json", rows)
    write_json(OUTPUT / "reused_queries.json", reused)
    write_json(OUTPUT / "prompts.json", {"expansion": h1.EXPANSION_PROMPT, "hyde": h1.HYDE_PROMPT})
    seeds, seed_hashes = {}, {}
    if SEED_ATTEMPT is not None:
        seed_hashes.update(read_json(SEED_ATTEMPT / "preparation.json").get("seed_artifact_sha256", {}))
        for case_id in NEW_IDS:
            path = SEED_ATTEMPT / "expansion" / (case_id + ".json")
            if not path.exists():
                continue
            saved = read_json(path)
            row = next(row for row in rows if row["case_id"] == case_id)
            for stage in ("expansion", "hyde"):
                if stage + "_raw" in saved:
                    value, validation = validate_stage(saved[stage + "_raw"], row, stage, resolver)
                    accepted = next(item for item in saved["attempt_history"] if item["stage"] == stage and item["accepted"])
                    seeds.setdefault(case_id, {})[stage] = {"raw": saved[stage + "_raw"], "value": value, "validation": validation,
                        "record_path": accepted.get("seed_record_path", path.relative_to(ROOT).as_posix()),
                        "accepted_attempt": accepted}
            seed_hashes[path.relative_to(ROOT).as_posix()] = sha256(path)
        for path in [*sorted((SEED_ATTEMPT / "expansion/calls").glob("*.json")), SEED_ATTEMPT / "expansion/state.json", SEED_ATTEMPT / "adapter_initial.py"]:
            seed_hashes[path.relative_to(ROOT).as_posix()] = sha256(path)
    write_json(OUTPUT / "generation_seeds.json", seeds)
    write_json(OUTPUT / "preparation.json", {"prepared_at": h1.now(), "gold_sha256": GOLD_SHA,
        "case_ids": [row["case_id"] for row in cases], "label_counts": {"full": 17, "partial": 10},
        "reused_case_ids": [row["case_id"] for row in reused], "new_generation_case_ids": list(NEW_IDS),
        "excluded": ["rag-v0-005", "rag-v0-031", "rag-v0-045"],
        "protected_before": before, "adapter_sha256": sha256(Path(__file__)),
        "artifact_sha256": {name: sha256(OUTPUT / name) for name in ("inputs.json", "reused_queries.json", "prompts.json", "generation_seeds.json")},
        "seed_artifact_sha256": seed_hashes,
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "generation_protocol": {"model": h1.MODEL, "temperature": 0, "SDK_retries": 0,
            "max_attempts_per_stage": MAX_GENERATION_ATTEMPTS, "max_new_calls": 18,
            "retry_policy": "Only constraint/semantic failures may get a fresh unchanged-prompt attempt; preserve every response; never select using retrieval scores. Provider failures stop.",
            "validators": "Unchanged final H1 global object-set coverage + HyDE checks; no new people/years/lost restrictions.",
            "new_054_review": "Every subquery must retain explicit source 索尼; no resolver or source filter change.",
            "authorization": "West explicitly requested new DeepSeek expansion/HyDE for the 27-case V5.1 baseline, including the three additions."},
        "qdrant_calls": 0, "new_llm_calls": 0, "gold_used_in_provider_request": False})
    prep = read_json(OUTPUT / "preparation.json")
    prep["generation_protocol"].update(constraint_clarification=CLARIFY_CONSTRAINTS,
        clarification_text=CONSTRAINT_REMINDER if CLARIFY_CONSTRAINTS else None,
        source_reminder_rule="If original question contains 索尼, explicitly require that exact source in every expansion query; no new source is added.",
        seeded_stages={key: list(value) for key, value in seeds.items()},
        initial_attempt_directory=SEED_ATTEMPT.relative_to(ROOT).as_posix() if SEED_ATTEMPT is not None else None)
    write_json(OUTPUT / "preparation.json", prep)
    print(json.dumps({"prepared": True, "cases": 27, "reused": 24, "new": list(NEW_IDS)}), flush=True)


def load_prepared():
    inputs = load_inputs()
    prep = read_json(OUTPUT / "preparation.json")
    if (prep["protected_before"] != protected_hashes() or prep["adapter_sha256"] != sha256(Path(__file__))
            or any(sha256(OUTPUT / name) != digest for name, digest in prep["artifact_sha256"].items())
            or any(sha256(ROOT / name) != digest for name, digest in prep.get("seed_artifact_sha256", {}).items())):
        raise BaselineError("v51_prepared_input_or_code_drift")
    return inputs


class Recorder(h1.Recorder):
    def __init__(self, directory):
        super().__init__(directory)
        self.attempt = None
        self.clarify = False

    async def request(self, request):
        if (self.scope is None or self.scope[0]["case_id"] not in NEW_IDS or self.attempt not in range(1, MAX_GENERATION_ATTEMPTS + 1)
                or request.method != "POST" or request.url.scheme != "https" or request.url.host != "api.deepseek.com"
                or request.url.path != "/v1/chat/completions" or request.url.username or request.url.password):
            raise h2.SafetyStop("v51_generation_http_scope_forbidden")
        row, stage = self.scope
        body = json.loads(request.content)
        if body != generation_body(row, stage, clarify=self.clarify) or type(body["temperature"]) is not int:
            raise h2.SafetyStop("v51_generation_request_body_drift")
        if any(entry["case_id"] == row["case_id"] and entry["stage"] == stage and entry["attempt"] == self.attempt for entry in self.entries):
            raise h2.SafetyStop("v51_duplicate_physical_http_attempt")
        entry = {"call_number": len(self.entries) + 1, "case_id": row["case_id"], "stage": stage,
                 "attempt": self.attempt, "request": body,
                 "request_sha256": hashlib.sha256(request.content).hexdigest(), "started_at": h1.now()}
        self.entries.append(entry)
        request.extensions["h1_record_index"] = len(self.entries) - 1
        request.extensions["h1_started"] = perf_counter()
        self.save(entry)


def generation_body(row, stage, *, clarify=False):
    body = h1.request_body(row, stage)
    if clarify:
        reminder = CONSTRAINT_REMINDER
        if stage == "expansion" and "索尼" in row["completed_query"]:
            reminder += "本题原问题的具名来源是“索尼”；queries数组中每一条子查询都必须逐字包含“索尼”，不能只在部分子查询出现。"
        if stage == "expansion":
            body["messages"][1]["content"] = json.dumps({"question": row["completed_query"], "constraint_reminder": reminder}, ensure_ascii=False)
        else:
            body["messages"][1]["content"] += "\n\n" + CONSTRAINT_REMINDER
    return body


def validate_stage(raw, row, stage, resolver):
    if stage == "expansion":
        queries, validation = union.validate_expansion(raw, row, resolver)
        if "索尼" in row["completed_query"] and any("索尼" not in query for query in queries):
            raise BaselineError("v51_named_source_restriction_lost")
        return queries, validation
    return checks.validate_hyde(raw, row, resolver)


async def generate():
    import httpx
    from openai import AsyncOpenAI

    *_, binding = load_prepared()
    rows = [row for row in read_json(OUTPUT / "inputs.json") if row["case_id"] in NEW_IDS]
    directory = OUTPUT / "expansion"
    if directory.exists():
        raise BaselineError("v51_generation_already_attempted_no_overwrite")
    directory.mkdir()
    _, resolver = h1.registry_components(binding)
    # Existing connection loader supplies credentials directly to the SDK in
    # process memory; never serialize or print connection/credential values.
    key, timeout = h1.approved_settings(True)
    recorder = Recorder(directory / "calls")
    recorder.clarify = read_json(OUTPUT / "preparation.json")["generation_protocol"].get("constraint_clarification", False)
    seeds = read_json(OUTPUT / "generation_seeds.json")
    state = {"started_at": h1.now(), "status": "generating", "completed_case_ids": [], "SDK_retries": 0}
    write_json(directory / "state.json", state)
    results, error = [], None
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=h1.BASE_URL, max_retries=0, timeout=timeout, http_client=http) as client:
                for row in rows:
                    item = {**row, "attempt_history": [], "provenance": {"kind": "new_DeepSeek"}}
                    for stage in ("expansion", "hyde"):
                        seed = seeds.get(row["case_id"], {}).get(stage)
                        if seed:
                            value, validation = validate_stage(seed["raw"], row, stage, resolver)
                            item[stage + "_raw"] = seed["raw"]
                            item["queries" if stage == "expansion" else "hyde"] = value
                            item[stage + "_checks"] = validation
                            item["attempt_history"].append({**seed["accepted_attempt"], "seed_record_path": seed["record_path"]})
                            continue
                        accepted = False
                        for attempt in range(1, MAX_GENERATION_ATTEMPTS + 1):
                            recorder.scope, recorder.attempt = (row, stage), attempt
                            response = await client.chat.completions.create(**generation_body(row, stage, clarify=recorder.clarify))
                            if not response.choices or response.choices[0].finish_reason != "stop" or not isinstance(response.choices[0].message.content, str):
                                raise BaselineError("v51_generation_response_incomplete")
                            raw = response.choices[0].message.content
                            entry = {"stage": stage, "attempt": attempt, "call_number": len(recorder.entries), "raw": raw,
                                     "constraint_clarification": recorder.clarify}
                            try:
                                value, validation = validate_stage(raw, row, stage, resolver)
                                entry["accepted"] = True
                                item[stage + "_raw"] = raw
                                item["queries" if stage == "expansion" else "hyde"] = value
                                item[stage + "_checks"] = validation
                                accepted = True
                            except (h1.H1Error, BaselineError) as exc:
                                entry.update(accepted=False, error_code=str(exc))
                            item["attempt_history"].append(entry)
                            write_json(directory / (row["case_id"] + ".json"), item)
                            print(json.dumps({"case_id": row["case_id"], "stage": stage, "attempt": attempt, "accepted": accepted,
                                              "error_code": entry.get("error_code")}), flush=True)
                            if accepted:
                                break
                        if not accepted:
                            raise BaselineError("v51_generation_validation_attempts_exhausted:" + row["case_id"] + ":" + stage)
                    if item["hyde"].casefold() in {text.casefold() for text in item["queries"]}:
                        raise BaselineError("v51_hyde_duplicates_subquery")
                    write_json(directory / (row["case_id"] + ".json"), item)
                    results.append(item)
                    state["completed_case_ids"].append(row["case_id"])
                    write_json(directory / "state.json", state)
        write_json(directory / "new_queries.json", results)
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (BaselineError, h1.H1Error, h2.SafetyStop)) else type(exc).__name__
    finally:
        state.update(status="complete_pending_read_review" if len(results) == 3 and error is None else "incomplete",
                     finished_at=h1.now(), error_code=error, usage=h1.usage_summary(recorder.entries),
                     protected_unchanged=protected_hashes() == read_json(OUTPUT / "preparation.json")["protected_before"])
        write_json(directory / "state.json", state)
    print(json.dumps({"status": state["status"], "completed": len(results), "error_code": error, "usage": state["usage"]}), flush=True)
    return 0 if state["status"] == "complete_pending_read_review" else 1


def freeze():
    cases, old_queries, _, projection, config, rerank, binding = load_prepared()
    if (OUTPUT / "freeze_manifest.json").exists():
        raise BaselineError("v51_queries_already_frozen")
    review = read_json(OUTPUT / "expansion/read_review.json")
    new = read_json(OUTPUT / "expansion/new_queries.json")
    if (review["reviewer"] != "Codex" or not review["passed"] or review["reviewed_case_ids"] != list(NEW_IDS)
            or review["new_queries_sha256"] != sha256(OUTPUT / "expansion/new_queries.json")
            or read_json(OUTPUT / "expansion/state.json")["status"] != "complete_pending_read_review"):
        raise BaselineError("v51_expansion_review_missing_or_stale")
    _, resolver = h1.registry_components(binding)
    by_id = {row["case_id"]: row for row in [*read_json(OUTPUT / "reused_queries.json"), *new]}
    queries = [by_id[case["case_id"]] for case in cases]
    plans, filters = [], []
    from evals.rag_h1_bundle_adapter import bundle_plan
    old_plans = {row["case_id"]: plan for row, plan in zip(old_queries, read_json(CONTROL / "plans.json"), strict=True)}
    old_filters = {row["case_id"]: condition for row, condition in zip(old_queries, read_json(CONTROL / "filters.json"), strict=True)}
    for row in queries:
        revalidate(row, row, resolver)
        if row["case_id"] in NEW_IDS:
            validate_stage(row["expansion_raw"], row, "expansion", resolver)
        constraints = row["constraints"]
        year = [constraints["start_year"], constraints["end_year"]] if constraints["start_year"] is not None else None
        gate0 = {"entity_ids": constraints["entity_ids"], "year_range": year}
        base = bundle_plan(row["completed_query"], row["queries"], row["hyde"], binding.profile).model_dump(mode="json")
        plan, compiled = h2.build_plan(base, gate0)
        condition = {"case_id": row["case_id"], **gate0, "effective_qdrant_filter": h2.dumped_filter(compiled),
                     "intermediate_ast_requires_three_tier_compiler": year is not None,
                     "eligible_children": sum(h2.matches(payload, h2.dumped_filter(compiled)) for payload in projection.children.values())}
        if row["case_id"] in old_plans and (plan.model_dump(mode="json") != old_plans[row["case_id"]]
                or condition["effective_qdrant_filter"] != old_filters[row["case_id"]]["effective_qdrant_filter"]):
            raise BaselineError("v51_common_h2_plan_or_filter_drift")
        plans.append(plan.model_dump(mode="json"))
        filters.append(condition)
    write_json(OUTPUT / "frozen_queries.json", queries)
    write_json(OUTPUT / "plans.json", plans)
    write_json(OUTPUT / "filters.json", filters)
    write_json(OUTPUT / "freeze_manifest.json", {"frozen_at": h1.now(), "frozen_by": "Codex; technical freeze for West-requested baseline, not West semantic signoff",
        "case_ids": [row["case_id"] for row in cases], "cases": 27, "reused": 24, "new": list(NEW_IDS),
        "artifact_sha256": {name: sha256(OUTPUT / name) for name in ("frozen_queries.json", "plans.json", "filters.json", "expansion/read_review.json", "expansion/new_queries.json")},
        "call_sha256": {p.relative_to(OUTPUT).as_posix(): sha256(p) for p in sorted((OUTPUT / "expansion/calls").glob("*.json"))},
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "retrieval_authorized": "Current West request explicitly authorizes the complete 27-case H2 baseline."})
    print(json.dumps({"frozen": True, "cases": 27, "shared_24_plans_filters_exactly_H2": True}), flush=True)


def load_frozen():
    inputs = load_prepared()
    manifest = read_json(OUTPUT / "freeze_manifest.json")
    for name, digest in {**manifest["artifact_sha256"], **manifest["call_sha256"]}.items():
        if sha256(OUTPUT / name) != digest:
            raise BaselineError("v51_frozen_query_drift:" + name)
    return inputs


def expected_legs(query):
    return [item for text in query["queries"] for item in ({"text": text, "channel": "dense"}, {"text": text, "channel": "lexical"})] + [{"text": query["hyde"], "channel": "dense"}]


def run(guard):
    cases, _, _, projection, config, _, binding = load_frozen()
    _, baseline = h2.stack()
    queries, plans, filters = read_json(OUTPUT / "frozen_queries.json"), read_json(OUTPUT / "plans.json"), read_json(OUTPUT / "filters.json")
    directory = OUTPUT / "retrieval"
    if directory.exists():
        raise BaselineError("v51_retrieval_already_attempted_no_repeat")
    directory.mkdir()
    state = {"started_at": h1.now(), "status": "loading_pinned_local_models", "completed_case_count": 0,
             "gold_sha256": GOLD_SHA, "qdrant_write_calls": 0, "new_llm_calls_during_retrieval": 0, "H3_legs": False}
    write_json(directory / "run_manifest.json", state)
    audit, results, error = h2.RequestAudit(), [], None
    try:
        encoder, _, scorer, _ = baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch
        state["runtime"] = {"torch": torch.__version__, "embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        with audit.installed():
            write_json(directory / "collection_before.json", h2.inventory(audit, projection))
            with (directory / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
                for i, (case, query, plan, condition) in enumerate(zip(cases, queries, plans, filters, strict=True), 1):
                    if case["case_id"] != query["case_id"] or case["case_id"] != condition["case_id"]:
                        raise BaselineError("v51_retrieval_case_order_drift")
                    audit.active, audit.expected_filter, audit.expected_lexical = case["case_id"], condition["effective_qdrant_filter"], query["queries"]
                    offset = len(audit.requests)
                    row = h2.collect_case({**case, "control_legs": expected_legs(query)}, query, plan, condition, projection, config, binding, audit)
                    row["qdrant_requests"] = audit.requests[offset:]
                    stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    results.append(row)
                    state.update(status="running", completed_case_count=len(results), last_case_id=row["case_id"])
                    write_json(directory / "run_manifest.json", state)
                    print(json.dumps({"progress": f"{i}/27", "case_id": row["case_id"], "healthy": row["healthy"], "metrics": row["metrics"]}), flush=True)
                    if not row["healthy"]:
                        raise BaselineError("v51_unhealthy_case_stop")
            audit.active = None
            write_json(directory / "collection_after.json", h2.inventory(audit, projection))
    except BaseException as exc:
        error = str(exc) if isinstance(exc, (BaselineError, h2.H2Error, h2.SafetyStop)) else type(exc).__name__
    finally:
        unchanged = protected_hashes() == read_json(OUTPUT / "preparation.json")["protected_before"]
        complete = len(results) == 27 and error is None and unchanged and not guard.denied and not audit.safety_events
        state.update(status="complete" if complete else "incomplete", error_code=error, finished_at=h1.now(),
            completed_case_count=len(results), protected_unchanged=unchanged, guard_denied=guard.denied, safety_events=audit.safety_events,
            qdrant_request_counts=dict(Counter(f"{row['method']} {row['path']}" for row in audit.requests)))
        write_json(directory / "run_manifest.json", state)
        write_json(directory / "qdrant_requests.json", audit.requests)
    print(json.dumps({"status": state["status"], "completed": len(results), "error_code": error}), flush=True)
    return 0 if complete else 1


def main():
    global OUTPUT, SEED_ATTEMPT, CLARIFY_CONSTRAINTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "generate", "freeze", "run"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--seed-attempt", type=Path)
    parser.add_argument("--clarify-constraints", action="store_true")
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    SEED_ATTEMPT = args.seed_attempt.resolve() if args.seed_attempt else None
    CLARIFY_CONSTRAINTS = args.clarify_constraints
    h2.configure()
    if args.command == "generate":
        sys.addaudithook(h1.secret_guard)
        return asyncio.run(generate())
    guard = h2.ReadOnlyGuard() if args.command == "run" else Gate0Guard()
    sys.addaudithook(guard.audit)
    if args.command == "run":
        import torch
        print(json.dumps({"cuda_bootstrap": torch.cuda.is_available(), "torch": torch.__version__}), flush=True)
        return run(guard)
    if args.command == "prepare":
        prepare()
    else:
        freeze()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
