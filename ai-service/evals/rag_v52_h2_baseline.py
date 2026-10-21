"""V5.2 baseline: frozen H2 retrieval, 27 reused bundles and seven additions.

This evaluation-only adapter never changes production defaults or index data.
Gold answers/anchors are deliberately excluded at the input boundary.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import sys
from time import perf_counter
from unittest.mock import patch

from evals import rag_h1_bundle_v5 as h1
from evals import rag_h1_disposition_checks_v5 as checks
from evals import rag_h1_union_v5 as union
from evals import rag_h2_metadata_v5 as h2
from evals import rag_source_baseline as baseline
from evals import rag_v51_h2_baseline as v51
from evals.ragas_gate0 import Gate0Guard


ROOT = h2.ROOT
AI_ROOT = ROOT / "ai-service"
OUTPUT = ROOT / "workspace/moodflow-review/v5-2-h2-baseline-20261019/run01"
SEED_ATTEMPT = None
EXPLICIT_RESTRICTIONS = False
OLD = v51.OUTPUT / "attempt-03-source-reminder"
RELEASE = AI_ROOT / "data/reruns/jay-chou-release-freeze-20261019/run01"
MANIFEST = RELEASE / "index/index_build_manifest.json"
PROJECTION = RELEASE / "index/index_projection.jsonl"
COLLECTION = "music_knowledge_recovery_20261017_ce001e1a586e2c20_v1_v2_v4"
RELEASE_ID = "knowledge-v2-ce001e1a586e2c20"
GOLD = AI_ROOT / "evals/datasets/rag_eval_gold_v5_2.frozen.jsonl"
GOLD_SHAS = {
    "rag_eval_gold_v5.frozen.jsonl": "b708332729177a095cb0993381bc9b3ffaef7808846e61bbdb8cbd0877afbfad",
    "rag_eval_gold_v5_1.frozen.jsonl": "57ece040c085c5c2a21f3e380c0550781f756fafb73c14d1702d05352a3a0649",
    "rag_eval_gold_v5_2.frozen.jsonl": "3f008114b7171831b5ea219f68b3fd6c7a506a943275a2f5e48e7f6c428432d4",
}
PROJECTION_SHA = "c876e1a72723cc1ecec906aa6ee78ffbc9a83be3162915188abf0241ff5f504a"
OLD_QUERY_SHA = "9aa4d228973b522fbdf5e5a723f3828fe2471c4a26523edf14faa7d7e45eb743"
NEW_IDS = tuple(
    "rag-v0-" + n for n in ("006", "061", "062", "063", "064", "065", "066")
)
TIMELINE_IDS = tuple("rag-v0-" + n for n in ("002", "036", "037", "039", "040"))
CHANGED_OLD_IDS = {"rag-v0-002", "rag-v0-007", "rag-v0-023"}
SOURCE_CUES = {"rag-v0-063": "环球音乐日本", "rag-v0-064": "马来亚大学"}
sha256, read_json, read_jsonl, write_json = (
    h2.sha256,
    h2.read_json,
    h2.read_jsonl,
    h2.write_json,
)


class BaselineError(v51.BaselineError):
    pass


def gold_rows(path):
    """Allowlist at deserialization; answers, anchors and facts cannot propagate."""
    return [
        {
            key: row[key]
            for key in ("case_id", "question", "answerability", "relevant_parent_ids")
        }
        for row in read_jsonl(path)
    ]


def protected_hashes():
    result = baseline.protected_hashes()
    paths = [GOLD.parent / name for name in GOLD_SHAS]
    paths += [
        MANIFEST,
        PROJECTION,
        RELEASE / "data/knowledge/v2/knowledge_release_manifest.json",
    ]
    paths += list((AI_ROOT / "evals").glob("rag_h[12]*.py"))
    paths += list((AI_ROOT / "evals").glob("rag_v5[12]*.py"))
    paths += list(
        (AI_ROOT / "app/rag/knowledge/entity_linking/registry").glob("*.yaml")
    )
    paths += [AI_ROOT / "tests/test_rag_v52_h2_baseline.py"]
    paths += [p for p in OLD.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    result.update(
        {p.relative_to(ROOT).as_posix(): sha256(p) for p in sorted(set(paths))}
    )
    return result


def load_inputs():
    from app.rag.retrieval.binding import load_retrieval_index_binding

    for name, digest in GOLD_SHAS.items():
        if sha256(GOLD.parent / name) != digest:
            raise BaselineError("protected_gold_sha256_mismatch:" + name)
    all_cases = gold_rows(GOLD)
    if (
        len(all_cases) != 50
        or len({c["case_id"] for c in all_cases}) != 50
        or Counter(c["answerability"] for c in all_cases)
        != {"full": 24, "partial": 10, "insufficient": 9, "needs_input": 7}
    ):
        raise BaselineError("v52_population_drift")
    cases = [c for c in all_cases if c["answerability"] in {"full", "partial"}]
    config, rerank = baseline.load_retrieval_config(), baseline.load_rerank_config()
    frozen = read_json(OLD / "freeze_manifest.json")
    if (
        config.model_dump(mode="json") != frozen["retrieval_config"]
        or rerank.model_dump(mode="json") != frozen["rerank_config"]
    ):
        raise BaselineError("h2_configuration_drift")
    for name, digest in {**frozen["artifact_sha256"], **frozen["call_sha256"]}.items():
        if sha256(OLD / name) != digest:
            raise BaselineError("old_frozen_artifact_drift:" + name)
    for name, digest in read_json(h2.H1 / "pre_call_manifest.json")[
        "code_sha256"
    ].items():
        if sha256(AI_ROOT / name) != digest:
            raise BaselineError("frozen_h2_code_drift:" + name)
    if (
        sha256(OLD / "frozen_queries.json") != OLD_QUERY_SHA
        or sha256(PROJECTION) != PROJECTION_SHA
    ):
        raise BaselineError("frozen_queries_or_projection_drift")
    binding = load_retrieval_index_binding(AI_ROOT, MANIFEST, config)
    if (
        binding.manifest.collection_name != COLLECTION
        or binding.manifest.source_release_id != RELEASE_ID
        or binding.manifest.child_count != 1270
        or binding.manifest.parent_count != 689
    ):
        raise BaselineError("new_collection_binding_drift")
    projection = baseline.Projection(read_jsonl(PROJECTION))
    if len(projection.children) != 1270 or len(projection.parents) != 689:
        raise BaselineError("new_projection_population_drift")
    old_queries, old_results = (
        read_json(OLD / "frozen_queries.json"),
        read_jsonl(OLD / "retrieval/cases.jsonl"),
    )
    old_by_id = {r["case_id"]: r for r in old_queries}
    if (
        len(cases) != 34
        or len(old_by_id) != 27
        or len(old_results) != 27
        or {c["case_id"] for c in cases} - set(old_by_id) != set(NEW_IDS)
        or set(old_by_id) - {c["case_id"] for c in cases}
        or read_json(OLD / "retrieval/run_manifest.json")["status"] != "complete"
        or not all(r["healthy"] for r in old_results)
    ):
        raise BaselineError("fixed_27_plus_7_population_drift")
    previous = {
        c["case_id"]: c
        for c in gold_rows(GOLD.parent / "rag_eval_gold_v5_1.frozen.jsonl")
    }
    for row in old_results:
        if any(
            row[k] != previous[row["case_id"]][k]
            for k in ("question", "answerability", "relevant_parent_ids")
        ):
            raise BaselineError("historical_baseline_scoring_differs_from_frozen2")
    for case in cases:
        if (
            not case["relevant_parent_ids"]
            or len(set(case["relevant_parent_ids"])) != len(case["relevant_parent_ids"])
            or not set(case["relevant_parent_ids"]) <= projection.parents
        ):
            raise BaselineError(
                "scored_parents_missing_or_duplicate:" + case["case_id"]
            )
        if (
            case["case_id"] in old_by_id
            and case["question"] != old_by_id[case["case_id"]]["question"]
        ):
            raise BaselineError("reused_question_drift")
    return cases, old_queries, old_results, projection, config, rerank, binding


def registry_components(binding):
    from app.rag.query_understanding.context import QueryContextResolver
    from app.rag.query_understanding.entity_resolver import QueryEntityResolver
    from app.rag.query_understanding.registry_binding import load_bound_registry

    registry = load_bound_registry(binding.profile, AI_ROOT)
    return QueryContextResolver(
        song_provider=h1.NoCatalog(), registry=registry
    ), QueryEntityResolver(registry)


def load_seeds(rows, resolver):
    if SEED_ATTEMPT is None:
        return {}, {}, 0
    if not SEED_ATTEMPT.is_relative_to(
        ROOT / "workspace/moodflow-review/v5-2-h2-baseline-20261019"
    ):
        raise BaselineError("seed_attempt_outside_this_baseline")
    prep = read_json(SEED_ATTEMPT / "preparation.json")
    if prep["gold_sha256"] != GOLD_SHAS or prep["old_queries_sha256"] != OLD_QUERY_SHA:
        raise BaselineError("seed_gold_or_query_drift")
    for name, digest in prep["protected_before"].items():
        if name in {
            "ai-service/evals/rag_v52_h2_baseline.py",
            "ai-service/tests/test_rag_v52_h2_baseline.py",
        }:
            continue  # A versioned operator update; preserve its original digest.
        if sha256(ROOT / name) != digest:
            raise BaselineError("seed_protected_input_drift:" + name)
    seeds, hashes = {}, dict(prep.get("seed_artifact_sha256", {}))
    paths = [
        SEED_ATTEMPT / "preparation.json",
        SEED_ATTEMPT / "inputs.json",
        SEED_ATTEMPT / "expansion/state.json",
    ]
    calls = sorted((SEED_ATTEMPT / "expansion/calls").glob("*.json"))
    paths += calls
    for row in rows:
        if row["case_id"] not in NEW_IDS:
            continue
        path = SEED_ATTEMPT / "expansion" / (row["case_id"] + ".json")
        if not path.exists():
            continue
        paths.append(path)
        saved = read_json(path)
        if (
            saved["question"] != row["question"]
            or saved["completed_query"] != row["completed_query"]
        ):
            raise BaselineError("seed_question_drift")
        for stage in ("expansion", "hyde"):
            if stage + "_raw" not in saved:
                continue
            raw = saved[stage + "_raw"]
            value, validation = validate_stage(raw, row, stage, resolver)
            accepted = next(
                a
                for a in saved["attempt_history"]
                if a["stage"] == stage and a["accepted"]
            )
            accepted_path = ROOT / accepted.get(
                "seed_record_path", path.relative_to(ROOT).as_posix()
            )
            if not accepted_path.is_relative_to(
                ROOT / "workspace/moodflow-review/v5-2-h2-baseline-20261019"
            ):
                raise BaselineError("seed_provider_path_outside_this_baseline")
            call = read_json(
                accepted_path.parent / "calls" / f"{accepted['call_number']:03}.json"
            )
            if (
                call["case_id"] != row["case_id"]
                or call["stage"] != stage
                or call["status_code"] != 200
                or call["response"]["choices"][0]["message"]["content"] != raw
            ):
                raise BaselineError("seed_provider_response_drift")
            seeds.setdefault(row["case_id"], {})[stage] = {
                "raw": raw,
                "value": value,
                "validation": validation,
                "record_path": path.relative_to(ROOT).as_posix(),
                "accepted_attempt": accepted,
            }
    hashes.update({p.relative_to(ROOT).as_posix(): sha256(p) for p in paths})
    return (
        seeds,
        hashes,
        len(calls) + prep["generation_protocol"].get("prior_call_count", 0),
    )


def prepare():
    cases, old_queries, _, _, config, rerank, binding = load_inputs()
    if OUTPUT.exists():
        raise BaselineError("output_exists_no_overwrite")
    before = protected_hashes()
    new_cases = [
        {k: c[k] for k in ("case_id", "question", "answerability")}
        for c in cases
        if c["case_id"] in NEW_IDS
    ]
    with patch.object(h1, "registry_components", registry_components):
        resolved = asyncio.run(h1.resolve_inputs(new_cases, binding))
    by_id = {r["case_id"]: r for r in [*old_queries, *resolved]}
    rows = [deepcopy(by_id[c["case_id"]]) for c in cases]
    if any(r["completion_changed"] or r["completion_triggered"] for r in rows):
        raise BaselineError("unexpected_context_completion")
    seeds, seed_hashes, prior_call_count = load_seeds(
        rows, registry_components(binding)[1]
    )
    if prior_call_count >= 42:
        raise BaselineError("total_generation_budget_exhausted")
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / ".gitattributes").write_text(
        "* text eol=lf\n", encoding="utf-8", newline="\n"
    )
    write_json(OUTPUT / "inputs.json", rows)
    write_json(OUTPUT / "generation_seeds.json", seeds)
    write_json(
        OUTPUT / "prompts.json",
        {"expansion": h1.EXPANSION_PROMPT, "hyde": h1.HYDE_PROMPT},
    )
    write_json(
        OUTPUT / "preparation.json",
        {
            "prepared_at": h1.now(),
            "case_ids": [c["case_id"] for c in cases],
            "gold_sha256": GOLD_SHAS,
            "collection": COLLECTION,
            "release_id": RELEASE_ID,
            "protected_before": before,
            "old_queries_sha256": OLD_QUERY_SHA,
            "seed_artifact_sha256": seed_hashes,
            "seed_attempt": SEED_ATTEMPT.relative_to(ROOT).as_posix()
            if SEED_ATTEMPT
            else None,
            "reused_case_ids": [
                c["case_id"] for c in cases if c["case_id"] not in NEW_IDS
            ],
            "new_generation_case_ids": list(NEW_IDS),
            "artifact_sha256": {
                n: sha256(OUTPUT / n)
                for n in ("inputs.json", "generation_seeds.json", "prompts.json")
            },
            "retrieval_config": config.model_dump(mode="json"),
            "rerank_config": rerank.model_dump(mode="json"),
            "generation_protocol": {
                "model": h1.MODEL,
                "temperature": 0,
                "SDK_retries": 0,
                "max_attempts_per_stage": 3,
                "max_new_calls": 42 - prior_call_count,
                "max_calls_including_prior_attempts": 42,
                "prior_call_count": prior_call_count,
                "explicit_restrictions": EXPLICIT_RESTRICTIONS,
                "seeded_stages": {k: list(v) for k, v in seeds.items()},
                "constraint_clarification": v51.CONSTRAINT_REMINDER,
                "source_cues": SOURCE_CUES,
                "retry_policy": "Same request; constraint failures only. Provider failures stop. No score-based selection.",
                "authorization": "West requested H1-style DeepSeek expansion/HyDE for these seven additions.",
                "gold_answers_or_evidence_in_request": False,
            },
            "qdrant_calls": 0,
            "new_llm_calls": 0,
        },
    )
    if before != protected_hashes():
        raise BaselineError("protected_input_drift_during_prepare")
    print(
        json.dumps({"prepared": True, "cases": 34, "reused": 27, "new": list(NEW_IDS)}),
        flush=True,
    )


def load_prepared():
    inputs = load_inputs()
    prep = read_json(OUTPUT / "preparation.json")
    if prep["protected_before"] != protected_hashes() or any(
        sha256(OUTPUT / name) != digest
        for name, digest in prep["artifact_sha256"].items()
    ):
        raise BaselineError("prepared_input_or_code_drift")
    if any(
        sha256(ROOT / name) != digest
        for name, digest in prep.get("seed_artifact_sha256", {}).items()
    ):
        raise BaselineError("seed_artifact_drift")
    if (
        prep["generation_protocol"].get("explicit_restrictions", False)
        != EXPLICIT_RESTRICTIONS
    ):
        raise BaselineError("generation_protocol_drift")
    return inputs


def generation_body(row, stage):
    if row["case_id"] not in NEW_IDS:
        raise BaselineError("generation_scope_forbidden")
    with patch.object(h1, "CASE_IDS", NEW_IDS):
        body = v51.generation_body(row, stage, clarify=True)
    cue = SOURCE_CUES.get(row["case_id"])
    if stage == "expansion" and cue:
        content = json.loads(body["messages"][1]["content"])
        content["constraint_reminder"] += (
            f"原题具名来源是“{cue}”；每条子查询都必须逐字保留该来源。"
        )
        body["messages"][1]["content"] = json.dumps(content, ensure_ascii=False)
    if stage == "expansion" and EXPLICIT_RESTRICTIONS:
        content = json.loads(body["messages"][1]["content"])
        years = sorted(set(h1.YEAR.findall(row["completed_query"])))
        if years:
            content["constraint_reminder"] += (
                "每一条子查询都必须逐字包含原题的全部年份："
                + "、".join(years)
                + "；不能只在部分子查询保留。"
            )
        content["constraint_reminder"] += (
            "若原题含多个问题，三条子查询的集合必须保留全部问法，不得只保留其中一问；不添加任何具体答案。"
        )
        body["messages"][1]["content"] = json.dumps(content, ensure_ascii=False)
    return body


def validate_stage(raw, row, stage, resolver):
    with patch.object(union, "ALL_IDS", frozenset((*h1.CASE_IDS, *NEW_IDS))):
        # Common bundles retain exactly the checker used for frozen H1/H2.
        # V5.1's extra source-per-query check applied only to its new cases.
        if row["case_id"] not in NEW_IDS:
            return (
                union.validate_expansion(raw, row, resolver)
                if stage == "expansion"
                else checks.validate_hyde(raw, row, resolver)
            )
        value, validation = v51.validate_stage(raw, row, stage, resolver)
    cue = SOURCE_CUES.get(row["case_id"])
    if stage == "expansion" and cue and any(cue not in text for text in value):
        raise BaselineError("named_source_restriction_lost")
    return value, validation


class Recorder(h1.Recorder):
    def __init__(self, directory):
        super().__init__(directory)
        self.attempt = None
        self.max_calls = 42

    async def request(self, request):
        if len(self.entries) >= self.max_calls:
            raise h2.SafetyStop("total_generation_budget_exhausted")
        if (
            self.scope is None
            or self.scope[0]["case_id"] not in NEW_IDS
            or self.attempt not in range(1, 4)
            or request.method != "POST"
            or request.url.scheme != "https"
            or request.url.host != "api.deepseek.com"
            or request.url.port not in {None, 443}
            or request.url.path != "/v1/chat/completions"
            or request.url.username
            or request.url.password
        ):
            raise h2.SafetyStop("generation_http_scope_forbidden")
        row, stage = self.scope
        body = json.loads(request.content)
        if body != generation_body(row, stage) or type(body["temperature"]) is not int:
            raise h2.SafetyStop("generation_request_body_drift")
        if any(
            e["case_id"] == row["case_id"]
            and e["stage"] == stage
            and e["attempt"] == self.attempt
            for e in self.entries
        ):
            raise h2.SafetyStop("duplicate_physical_http_attempt")
        entry = {
            "call_number": len(self.entries) + 1,
            "case_id": row["case_id"],
            "stage": stage,
            "attempt": self.attempt,
            "request": body,
            "request_sha256": hashlib.sha256(request.content).hexdigest(),
            "started_at": h1.now(),
        }
        self.entries.append(entry)
        request.extensions["h1_record_index"] = len(self.entries) - 1
        request.extensions["h1_started"] = perf_counter()
        self.save(entry)


async def generate():
    import httpx
    from openai import AsyncOpenAI

    *_, binding = load_prepared()
    directory = OUTPUT / "expansion"
    if directory.exists():
        raise BaselineError("generation_already_attempted_no_overwrite")
    directory.mkdir()
    rows = [r for r in read_json(OUTPUT / "inputs.json") if r["case_id"] in NEW_IDS]
    _, resolver = registry_components(binding)
    # Existing connection loader passes credentials directly into the SDK;
    # neither headers nor connection values are exported by the recorder.
    key, timeout = h1.approved_settings(True)
    recorder = Recorder(directory / "calls")
    recorder.max_calls = read_json(OUTPUT / "preparation.json")["generation_protocol"][
        "max_new_calls"
    ]
    seeds = read_json(OUTPUT / "generation_seeds.json")
    state = {
        "started_at": h1.now(),
        "status": "generating",
        "completed_case_ids": [],
        "SDK_retries": 0,
    }
    write_json(directory / "state.json", state)
    results, error = [], None
    try:
        async with httpx.AsyncClient(
            trust_env=False,
            follow_redirects=False,
            timeout=timeout,
            event_hooks={
                "request": [recorder.request],
                "response": [recorder.response],
            },
        ) as http:
            async with AsyncOpenAI(
                api_key=key,
                base_url=h1.BASE_URL,
                max_retries=0,
                timeout=timeout,
                http_client=http,
            ) as client:
                for row in rows:
                    item = {
                        **row,
                        "attempt_history": [],
                        "provenance": {"kind": "new_DeepSeek"},
                    }
                    for stage in ("expansion", "hyde"):
                        seed = seeds.get(row["case_id"], {}).get(stage)
                        if seed:
                            value, validation = validate_stage(
                                seed["raw"], row, stage, resolver
                            )
                            item[stage + "_raw"] = seed["raw"]
                            item["queries" if stage == "expansion" else "hyde"] = value
                            item[stage + "_checks"] = validation
                            item["attempt_history"].append(
                                {
                                    **seed["accepted_attempt"],
                                    "seed_record_path": seed["record_path"],
                                }
                            )
                            continue
                        for attempt in range(1, 4):
                            recorder.scope, recorder.attempt = (row, stage), attempt
                            response = await client.chat.completions.create(
                                **generation_body(row, stage)
                            )
                            if (
                                not response.choices
                                or response.choices[0].finish_reason != "stop"
                                or not isinstance(
                                    response.choices[0].message.content, str
                                )
                            ):
                                raise BaselineError("generation_response_incomplete")
                            raw = response.choices[0].message.content
                            entry = {
                                "stage": stage,
                                "attempt": attempt,
                                "call_number": len(recorder.entries),
                                "raw": raw,
                            }
                            try:
                                value, validation = validate_stage(
                                    raw, row, stage, resolver
                                )
                                item[stage + "_raw"] = raw
                                item["queries" if stage == "expansion" else "hyde"] = (
                                    value
                                )
                                item[stage + "_checks"] = validation
                                entry["accepted"] = True
                            except (h1.H1Error, v51.BaselineError) as exc:
                                entry.update(accepted=False, error_code=str(exc))
                            item["attempt_history"].append(entry)
                            write_json(directory / (row["case_id"] + ".json"), item)
                            print(
                                json.dumps(
                                    {
                                        "case_id": row["case_id"],
                                        **{
                                            k: v for k, v in entry.items() if k != "raw"
                                        },
                                    }
                                ),
                                flush=True,
                            )
                            if entry["accepted"]:
                                break
                        else:
                            raise BaselineError(
                                "generation_validation_attempts_exhausted:"
                                + row["case_id"]
                                + ":"
                                + stage
                            )
                    if item["hyde"].casefold() in {
                        q.casefold() for q in item["queries"]
                    }:
                        raise BaselineError("hyde_duplicates_subquery")
                    write_json(directory / (row["case_id"] + ".json"), item)
                    results.append(item)
                    state["completed_case_ids"].append(row["case_id"])
                    write_json(directory / "state.json", state)
        write_json(directory / "new_queries.json", results)
    except BaseException as exc:
        error = (
            str(exc)
            if isinstance(exc, (v51.BaselineError, h1.H1Error, h2.SafetyStop))
            else type(exc).__name__
        )
    finally:
        unchanged = (
            protected_hashes()
            == read_json(OUTPUT / "preparation.json")["protected_before"]
        )
        complete = len(results) == 7 and error is None and unchanged
        state.update(
            status="complete_pending_read_review" if complete else "incomplete",
            error_code=error,
            finished_at=h1.now(),
            usage=h1.usage_summary(recorder.entries),
            protected_unchanged=unchanged,
        )
        write_json(directory / "state.json", state)
    print(
        json.dumps(
            {
                "status": state["status"],
                "completed": len(results),
                "error_code": error,
                "usage": state["usage"],
            }
        ),
        flush=True,
    )
    return 0 if complete else 1


def freeze():
    from evals.rag_h1_bundle_adapter import bundle_plan

    cases, old_queries, _, projection, _, _, binding = load_prepared()
    if (OUTPUT / "freeze_manifest.json").exists():
        raise BaselineError("queries_already_frozen")
    review = read_json(OUTPUT / "expansion/read_review.json")
    if (
        review["reviewer"] != "Codex"
        or not review["passed"]
        or review["reviewed_case_ids"] != list(NEW_IDS)
        or review["new_queries_sha256"] != sha256(OUTPUT / "expansion/new_queries.json")
        or read_json(OUTPUT / "expansion/state.json")["status"]
        != "complete_pending_read_review"
    ):
        raise BaselineError("expansion_read_review_missing_or_stale")
    new_queries = read_json(OUTPUT / "expansion/new_queries.json")
    if len(new_queries) != 7 or [r["case_id"] for r in new_queries] != list(NEW_IDS):
        raise BaselineError("new_query_population_drift")
    by_id = {r["case_id"]: r for r in [*old_queries, *new_queries]}
    old_by_id = {r["case_id"]: r for r in old_queries}
    old_plans = dict(
        zip(
            [r["case_id"] for r in old_queries],
            read_json(OLD / "plans.json"),
            strict=True,
        )
    )
    old_filters = {r["case_id"]: r for r in read_json(OLD / "filters.json")}
    _, resolver = registry_components(binding)
    queries, plans, filters = [], [], []
    for case in cases:
        row = deepcopy(by_id[case["case_id"]])
        for stage in ("expansion", "hyde"):
            value, _ = validate_stage(row[stage + "_raw"], row, stage, resolver)
            if value != row["queries" if stage == "expansion" else "hyde"]:
                raise BaselineError("query_text_drift")
        row["answerability_for_reporting_only"] = case["answerability"]
        constraints = row["constraints"]
        year = (
            [constraints["start_year"], constraints["end_year"]]
            if constraints["start_year"] is not None
            else None
        )
        gate0 = {"entity_ids": constraints["entity_ids"], "year_range": year}
        base = bundle_plan(
            row["completed_query"], row["queries"], row["hyde"], binding.profile
        ).model_dump(mode="json")
        plan, compiled = h2.build_plan(base, gate0)
        actual_filter = h2.dumped_filter(compiled)
        condition = {
            "case_id": row["case_id"],
            **gate0,
            "effective_qdrant_filter": actual_filter,
            "intermediate_ast_requires_three_tier_compiler": year is not None,
            "eligible_children": sum(
                h2.matches(p, actual_filter) for p in projection.children.values()
            ),
            "year_tiers": dict(
                Counter(h2.tier(p, year) for p in projection.children.values())
            ),
        }
        if row["case_id"] in old_by_id:
            old = old_by_id[row["case_id"]]
            if any(
                row[k] != old[k]
                for k in (
                    "question",
                    "completed_query",
                    "context",
                    "entities",
                    "constraints",
                    "queries",
                    "hyde",
                    "expansion_raw",
                    "hyde_raw",
                )
            ):
                raise BaselineError("old_expansion_or_understanding_changed")
            original = old_plans[row["case_id"]]
            if {
                k: v
                for k, v in plan.model_dump(mode="json").items()
                if k not in {"plan_id", "index_profile"}
            } != {
                k: v
                for k, v in original.items()
                if k not in {"plan_id", "index_profile"}
            } or actual_filter != old_filters[row["case_id"]][
                "effective_qdrant_filter"
            ]:
                raise BaselineError("old_plan_or_filter_changed_beyond_index_binding")
            row["v52_provenance"] = {
                "kind": "reused_frozen_H1_H2",
                "path": (OLD / "frozen_queries.json").relative_to(ROOT).as_posix(),
                "sha256": OLD_QUERY_SHA,
            }
        queries.append(row)
        plans.append(plan.model_dump(mode="json"))
        filters.append(condition)
    for name, value in (
        ("frozen_queries.json", queries),
        ("plans.json", plans),
        ("filters.json", filters),
    ):
        write_json(OUTPUT / name, value)
    write_json(
        OUTPUT / "freeze_manifest.json",
        {
            "frozen_at": h1.now(),
            "cases": 34,
            "reused": 27,
            "new": list(NEW_IDS),
            "old_expansions_exact": True,
            "old_plans_only_index_binding_changed": True,
            "artifact_sha256": {
                n: sha256(OUTPUT / n)
                for n in (
                    "frozen_queries.json",
                    "plans.json",
                    "filters.json",
                    "expansion/read_review.json",
                    "expansion/new_queries.json",
                )
            },
            "call_sha256": {
                p.relative_to(OUTPUT).as_posix(): sha256(p)
                for p in sorted((OUTPUT / "expansion/calls").glob("*.json"))
            },
            "retrieval_authorized": "West requested a 34-case V5.2 H2 baseline on the frozen 48-document collection.",
        },
    )
    print(
        json.dumps({"frozen": True, "cases": 34, "old_expansions_exact": 27}),
        flush=True,
    )


def load_frozen():
    inputs = load_prepared()
    manifest = read_json(OUTPUT / "freeze_manifest.json")
    for name, digest in {
        **manifest["artifact_sha256"],
        **manifest["call_sha256"],
    }.items():
        if sha256(OUTPUT / name) != digest:
            raise BaselineError("frozen_query_drift:" + name)
    return inputs


def verify_inventory(points, projection):
    expected = {
        point: projection.children[child] for point, child in projection.points.items()
    }
    actual = {str(p["id"]): p["payload"] for p in points}
    if len(points) != 1270 or len(actual) != 1270 or actual != expected:
        raise BaselineError("live_payload_projection_drift")
    return hashlib.sha256(
        json.dumps(actual, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def inventory(projection):
    import httpx

    points, offset, seen = [], None, set()
    with httpx.Client(
        base_url="http://127.0.0.1:6333", trust_env=False, timeout=30
    ) as client:
        path = f"/collections/{COLLECTION}"
        response = client.get(path)
        response.raise_for_status()
        info = response.json()["result"]
        while True:
            body = {"limit": 128, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = client.post(path + "/points/scroll", json=body)
            response.raise_for_status()
            page = response.json()["result"]
            points.extend(page["points"])
            offset = page["next_page_offset"]
            if offset is None:
                break
            if str(offset) in seen:
                raise BaselineError("inventory_pagination_cycle")
            seen.add(str(offset))
    digest = verify_inventory(points, projection)
    if info["points_count"] != 1270:
        raise BaselineError("collection_count_drift")
    return {
        "collection_info": info,
        "payload_sha256": digest,
        "children": 1270,
        "parents": 689,
        "full_payload_projection_match": True,
        "vectors_read": False,
    }


@contextmanager
def target_scope():
    # Scope only this process's existing H2 HTTP audit to the requested collection.
    with patch.object(h2, "COLLECTION", COLLECTION):
        yield


def run(guard):
    cases, _, _, projection, config, _, binding = load_frozen()
    queries, plans, filters = [
        read_json(OUTPUT / n)
        for n in ("frozen_queries.json", "plans.json", "filters.json")
    ]
    directory = OUTPUT / "retrieval"
    if directory.exists():
        raise BaselineError("retrieval_already_attempted_no_repeat")
    directory.mkdir()
    state = {
        "started_at": h1.now(),
        "status": "loading_pinned_local_models",
        "completed_case_count": 0,
        "collection": COLLECTION,
        "gold_sha256_before": {n: sha256(GOLD.parent / n) for n in GOLD_SHAS},
        "qdrant_write_calls": 0,
        "new_llm_calls_during_retrieval": 0,
        "H3_legs": False,
    }
    write_json(directory / "run_manifest.json", state)
    audit, results, error = h2.RequestAudit(), [], None
    try:
        encoder, _, scorer, _ = baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch

        state["runtime"] = {
            "torch": torch.__version__,
            "embedding_device": encoder._encoder._device,
            "reranker_device": scorer._device,
        }
        with target_scope(), audit.installed():
            before = inventory(projection)
            write_json(directory / "collection_before.json", before)
            with (directory / "cases.jsonl").open(
                "x", encoding="utf-8", newline="\n"
            ) as stream:
                for i, (case, query, plan, condition) in enumerate(
                    zip(cases, queries, plans, filters, strict=True), 1
                ):
                    if not case["case_id"] == query["case_id"] == condition["case_id"]:
                        raise BaselineError("retrieval_case_order_drift")
                    audit.active, audit.expected_filter, audit.expected_lexical = (
                        case["case_id"],
                        condition["effective_qdrant_filter"],
                        query["queries"],
                    )
                    offset = len(audit.requests)
                    row = h2.collect_case(
                        {**case, "control_legs": v51.expected_legs(query)},
                        query,
                        plan,
                        condition,
                        projection,
                        config,
                        binding,
                        audit,
                    )
                    row["qdrant_requests"] = audit.requests[offset:]
                    stream.write(
                        json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
                    )
                    stream.flush()
                    results.append(row)
                    state.update(
                        status="running",
                        completed_case_count=len(results),
                        last_case_id=row["case_id"],
                    )
                    write_json(directory / "run_manifest.json", state)
                    print(
                        json.dumps(
                            {
                                "progress": f"{i}/34",
                                "case_id": row["case_id"],
                                "healthy": row["healthy"],
                                "metrics": row["metrics"],
                            }
                        ),
                        flush=True,
                    )
                    if not row["healthy"]:
                        raise BaselineError("unhealthy_case_stop")
            audit.active = None
            after = inventory(projection)
            write_json(directory / "collection_after.json", after)
            if before["payload_sha256"] != after["payload_sha256"]:
                raise BaselineError("collection_payload_changed")
    except BaseException as exc:
        error = (
            str(exc)
            if isinstance(exc, (v51.BaselineError, h2.H2Error, h2.SafetyStop))
            else type(exc).__name__
        )
    finally:
        unchanged = (
            protected_hashes()
            == read_json(OUTPUT / "preparation.json")["protected_before"]
        )
        complete = (
            len(results) == 34
            and error is None
            and unchanged
            and not guard.denied
            and not audit.safety_events
        )
        state.update(
            status="complete" if complete else "incomplete",
            finished_at=h1.now(),
            error_code=error,
            completed_case_count=len(results),
            protected_unchanged=unchanged,
            guard_denied=guard.denied,
            safety_events=audit.safety_events,
            gold_sha256_after={n: sha256(GOLD.parent / n) for n in GOLD_SHAS},
            qdrant_request_counts=dict(
                Counter(f"{r['method']} {r['path']}" for r in audit.requests)
            ),
        )
        write_json(directory / "run_manifest.json", state)
        write_json(directory / "qdrant_requests.json", audit.requests)
    print(
        json.dumps(
            {"status": state["status"], "completed": len(results), "error_code": error}
        ),
        flush=True,
    )
    return 0 if complete else 1


def mean(rows):
    return {
        "case_count": len(rows),
        "case_ids": [r["case_id"] for r in rows],
        **{
            k: math.fsum(r["metrics"][k] for r in rows) / len(rows) if rows else None
            for k in baseline.METRICS
        },
    }


def independently_score(case, record):
    relevant = set(case["relevant_parent_ids"])
    ids40 = {r["parent_id"] for r in record["rrf_top40"][:40]}
    ids12 = [r["parent_id"] for r in record["rerank_top12"][:12]]
    first = next((i for i, parent in enumerate(ids12, 1) if parent in relevant), None)
    return {
        "recall_at_40": len(relevant & ids40) / len(relevant),
        "recall_at_12": len(relevant & set(ids12)) / len(relevant),
        "precision_at_12": len(relevant & set(ids12)) / 12,
        "mrr": 1 / first if first else 0,
        "first_hit_child_rank": first,
        "unique_rerank_parent_count": len(set(ids12)),
    }


def report():
    cases, _, old_results, projection, _, _, _ = load_frozen()
    state = read_json(OUTPUT / "retrieval/run_manifest.json")
    records = read_jsonl(OUTPUT / "retrieval/cases.jsonl")
    if (
        state["status"] != "complete"
        or len(records) != 34
        or not all(r["healthy"] for r in records)
    ):
        raise BaselineError("incomplete_baseline_cannot_score_as_34")
    before, after = [
        read_json(OUTPUT / ("retrieval/collection_" + n + ".json"))
        for n in ("before", "after")
    ]
    if before["payload_sha256"] != after["payload_sha256"]:
        raise BaselineError("collection_drift_at_report")
    by_id = {c["case_id"]: c for c in cases}
    if [r["case_id"] for r in records] != list(by_id):
        raise BaselineError("report_population_order_drift")
    for row in records:
        if row["metrics"] != independently_score(by_id[row["case_id"]], row):
            raise BaselineError("independent_metric_replay_mismatch")
        if any(
            h["child_id"] not in projection.children
            for h in row["rrf_top40"] + row["rerank_top12"]
        ):
            raise BaselineError("returned_child_outside_release")
    common = [r for r in records if r["case_id"] not in NEW_IDS]
    rebased = [
        {**r, "metrics": independently_score(by_id[r["case_id"]], r)}
        for r in old_results
    ]
    groups = {
        "all34": records,
        "full": [r for r in records if r["answerability"] == "full"],
        "partial": [r for r in records if r["answerability"] == "partial"],
        "timeline5": [r for r in records if r["case_id"] in TIMELINE_IDS],
        "common27": common,
        "new7_first_baseline": [r for r in records if r["case_id"] in NEW_IDS],
        "unchanged_gold24": [r for r in common if r["case_id"] not in CHANGED_OLD_IDS],
    }
    summary = {name: mean(rows) for name, rows in groups.items()}
    summary["comparison"] = {
        "historical_v51_old_gold_old_collection": mean(old_results),
        "historical_rankings_rebased_to_v52_common27": mean(rebased),
        "new_collection_v52_common27": mean(common),
        "historical_unchanged_gold24": mean(
            [r for r in old_results if r["case_id"] not in CHANGED_OLD_IDS]
        ),
        "note": "34 vs 27 is not a controlled gain. Common27 also changes Gold for 002/007/023; rebasing isolates annotation arithmetic. Unchanged24 keeps queries and Gold fixed, but corpus/linking changed together.",
    }
    write_json(OUTPUT / "metrics.json", summary)
    write_json(
        OUTPUT / "verification.json",
        {
            "gold_sha256_after": {n: sha256(GOLD.parent / n) for n in GOLD_SHAS},
            "protected_unchanged": True,
            "case_count": 34,
            "independent_scores_exact": True,
            "collection_payload_unchanged": True,
            "only_target_collection_accessed": True,
            "no_qdrant_writes": True,
            "fallback_events": state["safety_events"],
            "soft_cap_refill_case_ids": [
                r["case_id"]
                for r in records
                if "selection_caps_relaxed_for_recall" in r["selection_warnings"]
            ],
        },
    )
    lines = [
        "# V5.2 检索 baseline（H2 冻结管线）",
        "",
        "34/34题完成；full24 + partial10。",
        "",
        f"Gold SHA256：`{GOLD_SHAS[GOLD.name]}`。Collection：`{COLLECTION}`。",
        "",
        "27题原始expansion/HyDE复用；新7题先通过规则与阅读自检，再冻结，之后才检索。",
        "输入边界只保留case_id、题干、标签和计分Parent；生成请求只包含问题及原有限定的提醒，rerank仍用原题。Gold答案、facts、anchors未进入输入。",
        "",
        "7 leg=3×(dense+BM25)+HyDE dense；三档filter、RRF k60、candidate40、overfetch2、每路80、doc cap4/source cap5、rerank12；模型与revision、超时、权重全等于H2。",
        "H2原有selection软上限溢出回填保持原样并记录；EXPLORATORY实体过滤回补仍拦截，未放行额外检索。",
        "R@40先截Child前40再按Parent计命中；R@12/P@12取rerank前12、Parent去重，P分母固定12；MRR为前12首个命中倒数，按题宏平均。selection可取RRF前80，所以R@12可高于R@40。",
        "",
        "| 分组 | n | R@40 | R@12 | P@12 | MRR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, value in summary.items():
        if name != "comparison":
            lines.append(
                "| "
                + name
                + " | "
                + str(value["case_count"])
                + " | "
                + " | ".join(f"{value[k]:.9f}" for k in baseline.METRICS)
                + " |"
            )
    for title, rows in (
        ("新7题（首次baseline，只记录）", groups["new7_first_baseline"]),
        ("Timeline指定5题", groups["timeline5"]),
        ("全部34题", records),
    ):
        lines += [
            "",
            "## " + title,
            "",
            "| case | 标签 | Gold Parents | R@40 | R@12 | P@12 | MRR |",
            "|---|---|---:|---:|---:|---:|---:|",
        ]
        for row in rows:
            lines.append(
                "| "
                + row["case_id"]
                + " | "
                + row["answerability"]
                + " | "
                + str(len(row["relevant_parent_ids"]))
                + " | "
                + " | ".join(f"{row['metrics'][k]:.9f}" for k in baseline.METRICS)
                + " |"
            )
    lines += [
        "",
        "## 与V5.1比较",
        "",
        "历史27题R@40=0.835978836；当前34题不能直接视作其提升/退步，样本、语料和三题Gold均改变。历史baseline绑定的V5.1文件SHA为792e9bb7…；其27题题干、标签和Parent与当前57ece040… frozen-2逐项相同，已核验。",
        "",
        "| 对照口径 | n | R@40 | R@12 | P@12 | MRR |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for name, value in summary["comparison"].items():
        if name != "note":
            lines.append(
                "| "
                + name
                + " | "
                + str(value["case_count"])
                + " | "
                + " | ".join(f"{value[k]:.9f}" for k in baseline.METRICS)
                + " |"
            )
    lines += [
        "",
        "共同27题用同一组问题/expansion；旧排名按V5.2 Parent重算的行用于区分标注口径变化。unchanged24还固定Gold Parent，但只能说明新语料+新链接的合并影响，不能单独归因于linker。",
        "",
        "本次不生成答案、不作judge，不改变参数；新7题无历史成绩，不评判升降。三份Gold的跑前后SHA一致；目标集合前后1270点及完整payload一致，其他集合未访问。",
        "",
    ]
    (OUTPUT / "report.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {"complete": True, "metrics": summary["all34"], "output": str(OUTPUT)}
        ),
        flush=True,
    )


def main():
    global OUTPUT, SEED_ATTEMPT, EXPLICIT_RESTRICTIONS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=("prepare", "generate", "freeze", "run", "report")
    )
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--seed-attempt", type=Path)
    parser.add_argument("--explicit-restrictions", action="store_true")
    args = parser.parse_args()
    OUTPUT = args.output.resolve()
    SEED_ATTEMPT = args.seed_attempt.resolve() if args.seed_attempt else None
    EXPLICIT_RESTRICTIONS = args.explicit_restrictions
    h2.configure()
    if args.command == "generate":
        sys.addaudithook(h1.secret_guard)
        return asyncio.run(generate())
    guard = h2.ReadOnlyGuard() if args.command == "run" else Gate0Guard()
    sys.addaudithook(guard.audit)
    if args.command == "run":
        import torch

        print(
            json.dumps(
                {
                    "cuda_bootstrap": torch.cuda.is_available(),
                    "torch": torch.__version__,
                }
            ),
            flush=True,
        )
        return run(guard)
    {"prepare": prepare, "freeze": freeze, "report": report}[args.command]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
