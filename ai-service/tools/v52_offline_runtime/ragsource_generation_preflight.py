"""Offline Gate 0b wiring audit; never invent or invoke a QA generator."""

from collections import Counter
import hashlib
import json
import sys

from tools.v52_offline_runtime import generation_baseline_v53 as prior


OUTPUT = prior.ROOT / "workspace/moodflow-review/generation-baseline-ragsource-v53-20261019"


def restored_items(saved):
    from app.agent.foundation.context_engineering import ContextItem

    items = []
    for row in saved:
        metadata = row["metadata"]
        fusion = metadata["fusion_score"]
        score = metadata["rerank_score"]
        items.append(ContextItem(id="evidence:rag:" + metadata["evidence_id"],
            type="rag_evidence", source="rag", content=row["content"], metadata=metadata,
            relevance=score if score is not None else fusion / (1 + fusion),
            references=[metadata["doc_id"], metadata["source_id"]]))
    return items


def route_probe(question, saved):
    from app.agent.foundation.context_engineering import build_context
    from app.agent.foundation.routing import RouteKey

    items = restored_items(saved)
    output = {}
    for route in RouteKey:
        pack = build_context(route, query=question, system="",
            registered_items=items, intent={"routes": [route.value]})
        final = [item for item in pack.items if item.source == "rag"]
        output[route.value] = {"input_items": len(items), "included_items": len(final),
            "included_ids": [item.id for item in final],
            "drop_reasons": dict(Counter(entry.reason for entry in pack.trace.entries
                if entry.item_id.startswith("evidence:rag:") and entry.stage == "dropped")),
            "rag_in_model_messages": any("<rag_evidence citation=" in str(message.content)
                for message in pack.messages)}
    return output


def scope_status(probes):
    non_explorer = ("general_chat", "current_song", "tool_calling")
    if all(not probe[route]["included_items"] for probe in probes for route in non_explorer):
        return "blocked_no_non_worldline_rag_consumer_in_verified_routes"
    return "requires_new_production_path_review_do_not_generate"


def main():
    from evals.ragas_gate0 import Gate0Guard, configure_offline

    configure_offline()
    guard = Gate0Guard()
    sys.addaudithook(guard.audit)
    gold = prior.selected_gold()
    if OUTPUT.exists():
        raise ValueError("gate0b_preflight_already_exists")
    paths = [prior.GOLD, prior.OUTPUT / "analysis.json",
             *sorted((prior.OUTPUT / "answers").glob("*.json"))]
    paths.extend(path for path in (prior.ROOT / "ai-service/app").rglob("*")
                 if path.is_file() and path.suffix in {".py", ".yaml", ".yml", ".json"})
    pins = {path.relative_to(prior.ROOT).as_posix(): prior.digest(path) for path in paths}
    per_case = []
    for case in gold:
        answer = prior.read(prior.OUTPUT / "answers" / (case["case_id"] + ".json"))
        if any(answer[key] != case[key] for key in ("question", "relevant_parent_ids", "answerability")):
            raise ValueError("gate0b_saved_case_drift")
        probe = route_probe(case["question"], answer["retrieval_source_items"])
        per_case.append({"case_id": case["case_id"], "answerability": case["answerability"], "routes": probe})
    from app.agent.foundation.context_sources import SourceRegistry
    from app.agent.foundation.context_engineering import POLICIES, registered_policy
    from app.agent.foundation.prompt_bindings import PROMPT_BINDINGS
    from app.agent.foundation.routing import RouteKey

    result = {"status": scope_status([row["routes"] for row in per_case]),
        "measurement_completed": False, "offline_saved_input_cases": len(per_case),
        "population": dict(Counter(case["answerability"] for case in gold)),
        "fresh_retrieval_calls": 0, "generation_calls": 0, "judge_calls": 0, "provider_tokens": 0,
        "credentials_read": False, "production_changes": False, "guard_denied": guard.denied,
        "source_registry": list(SourceRegistry().sources),
        "route_policies": {route.value: {"allows_rag_evidence": "rag_evidence" in registered_policy(POLICIES[route]).allowed_types}
                           for route in RouteKey},
        "production_prompt_bindings": [{"reference": name, "owners": binding.owners,
            "phases": binding.phases, "sha256": hashlib.sha256(binding.text.encode()).hexdigest()}
            for name, binding in PROMPT_BINDINGS.items()],
        "per_case": per_case, "protected_sha256": pins,
        "limitations": ["Saved SourceItems replay is a wiring preflight, not fresh retrieval or generation.",
            "No generated answer, benchmark failure, Ragas score or WorldLine score delta is inferred.",
            "Static production entry review found no standalone shared factual-QA generator; route probes only establish the inspected ContextPack behavior."]}
    prior.check_pins(pins)
    if guard.denied:
        raise ValueError("gate0b_forbidden_operation_attempted")
    OUTPUT.mkdir()
    prior.write(OUTPUT / "preflight.json", result)
    print(json.dumps({key: value for key, value in result.items() if key in {
        "status", "measurement_completed", "offline_saved_input_cases", "population", "route_policies",
        "generation_calls", "judge_calls", "provider_tokens", "guard_denied"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()
