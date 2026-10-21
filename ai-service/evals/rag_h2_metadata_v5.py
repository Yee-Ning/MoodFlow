"""West-approved H2: frozen H1 seven-leg retrieval with three-tier metadata."""

from __future__ import annotations

import argparse
from collections import Counter
from contextlib import contextmanager
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import math
import os
from pathlib import Path
import sys
from time import perf_counter
from types import SimpleNamespace
from unittest.mock import patch

from evals.ragas_gate0 import Gate0Guard, configure_offline


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "workspace/moodflow-review/h2-metadata-filter-gate1-20261018"
GATE0 = ROOT / "workspace/moodflow-review/h2-metadata-filter-gate0-20261018"
H1 = ROOT / "workspace/moodflow-review/h1-retrieval-25-v5-20261018"
CONTROL = H1 / "attempt-03"
COLLECTION = "music_knowledge_v2_e40ab37ffcc7bcf0_v1_v2_v4"
YEAR_FIELDS = ("year", "start_year", "end_year", "min_mentioned_year", "max_mentioned_year")
STAGES = ("rrf_top40", "selection_input_rrf", "selection", "rerank_top12")


class H2Error(RuntimeError):
    pass


class SafetyStop(BaseException):
    pass


class ReadOnlyGuard(Gate0Guard):
    def audit(self, event, args):
        if event == "socket.getaddrinfo":
            host = args[0].decode("ascii") if isinstance(args[0], bytes) else args[0]
            if host in {"localhost", "127.0.0.1", "::1"} and str(args[1]) == "6333":
                return
        if event == "socket.connect" and isinstance(args[1], tuple):
            if args[1][0] in {"127.0.0.1", "::1"} and args[1][1] == 6333:
                return
            caller = sys._getframe(1)
            if (caller.f_globals.get("__name__") == "socket" and caller.f_code.co_name == "_fallback_socketpair"
                    and caller.f_locals.get("csock") is args[0]
                    and args[1] == (caller.f_locals.get("addr"), caller.f_locals.get("port"))
                    and args[1][0] in {"127.0.0.1", "::1"}):
                self.local_socketpairs += 1
                return
        if event == "open" and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).name.lower() in {"token", "stored_tokens", "credentials", "id_rsa", "id_ed25519"}:
                self.denied.append("credential_file_open")
                raise SafetyStop("h2_credential_file_access_forbidden")
        super().audit(event, args)


def configure():
    configure_offline()
    os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1,::1"
    os.environ["no_proxy"] = "localhost,127.0.0.1,::1"


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8", newline="\n")


@lru_cache
def stack():
    from evals import rag_h1_retrieval_v5, rag_source_baseline
    return rag_h1_retrieval_v5, rag_source_baseline


def protected_hashes():
    _, baseline = stack()
    result = baseline.protected_hashes()
    for directory in (GATE0, H1, baseline.OUTPUT):
        result.update({path.relative_to(ROOT).as_posix(): sha256(path)
                       for path in sorted(directory.rglob("*")) if path.is_file()})
    return result


def filter_parts(entity_ids, year_range):
    from app.rag.query_understanding.filters import _temporal_filter
    from app.rag.query_understanding.models import FilterCondition, FilterGroup, FilterOperator, MetadataField, MetadataFilter
    from app.rag.retrieval.filter_compiler import compile_qdrant_filter
    from qdrant_client import models

    children = []
    if entity_ids:
        children.append(FilterCondition(field=MetadataField.ENTITY_IDS, operator=FilterOperator.MATCH_ANY, value=entity_ids))
    if year_range is not None:
        children.append(_temporal_filter(*year_range))
    intermediate = MetadataFilter(root=FilterGroup(operator="all", children=children) if children else None)
    compiled = compile_qdrant_filter(intermediate)
    if year_range is not None:
        known_in_range = compiled.must[-1]
        unknown = models.Filter(must=[models.IsEmptyCondition(is_empty=models.PayloadField(key=field)) for field in YEAR_FIELDS])
        compiled.must[-1] = models.Filter(should=[known_in_range, unknown])
    return intermediate, compiled


def dumped_filter(value):
    return value.model_dump(mode="json", exclude_none=True) if value is not None else None


def query_document(value):
    return value.get("nearest", value) if isinstance(value, dict) else value


def matches(payload, condition):
    if condition is None:
        return True
    if "must" in condition:
        return all(matches(payload, child) for child in condition["must"])
    if "should" in condition:
        return any(matches(payload, child) for child in condition["should"])
    if "is_empty" in condition:
        value = payload.get(condition["is_empty"]["key"])
        return value is None or value == []
    value = payload.get(condition["key"])
    if "match" in condition:
        return bool(set(value or []) & set(condition["match"]["any"]))
    bounds = condition["range"]
    return type(value) is int and ("gte" not in bounds or value >= bounds["gte"]) and ("lte" not in bounds or value <= bounds["lte"])


def tier(payload, year_range):
    if year_range is None:
        return "no_explicit_year"
    if all(payload.get(field) is None or payload.get(field) == [] for field in YEAR_FIELDS):
        return "unknown_year_pass"
    _, condition = filter_parts([], year_range)
    return "known_year_overlap_pass" if matches(payload, dumped_filter(condition)) else "known_year_outside_reject"


def load_inputs():
    h1, baseline = stack()
    queries, cases, _, projection, config, rerank, binding = h1.load_inputs()
    controls = read_jsonl(CONTROL / "cases.jsonl")
    plans = read_json(H1 / "plans.json")
    prereg = read_json(H1 / "pre_call_manifest.json")
    gate0 = read_json(GATE0 / "per_question_filters.json")
    if config.model_dump(mode="json") != prereg["retrieval_config"] or rerank.model_dump(mode="json") != prereg["rerank_config"]:
        raise H2Error("h2_runtime_configuration_drift")
    if sha256(H1 / "inputs.json") != h1.FROZEN_SHA or len(controls) != 25:
        raise H2Error("h2_frozen_inputs_drift")
    for name, expected in prereg["code_sha256"].items():
        if sha256(baseline.AI_ROOT / name) != expected:
            raise H2Error("h2_h1_pipeline_code_drift:" + name)
    for query, case, control, plan, row in zip(queries, cases, controls, plans, gate0, strict=True):
        if not (query["case_id"] == case["case_id"] == control["case_id"] == row["case_id"]
                and query["question"] == control["question"] == row["question"] and control["healthy"]):
            raise H2Error("h2_control_population_drift")
        replay = baseline.score_case(control, control["rrf_top40"], control["selection"], control["rerank_top12"])
        if any(control[key] != value for key, value in replay.items()):
            raise H2Error("h2_control_score_drift")
        if plan != control["plan"] or row["entities"] != query["entities"]:
            raise H2Error("h2_frozen_plan_or_entities_drift")
    return queries, cases, controls, plans, gate0, projection, config, rerank, binding


def build_plan(control_plan, gate0_row):
    from app.rag.query_understanding.models import SearchPlan

    ast, actual = filter_parts(gate0_row["entity_ids"], gate0_row["year_range"])
    plan = deepcopy(control_plan)
    plan["metadata_filter"] = ast.model_dump(mode="json")
    material = json.dumps([plan, dumped_filter(actual), "h2-three-tier-v1"], sort_keys=True, ensure_ascii=False).encode()
    plan["plan_id"] = "query-plan:" + hashlib.sha256(material).hexdigest()[:24]
    if {key: value for key, value in plan.items() if key not in {"metadata_filter", "plan_id"}} != {
        key: value for key, value in control_plan.items() if key not in {"metadata_filter", "plan_id"}
    } or plan["query_types"] != ["exploratory"]:
        raise H2Error("h2_non_filter_plan_change")
    return SearchPlan.model_validate(plan), actual


class RequestAudit:
    def __init__(self):
        self.requests = []
        self.active = None
        self.expected_filter = None
        self.expected_lexical = None
        self.safety_events = []

    def validate(self, request):
        base = f"/collections/{COLLECTION}"
        if (request.url.scheme != "http" or request.url.host not in {"localhost", "127.0.0.1", "::1"}
                or request.url.port != 6333 or request.url.username or request.url.password):
            raise SafetyStop("h2_http_destination_forbidden")
        if request.method == "GET" and request.url.path in {"/", base}:
            return
        if request.method == "POST" and request.url.path == base + "/points/scroll" and self.active is None:
            body = json.loads(request.content)
            if body.get("with_vector") is False and body.get("with_payload") is True and 1 <= body["limit"] <= 128:
                return
        if request.method == "POST" and request.url.path == base + "/points/query/batch" and self.active is not None:
            searches = json.loads(request.content)["searches"]
            if (len(searches) != 7 or any(item.get("filter") != self.expected_filter or item["limit"] != 80 or item.get("with_vector") is not False for item in searches)
                    or [item["using"] for item in searches] != ["dense", "bm25", "dense", "bm25", "dense", "bm25", "dense"]
                    or any(item.get("params", {}).get("hnsw_ef") != 128 for item in searches if item["using"] == "dense")
                    or [query_document(item["query"])["text"] for item in searches if item["using"] == "bm25"] != self.expected_lexical):
                raise SafetyStop("h2_http_filter_or_seven_leg_contract_drift")
            return
        raise SafetyStop("h2_http_operation_forbidden")

    def entry(self, request):
        try:
            self.validate(request)
        except SafetyStop as exc:
            self.safety_events.append({"case_id": self.active, "event": str(exc), "method": request.method,
                                       "path": request.url.path, "request_sent": False})
            raise
        except Exception as exc:
            self.safety_events.append({"case_id": self.active, "event": "h2_http_audit_validation_error", "error_type": type(exc).__name__,
                                       "method": request.method, "path": request.url.path, "request_sent": False})
            raise SafetyStop("h2_http_audit_validation_error") from exc
        entry = {"case_id": self.active, "method": request.method, "path": request.url.path}
        if request.method == "POST":
            body = json.loads(request.content)
            if request.url.path.endswith("/scroll"):
                entry["body"] = body
            else:
                entry["queries"] = [{"using": item["using"], "limit": item["limit"], "filter": item.get("filter"),
                                     "params": item.get("params"), "bm25": item["query"] if item["using"] == "bm25" else None}
                                    for item in body["searches"]]
        self.requests.append(entry)
        return entry

    @contextmanager
    def installed(self):
        import httpx

        sync_send, async_send = httpx.Client.send, httpx.AsyncClient.send

        def sync(client, request, *args, **kwargs):
            entry = self.entry(request)
            response = sync_send(client, request, *args, **kwargs)
            entry["status_code"] = response.status_code
            return response

        async def asynchronous(client, request, *args, **kwargs):
            entry = self.entry(request)
            response = await async_send(client, request, *args, **kwargs)
            entry["status_code"] = response.status_code
            return response

        with patch.object(httpx.Client, "send", sync), patch.object(httpx.AsyncClient, "send", asynchronous):
            yield


def inventory(audit, projection):
    import httpx

    with httpx.Client(base_url="http://127.0.0.1:6333", trust_env=False, timeout=30) as client:
        path = f"/collections/{COLLECTION}"
        response = client.get(path)
        response.raise_for_status()
        info = response.json()["result"]
        points, offset, seen = [], None, set()
        while True:
            body = {"limit": 128, "with_payload": True, "with_vector": False}
            if offset is not None:
                body["offset"] = offset
            response = client.post(path + "/points/scroll", json=body)
            response.raise_for_status()
            result = response.json()["result"]
            points.extend(result["points"])
            offset = result["next_page_offset"]
            if offset is None:
                break
            if str(offset) in seen:
                raise H2Error("h2_inventory_pagination_cycle")
            seen.add(str(offset))
    expected = {point: projection.children[child] for point, child in projection.points.items()}
    if len(points) != 691 or {str(point["id"]): point["payload"] for point in points} != expected:
        raise H2Error("h2_live_payload_projection_drift")
    return {"collection_info": info, "full_payload_projection_match": True, "children": 691,
            "parents": len(projection.parents), "vectors_read": False}


def prepare(guard):
    h1, baseline = stack()
    inputs = load_inputs()
    queries, _, controls, control_plans, gate0, projection, config, rerank, _ = inputs
    if (OUTPUT / "pre_call_manifest.json").exists():
        raise H2Error("h2_already_prepared_no_overwrite")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / ".gitattributes").write_text("* text eol=lf\n", encoding="utf-8", newline="\n")
    protected = protected_hashes()
    plans, filters = [], []
    for control_plan, constraint in zip(control_plans, gate0, strict=True):
        plan, compiled = build_plan(control_plan, constraint)
        plans.append(plan.model_dump(mode="json"))
        payload_filter = dumped_filter(compiled)
        filters.append({"case_id": constraint["case_id"], "year_range": constraint["year_range"], "entity_ids": constraint["entity_ids"],
                        "effective_qdrant_filter": payload_filter, "intermediate_ast_requires_three_tier_compiler": constraint["year_range"] is not None,
                        "eligible_children": sum(matches(payload, payload_filter) for payload in projection.children.values()),
                        "year_tiers": dict(Counter(tier(payload, constraint["year_range"]) for payload in projection.children.values()))})
    audit = RequestAudit()
    with audit.installed():
        snapshot = inventory(audit, projection)
        special = next(row for row in gate0 if row["case_id"] == "rag-v0-036")
        special_ids = special["h1_top40_excluded_gold_child_ids"]
        if len(special_ids) != 4:
            raise H2Error("h2_036_special_child_population_drift")
        actual_filter = next(row["effective_qdrant_filter"] for row in filters if row["case_id"] == "rag-v0-036")
        control036 = next(row for row in controls if row["case_id"] == "rag-v0-036")
        point_ids = [item["point_id"] for item in control036["rrf_top40"] if item["child_id"] in special_ids]
        import httpx

        probe_filter = {"must": [actual_filter, {"has_id": point_ids}]}
        with httpx.Client(trust_env=False, timeout=15) as client:
            response = client.post(f"http://127.0.0.1:6333/collections/{COLLECTION}/points/scroll", json={
                "limit": 128, "with_payload": True, "with_vector": False, "filter": probe_filter})
            response.raise_for_status()
            probe = response.json()["result"]
        returned = sorted(point["payload"]["chunk_id"] for point in probe["points"])
        details = [{"child_id": child, "parent_id": projection.children[child]["parent_id"],
                    "year_fields": {field: projection.children[child].get(field) for field in YEAR_FIELDS},
                    "tier": tier(projection.children[child], special["year_range"]),
                    "three_tier_filter_pass": matches(projection.children[child], actual_filter), "server_scroll_pass": child in returned}
                   for child in special_ids]
    expected_pass = sorted(detail["child_id"] for detail in details if detail["three_tier_filter_pass"])
    if guard.denied or protected != protected_hashes() or returned != expected_pass or probe["next_page_offset"] is not None:
        raise H2Error("h2_preflight_guard_or_special_child_probe_failed")
    (OUTPUT / "inputs.json").write_bytes((H1 / "inputs.json").read_bytes())
    write_json(OUTPUT / "plans.json", plans)
    write_json(OUTPUT / "filters.json", filters)
    write_json(OUTPUT / "036_four_gold_children_preflight.json", details)
    write_json(OUTPUT / "collection_before.json", snapshot)
    write_json(OUTPUT / "preflight_requests.json", audit.requests)
    write_json(OUTPUT / "pre_call_manifest.json", {
        "recorded_at": baseline.now(), "authorization": "West approved H2 Gate 1 three-tier revision in this chat.",
        "case_ids": [row["case_id"] for row in queries], "frozen_inputs_sha256": h1.FROZEN_SHA,
        "code_sha256": {"evals/rag_h2_metadata_v5.py": sha256(Path(__file__))}, "protected_before": protected,
        "artifact_sha256": {name: sha256(OUTPUT / name) for name in ("inputs.json", "plans.json", "filters.json", "036_four_gold_children_preflight.json", "collection_before.json", "preflight_requests.json")},
        "retrieval_config": config.model_dump(mode="json"), "rerank_config": rerank.model_dump(mode="json"),
        "preregistration": {"holds_at_least": 0.6, "partially_holds_strictly_above": 0.351,
                            "otherwise": "不成立", "threshold_source": "West's literal three-tier Gate 1 instruction"},
        "baseline_retrieval_rerun": False, "expansion_regenerated": False, "secret_reads_allowed": False,
        "proxy_policy": "process NO_PROXY loopback bypass; original compatibility and binding checks retained",
        "h1_original_audit_caveat": read_json(CONTROL / "analysis_manifest.json")})
    print(json.dumps({"prepared": True, "cases": 25, "special_036_children_pass": len(returned), "preflight_read_requests": len(audit.requests)}), flush=True)


def load_prepared():
    inputs = load_inputs()
    prep = read_json(OUTPUT / "pre_call_manifest.json")
    if prep["protected_before"] != protected_hashes() or prep["code_sha256"]["evals/rag_h2_metadata_v5.py"] != sha256(Path(__file__)):
        raise H2Error("h2_pre_call_protected_or_code_drift")
    if any(sha256(OUTPUT / name) != digest for name, digest in prep["artifact_sha256"].items()):
        raise H2Error("h2_prepared_artifact_drift")
    return inputs


def intercept_fallback(plan, count, config, predicate, audit):
    triggered = predicate(plan, count, config)
    if triggered:
        event = {"case_id": audit.active, "selection_count": count, "threshold": config.selection.exploratory_entity_fallback_below,
                 "plan_id": plan.plan_id, "unfiltered_request_sent": False, "event": "exploratory_entity_fallback_intercepted"}
        audit.safety_events.append(event)
        raise SafetyStop("h2_exploratory_entity_fallback_intercepted")
    return False


def collect_case(case, query, plan_data, filter_data, projection, config, binding, audit):
    h1, baseline = stack()
    from app.rag.query_understanding.models import SearchPlan
    from app.rag.retrieval import qdrant as qdrant_module
    from qdrant_client import models

    service, source = baseline.retrieval_module, baseline.rag_source
    actual_backend, actual_rrf = baseline.QdrantHybridSearchBackend, service.weighted_rrf
    actual_collect, actual_predicate = source.RagSource.collect, service._needs_exploratory_entity_fallback
    plan = SearchPlan.model_validate(plan_data)
    compiled = models.Filter.model_validate(filter_data["effective_qdrant_filter"]) if filter_data["effective_qdrant_filter"] is not None else None
    observed = {"search_calls": 0, "rrf_calls": 0, "fallback_checks": []}

    class ObservedBackend(actual_backend):
        async def search(self, supplied_binding, legs, query_filter, *, limit):
            observed["search_calls"] += 1
            if (observed["search_calls"] != 1 or len(legs) != 7 or limit != 80
                    or dumped_filter(query_filter) != filter_data["effective_qdrant_filter"]):
                raise SafetyStop("h2_unexpected_extra_search_or_filter_drift")
            expected = [(item["text"], item["channel"]) for item in case["control_legs"]]
            if [(leg.text, leg.channel.value) for leg in legs] != expected:
                raise SafetyStop("h2_frozen_leg_drift")
            started = perf_counter()
            results = await super().search(supplied_binding, legs, query_filter, limit=limit)
            if any(not matches(projection.children[hit.payload["chunk_id"]], filter_data["effective_qdrant_filter"]) for result in results for hit in result.hits):
                raise SafetyStop("h2_returned_child_violates_filter")
            observed["leg_results"] = h1.serialize_legs(results, (perf_counter() - started) * 1000,
                projection=projection, relevant=set(case["relevant_parent_ids"]), limit=limit)
            return results

    def compile_filter(intermediate):
        if intermediate != plan.metadata_filter:
            raise SafetyStop("h2_intermediate_ast_drift")
        return compiled

    def literal(query_text, profile, limit):
        if query_text != case["question"] or profile != binding.profile or limit != 40:
            raise SafetyStop("h2_original_query_or_binding_drift")
        return plan

    def fusion(results, *, k):
        observed["rrf_calls"] += 1
        if observed["rrf_calls"] != 1 or k != 60 or len(results) != 7 or any(result.leg.weight != 1 for result in results):
            raise SafetyStop("h2_fusion_contract_drift")
        return actual_rrf(results, k=k)

    def fallback(plan, count, supplied_config):
        observed["fallback_checks"].append({"candidate_count": count, "threshold": supplied_config.selection.exploratory_entity_fallback_below})
        return intercept_fallback(plan, count, supplied_config, actual_predicate, audit)

    def collect(self, request, *, limit):
        result = actual_collect(self, request, limit=limit)
        observed["returned_source_child_ids"] = [item.metadata["chunk_id"] for item in result]
        return result

    def no_credentials(name, default=None):
        if name == config.qdrant.api_key_env:
            return None
        raise SafetyStop("h2_unexpected_environment_read")

    started = perf_counter()
    with (patch.object(source, "literal_search_plan", literal), patch.object(source.RagSource, "collect", collect),
          patch.object(service, "build_retrieval_legs", h1.seven_legs), patch.object(service, "weighted_rrf", fusion),
          patch.object(service, "compile_qdrant_filter", compile_filter), patch.object(service, "_needs_exploratory_entity_fallback", fallback),
          patch.object(baseline, "QdrantHybridSearchBackend", ObservedBackend),
          patch.object(baseline, "guard_request", lambda request, _log: audit.validate(request)),
          patch.object(baseline, "os", SimpleNamespace(getenv=no_credentials)),
          patch.object(qdrant_module, "os", SimpleNamespace(getenv=no_credentials))):
        record = baseline.collect_case(case, projection, binding, config, [])
    record.update(observed, query_understanding=query, effective_qdrant_filter=filter_data["effective_qdrant_filter"],
                  intermediate_ast_requires_three_tier_compiler=filter_data["intermediate_ast_requires_three_tier_compiler"],
                  case_wall_ms=(perf_counter() - started) * 1000)
    selected = record.get("selection", [])
    expected_status = "success" if selected else "empty"
    record["healthy"] = (record.get("retrieval_status") == expected_status and record.get("rerank_status") == expected_status
        and not record["source_status"] and not record.get("rerank_fallback_used")
        and observed["search_calls"] == observed["rrf_calls"] == len(observed["fallback_checks"]) == 1
        and len(record.get("leg_results", [])) == 7 and all(leg["error_code"] is None for leg in record.get("leg_results", []))
        and len(record["rrf_top40"]) <= 40 and len(record["selection_input_rrf"]) <= 80
        and len(selected) <= 40 and len(record["rerank_top12"]) == min(12, len(selected))
        and len({item["parent_id"] for item in selected}) == len(selected)
        and observed["returned_source_child_ids"] == [item["child_id"] for item in record["rerank_top12"]])
    record.update(baseline.score_case(case, record["rrf_top40"], selected, record["rerank_top12"]))
    if not record["healthy"]:
        record["metrics"] = None
    return record


def run(guard):
    inputs = load_prepared()
    queries, cases, controls, _, _, projection, config, _, binding = inputs
    _, baseline = stack()
    if (OUTPUT / "run_manifest.json").exists() or (OUTPUT / "cases.jsonl").exists():
        raise H2Error("h2_already_attempted_no_automatic_repeat")
    plans, filters = read_json(OUTPUT / "plans.json"), read_json(OUTPUT / "filters.json")
    state = {"started_at": baseline.now(), "status": "loading_pinned_local_models", "completed_case_count": 0,
             "new_deepseek_calls": 0, "judge_calls": 0, "secret_reads": 0, "baseline_rerun": False,
             "qdrant_write_calls": 0, "safety_events": []}
    write_json(OUTPUT / "run_manifest.json", state)
    audit, records, error = RequestAudit(), [], None
    try:
        encoder, _, scorer, _ = baseline.rag_source._local_models()
        encoder._encoder._ensure_loaded()
        scorer.prepare()
        import torch

        state["runtime"] = {"torch": torch.__version__, "cuda": torch.cuda.is_available(),
                            "device": torch.cuda.get_device_name(0) if torch.cuda.is_available() else "cpu",
                            "embedding_device": encoder._encoder._device, "reranker_device": scorer._device}
        write_json(OUTPUT / "run_manifest.json", state)
        with audit.installed(), (OUTPUT / "cases.jsonl").open("x", encoding="utf-8", newline="\n") as stream:
            for index, (query, case, control, plan, condition) in enumerate(zip(queries, cases, controls, plans, filters, strict=True), 1):
                audit.active = case["case_id"]
                audit.expected_filter = condition["effective_qdrant_filter"]
                audit.expected_lexical = query["queries"]
                offset = len(audit.requests)
                row = collect_case({**case, "control_legs": control["leg_results"]}, query, plan, condition, projection, config, binding, audit)
                row["qdrant_requests"] = audit.requests[offset:]
                stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
                stream.flush()
                records.append(row)
                state.update(status="running", completed_case_count=len(records), last_case_id=row["case_id"])
                write_json(OUTPUT / "run_manifest.json", state)
                print(json.dumps({"progress": f"{index}/25", "case_id": row["case_id"], "healthy": row["healthy"],
                                  "recall_at_40": row["metrics"]["recall_at_40"] if row["metrics"] else None,
                                  "candidates": len(row["selection"])}, ensure_ascii=False), flush=True)
                if not row["healthy"]:
                    raise H2Error("h2_unhealthy_case_stop")
            audit.active = None
            write_json(OUTPUT / "collection_after.json", inventory(audit, projection))
    except SafetyStop as exc:
        error = str(exc)
        state["status"] = "stopped_safety_valve" if error == "h2_exploratory_entity_fallback_intercepted" else "stopped_http_guard"
        state["stopped_case_id"] = audit.active
    except Exception as exc:
        error = str(exc) if isinstance(exc, H2Error) else type(exc).__name__
        state["status"] = "incomplete"
    finally:
        unchanged = protected_hashes() == read_json(OUTPUT / "pre_call_manifest.json")["protected_before"]
        complete = len(records) == 25 and error is None and unchanged and not guard.denied and not audit.safety_events
        state.update(finished_at=baseline.now(), completed_case_count=len(records), error_code=error,
                     protected_unchanged=unchanged, guard_denied=guard.denied, safety_events=audit.safety_events,
                     qdrant_request_counts=dict(Counter(f"{row['method']} {row['path']}" for row in audit.requests)))
        if complete:
            state["status"] = "complete"
        elif state["status"] not in {"stopped_safety_valve", "stopped_http_guard", "incomplete"}:
            state["status"] = "incomplete"
        write_json(OUTPUT / "run_manifest.json", state)
        write_json(OUTPUT / "qdrant_requests.json", audit.requests)
    print(json.dumps({"status": state["status"], "completed": len(records), "error_code": error}), flush=True)
    return 0 if complete else 1


def verify():
    _, baseline = stack()
    from app.rag.query_understanding.models import QueryVariantKind, SearchChannel, SearchPlan
    from app.rag.retrieval.fusion import LegSearchResult, RawSearchHit, weighted_rrf
    from app.rag.retrieval.legs import RetrievalLeg
    from app.rag.retrieval.selection import select_candidates

    queries, _, controls, _, _, projection, config, _, _ = load_prepared()
    state = read_json(OUTPUT / "run_manifest.json")
    rows = read_jsonl(OUTPUT / "cases.jsonl")
    if state["status"] != "complete" or len(rows) != 25 or state["safety_events"] or state["guard_denied"]:
        raise H2Error("h2_run_incomplete_or_safety_stop")
    if [row["case_id"] for row in rows] != [row["case_id"] for row in queries] or any(not row["healthy"] for row in rows):
        raise H2Error("h2_population_or_health_mismatch")
    filters = read_json(OUTPUT / "filters.json")
    for row, control, condition in zip(rows, controls, filters, strict=True):
        if len(row["leg_results"]) != 7 or row["search_calls"] != 1 or row["rrf_calls"] != 1:
            raise H2Error("h2_offline_seven_leg_contract_mismatch")
        raw = []
        for leg in row["leg_results"]:
            reconstruction = RetrievalLeg(variant_index=leg["variant_index"], variant_kind=QueryVariantKind(leg["variant_kind"]),
                channel=SearchChannel(leg["channel"]), text=leg["text"], weight=leg["weight"])
            raw.append(LegSearchResult(leg=reconstruction, error_code=leg["error_code"], hits=[
                RawSearchHit(hit["point_id"], hit["raw_score"], projection.children[hit["child_id"]]) for hit in leg["hits"]]))
            if any(not matches(projection.children[hit["child_id"]], condition["effective_qdrant_filter"]) for hit in leg["hits"]):
                raise H2Error("h2_offline_filter_violation")
        fused = weighted_rrf(raw, k=60)
        for field, count in (("rrf_top40", 40), ("selection_input_rrf", 80)):
            if [hit.payload["chunk_id"] for hit in fused[:count]] != [hit["child_id"] for hit in row[field]]:
                raise H2Error("h2_offline_rrf_order_mismatch")
            if any(not math.isclose(hit.fusion_score, saved["fusion_score"], abs_tol=1e-15) for hit, saved in zip(fused[:count], row[field], strict=True)):
                raise H2Error("h2_offline_rrf_score_mismatch")
        selected, warnings = select_candidates(fused[:80], SearchPlan.model_validate(row["plan"]), config)
        if ([candidate.primary_child_id for candidate in selected] != [item["child_id"] for item in row["selection"]]
                or warnings != row["selection_warnings"] or len(row["rerank_top12"]) != min(12, len(selected))):
            raise H2Error("h2_offline_selection_mismatch")
        scores = [item["rerank_score"] for item in row["rerank_top12"]]
        if any(value is None or not math.isfinite(value) for value in scores) or scores != sorted(scores, reverse=True):
            raise H2Error("h2_invalid_rerank_scores")
        if not {item["child_id"] for item in row["rerank_top12"]} <= {item["child_id"] for item in row["selection"]}:
            raise H2Error("h2_rerank_candidate_provenance_mismatch")
        if any(row[key] != value for key, value in baseline.score_case(row, row["rrf_top40"], row["selection"], row["rerank_top12"]).items()):
            raise H2Error("h2_offline_metrics_mismatch")
        for field in STAGES:
            for hit in row[field]:
                projection.item(child=hit["child_id"], parent=hit["parent_id"], point=hit["point_id"], relevant=set(row["relevant_parent_ids"]))
        expected = {key: value for key, value in control["plan"].items() if key not in {"metadata_filter", "plan_id"}}
        if {key: value for key, value in row["plan"].items() if key not in {"metadata_filter", "plan_id"}} != expected:
            raise H2Error("h2_offline_non_filter_plan_change")
    requests = read_json(OUTPUT / "qdrant_requests.json")
    batches = [request for request in requests if request["method"] == "POST" and request["path"].endswith("/query/batch")]
    if len(batches) != 25 or any(len(request["queries"]) != 7 or request["status_code"] != 200 for request in batches):
        raise H2Error("h2_query_request_count_or_response_mismatch")
    for request, condition in zip(batches, filters, strict=True):
        if request["case_id"] != condition["case_id"] or any(query["filter"] != condition["effective_qdrant_filter"] for query in request["queries"]):
            raise H2Error("h2_recorded_request_filter_mismatch")
    result = {"verified": True, "cases": 25, "legs": 175, "queries_added_by_verification": 0,
              "rrf_selection_and_metrics_replayed": True, "all_parent_changes_recomputable": True,
              "protected_inputs_unchanged": True, "unfiltered_fallback_sent": False}
    write_json(OUTPUT / "verification.json", result)
    print(json.dumps(result), flush=True)
    return result


def main():
    global OUTPUT

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "run", "verify"))
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    OUTPUT = args.output
    configure()
    guard = Gate0Guard() if args.command == "verify" else ReadOnlyGuard()
    sys.addaudithook(guard.audit)
    if args.command == "run":
        import torch

        print(json.dumps({"cuda_bootstrap": torch.cuda.is_available(), "torch": torch.__version__}), flush=True)
        return run(guard)
    if args.command == "prepare":
        prepare(guard)
    else:
        verify()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
