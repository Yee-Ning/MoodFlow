import json
from types import SimpleNamespace

import pytest

from tests.test_rag_knowledge import _enriched, _enriched_child
from app.rag.knowledge.claims.extractor import ClaimExtractor
from app.rag.knowledge.claims.models import ClaimCandidate, ClaimSource, ClaimExtractionOutput
from app.rag.knowledge.claims.grounding import validate_local
from app.rag.knowledge.config import KnowledgeConfig
from app.rag.knowledge.entity_linking.models import CanonicalEntity
from app.rag.knowledge.models import KnowledgeEntities, KnowledgeEnrichment, KnowledgeStats, KnowledgeQuality, KnowledgeProvenance
from app.rag.knowledge.pipeline import KnowledgePipeline
from app.rag.knowledge.verification.models import VerifiedClaim, ClaimVerification
from app.rag.knowledge.child_grounding import ground_parent_claims, validate_grounding


ENTITIES = [CanonicalEntity(entity_id=f"artist:{name.lower()}", entity_type="artist",
                           canonical_name=name, aliases=[name], confidence=1) for name in ("Alpha", "Beta")]


def candidate(span="Alpha influenced Beta.", **updates):
    return ClaimCandidate.model_validate(dict(statement="Alpha influenced Beta.",
        subject_entity_id="artist:alpha", predicate="influenced", object_entity_id="artist:beta",
        evidence_span=span, confidence=1, **updates))


@pytest.mark.parametrize("text,span,error", [
    ("Gamma influenced Beta.", "Gamma influenced Beta.", "entity_not_explicit"),
    ("If Alpha influenced Beta, things would differ.", "Alpha influenced Beta", "nonfactual"),
    ("Alpha might have influenced Beta.", "Alpha might have influenced Beta.", "nonfactual"),
    ("Alpha never influenced Beta.", "Alpha never influenced Beta.", "nonfactual"),
    ("I think Alpha influenced Beta.", "Alpha influenced Beta", "nonfactual"),
    ("Alphaville influenced Beta.", "Alphaville influenced Beta.", "entity_not_explicit"),
])
def test_local_binding_and_modality_fail_closed(text, span, error):
    with pytest.raises(ValueError, match=error):
        validate_local(candidate(span), text, ENTITIES)


def test_factual_direction_alias_ambiguity_and_ungrounded_date():
    validate_local(candidate(), "Alpha influenced Beta.", ENTITIES)
    ambiguous = [ENTITIES[0], ENTITIES[1].model_copy(update={"aliases": ["Alpha", "Beta"]})]
    with pytest.raises(ValueError, match="entity_not_explicit"):
        validate_local(candidate(), "Alpha influenced Beta.", ambiguous)
    with pytest.raises(ValueError, match="time_not_explicit"):
        validate_local(candidate(time={"year": 1994}), "Alpha influenced Beta.", ENTITIES)


@pytest.mark.parametrize("predicate", ["includes_track", "performed_at", "has_genre"])
def test_named_artist_is_not_a_track_event_or_genre(predicate):
    proposal = candidate().model_copy(update={"predicate": __import__(
        "app.rag.knowledge.claims.models", fromlist=["ClaimPredicate"]).ClaimPredicate(predicate)})
    with pytest.raises(ValueError, match="predicate_argument_type_mismatch"):
        validate_local(proposal, proposal.evidence_span, ENTITIES)


@pytest.mark.asyncio
async def test_failed_batch_splits_without_salvaging_json_or_retrying_success():
    calls = []
    async def generate(payload, **kwargs):
        text = payload["chunk_content"]
        calls.append(text)
        if "Gamma" in text and "Alpha" in text:
            raise RuntimeError("claim_extraction_llm_incomplete:max_output_tokens")
        return json.dumps({"claims": [candidate().model_dump(mode="json")] if "Alpha" in text else []})
    result = await ClaimExtractor(KnowledgeConfig(prompt_version="v2"), runtime=SimpleNamespace(generate=generate)).extract(
        _enriched("Alpha influenced Beta. Gamma influenced Beta."), ENTITIES)
    assert len(result.claims) == 1 and result.status == "success"
    assert len(calls) == 3 and len(set(calls)) == 3


@pytest.mark.asyncio
async def test_invalid_schema_retries_only_failed_batch_and_rejected_candidate_keeps_valid():
    calls = []
    async def generate(payload, **kwargs):
        calls.append(payload)
        if len(calls) == 1:
            return '{"claims": ['
        bad = candidate("Gamma influenced Beta.")
        return json.dumps({"claims": [candidate().model_dump(mode="json"), bad.model_dump(mode="json")]})
    result = await ClaimExtractor(KnowledgeConfig(prompt_version="v2"), runtime=SimpleNamespace(generate=generate)).extract(
        _enriched("Alpha influenced Beta."), ENTITIES)
    assert len(calls) == 2 and len(result.claims) == 1
    assert any("claim_candidate_rejected" in warning for warning in result.warnings)


def knowledge(base, claims):
    entities = KnowledgeEntities(linked_entities=ENTITIES)
    data = KnowledgeEnrichment(entities=entities, claims=claims, stats=KnowledgeStats.build(entities, claims),
        quality=KnowledgeQuality(status="success"), provenance=KnowledgeProvenance(
            entity_linking_version="v4", claim_extraction_version="v11", claim_verification_version="v3",
            registry_version="v5", prompt_version="v2", knowledge_input_hash="sha256:" + "0" * 64))
    return KnowledgePipeline._attach(base.model_copy(update={"metadata": {"document_metadata": {
        "source_metadata": {"content_hash": "sha256:" + "a" * 64}}}}), data)


def pair(status="SUPPORTED"):
    span = "Alpha influenced Beta."
    item = VerifiedClaim(claim=ClaimExtractor._claim(candidate(), "parent-1", ClaimSource.LLM),
        verification=ClaimVerification(status=status, confidence=1, evidence_span=span, reason="Explicit assertion"))
    return [knowledge(_enriched(span), [item]), knowledge(_enriched_child(span, mentions=[]), [])]


def test_unique_child_preserves_facts_verification_hashes_and_legacy_serialization():
    parent, child = ground_parent_claims(pair())
    item = child.knowledge.claims[0]
    assert item.grounding.derived_from == parent.knowledge.claims[0].claim.claim_id
    assert item.claim.subject_entity_id == "artist:alpha" and item.claim.object_entity_id == "artist:beta"
    assert item.verification == parent.knowledge.claims[0].verification
    validate_grounding(item, child, parent)
    assert "grounding" not in parent.knowledge.claims[0].model_dump(mode="json")
    assert VerifiedClaim.model_validate_json(item.model_dump_json()) == item
    assert len(ground_parent_claims([parent, child])[1].knowledge.claims) == 1
    with pytest.raises(ValueError, match="grounding_mismatch"):
        validate_grounding(item, child.model_copy(update={"content": "tampered"}), parent)


@pytest.mark.parametrize("mutation", ["unsupported", "duplicate_child", "repeated_span", "different_source", "different_doc", "missing_hash", "split_span"])
def test_unsafe_parent_mapping_stays_parent_only(mutation):
    parent, child = pair("PARTIALLY_SUPPORTED" if mutation == "unsupported" else "SUPPORTED")
    chunks = [parent, child]
    if mutation == "duplicate_child":
        chunks.append(child.model_copy(update={"chunk_id": "child-2"}))
    if mutation == "repeated_span":
        chunks[0] = parent.model_copy(update={"content": parent.content * 2})
    if mutation == "different_source":
        chunks[1] = child.model_copy(update={"enrichment": child.enrichment.model_copy(update={"source": child.enrichment.source.model_copy(update={"source_id": "other"})})})
    if mutation == "different_doc":
        chunks[1] = child.model_copy(update={"doc_id": "other"})
    if mutation == "missing_hash":
        chunks[0] = parent.model_copy(update={"metadata": {}})
    if mutation == "split_span":
        chunks[1] = child.model_copy(update={"content": "Alpha influenced"})
    assert all(not item.knowledge.claims for item in ground_parent_claims(chunks)[1:])


@pytest.mark.asyncio
async def test_extraction_runtime_budget_does_not_change_provider_claim_schema(monkeypatch):
    from app.rag.knowledge.claims.runtime import ExistingLLMClaimExtractionRuntime
    observed = {}
    async def response(*args, **kwargs):
        observed.update(kwargs)
        return SimpleNamespace(status="completed", output_text='{"claims": []}')
    monkeypatch.setattr("app.llm.client.create_structured_response", response)
    await ExistingLLMClaimExtractionRuntime(max_output_tokens=4096).generate({}, instructions="strict", schema=ClaimExtractionOutput.model_json_schema(), schema_name="test")
    assert observed["max_output_tokens"] == 4096


@pytest.mark.asyncio
async def test_semantic_actor_rejection_is_retained_before_factual_claim_ownership():
    calls = []
    proposal = candidate("A member of Alpha influenced Beta.").model_copy(
        update={"statement": "A member of Alpha influenced Beta."})
    async def generate(payload, **kwargs):
        calls.append((payload, kwargs))
        if kwargs["schema_name"] == "music_claim_admission_v3":
            return json.dumps({"status": "INSUFFICIENT_EVIDENCE", "confidence": 1,
                               "evidence_span": None, "reason": "A member's action is not Alpha's action"})
        return json.dumps({"claims": [proposal.model_dump(mode="json")]})
    result = await ClaimExtractor(KnowledgeConfig(prompt_version="v3"), runtime=SimpleNamespace(generate=generate)).extract(
        _enriched(proposal.evidence_span), ENTITIES)
    assert result.claims == [] and result.status == "success"
    assert "semantic_admission_rejected" in result.warnings[0]
    assert calls[1][0]["subject_identity"]["canonical_name"] == "Alpha"


@pytest.mark.asyncio
async def test_semantic_admission_preserves_real_direction_and_cannot_rewrite_evidence():
    async def generate(payload, **kwargs):
        if kwargs["schema_name"] == "music_claim_admission_v3":
            return json.dumps({"status": "SUPPORTED", "confidence": 1,
                               "evidence_span": payload["chunk_content"], "reason": "Both semantic roles explicit"})
        return json.dumps({"claims": [candidate().model_dump(mode="json")]})
    extractor = ClaimExtractor(KnowledgeConfig(prompt_version="v3"), runtime=SimpleNamespace(generate=generate))
    result = await extractor.extract(_enriched(candidate().evidence_span), ENTITIES)
    assert result.claims[0].subject_entity_id == "artist:alpha"
    assert result.claims[0].object_entity_id == "artist:beta"
    async def tamper(payload, **kwargs):
        return json.dumps({"status": "SUPPORTED", "confidence": 1, "evidence_span": "invented", "reason": "invalid"})
    extractor.runtime = SimpleNamespace(generate=tamper)
    with pytest.raises(ValueError, match="admission_evidence_mismatch"):
        await extractor._admit(candidate(), _enriched(candidate().evidence_span), ENTITIES)


@pytest.mark.asyncio
async def test_admission_transport_failure_does_not_approve_a_proposal():
    async def generate(payload, **kwargs):
        if kwargs["schema_name"] == "music_claim_admission_v3":
            raise RuntimeError("provider_unavailable")
        return json.dumps({"claims": [candidate().model_dump(mode="json")]})
    result = await ClaimExtractor(KnowledgeConfig(prompt_version="v3", retry_count=0), runtime=SimpleNamespace(generate=generate)).extract(
        _enriched(candidate().evidence_span), ENTITIES)
    assert result.claims == [] and result.status == "partial"
    assert "admission_unavailable" in result.warnings[0]
