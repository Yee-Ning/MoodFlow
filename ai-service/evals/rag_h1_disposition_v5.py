"""West's eight-case H1 dispositions; no retrieval or automatic extra retries."""

from __future__ import annotations

import argparse
import asyncio
from copy import deepcopy
import json
import platform
import sys

from evals import rag_h1_batch_v5 as batch
from evals import rag_h1_bundle_v5 as original
from evals import rag_h1_disposition_checks_v5 as checks
from evals.ragas_gate0 import Gate0Guard, configure_offline


OUTPUT = batch.OUTPUT / "batch-disposition-20261018"
OFFLINE_IDS = tuple("rag-v0-" + n for n in ("031", "034", "058"))
RETRY_STAGES = {"rag-v0-009": "hyde", "rag-v0-045": "expansion", "rag-v0-049": "expansion"}
RUN_IDS = tuple("rag-v0-" + n for n in ("009", "031", "034", "045", "049", "058"))
MAX_NEW_CALLS = 8


def code_hashes():
    return {**batch.code_hashes(), **{name: original.sha256(original.AI_ROOT / name) for name in (
        "evals/rag_h1_disposition_checks_v5.py", "evals/rag_h1_disposition_v5.py")}}


def history():
    rows, _, old_calls = batch.load_prepared()
    completion = original.read_json(batch.OUTPUT / "completion.json")
    if (completion["code_sha256"] != batch.code_hashes()
            or any(original.sha256(batch.OUTPUT / name) != digest
                   for name, digest in completion["artifact_hashes"].items())):
        raise original.H1Error("h1_disposition_history_drift")
    results = original.read_json(batch.OUTPUT / "batch_results.json")
    calls = [*old_calls, *[original.read_json(p) for p in sorted((batch.OUTPUT / "calls").glob("*.json"))]]
    if (tuple(r["case_id"] for r in results) != batch.CASE_IDS or len(calls) != 53
            or sum(r["batch_status"].startswith("passed_") for r in results) != 21):
        raise original.H1Error("h1_disposition_history_scope_drift")
    return rows, {r["case_id"]: r for r in results}, calls


def resolver():
    from evals import rag_source_baseline as baseline
    _, _, _, _, binding = baseline.preflight()
    return original.registry_components(binding)[1]


def scopes():
    return [(case_id, stage) for case_id in RUN_IDS
            for stage in (("expansion", "hyde") if RETRY_STAGES.get(case_id) == "expansion" else ("hyde",))]


def diagnose054(row, entity_resolver):
    from app.rag.query_understanding.entity_resolver import infer_entity_type
    queries = original.ExpansionOutput.model_validate_json(row["expansion_raw"]).queries

    def probe(text):
        hint = infer_entity_type(text, "稻香")
        result = entity_resolver.registry.resolve("稻香", type_hint=hint)
        entities = entity_resolver.resolve(text, catalog_references=[])
        try:
            checks.validate_text_constraints(text, row, entity_resolver=entity_resolver)
            verdict = "passed"
        except original.H1Error as exc:
            verdict = str(exc)
        return {"text": text, "type_hint_for_rice_field": hint.value if hint else None,
                "registry_reason": result.reason, "registry_confidence": result.confidence,
                "registry_entity_id": result.entity.entity_id if result.entity else None,
                "registry_candidate_ids": list(result.candidate_entity_ids),
                "entities": [e.model_dump(mode="json") for e in entities],
                "contains_sony": "索尼" in text, "checker_verdict": verdict}

    return {"case_id": row["case_id"], "question": row["completed_query"],
            "original_raw_response": row["expansion_raw"], "original_constraints": row["constraints"],
            "registry_version": entity_resolver.registry.version,
            "registry_candidates": [e.model_dump(mode="json") for e in entity_resolver.registry.candidates("稻香")],
            "actual_subquery_probes": [probe(q) for q in queries],
            "counterfactual_diagnostic_probes_only_never_sent": [probe(queries[1].replace("专辑 ", "")),
                                                                  probe(queries[1].replace("稻香", "歌曲《稻香》"))],
            "cause": "Immediately preceding 专辑 supplies an ALBUM hint for 稻香; the sole registry candidate is a SONG. Type mismatch is exposed as AMBIGUOUS by QueryEntityResolver, despite a single candidate.",
            "source_loss": "Second query omits 索尼. Current document-cue whitelist does not cover named sources; existing rule pass would not prove source preservation.",
            "fix_authorized": False, "disposition": "pending_West",
            "recommendation": "Exclude 054 for this experiment, or separately authorize source-check/type-cue correction and disposition. Do not silently accept the saved response."}


def write_diagnosis_report(diag):
    probes = diag["actual_subquery_probes"]
    lines = ["# H1 054 只读诊断（零 API）", "",
             "结论：registry 没有同名多候选。这里是局部类型提示误附着，再由 resolver 将类型冲突统一展示为 ambiguous；同时模型第二条子查询确实丢了‘索尼’。本轮未修 054。", "",
             f"原问题：{diag['question']}", "", "原始响应（逐字保留）：", "```json", diag["original_raw_response"], "```", "",
             "| 子查询 | 稻香 type hint | registry 原因 / 实体 | 保留索尼 | 原检查器结果 |",
             "|---|---|---|---|---|"]
    for i, p in enumerate(probes, 1):
        lines.append(f"| {i} | {p['type_hint_for_rice_field']} | {p['registry_reason']} / {p['registry_entity_id']} | {p['contains_sony']} | {p['checker_verdict']} |")
    lines += ["", "第二条：`《魔杰座》专辑 稻香 歌曲介绍 曲风 主题`。`infer_entity_type` 只看提及之前最多 12 字、紧贴提及的类型词。‘专辑 ’匹配，稻香后面的‘歌曲介绍’不参与提示。",
              "registry 中稻香只有 `song:rice_field`（song）；施加 album 提示后得到 `registry_candidate_type_mismatch`、单候选、confidence=0。QueryEntityResolver 对所有有候选但无结果的情况都输出 AMBIGUOUS，未区分类型冲突。",
              "", "离线反事实探针：只去掉相邻的‘专辑 ’，或把稻香明确写成‘歌曲《稻香》’，都能恢复 song 解析。这些变体仅用于诊断，没有替换保存查询、没有发给模型或检索。",
              "", "代码位置：", "",
              "- `app/rag/query_understanding/entity_resolver.py:26`：infer_entity_type 前缀局部提示；同文件 :155 将有候选的失败统一映射为 AMBIGUOUS。",
              "- `app/rag/knowledge/entity_linking/registry.py:97`：按 type_hint 过滤，:109 返回 registry_candidate_type_mismatch。",
              "- `evals/rag_h1_title_format_v5.py:32`：标题文字虽然保留，但要求仍解析到同一作品 ID，故第二条被拦截。",
              "- `evals/rag_h1_title_format_v5.py:66`：来源约束只检查语言/资料类别词，未检查专名‘索尼’。", "",
              "来源丢失是模型输出违反原问题限制；来源检查覆盖不足与类型误附着是另两项问题。即使消歧检查修正，不能据此放行缺失‘索尼’的原响应。", "",
              "建议：本轮排除 054 并保留证据。若决定保留，另行拍板检查器/消歧处理及是否允许重试；当前不自行修改、重试或排除。", "",
              "完整探针、真实 entities/置信度与原始 constraints 见 `054_diagnosis.json`。"]
    (OUTPUT / "054_diagnosis.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def prepare():
    rows, records, calls = history()
    entity_resolver = resolver()
    offline = []
    for row in records.values():
        if row["case_id"] in OFFLINE_IDS or row["batch_status"].startswith("passed_"):
            queries, validation = checks.validate_expansion(row["expansion_raw"], row, entity_resolver)
            if row["case_id"] not in OFFLINE_IDS:
                if queries != row["queries"] or checks.validate_hyde(row["hyde_raw"], row, entity_resolver)[0] != row["hyde"]:
                    raise original.H1Error("h1_disposition_old_output_drift")
            offline.append({"case_id": row["case_id"], "expansion_raw_sha256": original.text_hash(row["expansion_raw"]),
                            "queries": queries, "checks": validation, "api_calls": 0,
                            "old_hyde_reused": row["case_id"] not in OFFLINE_IDS})
    OUTPUT.mkdir(parents=True, exist_ok=False)
    (OUTPUT / "inputs.json").write_bytes((batch.OUTPUT / "inputs.json").read_bytes())
    original.write_json(OUTPUT / "offline_revalidation.json", {"real_api_calls": 0, "records": offline,
                                                               "old_21_unchanged": True, "corrected_cases": list(OFFLINE_IDS)})
    diagnosis = diagnose054(records["rag-v0-054"], entity_resolver)
    original.write_json(OUTPUT / "054_diagnosis.json", diagnosis)
    write_diagnosis_report(diagnosis)
    original.write_json(OUTPUT / "pre_call_manifest.json", {
        "created_at": original.now(), "case_ids": list(batch.CASE_IDS),
        "explicit_exclusions": ["rag-v0-005", "rag-v0-008"], "pending_diagnosis_case": "rag-v0-054",
        "allowed_scopes": scopes(), "max_new_http_calls": MAX_NEW_CALLS, "max_retries": 0, "temperature": 0,
        "retry_stages": RETRY_STAGES, "first_hyde_after_offline_pass": list(OFFLINE_IDS),
        "same_code_retry_failure": "exclude_only_the_3_approved_retry_stages",
        "different_code_or_first_hyde_failure": "pending_West_no_retry_no_auto_exclusion",
        "secret_authorization": "West separately approved this invocation's in-memory .env key/connection reads for at most 8 HTTP, including five conditional first HyDE calls.",
        "focus_six_semantic_review": "West latest instruction confirms no constraint drift; final retrieval approval still required.",
        "code_sha256": code_hashes(), "prior_completion_sha256": original.sha256(batch.OUTPUT / "completion.json"),
        "protected_sha256": original.protected_hashes(),
        "artifact_sha256": {n: original.sha256(OUTPUT / n) for n in (
            "inputs.json", "offline_revalidation.json", "054_diagnosis.json", "054_diagnosis.md")},
        "title_conversion": {"engine": "Windows LCMapStringEx", "locale": "zh-CN", "flag": "LCMAP_SIMPLIFIED_CHINESE=0x02000000",
                             "runtime": platform.platform(), "probe": {s: checks.title_key(s) for s in ("流浪詩人", "流浪诗人", "東風破")}},
        "retrieval_allowed": False, "normalization_affects_query_text": False,
    })
    print(json.dumps({"prepared_zero_api": True, "offline_revalidated": len(offline),
                      "pending_054": True, "max_http_calls": 8}), flush=True)


def load_prepared():
    rows, records, calls = history()
    prep = original.read_json(OUTPUT / "pre_call_manifest.json")
    if (prep["code_sha256"] != code_hashes() or prep["allowed_scopes"] != [list(s) for s in scopes()]
            or prep["protected_sha256"] != original.protected_hashes()
            or prep["prior_completion_sha256"] != original.sha256(batch.OUTPUT / "completion.json")
            or original.read_json(OUTPUT / "inputs.json") != rows
            or any(original.sha256(OUTPUT / n) != digest for n, digest in prep["artifact_sha256"].items())):
        raise original.H1Error("h1_disposition_pre_call_drift")
    return rows, records, calls


class Recorder(original.Recorder):
    async def request(self, request):
        if (self.scope is None or (self.scope[0]["case_id"], self.scope[1]) not in scopes()
                or len(self.entries) >= MAX_NEW_CALLS):
            raise original.H1Error("h1_disposition_unapproved_scope_or_budget")
        await super().request(request)
        row, stage = self.scope
        self.entries[-1].update(global_call_number=53 + len(self.entries),
                                attempt=2 if RETRY_STAGES.get(row["case_id"]) == stage else 1)
        self.save(self.entries[-1])


def initial_outcomes(records, entity_resolver):
    outcomes = []
    for case_id, row in records.items():
        item = {"case_id": case_id, "record": deepcopy(row), "prior_error_code": row.get("error_code")}
        if case_id == "rag-v0-008":
            item.update(disposition="excluded", reason="h1_added_registry_entity systematic interception, explicitly excluded by West; not score based")
        elif case_id == "rag-v0-054":
            item.update(disposition="pending_West", reason="054 diagnosis only; type mismatch and loss of 索尼, no fix/exclusion authorized")
        elif case_id in RUN_IDS:
            item["disposition"] = "pending_execution"
            if case_id in OFFLINE_IDS:
                item["record"]["queries"], item["record"]["expansion_checks"] = checks.validate_expansion(row["expansion_raw"], row, entity_resolver)
        else:
            item.update(disposition="passed", reason="saved expansion/HyDE revalidated unchanged, zero API")
        outcomes.append(item)
    return outcomes


async def process_cases(records, entity_resolver, call_stage):
    outcomes = initial_outcomes(records, entity_resolver)
    by_id = {o["case_id"]: o for o in outcomes}
    (OUTPUT / "query_understanding").mkdir(exist_ok=False)
    streak = {"code": None, "case_ids": []}
    for case_id in RUN_IDS:
        outcome = by_id[case_id]
        row, record = records[case_id], outcome["record"]
        stages = [stage for scoped_id, stage in scopes() if scoped_id == case_id]
        path = OUTPUT / "query_understanding" / (case_id + ".json")
        for stage in stages:
            outcome["active_stage"] = stage
            outcome["disposition"] = "running"
            record.setdefault("attempts", {}).setdefault(stage, 0)
            record["attempts"][stage] += 1
            original.write_json(path, outcome)
            try:
                raw, number = await call_stage(row, stage)
                record[stage + "_raw"], record[stage + "_call_number"] = raw, number
                original.write_json(path, outcome)
                if stage == "expansion":
                    record["queries"], record["expansion_checks"] = checks.validate_expansion(raw, row, entity_resolver)
                else:
                    record["hyde"], record["hyde_checks"] = checks.validate_hyde(raw, row, entity_resolver)
                    if record["hyde"].casefold() in {q.casefold() for q in record["queries"]}:
                        raise original.H1Error("h1_hyde_duplicates_subquery")
            except Exception as exc:
                code = batch.error_code(exc)
                outcome.update(disposition="excluded" if RETRY_STAGES.get(case_id) == stage
                               and code == outcome["prior_error_code"] else "pending_West",
                               error_code=code, error_type=type(exc).__name__,
                               reason="same-code approved single retry failed; no further retry" if RETRY_STAGES.get(case_id) == stage
                               and code == outcome["prior_error_code"] else "different retry code or first HyDE failure; requires West disposition")
                break
        else:
            outcome.update(disposition="passed", reason="approved offline revalidation/retry plus HyDE passed")
            # Historical failure remains available in prior batch and wrapper.
            record.pop("error_code", None)
            record.pop("error_type", None)
            record.update(status="validated_pending_final_population_approval",
                          batch_status="passed_rules_pending_final_population_approval")
        original.write_json(path, outcome)
        original.write_json(OUTPUT / "dispositions.json", outcomes)
        code = outcome.get("error_code")
        streak = {"code": code, "case_ids": [*streak["case_ids"], case_id] if code and streak["code"] == code
                  else [case_id] if code else []}
        print(json.dumps({"case_id": case_id, "disposition": outcome["disposition"],
                          "stage": outcome["active_stage"], "code": code}, ensure_ascii=False), flush=True)
        if len(streak["case_ids"]) >= 3:
            original.write_json(OUTPUT / "safety_valve.json", streak)
            break
    return outcomes


def snapshot(outcomes, old_calls, new_calls):
    from evals import rag_source_baseline as baseline
    accepted = [o["record"] for o in outcomes if o["disposition"] == "passed"]
    ids = [r["case_id"] for r in accepted]
    if not set(original.FOCUS_IDS) <= set(ids):
        raise original.H1Error("h1_disposition_focus_population_drift")
    pending = [o["case_id"] for o in outcomes if o["disposition"] not in {"passed", "excluded"}]
    excluded = [{"case_id": "rag-v0-005", "reason": "h1_added_registry_entity systematic interception, both prior attempts retained; West excluded; not score based"},
                *[{"case_id": o["case_id"], "reason": o["reason"], "code": o.get("error_code", o["prior_error_code"])}
                  for o in outcomes if o["disposition"] == "excluded"]]
    control_rows = [r for r in baseline.read_jsonl(baseline.OUTPUT / "cases.jsonl") if r["case_id"] in ids]
    if [r["case_id"] for r in control_rows] != ids:
        raise original.H1Error("h1_disposition_control_population_drift")
    for row in control_rows:
        replay = baseline.score_case(row, row["rrf_top40"], row["selection"], row["rerank_top12"])
        if any(row[k] != v for k, v in replay.items()):
            raise original.H1Error("h1_disposition_control_metric_drift")
    metrics = baseline.summarize(control_rows)
    original.write_json(OUTPUT / "candidate_inputs.json", accepted)
    original.write_json(OUTPUT / "candidate_baseline.json", {"case_ids": ids, "metrics": metrics,
                                                           "retrieval_rerun": False, "real_api_calls": 0})
    original.write_json(OUTPUT / "exclusions.json", excluded)
    report = {"recorded_at": original.now(), "status": "pending_West_dispositions" if pending else "frozen_pending_final_retrieval_approval",
              "candidate_case_ids": ids, "candidate_count": len(ids), "excluded": excluded,
              "pending_case_ids": pending, "population_finalized": not pending,
              "focus_six_semantic_approval": "West explicitly confirmed no drift in latest batch-disposition instruction",
              "hypothesis": {"timeline_recall_at_40_gte": 0.6, "artist_overview_recall_at_40_gt": 1 / 3},
              "new_usage": original.usage_summary(new_calls),
              "cumulative_usage_including_all_excluded_attempts": original.usage_summary([*old_calls, *new_calls]),
              "request_temperature_zero_verified": all(type(c["request"]["temperature"]) is int
                                                        and c["request"]["temperature"] == 0 for c in new_calls),
              "request_body_unchanged_verified": all(c["request"] == original.request_body(
                  next(o["record"] for o in outcomes if o["case_id"] == c["case_id"]), c["stage"]) for c in new_calls),
              "qdrant_calls": 0, "judge_calls": 0, "retrieval_allowed": False,
              "baseline_candidate_metrics": metrics,
              "artifact_sha256": {n: original.sha256(OUTPUT / n) for n in (
                  "inputs.json", "candidate_inputs.json", "candidate_baseline.json", "exclusions.json", "dispositions.json")}}
    original.write_json(OUTPUT / "review_snapshot.json", report)
    if not pending:
        original.write_json(OUTPUT / "freeze_manifest.json", report)
    return report


async def generate(allow_env_read):
    import httpx
    from openai import AsyncOpenAI
    _, records, old_calls = load_prepared()
    if (OUTPUT / "calls").exists() or (OUTPUT / "generation_state.json").exists():
        raise original.H1Error("h1_disposition_already_attempted_no_resume")
    entity_resolver = resolver()
    key, timeout = original.approved_settings(allow_env_read)
    recorder = Recorder(OUTPUT / "calls")
    state = {"started_at": original.now(), "status": "running", "max_retries": 0, "qdrant_calls": 0, "judge_calls": 0}
    original.write_json(OUTPUT / "generation_state.json", state)
    try:
        async with httpx.AsyncClient(trust_env=False, follow_redirects=False, timeout=timeout,
                                    event_hooks={"request": [recorder.request], "response": [recorder.response]}) as http:
            async with AsyncOpenAI(api_key=key, base_url=original.BASE_URL, max_retries=0,
                                   timeout=timeout, http_client=http) as client:
                async def call_stage(row, stage):
                    recorder.scope = (row, stage)
                    response = await client.chat.completions.create(**original.request_body(row, stage))
                    if not response.choices or response.choices[0].finish_reason != "stop":
                        raise original.H1Error("h1_response_not_finished")
                    raw = response.choices[0].message.content
                    if not isinstance(raw, str):
                        raise original.H1Error("h1_missing_response_text")
                    return raw, 53 + len(recorder.entries)
                outcomes = await process_cases(records, entity_resolver, call_stage)
        summary = snapshot(outcomes, old_calls, recorder.entries)
        state.update(status=summary["status"], candidate_count=summary["candidate_count"], pending_case_ids=summary["pending_case_ids"])
    except Exception as exc:
        state.update(status="fatal_runtime_paused", error_type=type(exc).__name__, error_code=batch.error_code(exc))
    finally:
        state.update(finished_at=original.now(), new_usage=original.usage_summary(recorder.entries))
        try:
            load_prepared()
            state["history_and_protected_inputs_unchanged"] = True
        except Exception:
            state.update(status="fatal_history_drift", history_and_protected_inputs_unchanged=False)
        original.write_json(OUTPUT / "generation_state.json", state)
        print(json.dumps(state, ensure_ascii=False), flush=True)
    return 1 if state["status"].startswith("fatal_") else 0


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
            raise original.H1Error("h1_disposition_prepare_forbidden_operation")
        return 0
    sys.addaudithook(original.secret_guard)
    return asyncio.run(generate(args.authorized_env_read))


if __name__ == "__main__":
    raise SystemExit(main())
