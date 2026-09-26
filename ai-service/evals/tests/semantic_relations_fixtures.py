"""Synthetic contract data only; never eligible for human Gold metrics."""

from hashlib import sha256

from app.agent.foundation.subgraphs.worldline.models import WorldLineGraph, WorldLineNode
from app.rag.query_understanding.models import SearchPlan
from evals.semantic_relations_models import ReplayInput, SemanticGoldCase, SourceSnapshot


def identity_inputs(query, entities):
    nodes = [WorldLineNode(id=f"eval:{kind}:{slug}", type=kind, name=name,
                           metadata={"entity_reference": {"knowledge_entity_id": f"{kind}:{slug}"}})
             for slug, name, kind in entities]
    graph = WorldLineGraph(root=nodes[0].id, nodes=nodes)
    plan = SearchPlan.model_validate({
        "plan_id": "query-plan:0123456789abcdef01234567", "original_query": query,
        "standalone_query": query, "query_types": ["timeline", "relationship", "influence_style"],
        "entities": [{"surface": name, "canonical_name": name, "entity_type": kind,
                      "knowledge_entity_id": f"{kind}:{slug}", "status": "resolved", "confidence": 1,
                      "catalog_reference": {"provider": "eval", "entity_id": slug,
                                            "entity_type": kind, "name": name}}
                     for slug, name, kind in entities],
        "queries": [{"text": query, "kind": "original", "channel": "both", "weight": 1}],
        "metadata_filter": {}, "strategy": "hybrid", "candidate_top_k": 40, "final_top_k": 10,
        "needs_rerank": True, "analysis_version": "v1", "prompt_version": "v1",
    })
    return graph, plan


def snapshot(source_id, text, *, url=None, title=None, publisher="Synthetic fixture", locator="authored fixture"):
    return SourceSnapshot(
        source_id=source_id, fixture_source_id=f"fixture:{source_id}", title=title or source_id,
        publisher=publisher, url=url, provenance="Evaluation-only source snapshot",
        language="en", source_type="primary_web_excerpt" if url else "synthetic",
        content=text, content_hash=sha256(text.encode()).hexdigest(),
        origin="real_source" if url else "synthetic", retrieved_at="2026-09-26",
        locator=locator, capture_method="verbatim_web_excerpt" if url else "synthetic_fixture",
    )


def annotated_document(source, document_id, predicate, *, subject="artist:a", target=None,
                       value=None, time=None, confidence=0.96, span=None, **extra):
    claim = {"claim_id": f"claim:{document_id}", "subject_entity_id": subject,
             "predicate": predicate, "object_entity_id": target, "object_value": value,
             "time": time, "source_chunk_id": f"chunk:{document_id}",
             "evidence_span": span or source.content, "extraction_confidence": confidence,
             "verification_confidence": confidence, **extra}
    return {"id": document_id, "title": source.title, "content": source.content,
            "source": source.publisher, "url": str(source.url) if source.url else None,
            "metadata": {"snapshot_source_id": source.source_id, "parent_id": document_id,
                         "chunk_id": f"chunk:{document_id}", "context_version": "context-v1",
                         "retrieval_version": "evaluation-frozen-v1", "sufficiency_status": "sufficient",
                         "supported_claims": [claim]}}


def case_record(case_id, query, family, documents, sources, expected, categories, *, entities=None,
                probe_target=None, rationale="Synthetic contract case."):
    real = all(source.origin == "real_source" for source in sources)
    graph, plan = identity_inputs(query, entities or [("a", "A", "artist"), ("b", "B", "artist")])
    spans = []
    for document in documents:
        claim = document["metadata"]["supported_claims"][0]
        source = next(item for item in sources if item.source_id == document["metadata"]["snapshot_source_id"])
        # Gold never copies an invalid runtime candidate span.
        span = claim["evidence_span"] if claim["evidence_span"] in source.content else source.content
        start = source.content.index(span)
        spans.append({"source_id": source.source_id, "document_id": document["id"],
                      "chunk_id": document["metadata"]["chunk_id"], "claim_id": claim["claim_id"],
                      "exact_span": span, "span_start": start, "span_end": start + len(span),
                      "stance": "contradiction" if "negation" in categories else "support"})
    case = SemanticGoldCase.model_validate({
        "case_id": case_id, "dataset_kind": "real_source" if real else "synthetic", "query": query,
        "relation_type": family, "source_entity": expected[0]["source_entity"] if expected else graph.root,
        "target_entity": expected[0]["target_entity"] if expected else probe_target or "eval:artist:b",
        "label": "positive" if expected else "negative", "categories": categories,
        "expected_relations": expected, "supporting_evidence": spans,
        "review": {"status": "pending" if real else "synthetic",
                   "method": "pending_human" if real else "synthetic", "notes": "No human approval is inferred."},
        "rationale": rationale,
    })
    replay = ReplayInput(case_id=case_id, query=query, search_plan=plan, graph=graph,
                         documents=documents, input_kind="real_source_annotation" if real else "synthetic_adversarial")
    return case, replay


def expected(source, target, predicate, docs, *, fields=None, flags=None):
    return {"source_entity": source, "target_entity": target, "predicate": predicate,
            "evidence_claim_ids": [f"claim:{document_id}" for document_id in docs],
            "fields": fields or {}, "conflict_flags": flags or []}


def synthetic_suite():
    cases, inputs, sources = [], [], []
    for family in ("era", "scene", "influence"):
        query = {"era": "A 的年代", "scene": "A 的音乐场景", "influence": "A 对 B 的音乐影响"}[family]
        predicate = {"era": "belongs_to_era", "scene": "belongs_to_scene", "influence": "influenced"}[family]
        target = {"era": "era:1990s", "scene": "scene:seattle_scene", "influence": "eval:artist:b"}[family]
        positive_span = {"era": "A was active during the 1990s in 1991.",
                         "scene": "A was part of the Seattle scene in 1991.",
                         "influence": "A influenced B."}[family]
        for category in ("positive", "negative", "weak_evidence", "negation", "conflict", "confusable", "isolation", "retrieval_missing", "evidence_mismatch"):
            case_id = f"synthetic-{family}-{category}"
            span = positive_span
            confidence = 0.96
            if category in {"negative", "confusable"}:
                span = "A and B both played music in Seattle in 1991."
            if category == "weak_evidence":
                span = {"era": "A may have been active during the 1990s in 1991.",
                        "scene": "A may have been part of the Seattle scene in 1991.",
                        "influence": "A may have influenced B."}[family]
            if category == "negation":
                span = {"era": "A was not active during the 1990s in 1991.",
                        "scene": "A was not part of the Seattle scene in 1991.",
                        "influence": "B was not influenced by A."}[family]
            source = snapshot(f"source:{case_id}", span)
            sources.append(source)
            document = annotated_document(source, case_id, predicate,
                                          target="artist:b" if family == "influence" else None,
                                          value={"era": "1990s", "scene": "Seattle scene", "influence": None}[family],
                                          time={"year": 1991} if family != "influence" else None,
                                          confidence=confidence)
            docs = [document]
            flags, fields = [], {}
            if category == "conflict":
                other_span = positive_span.replace("1991", "1992") if family != "influence" else "B was not influenced by A."
                other_source = snapshot(f"source:{case_id}:other", other_span)
                sources.append(other_source)
                other_doc = annotated_document(other_source, f"{case_id}:other", predicate,
                                               target="artist:b" if family == "influence" else None,
                                               value={"era": "1990s", "scene": "Seattle scene", "influence": None}[family],
                                               time={"year": 1992} if family != "influence" else None)
                docs.append(other_doc)
                flags = ["attribution_conflict" if family == "influence" else "temporal_conflict"]
                fields = {} if family == "influence" else {"start_year": None, "end_year": None, "temporal_precision": "unknown"}
            gold = [expected("eval:artist:a", target, predicate, [doc["id"] for doc in docs], fields=fields, flags=flags)] if category in {"positive", "conflict", "isolation", "retrieval_missing", "evidence_mismatch"} else []
            categories = [category if category != "evidence_mismatch" else "positive"]
            if gold and "positive" not in categories:
                categories.append("positive")
            if not gold and "negative" not in categories:
                categories.append("negative")
            case, replay = case_record(case_id, query, family, docs, sources, gold, categories)
            if category == "retrieval_missing":
                replay = replay.model_copy(update={"documents": []})
            if category == "evidence_mismatch":
                raw = replay.model_dump(mode="json")
                raw["documents"][0]["metadata"]["supported_claims"][0]["evidence_span"] = "A made an unsupported assertion."
                replay = ReplayInput.model_validate(raw)
            cases.append(case)
            inputs.append(replay)
    # Reverse claim orientation is input data, not an evaluator special rule.
    source = snapshot("source:synthetic-influence-passive", "B was influenced by A.")
    doc = annotated_document(source, "synthetic-influence-passive", "influenced_by", subject="artist:b", target="artist:a")
    case, replay = case_record("synthetic-influence-passive", "A 对 B 的影响", "influence", [doc], [source],
                               [expected("eval:artist:a", "eval:artist:b", "influenced", [doc["id"]])],
                               ["positive", "direction_sensitive"])
    cases.append(case)
    inputs.append(replay)
    sources.append(source)
    return cases, inputs, sources
