"""Observable metric denominators and independent failure classification."""

from __future__ import annotations

from collections import Counter

from evals.semantic_relations_models import PREDICATES, SemanticGoldCase


METRICS = (
    "relation_precision", "evidence_coverage", "evidence_traceability",
    "unsupported_hallucination_rate", "influence_direction_accuracy",
    "weak_evidence_rejection", "negation_rejection", "conflict_handling",
    "query_isolation", "checkpoint_continuity",
)


def relation_key(row: dict) -> tuple[str, str, str]:
    return row["source_entity"], row["target_entity"], row["predicate"]


def diagnose(case: SemanticGoldCase, actual: dict, trace: dict) -> dict:
    expected = {item.key: item for item in case.expected_relations}
    final = actual["relations"]
    final_keys = {relation_key(item) for item in final}
    reasons: set[str] = set()
    matched_claims: set[str] = set()
    traceable = correct = unsupported = 0
    evidence_count = sum(len(item["evidence"]) for item in final)
    for item in final:
        key = relation_key(item)
        gold = expected.get(key)
        evidence = item["evidence"]
        traceable += sum(ev["traceable"] for ev in evidence)
        gold_spans = {span.claim_id: span for span in case.supporting_evidence}
        valid_claims = {
            ev["claim_id"] for ev in evidence
            if ev["traceable"] and ev["claim_id"] in gold_spans
            and ev["exact_span"] == gold_spans[ev["claim_id"]].exact_span
            and ev["snapshot_source_id"] == gold_spans[ev["claim_id"]].source_id
            and ev["source_metadata"].get("document_id") == gold_spans[ev["claim_id"]].document_id
            and ev["source_metadata"].get("child_id") == gold_spans[ev["claim_id"]].chunk_id
        }
        if gold:
            matched_claims.update(valid_claims & set(gold.evidence_claim_ids))
        valid = bool(gold and evidence and all(ev["traceable"] for ev in evidence)
                     and set(gold.evidence_claim_ids) <= valid_claims
                     and {ev["claim_id"] for ev in evidence} <= valid_claims
                     and all(item["raw"].get(field) == value for field, value in gold.fields.items())
                     and all(item["raw"]["metadata"].get(flag) is True for flag in gold.conflict_flags))
        if valid:
            correct += 1
        else:
            unsupported += 1
            if gold:
                reasons.add("evidence mismatch")
            else:
                reasons.add("unsupported relation")
                if any(key[:2] == candidate[:2] for candidate in expected):
                    reasons.add("relation classification")
        if item["relation_type"] != case.relation_type:
            reasons.add("cross-relation contamination")
        if case.relation_type == "influence" and any(
            key[:2] == (target, source) for source, target, _ in expected
        ):
            reasons.add("direction error")

    retrieved_claims = {claim["claim_id"]
                        for document in trace["retrieved_evidence"]
                        for claim in document["metadata"].get("supported_claims", [])}
    required_claims = {claim for gold in expected.values() for claim in gold.evidence_claim_ids}
    retrieved_spans = {(document["id"], document["content"])
                       for document in trace["retrieved_evidence"]}
    for key in set(expected) - final_keys:
        wanted = expected[key]
        spans = [span for span in case.supporting_evidence
                 if span.claim_id in wanted.evidence_claim_ids]
        if not all(any(doc_id == span.document_id and span.exact_span in content
                       for doc_id, content in retrieved_spans) for span in spans):
            reasons.add("retrieval failure")
        elif not set(wanted.evidence_claim_ids) <= retrieved_claims:
            reasons.add("relation classification")
        else:
            decisions = [decision for stage in trace["filtering_scoring"].values()
                         for decision in stage["decisions"]
                         if decision["candidate"]["evidence"]["claim_id"]
                         in wanted.evidence_claim_ids]
            rejections = {reason for decision in decisions for reason in decision["rejection_reasons"]}
            if any("direction" in reason for reason in rejections):
                reasons.add("direction error")
            elif any("evidence" in reason or "claim_not" in reason for reason in rejections):
                reasons.add("evidence mismatch")
            else:
                reasons.add("relation classification")

    negative_ok = not final
    if case.label == "negative" and not negative_ok:
        if "weak_evidence" in case.categories:
            reasons.add("weak evidence accepted")
        if "negation" in case.categories:
            reasons.add("negation failure")

    conflict_ok = None
    if "conflict" in case.categories:
        conflict_ok = bool(expected) and set(expected) == final_keys
        for item in final:
            gold = expected.get(relation_key(item))
            if not gold:
                conflict_ok = False
                continue
            claims = {ev["claim_id"] for ev in item["evidence"] if ev["traceable"]}
            conflict_ok = conflict_ok and set(gold.evidence_claim_ids) <= claims
            conflict_ok = conflict_ok and all(
                item["raw"]["metadata"].get(flag) is True for flag in gold.conflict_flags
            )
            conflict_ok = conflict_ok and all(
                item["raw"].get(field) == value for field, value in gold.fields.items()
            )
        if not conflict_ok:
            reasons.add("conflict failure")
    else:
        for item in final:
            gold = expected.get(relation_key(item))
            if gold and any(item["raw"].get(field) != value for field, value in gold.fields.items()):
                reasons.add("relation classification")

    isolated = all(item["relation_type"] == case.relation_type for item in final)
    if not isolated:
        reasons.add("cross-relation contamination")
    continuity = actual["checkpoint"]["passed"]
    if not continuity:
        reasons.add("checkpoint continuity failure")
    # Direction recall denominator includes abstentions; conservative rejection
    # must never masquerade as perfect influence direction accuracy.
    direction_total = len(expected) if case.relation_type == "influence" else 0
    direction_correct = sum(any(
        relation_key(item) == key and bool(item["evidence"])
        and all(ev["traceable"] for ev in item["evidence"])
        for item in final
    ) for key in expected) if direction_total else 0
    counts = {
        "relation_precision": (correct, len(final)),
        "evidence_coverage": (len(matched_claims), len(required_claims)),
        "evidence_traceability": (traceable, evidence_count),
        "unsupported_hallucination_rate": (unsupported, len(final)),
        "influence_direction_accuracy": (direction_correct, direction_total),
        "weak_evidence_rejection": (int(negative_ok), 1) if "weak_evidence" in case.categories and case.label == "negative" else (0, 0),
        "negation_rejection": (int(negative_ok), 1) if "negation" in case.categories and case.label == "negative" else (0, 0),
        "conflict_handling": (int(bool(conflict_ok)), 1) if conflict_ok is not None else (0, 0),
        "query_isolation": (int(isolated), 1),
        "checkpoint_continuity": (int(continuity), 1),
    }
    return {
        "passed": not reasons and final_keys == set(expected),
        "failure_reasons": sorted(reasons),
        "metric_counts": {name: {"numerator": n, "denominator": d}
                          for name, (n, d) in counts.items()},
    }


def aggregate(rows: list[dict]) -> dict:
    result = {}
    for cohort in ("approved_real_baseline", "pending_real_diagnostic", "synthetic_regression"):
        selected = [row for row in rows if row["cohort"] == cohort]
        groups = {}
        for family in ("overall", *PREDICATES):
            group = [row for row in selected if family == "overall" or row["relation_type"] == family]
            metrics = {}
            for metric in METRICS:
                n = sum(row["metric_counts"][metric]["numerator"] for row in group)
                d = sum(row["metric_counts"][metric]["denominator"] for row in group)
                metrics[metric] = {"numerator": n, "denominator": d,
                                   "value": n / d if d else None}
            groups[family] = {"case_count": len(group), "passed": sum(row["passed"] for row in group),
                              "metrics": metrics,
                              "failure_categories": dict(Counter(
                                  reason for row in group for reason in row["failure_reasons"]))}
        result[cohort] = groups
    return result
