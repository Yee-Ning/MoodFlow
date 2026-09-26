"""Generic source-bound article/Q&A context; no Gold fixtures in runtime tests."""

from copy import deepcopy
from hashlib import sha256

import pytest

from app.agent.foundation.subgraphs.worldline.source_context import RelationSourceContext
from tests.test_worldline_conflict_handling import influence_pair
from tests import test_worldline_influence_expansion as influence


def segment(content, role, text):
    start = content.index(text)
    return {"role": role, "exact_span": text, "start": start, "end": start + len(text)}


def attach(document, content, segments, role):
    claim = document.metadata["supported_claims"][0]
    document = document.model_copy(update={"metadata": {
        **document.metadata, "snapshot_source_id": "snapshot:" + document.id,
    }})
    payload = {
        "context_id": "context:" + document.id, "source_id": "snapshot:" + document.id,
        "document_id": document.id, "chunk_id": document.metadata["chunk_id"], "claim_id": claim["claim_id"],
        "source_url": str(document.url), "publisher": document.source, "title": document.title,
        "locator": "paragraph and adjacent Q/A", "captured_at": "2026-09-26T00:00:00Z",
        "response_sha256": "0" * 64, "source_content_sha256": sha256(document.content.encode()).hexdigest(),
        "content": content, "content_sha256": sha256(content.encode()).hexdigest(),
        "anchor": segment(content, role, claim["evidence_span"]),
        "segments": [segment(content, name, text) for name, text in segments],
    }
    context = RelationSourceContext.model_validate(payload)
    assert context.bound_to(document, claim["claim_id"], claim["evidence_span"])
    return document.model_copy(update={"metadata": {
        **document.metadata, "relation_source_context": context.model_dump(mode="json"),
    }})


def context_pair(*, question=None, actor="B", heading=None, separator="\n\n"):
    support, denial = influence_pair(with_context=False)
    # Actor-to-verb gap includes a realistic list, longer than an entity pair.
    full = (
        f"{actor} listened to American artists, chamber orchestras, experimental instrumental "
        f"ensembles and traditional folk musicians and {support.content}"
    )
    support = attach(support, full, [("article_sentence", full)], "article_sentence")
    denial = denial.model_copy(update={"title": heading or "B: intervista a Rowan Vale"})
    question = question or "Altri artisti erano influenzati dall’opera di A. È così anche per voi?"
    text = denial.title + "\n\n" + question + separator + denial.content
    denial = attach(denial, text, [("speaker_binding", denial.title),
                                 ("interview_question", question), ("interview_answer", denial.content)], "interview_answer")
    return support, denial


def test_input_context_recovers_actor_and_passive_elliptical_question_without_replacing_span():
    docs = context_pair()
    result = influence.expand(list(docs))
    assert len(result.relations) == 1 and result.relations[0].metadata["attribution_conflict"]
    assert {e.stance.value for e in result.relations[0].evidence} == {"support", "contradiction"}
    for source, doc in zip(result.sources, docs):
        claim = doc.metadata["supported_claims"][0]
        assert source.excerpt == claim["evidence_span"] == doc.content
        assert source.metadata["relation_source_context"] == doc.metadata["relation_source_context"]
        assert source.metadata["context_document_sha256"] == sha256(doc.content.encode()).hexdigest()
    assert result.trace.aggregation_trace[0]["inputs"][1]["rejection_reasons"] == ["negated_influence"]


@pytest.mark.parametrize("question", [
    "Altri artisti erano influenzati dall’opera di A. È così anche per voi?",
    "Other artists were influenced by A. Is that also true for you?",
    "Did A influence your music?",
])
def test_interview_question_forms_are_bound_to_adjacent_respondent(question):
    assert influence.expand(list(context_pair(question=question))).relations[0].metadata["attribution_conflict"]


@pytest.mark.parametrize("mutation", [
    "content", "content_sha256", "source_content_sha256", "anchor", "source_id", "document_id",
    "chunk_id", "claim_id", "source_url", "publisher", "title", "segment",
])
def test_changed_source_context_fails_closed(mutation):
    support, denial = context_pair()
    metadata = deepcopy(support.metadata)
    payload = metadata["relation_source_context"]
    if mutation in {"anchor", "segment"}:
        span = payload["anchor"] if mutation == "anchor" else payload["segments"][0]
        span["start"] += 1
    else:
        payload[mutation] = "f" * 64 if mutation.endswith("sha256") else payload[mutation] + "changed"
    support = support.model_copy(update={"metadata": metadata})
    result = influence.expand([support, denial])
    assert result.relations == []
    assert "direction_missing" in result.trace.aggregation_trace[0]["inputs"][0]["rejection_reasons"]


@pytest.mark.parametrize("kwargs", [
    {"question": "Did A join your scene?"},
    {"question": "Did your music influence A?"},
    {"question": "Other artists were influenced by A. Did you interview them?"},
    {"heading": "B: intervista a Morgan Lane"},
    {"heading": "A: intervista a Rowan Vale"},
    {"separator": "\nUnrelated intervening answer.\n"},
])
def test_missing_direction_speaker_or_adjacency_does_not_form_conflict(kwargs):
    result = influence.expand(list(context_pair(**kwargs)))
    assert not any(r.metadata["attribution_conflict"] for r in result.relations)
    assert "direction_missing" in result.trace.aggregation_trace[0]["inputs"][1]["rejection_reasons"]


@pytest.mark.parametrize("actor", ["A", "B and A", "B might have", "B never"])
def test_article_cooccurrence_reverse_joint_actor_and_speculation_are_not_direction(actor):
    assert influence.expand(list(context_pair(actor=actor))).relations == []


def test_source_context_is_not_a_standalone_direction_repair_or_negation_override():
    support, denial = context_pair()
    assert influence.expand([support]).relations == []
    assert influence.expand([denial]).relations == []
    assert influence.expand([support, denial], query="B 的发行年代").relations == []
    modified = []
    for doc in (support, denial):
        metadata = deepcopy(doc.metadata)
        claim = metadata["supported_claims"][0]
        claim["subject_entity_id"], claim["object_entity_id"] = claim["object_entity_id"], claim["subject_entity_id"]
        modified.append(doc.model_copy(update={"metadata": metadata}))
    assert influence.expand(modified).relations == []
    insufficient = support.model_copy(update={"metadata": {**support.metadata, "sufficiency_status": "insufficient"}})
    assert influence.expand([insufficient, denial]).relations == []
