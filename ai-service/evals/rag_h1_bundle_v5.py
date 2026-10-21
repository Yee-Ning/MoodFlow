"""H1 revised Gate 1, preparation -> two LLM calls/case -> freeze -> West review.

This module deliberately has no retrieval command. No Qdrant access is allowed
before West reviews the frozen six focus chains. Run with the production .venv.
"""

from __future__ import annotations

import argparse
import asyncio
from contextvars import ContextVar
from datetime import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
from time import perf_counter
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from evals.ragas_gate0 import (
    AI_ROOT, GOLD_V4, GOLD_SHA, PROJECTION_SHA, REPO_ROOT, STEP2_SHA,
    Gate0Guard, configure_offline, sha256,
)


OUTPUT = REPO_ROOT / "workspace/moodflow-review/h1-query-bundle-gate1-20261018"
GATE0 = REPO_ROOT / "workspace/moodflow-review/h1-query-expansion-gate0-20261018"
CASE_IDS = tuple("rag-v0-" + value for value in (
    "001", "002", "003", "004", "005", "007", "008", "009", "023", "024", "026", "031", "034",
    "036", "037", "039", "040", "045", "049", "050", "051", "052", "053", "054", "055", "056",
    "057", "058", "059", "060"))
FOCUS_IDS = tuple("rag-v0-" + value for value in ("001", "004", "036", "037", "039", "040"))
EXPANSION_PROMPT = """你为音乐知识检索生成查询。仅根据用户问题，输出 3 个非空、互不重复的中文检索子查询。
综合问题拆为互补但相关的检索侧面；具体事实问题生成保持同一意图的检索表述。
每个子查询必须能独立理解，并保留原问题显式给出的对象、年份范围、排除要求及其他限制。
没有明确年份时不要猜测年份；不要添加原问题未给出的具体作品、人名、事件或答案事实。
不要回答问题，不要解释，不要构造假设答案，不要要求或使用检索结果、历史对话、目录或 Gold。
只返回 JSON 对象，唯一顶层键为 queries，其值为恰好 3 个字符串的数组。"""
HYDE_PROMPT = """你是一名音乐知识助手。根据下面的问题，写一段与之相关的音乐知识文本。
描述相关的音乐背景、风格、时期等通用知识；不要编造具体的作品名、年份、
人名、奖项等事实细节；不要直接回答问题；只输出这段文本，不要解释。"""
MODEL = "deepseek-chat"
BASE_URL = "https://api.deepseek.com/v1"
ENV_READ = ContextVar("h1_authorized_env_read", default=False)
YEAR = re.compile(r"(?<!\d)(?:1[89]\d{2}|20\d{2}|21\d{2})(?!\d)")
FORBIDDEN_CATALOG_CALLS = []


class H1Error(ValueError):
    pass


class ExpansionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    queries: list[str] = Field(min_length=3, max_length=3)


def now():
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                    encoding="utf-8", newline="\n")


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def text_hash(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def literal(value):
    return " ".join(value.split())[:1000]


def protected_hashes():
    from evals import rag_source_baseline as baseline
    result = baseline.protected_hashes()
    for directory in (baseline.OUTPUT, GATE0, baseline.RELEASE_ROOT / "config_snapshot/entity_registry"):
        for path in sorted(directory.iterdir()):
            if path.is_file():
                result[path.relative_to(REPO_ROOT).as_posix()] = sha256(path)
    return result


def code_hashes():
    return {name: sha256(AI_ROOT / name) for name in (
        "evals/rag_h1_bundle_v5.py", "evals/rag_h1_bundle_adapter.py")}


def verify_prompt():
    report = (GATE0 / "report.md").read_text(encoding="utf-8")
    approved = re.findall(r"```text\n(.*?)\n```", report, re.S)
    if not approved or approved[0] != EXPANSION_PROMPT:
        raise H1Error("h1_gate0_expansion_prompt_changed")


class NoCatalog:
    async def get_song(self, *_args, **_kwargs):
        FORBIDDEN_CATALOG_CALLS.append("get_song")
        raise H1Error("h1_external_catalog_disabled")


def registry_components(binding):
    from evals import rag_source_baseline as baseline
    from app.rag.query_understanding.context import QueryContextResolver
    from app.rag.query_understanding.entity_resolver import QueryEntityResolver
    from app.rag.query_understanding.registry_binding import load_bound_registry
    registry = load_bound_registry(binding.profile, baseline.RELEASE_ROOT)
    return QueryContextResolver(song_provider=NoCatalog(), registry=registry), QueryEntityResolver(registry)


async def resolve_inputs(cases, binding):
    from app.rag.query_understanding.constraints import extract_query_constraints
    context_resolver, entity_resolver = registry_components(binding)
    result = []
    for case in cases:
        query = literal(case["question"])
        context = await context_resolver.resolve(query, messages=[], routing_context=None)
        if context.status.value != "resolved" or not context.standalone_query:
            raise H1Error("h1_context_needs_input:" + case["case_id"])
        completed = literal(context.standalone_query)
        entities = entity_resolver.resolve(completed, context_entities=context.entities, catalog_references=[])
        constraints, warnings = extract_query_constraints(completed, entities)
        result.append({"case_id": case["case_id"], "question": case["question"],
                       "answerability_for_reporting_only": case["answerability"],
                       "normalized_before": query, "completed_query": completed,
                       "completion_triggered": context.dependency.value != "standalone",
                       "completion_changed": query != completed,
                       "context": context.model_dump(mode="json"),
                       "entities": [e.model_dump(mode="json") for e in entities],
                       "entity_effect": "recorded_only_no_query_rewrite_or_metadata_filter",
                       "constraints": constraints.model_dump(mode="json"),
                       "constraint_warnings": warnings})
    if FORBIDDEN_CATALOG_CALLS:
        raise H1Error("h1_catalog_was_attempted")
    return result


def prepare():
    from evals import rag_source_baseline as baseline
    verify_prompt()
    cases, _, config, rerank, binding = baseline.preflight()
    before = protected_hashes()
    if (sha256(baseline.GOLD) != GOLD_SHA or sha256(baseline.PROJECTION) != PROJECTION_SHA
            or sha256(baseline.OUTPUT / "cases.jsonl") != STEP2_SHA
            or sha256(GOLD_V4) != "1ca311c731b83c0fd0f3ab95dcf31d06a95565e54e631e174e09dd890b81ad4e"):
        raise H1Error("h1_frozen_input_drift")
    if baseline.protected_hashes() != read_json(baseline.OUTPUT / "run_manifest.json")["protected_after"]:
        raise H1Error("h1_baseline_production_drift")
    scored = [row for row in cases if row["answerability"] in {"full", "partial"}]
    if tuple(row["case_id"] for row in scored) != CASE_IDS:
        raise H1Error("h1_fixed_population_drift")
    rows = asyncio.run(resolve_inputs(scored, binding))
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    write_json(OUTPUT / "inputs.json", rows)
    write_json(OUTPUT / "prompts.json", {
        "expansion": EXPANSION_PROMPT, "hyde": HYDE_PROMPT,
        "sha256": {"expansion": text_hash(EXPANSION_PROMPT), "hyde": text_hash(HYDE_PROMPT)},
        "model": MODEL, "base_url": BASE_URL, "temperature": 0, "max_retries": 0,
        "max_tokens": 4096, "expansion_response_format": {"type": "json_object"},
    })
    after = protected_hashes()
    if before != after:
        raise H1Error("h1_prepare_changed_protected_files")
    write_json(OUTPUT / "preparation.json", {
        "prepared_at": now(), "status": "prepared_zero_api",
        "scope": "H1 revised: context + registry entity audit + 3 LLM subqueries + HyDE dense",
        "case_ids": list(CASE_IDS), "focus_ids": list(FOCUS_IDS),
        "inputs_sha256": sha256(OUTPUT / "inputs.json"), "prompts_sha256": sha256(OUTPUT / "prompts.json"),
        "code_sha256": code_hashes(), "protected_before": before, "protected_after": after,
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "leg_guard_exception": "Evaluation scope only: original builder builds 6; append approved HyDE dense #7. Production maximum_legs remains 6.",
        "hypothesis": {"timeline_recall_at_40_gte": 0.6, "artist_overview_recall_at_40_gt": 1 / 3},
        "context_triggered": sum(r["completion_triggered"] for r in rows),
        "context_changed": sum(r["completion_changed"] for r in rows),
        "external_catalog_calls": 0, "api_calls": 0, "qdrant_calls": 0,
        "retrieval_approval": "Required after frozen six-focus review; not granted by prepare or generate commands",
    })
    print(json.dumps({"status": "prepared_zero_api", "cases": len(rows),
                      "context_changed": sum(r["completion_changed"] for r in rows)}, ensure_ascii=False))


def load_inputs():
    verify_prompt()
    prep = read_json(OUTPUT / "preparation.json")
    if (sha256(OUTPUT / "inputs.json") != prep["inputs_sha256"]
            or sha256(OUTPUT / "prompts.json") != prep["prompts_sha256"]
            or protected_hashes() != prep["protected_after"]
            or code_hashes() != prep["code_sha256"]):
        raise H1Error("h1_fixed_input_code_or_protected_hash_drift")
    rows = read_json(OUTPUT / "inputs.json")
    if tuple(r["case_id"] for r in rows) != CASE_IDS:
        raise H1Error("h1_population_changed")
    return rows


def validate_text_constraints(text, row, *, hyde=False, entity_resolver=None):
    original = row["completed_query"]
    original_years = set(YEAR.findall(original))
    years = set(YEAR.findall(text))
    if years - original_years or (not hyde and original_years != years):
        raise H1Error("h1_year_constraint_drift")
    titles = set(re.findall(r"《([^》]+)》", text))
    original_titles = set(re.findall(r"《([^》]+)》", original))
    if titles - original_titles or (not hyde and not original_titles <= titles):
        raise H1Error("h1_title_constraint_drift")
    if not hyde:
        for entity in row["entities"]:
            if entity["status"] != "resolved":
                continue
            names = [entity["surface"], entity["canonical_name"], *entity["aliases"]]
            if not any(name and name.casefold() in text.casefold() for name in names):
                raise H1Error("h1_entity_constraint_drift")
        for term in row["constraints"]["negative_terms"]:
            if not re.search(r"(?:不要|排除|不包括|不含)\s*" + re.escape(term), text):
                raise H1Error("h1_negative_constraint_drift")
        for cue in ("中文资料", "中文資料", "繁体资料", "繁體資料", "英文资料", "英文資料",
                    "采访资料", "訪談資料", "新闻资料", "新聞資料", "乐评资料", "樂評資料"):
            if cue in original and cue not in text:
                raise H1Error("h1_document_constraint_drift")
    if entity_resolver is not None:
        original_ids = {e["knowledge_entity_id"] for e in row["entities"] if e["knowledge_entity_id"]}
        new_entities = entity_resolver.resolve(text, catalog_references=[])
        added = {e.knowledge_entity_id for e in new_entities if e.knowledge_entity_id
                 and e.entity_type.value in {"artist", "song", "album", "person"}} - original_ids
        if added:
            raise H1Error("h1_added_registry_entity")
    return {"years": sorted(years), "quoted_titles": sorted(titles),
            "checks": "passed", "semantic_equivalence": "requires_West_review_not_proved_by_rules"}


def validate_expansion(raw, row, entity_resolver=None):
    try:
        parsed = ExpansionOutput.model_validate_json(raw)
    except ValidationError as exc:
        raise H1Error("h1_expansion_json_schema_invalid") from exc
    queries = [literal(text) for text in parsed.queries]
    if any(not q for q in queries) or len({q.casefold() for q in queries}) != 3:
        raise H1Error("h1_expansion_blank_or_duplicate")
    checks = [validate_text_constraints(q, row, entity_resolver=entity_resolver) for q in queries]
    return queries, checks


def validate_hyde(raw, row, entity_resolver=None):
    text = " ".join(raw.split())
    if not text or len(text) > 1000 or "```" in text:
        raise H1Error("h1_hyde_blank_long_or_fenced")
    return text, validate_text_constraints(text, row, hyde=True, entity_resolver=entity_resolver)


def request_body(row, stage):
    if row["case_id"] not in CASE_IDS or stage not in {"expansion", "hyde"}:
        raise H1Error("h1_unapproved_call_scope")
    body = {"model": MODEL, "temperature": 0, "max_tokens": 4096,
            "messages": [{"role": "system", "content": EXPANSION_PROMPT if stage == "expansion" else HYDE_PROMPT},
                         {"role": "user", "content": json.dumps({"question": row["completed_query"]}, ensure_ascii=False)
                          if stage == "expansion" else row["completed_query"]}]}
    if stage == "expansion":
        body["response_format"] = {"type": "json_object"}
    return body


def secret_guard(event, args):
    if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
        path = Path(os.fsdecode(args[0]))
        if any(p.lower() in {"secret", "secrets", ".env"} or p.lower().startswith(".env.") for p in path.parts):
            if path.resolve() != (AI_ROOT / ".env").resolve() or not ENV_READ.get():
                raise H1Error("h1_secret_read_outside_single_run_authorization")


def approved_settings(allow_env_read):
    from dotenv import dotenv_values
    fields = {"LLM_API_KEY", "LLM_BASE_URL", "LLM_TIMEOUT_SECONDS"}
    selected = {key: os.environ[key] for key in fields if key in os.environ}
    if not selected.get("LLM_API_KEY"):
        if not allow_env_read:
            raise H1Error("h1_env_read_requires_West_authorization")
        token = ENV_READ.set(True)
        try:
            with (AI_ROOT / ".env").open(encoding="utf-8-sig") as stream:
                for line in stream:
                    match = re.match(r"^\s*(?:export\s+)?([A-Z0-9_]+)\s*=", line)
                    if match and match.group(1) in fields and match.group(1) not in selected:
                        selected.update(dotenv_values(stream=io.StringIO(line), interpolate=False))
        finally:
            ENV_READ.reset(token)
    if not selected.get("LLM_API_KEY"):
        raise H1Error("h1_approved_key_unavailable")
    # Endpoint/model are fixed by the protocol, never redirected by environment.
    return selected["LLM_API_KEY"], float(selected.get("LLM_TIMEOUT_SECONDS", "120"))


class Recorder:
    def __init__(self, directory):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=False)
        self.entries = []
        self.scope = None

    async def request(self, request):
        if (self.scope is None or request.method != "POST" or request.url.scheme != "https"
                or request.url.host != "api.deepseek.com" or request.url.path != "/v1/chat/completions"):
            raise H1Error("h1_unapproved_http_request")
        row, stage = self.scope
        body = json.loads(request.content)
        if body != request_body(row, stage) or type(body["temperature"]) is not int or body["temperature"] != 0:
            raise H1Error("h1_outbound_body_or_temperature_drift")
        if any(e["case_id"] == row["case_id"] and e["stage"] == stage for e in self.entries):
            raise H1Error("h1_second_http_attempt_forbidden")
        entry = {"call_number": len(self.entries) + 1, "case_id": row["case_id"], "stage": stage,
                 "request": body, "request_sha256": hashlib.sha256(request.content).hexdigest(),
                 "started_at": now()}
        self.entries.append(entry)
        request.extensions["h1_record_index"] = len(self.entries) - 1
        request.extensions["h1_started"] = perf_counter()
        self.save(entry)

    def save(self, entry):
        write_json(self.directory / f"{entry['call_number']:03}.json", entry)

    async def response(self, response):
        await response.aread()
        entry = self.entries[response.request.extensions["h1_record_index"]]
        entry.update(status_code=response.status_code, finished_at=now(),
                     elapsed_ms=(perf_counter() - response.request.extensions["h1_started"]) * 1000)
        if response.status_code == 200:
            body = response.json()
            entry.update(response=body, usage=body.get("usage"))
        else:
            entry["error"] = "provider_error_body_not_exported"
        self.save(entry)


def usage_summary(calls):
    def aggregate(items):
        values = [r["usage"] for r in items if r.get("usage")]
        return {"http_calls": len(items), "responses_with_usage": len(values),
                **{key: sum(v.get(key, 0) for v in values) for key in (
                    "prompt_tokens", "completion_tokens", "total_tokens",
                    "prompt_cache_hit_tokens", "prompt_cache_miss_tokens")}}
    return {"all": aggregate(calls), "by_stage": {
        stage: aggregate([r for r in calls if r["stage"] == stage]) for stage in ("expansion", "hyde")},
        "money": "Provider usage does not report charged currency; no fabricated cost estimate."}


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    from evals import rag_source_baseline as baseline
    rows = load_inputs()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise H1Error("h1_no_automatic_resume_or_repeat_calls")
    key, timeout = approved_settings(allow_env_read)
    _, _, _, _, binding = baseline.preflight()
    _, entity_resolver = registry_components(binding)
    recorder = Recorder(OUTPUT / "calls")
    (OUTPUT / "query_understanding").mkdir(exist_ok=False)
    state = {"status": "generating_before_retrieval", "started_at": now(),
             "secret_authorization": "West explicitly approved H1 fixed 30-case in-memory key/connection reads in chat.",
             "max_retries": 0, "qdrant_calls": 0, "judge_calls": 0, "completed_cases": 0}
    write_json(OUTPUT / "generation_state.json", state)
    results = []
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                    event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                for row in rows:
                    item = {**row, "status": "started", "attempts": {"expansion": 0, "hyde": 0}}
                    path = OUTPUT / "query_understanding" / (row["case_id"] + ".json")
                    write_json(path, item)
                    try:
                        for stage in ("expansion", "hyde"):
                            recorder.scope = (row, stage)
                            item["attempts"][stage] += 1
                            response = await client.chat.completions.create(**request_body(row, stage))
                            if response.choices[0].finish_reason != "stop":
                                raise H1Error("h1_response_not_finished")
                            raw = response.choices[0].message.content
                            if not isinstance(raw, str):
                                raise H1Error("h1_missing_response_text")
                            item[stage + "_raw"] = raw
                            item[stage + "_call_number"] = len(recorder.entries)
                            write_json(path, item)
                            if stage == "expansion":
                                item["queries"], item["expansion_checks"] = validate_expansion(raw, row, entity_resolver)
                            else:
                                item["hyde"], item["hyde_checks"] = validate_hyde(raw, row, entity_resolver)
                            write_json(path, item)
                        if item["hyde"].casefold() in {q.casefold() for q in item["queries"]}:
                            raise H1Error("h1_hyde_duplicates_subquery")
                        item["status"] = "validated_pending_West_semantic_review"
                        write_json(path, item)
                        results.append(item)
                        state["completed_cases"] = len(results)
                        write_json(OUTPUT / "generation_state.json", state)
                        print(json.dumps({"progress": f"{len(results)}/30", "case_id": row["case_id"],
                                          "http_calls": len(recorder.entries)}, ensure_ascii=False), flush=True)
                    except Exception as exc:
                        item.update(status="failed_paused_no_retry", error_type=type(exc).__name__,
                                    error_code=str(exc) if isinstance(exc, H1Error) else "provider_or_runtime_error_no_secret_text")
                        write_json(path, item)
                        raise
        if len(results) != 30 or len(recorder.entries) != 60:
            raise H1Error("h1_generation_population_incomplete")
        write_json(OUTPUT / "frozen_query_understanding.json", results)
        state["status"] = "frozen_pending_West_six_focus_review"
        write_json(OUTPUT / "freeze_manifest.json", {
            "frozen_at": now(), "case_ids": list(CASE_IDS), "case_count": 30,
            "frozen_queries_sha256": sha256(OUTPUT / "frozen_query_understanding.json"),
            "inputs_sha256": sha256(OUTPUT / "inputs.json"), "prompts_sha256": sha256(OUTPUT / "prompts.json"),
            "code_sha256": code_hashes(),
            "per_case_sha256": {r["case_id"]: sha256(OUTPUT / "query_understanding" / (r["case_id"] + ".json")) for r in results},
            "call_sha256": {p.name: sha256(p) for p in sorted((OUTPUT / "calls").glob("*.json"))},
            "retrieval_allowed": False, "reason": "Await West confirmation of six full chains; no retrieval has run.",
        })
    except Exception as exc:
        state.update(status="failed_paused_no_retry", error_type=type(exc).__name__,
                     error_code=str(exc) if isinstance(exc, H1Error) else "provider_or_runtime_error_no_secret_text")
    finally:
        state.update(finished_at=now(), completed_cases=len(results), usage=usage_summary(recorder.entries),
                     protected_unchanged=protected_hashes() == read_json(OUTPUT / "preparation.json")["protected_after"])
        if not state["protected_unchanged"]:
            state["status"] = "failed_protected_hash_drift"
        write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps({k: state[k] for k in ("status", "completed_cases", "usage")}, ensure_ascii=False), flush=True)
    return 0 if state["status"] == "frozen_pending_West_six_focus_review" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "generate"))
    parser.add_argument("--authorized-env-read", action="store_true")
    args = parser.parse_args()
    configure_offline()
    if args.command == "prepare":
        guard = Gate0Guard()
        sys.addaudithook(guard.audit)
        prepare()
        if guard.denied:
            raise H1Error("h1_prepare_forbidden_operation_attempted")
        return 0
    sys.addaudithook(secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
