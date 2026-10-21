"""Explorer stage recipes, actual RAG audit, old-path parity and budget gates."""

from copy import deepcopy
from hashlib import sha256
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.agent.foundation.agent_pack import AgentPackInputs
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.context_engineering import POLICIES
from app.agent.foundation.context_sources import SourceRegistry
from app.agent.foundation.hop_sources import ReceivedEnvelopeSource, SessionViewSource
from app.agent.foundation.rag_usage import RagAccountingFailure, RagBudgetRefusal
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.source_errors import SourceIntegrityError
from app.agent.foundation.subgraphs.worldline.generation import ProviderWorldLineRuntime, StructuredWorldLineGenerator
from app.agent.foundation.subgraphs.worldline.graph import create_worldline_subgraph
from app.agent.foundation.subgraphs.worldline.models import EntityType, MusicEntityFacts, WorldLineResult
from app.agent.foundation.subgraphs.worldline.prompts import (
    BRANCH_SYSTEM_PROMPT, EXPLORATION_BRANCH_BASE_V1, EXPLORATION_BRANCH_RULE_V1,
    NARRATIVE_SYSTEM_PROMPT, WORLDLINE_RETRY_RULE_V1,
)
from app.agent.foundation.subgraphs.worldline import stage_pack
from app.agent.handoff.hop_context import hop_source_scope
from app.llm.runtime import RequestLedger, RuntimeLimitError, active_ledger, active_manifest, agent_scope, estimate_tokens, request_scope
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter
from app.rag.query_understanding.analyzer import ExistingLLMQueryAnalysisRuntime, QUERY_ANALYSIS_PROMPT_V1
from app.rag.query_understanding.config import QueryUnderstandingConfig
from app.rag.query_understanding.analyzer import QueryAnalyzer
from tests.test_agent_pack_impl_a import ledger
from tests.test_agent_pack_impl_b import edge_harness, state
from tests.test_context_rag_source import source
from tests.test_worldline_generation import entity, graph, valid_branch_drafts
from tests.test_worldline_graph import EmptyRetriever, FakeMusicService, invoke


def response(text='{}'):
    return SimpleNamespace(output_text=text, status="completed", incomplete_details=None, output=[])


def render(pack):
    instructions = next(item.content for item in pack.items if item.type == "system")
    rows = [{"type": item.type, "content": item.content} for item in pack.items
            if item.type != "system" and item.source != "retry_feedback"]
    return [{"role": "user", "content": json.dumps(rows, ensure_ascii=False)}], instructions


async def prepare(phase="narrative", *, inputs=None, registry=None, instructions=None, **kwargs):
    prompt = {"narrative": NARRATIVE_SYSTEM_PROMPT, "branches": BRANCH_SYSTEM_PROMPT,
              "query_analysis": QUERY_ANALYSIS_PROMPT_V1}[phase]
    return await stage_pack.prepare_explorer_input(phase, query="继续音乐探索", instructions=instructions or prompt,
        schema={}, schema_name=phase, renderer=render, inputs=inputs, registry=registry or SourceRegistry([]), **kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase,prompt_id", [
    ("query_analysis", "query-analysis"), ("narrative", "explorer-narrative"), ("branches", "explorer-branches"),
])
async def test_each_actual_provider_phase_uses_recipe_and_static_prompt(monkeypatch, phase, prompt_id):
    monkeypatch.setattr(stage_pack, "RagSource", lambda: source(mode="empty"))
    if phase == "query_analysis":
        with patch("app.llm.client.create_structured_response", AsyncMock(return_value=response(
            '{"query_types":["exploratory"],"confidence":0.8}'))):
            await ExistingLLMQueryAnalysisRuntime().analyze("深入了解音乐")
    else:
        with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response",
                   AsyncMock(return_value=response())):
            await ProviderWorldLineRuntime().generate({"user_query": "继续音乐探索"},
                instructions=NARRATIVE_SYSTEM_PROMPT if phase == "narrative" else BRANCH_SYSTEM_PROMPT,
                schema={}, schema_name="worldline_" + phase, retry=False)
    audit = PackManifest.model_validate(active_manifest.get()).agent_context
    assert audit.recipe_id == "explorer/v1" and audit.recipe_version == "v1"
    assert audit.phase == phase and audit.owner_agent == "explorer"
    assert audit.prompts[0].prompt_id == prompt_id
    assert audit.prompts[0].version == ("v2" if phase == "narrative" else "v1")
    prompt = {"query_analysis": QUERY_ANALYSIS_PROMPT_V1, "narrative": NARRATIVE_SYSTEM_PROMPT,
              "branches": BRANCH_SYSTEM_PROMPT}[phase]
    assert audit.prompts[0].content_digest == "sha256:" + sha256(prompt.encode("utf-8")).hexdigest()


@pytest.mark.asyncio
async def test_query_analysis_never_collects_or_reuses_rag(monkeypatch):
    forbidden = Mock(side_effect=AssertionError("query phase collected RAG"))
    monkeypatch.setattr(stage_pack, "RagSource", forbidden)
    rag = SimpleNamespace(name="rag", collect=forbidden)
    result = await prepare("query_analysis", registry=SourceRegistry([rag]), inputs=AgentPackInputs(
        rag_pack=SimpleNamespace(items=[{"id": "old", "excerpt": "stale RAG"}]),
        continuity_context={"entry_candidates": [{"id": "stale", "type": "rag_evidence", "source": "rag",
                                                 "content": "stale RAG"}]}))
    assert not forbidden.called
    assert not any(item.type == "rag_evidence" for item in result.pack.items)
    assert "stale RAG" not in str(result.input_items)
    runtime = SimpleNamespace(analyze=AsyncMock(side_effect=AssertionError("rule hit called model")))
    await QueryAnalyzer(QueryUnderstandingConfig(analysis_version="v1", prompt_version="v1",
        llm_fallback_enabled=True), runtime=runtime).analyze("比较两首歌")
    assert not runtime.analyze.called


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["empty", "shadow", "unavailable"])
async def test_transitional_source_matches_old_provider_payload(monkeypatch, mode):
    provider = source(mode="hybrid" if mode == "unavailable" else mode)
    if mode == "unavailable":
        provider.binding = None
    monkeypatch.setattr(stage_pack, "RagSource", lambda: provider)
    payload = {"user_query": "继续音乐探索", "root_entity": {"name": "歌曲"}, "knowledge_available": False,
        "catalog_facts": [], "worldline_graph": {"edges": [{"evidence_ids": ["one"]}]},
        "sources": [{"id": "one", "title": "目录事实", "excerpt": "可靠原文", "source_type": "music_api",
                     "source": "目录"}, {"id": "two", "title": "重复", "excerpt": "可靠原文"}]}
    original = deepcopy(payload)
    dispatch = AsyncMock(return_value=response())
    with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response", dispatch):
        await ProviderWorldLineRuntime().generate(payload, instructions=NARRATIVE_SYSTEM_PROMPT,
            schema={}, schema_name="legacy-parity", retry=False)
        legacy = dispatch.await_args.args[:2]
        await ProviderWorldLineRuntime().generate(payload, instructions=NARRATIVE_SYSTEM_PROMPT,
            schema={}, schema_name="worldline_narrative", retry=False)
        actual = dispatch.await_args.args[:2]
    assert actual == legacy
    assert payload == original
    selection = active_manifest.get()["agent_context"]["source_selection"]
    legacy_rows = [row for row in selection if row["source"] == "rag_evidence"]
    assert legacy_rows and all("transitional:legacy-rag-pack/v1" in row["references"] for row in legacy_rows)


@pytest.mark.asyncio
async def test_formal_source_preferred_and_shared_quota_preserves_required_evidence():
    rag = source(contents=["正式证据" * 120, "正式另一证据" * 120, "正式第三证据" * 120])
    legacy = SimpleNamespace(items=[{"id": "parent-1", "excerpt": "旧证据"},
        {"id": "required", "excerpt": "必须保留事实" * 110},
        {"id": "unique-old", "excerpt": "旧独立证据" * 200}])
    limit = 1600
    policy = POLICIES[RouteKey.WORLDLINE].model_copy(update={"source_budgets": {"rag": limit, "rag_evidence": limit}})
    result = await prepare(registry=SourceRegistry([rag]), policy=policy,
        inputs=AgentPackInputs(rag_pack=legacy, rag_required_ids={"required"}))
    items = [item for item in result.pack.items if item.type == "rag_evidence"]
    assert any(item.source == "rag" and item.metadata["evidence_id"] == "parent-1" for item in items)
    assert not any(item.source == "rag_evidence" and item.metadata["evidence_id"] == "parent-1" for item in items)
    assert any(item.required and item.metadata["evidence_id"] == "required" for item in items)
    assert sum(item.token_count for item in items) <= limit
    audit = result.pack.manifest["sources"]["rag"]
    assert len(audit["chunks"]) == 3 and audit["model_usage"] and audit["collection"]
    assert audit["final_tokens"] == sum(item.token_count for item in items if item.source == "rag")
    assert result.pack.dropped_items and result.pack.total_tokens <= result.pack.budget


@pytest.mark.asyncio
async def test_combined_optional_pool_cannot_spend_two_times_5000(monkeypatch):
    # Exercise the shared 5000-token pool independently of the newer per-item
    # 900-token compression cap, which would make this fixture too small.
    from app.agent.foundation import rag_source
    config = rag_source.load_context_packing_config()
    config = config.model_copy(update={"compression": config.compression.model_copy(
        update={"max_evidence_tokens": 1900, "max_item_tokens": 2100})})
    monkeypatch.setattr(rag_source, "load_context_packing_config", lambda: config)
    rag = source(contents=["甲" * 1900, "乙" * 1900, "丙" * 1900])
    result = await prepare(registry=SourceRegistry([rag]), inputs=AgentPackInputs(rag_pack=SimpleNamespace(
        items=[{"id": "legacy-1", "excerpt": "丁" * 1900}, {"id": "legacy-2", "excerpt": "戊" * 1900}])))
    items = [item for item in result.pack.items if item.type == "rag_evidence"]
    assert sum(item.token_count for item in items) <= 5000
    assert {item.metadata["evidence_id"] for item in items} == {"parent-1", "parent-2"}
    assert all(item.source == "rag" for item in items)
    assert result.pack.dropped_items


@pytest.mark.asyncio
async def test_retry_exploration_rules_and_feedback_audit_preserve_instruction_order():
    feedback = "The previous object did not match the required schema. Validation code: test_only."
    base = EXPLORATION_BRANCH_BASE_V1 + "\n" + EXPLORATION_BRANCH_RULE_V1
    expected = base + "\n" + feedback + "\n" + WORLDLINE_RETRY_RULE_V1
    result = await prepare("branches", instructions=expected, exploration=True, retry=True)
    assert result.instructions.startswith(expected)
    audit = result.pack.manifest["agent_context"]
    assert [row["prompt_id"] for row in audit["prompts"]] == [
        "explorer-branches-exploration", "explorer-exploration-rules", "explorer-retry"]
    assert [row["variant"] for row in audit["prompts"]] == [None, "exploration", "retry"]
    assert feedback not in json.dumps(result.pack.manifest)
    row = next(row for row in audit["source_selection"] if row["source"] == "retry_feedback")
    assert row["final_tokens"] > 0
    assert row["references"] == ["structured-output-feedback:sha256:" + sha256(feedback.encode("utf-8")).hexdigest()]


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["query_analysis", "narrative", "branches"])
async def test_explorer_hop_keeps_committed_refs_and_bound_original_task(tmp_path, phase):
    h = await edge_harness(tmp_path / "hop.db", ("curator", "explorer"))
    before = h.store.query_task_history(h.session.task_id, limit=500)
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=60, run_id=h.run.run_id)
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        with request_scope(book), agent_scope("explorer"):
            provider = source(count=1)
            result = await prepare(phase, registry=SourceRegistry([ReceivedEnvelopeSource(), SessionViewSource(), provider]))
    audit = result.pack.manifest["agent_context"]
    assert audit["envelope"]["commit_ref"] and audit["envelope"]["selected_fields"]
    assert next(item.content for item in result.pack.items if item.type == "user_query") == h.run.pending_tasks[0].input_text
    assert provider.encoder.calls == (0 if phase == "query_analysis" else 1)
    if phase != "query_analysis":
        assert result.pack.manifest["sources"]["rag"]["collection"]["plan_id"]
    assert h.store.query_task_history(h.session.task_id, limit=500) == before


@pytest.mark.asyncio
async def test_rag_cost_reduces_final_budget_and_surviving_audit_is_not_lost():
    book = ledger(l2=6400)
    rag = source(contents=["资料甲" * 70, "资料乙" * 70, "资料丙" * 70])
    with request_scope(book), agent_scope("explorer"):
        before = book.remaining
        result = await prepare(registry=SourceRegistry([rag]))
        spent = sum(row["tokens"] for row in book.usage.values())
        assert spent > 0 and result.budget.total_tokens == before - spent
        assert result.pack.manifest["agent_context"]["budget_snapshot"]["owner_remaining"] == book.remaining
        assert result.pack.manifest["sources"]["rag"]["model_usage"]
        assert estimate_tokens({"input": result.input_items, "instructions": result.instructions}) <= result.budget.input_tokens


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [RagBudgetRefusal("token_limit"), RagAccountingFailure("accounting")])
async def test_rag_budget_and_accounting_failures_are_not_softened(error):
    provider = SimpleNamespace(name="rag", collect=Mock(side_effect=error))
    with pytest.raises(type(error)):
        await prepare(registry=SourceRegistry([provider]))


@pytest.mark.asyncio
async def test_expired_hop_rejects_before_collect(tmp_path):
    h = await edge_harness(tmp_path / "expired.db", ("curator", "explorer"))
    forbidden = Mock(side_effect=AssertionError("expired hop collected RAG"))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity) as snapshot:
        snapshot.lease.active = False
        with pytest.raises(SourceIntegrityError):
            await prepare(registry=SourceRegistry([SimpleNamespace(name="rag", collect=forbidden)]))
    assert not forbidden.called


@pytest.mark.asyncio
@pytest.mark.parametrize("label_tokens", [250, 500])
async def test_reselect_preserves_static_and_rag_audit_without_recollecting(label_tokens):
    rag = source(contents=["甲" * 80, "乙" * 80, "丙" * 80])
    seen = []
    def expensive_render(pack):
        selected = [item for item in pack.items if item.type == "rag_evidence"]
        seen.append([item.id for item in selected])
        system = next(item.content for item in pack.items if item.type == "system")
        return [{"role": "user", "content": json.dumps([
            {"excerpt": item.content, "label": "栏" * label_tokens} for item in selected], ensure_ascii=False)}], system
    default = POLICIES[RouteKey.WORLDLINE]
    # Keep enough room for three short evidence items after the current static
    # prompt, but force reselection once the provider framing adds long labels.
    counter = UnicodeHeuristicTokenCounter()
    input_limit = counter.count(NARRATIVE_SYSTEM_PROMPT) + 500
    policy = default.model_copy(update={
        "total_input_budget": default.reserved_output_tokens + default.safety_margin + input_limit})
    trace_context = {"task_id": "fixture:task", "session_id": "fixture:session", "request_id": "fixture:request"}
    result = await stage_pack.prepare_explorer_input("narrative", query="探索", instructions=NARRATIVE_SYSTEM_PROMPT,
        schema={}, schema_name="test", renderer=expensive_render, policy=policy, registry=SourceRegistry([rag]),
        trace_context=trace_context)
    assert len(seen) > 1 and set(seen[-1]).issubset(seen[0])
    assert rag.encoder.calls == 1
    manifest = result.pack.manifest
    assert manifest["agent_context"]["prompts"] and len(manifest["sources"]["rag"]["chunks"]) == 3
    assert len(manifest["sources"]["rag"]["model_usage"]) == 2
    assert manifest["sources"]["rag"]["candidate_tokens"] > manifest["sources"]["rag"]["final_tokens"]
    assert manifest["sources"]["rag"]["events"]
    assert all(all(getattr(event, key) == value for key, value in trace_context.items())
               for event in result.pack.trace.entries)


@pytest.mark.asyncio
async def test_public_sources_count_keeps_original_thirty_boundary():
    rag = source(count=10)
    result = await prepare(registry=SourceRegistry([rag]), inputs=AgentPackInputs(rag_pack=SimpleNamespace(
        items=[{"id": f"legacy-{index}", "excerpt": f"独立事实 {index}"} for index in range(30)])))
    assert sum(item.type == "rag_evidence" for item in result.pack.items) <= 30
    assert any(event.reason == "worldline_source_limit" for event in result.pack.trace.entries)


@pytest.mark.asyncio
async def test_formal_citations_reach_worldline_response_and_unknown_citation_still_retries(monkeypatch):
    collections = []
    def fresh_source():
        provider = source(count=1)
        collections.append(provider)
        return provider
    monkeypatch.setattr(stage_pack, "RagSource", fresh_source)
    calls = []
    async def dispatch(inputs, instructions, schema, *, schema_name, request_budget):
        calls.append((schema_name, json.loads(inputs[0]["content"])))
        if schema_name == "worldline_branches":
            return response(valid_branch_drafts())
        rows = calls[-1][1]["sources"]
        citation = next(row["id"] for row in rows if row["id"].startswith("knowledge:rag:"))
        return response(json.dumps({"narrative": f"真实检索资料说明了这首歌的创作背景。[{'unknown' if len(calls) == 1 else citation}]"},
                                   ensure_ascii=False))
    root = entity()
    related = [root.model_copy(update={"entity_id": "netease:artist:2", "entity_type": EntityType.ARTIST, "name": "歌手"}),
               root.model_copy(update={"entity_id": "netease:album:3", "entity_type": EntityType.ALBUM, "name": "专辑"})]
    workflow = create_worldline_subgraph(music_service=FakeMusicService(root, related),
        retriever=EmptyRetriever(), generator=StructuredWorldLineGenerator())
    with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response", dispatch):
        result = await invoke(workflow, "歌曲《晴天》的世界线")
    assert [name for name, _ in calls] == ["worldline_narrative", "worldline_narrative", "worldline_branches"]
    public = WorldLineResult.model_validate(result["task_result"].data["worldline_result"])
    assert public is not None
    citation_ids = {row.id for row in public.citations}
    assert citation_ids.issubset({row.id for row in public.sources})
    formal = next(row for row in public.sources if row.id in citation_ids)
    assert formal.metadata["doc_id"] == "doc-1" and formal.metadata["chunk_id"] == "child-1"
    assert "unknown" not in citation_ids
    assert len(collections) == 3 and all(provider.encoder.calls == 1 for provider in collections)
    assert not any(row["id"].startswith("knowledge:rag:") for row in calls[-1][1]["sources"]
                   if row.get("source_type") != "knowledge")


@pytest.mark.asyncio
async def test_actual_exploration_retry_keeps_rules_feedback_and_target_guard(monkeypatch):
    monkeypatch.setattr(stage_pack, "RagSource", lambda: source(mode="empty"))
    observed = []
    valid = json.loads(valid_branch_drafts())
    valid["branches"] = [row for row in valid["branches"] if row["target_node_id"] != entity().entity_id]
    invalid = {"branches": [{**valid["branches"][0], "target_node_id": "missing:node"}]}
    async def dispatch(inputs, instructions, schema, **kwargs):
        observed.append((instructions, deepcopy(active_manifest.get())))
        return response(json.dumps(invalid if len(observed) == 1 else valid, ensure_ascii=False))
    with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response", dispatch):
        branches = await StructuredWorldLineGenerator().generate_exploration_branches(
            entity(), graph(), [], visited_nodes=[entity().entity_id], user_query="继续探索")
    assert len(observed) == 2 and len(branches) == 2
    assert all(branch.target_entity.id != entity().entity_id for branch in branches)
    base = EXPLORATION_BRANCH_BASE_V1 + "\n" + EXPLORATION_BRANCH_RULE_V1
    assert observed[0][0] == base
    assert observed[1][0].startswith(base + "\n") and observed[1][0].endswith("\n" + WORLDLINE_RETRY_RULE_V1)
    first, second = [row[1]["agent_context"] for row in observed]
    assert first["phase"] == second["phase"] == "branches"
    assert [row["variant"] for row in first["prompts"]] == [None, "exploration"]
    assert [row["variant"] for row in second["prompts"]] == [None, "exploration", "retry"]
    assert [row["content_digest"] for row in second["prompts"]] == [
        "sha256:" + sha256(text.encode("utf-8")).hexdigest()
        for text in (EXPLORATION_BRANCH_BASE_V1, EXPLORATION_BRANCH_RULE_V1, WORLDLINE_RETRY_RULE_V1)]
    feedback = observed[1][0][len(base) + 1:-(len(WORLDLINE_RETRY_RULE_V1) + 1)]
    assert feedback and feedback not in json.dumps(observed[1][1])
    assert any(row["source"] == "retry_feedback" for row in second["source_selection"])


@pytest.mark.asyncio
async def test_actual_query_stage_reads_hop_but_keeps_current_analysis_query(tmp_path):
    h = await edge_harness(tmp_path / "query-hop.db", ("curator", "explorer"))
    book = RequestLedger(h.run.usage, h.run.current_agent, h.run.limits, deadline=60, run_id=h.run.run_id)
    dispatch = AsyncMock(return_value=response('{"query_types":["exploratory"],"confidence":0.8}'))
    async with hop_source_scope(state(h), h.run, journal=h.journal, identity=h.identity):
        with request_scope(book), patch("app.llm.client.create_structured_response", dispatch):
            await ExistingLLMQueryAnalysisRuntime().analyze("音乐背景的当前问题")
            audit = deepcopy(active_manifest.get()["agent_context"])
    projected = json.loads(dispatch.await_args.args[0][0]["content"])
    assert projected["query"] == "音乐背景的当前问题"
    assert {"type": "active_task", "content": h.run.pending_tasks[0].input_text} in projected["context"]
    assert audit["phase"] == "query_analysis" and audit["envelope"]["selected_fields"]
    assert {row["source"] for row in audit["source_selection"]}.isdisjoint({"rag", "rag_evidence"})


@pytest.mark.asyncio
async def test_source_stop_and_insufficient_budget_do_not_collect_or_dispatch():
    forbidden = Mock(side_effect=AssertionError("stopped source collected"))
    provider = SimpleNamespace(name="rag", collect=forbidden)
    book = ledger()
    book.source_stops.add("explorer")
    with request_scope(book), agent_scope("explorer"):
        result = await prepare(registry=SourceRegistry([provider]))
        assert not any(item.source == "rag" for item in result.pack.items)
    with request_scope(ledger(l2=500)), agent_scope("explorer"):
        with pytest.raises(RuntimeLimitError):
            await prepare(registry=SourceRegistry([provider]))
    assert not forbidden.called


@pytest.mark.asyncio
async def test_concurrent_stage_manifests_and_grounding_sources_are_call_local(monkeypatch):
    monkeypatch.setattr(stage_pack, "RagSource", lambda: source(count=1, contents=[active_ledger.get().run_id]))
    async def dispatch(*args, **kwargs):
        await asyncio.sleep(0)
        return response()
    async def call(label):
        book = ledger()
        book.run_id = label
        with request_scope(book), stage_pack.selected_evidence_scope() as evidence:
            await ProviderWorldLineRuntime().generate({"user_query": label}, instructions=NARRATIVE_SYSTEM_PROMPT,
                schema={}, schema_name="worldline_narrative", retry=False)
            await asyncio.sleep(0)
            return evidence.sources, deepcopy(active_manifest.get()), book.usage
    with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response", dispatch):
        results = await asyncio.gather(call("run:left"), call("run:right"))
    for label, (sources, manifest, usage) in zip(("run:left", "run:right"), results):
        assert sources and label in sources[0].excerpt
        assert manifest["agent_context"]["phase"] == "narrative"
        assert manifest["sources"]["rag"]["model_usage"] and usage["explorer"]["tokens"] > 0
    assert stage_pack.active_evidence.get() is None


@pytest.mark.asyncio
async def test_query_stage_does_not_count_the_same_required_query_twice():
    query = "曲" * 3000
    dispatch = AsyncMock(return_value=response('{"query_types":["exploratory"],"confidence":0.8}'))
    with request_scope(ledger(l2=8000)), patch("app.llm.client.create_structured_response", dispatch):
        await ExistingLLMQueryAnalysisRuntime().analyze(query)
        selection = active_manifest.get()["agent_context"]["source_selection"]
    assert json.loads(dispatch.await_args.args[0][0]["content"]) == {"query": query}
    assert sum(row["type"] == "user_query" for row in selection) == 1
    assert not any(row["type"] == "structured_output" for row in selection)


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["narrative", "branches"])
@pytest.mark.parametrize("error", [SourceIntegrityError(), RagBudgetRefusal("token_limit"), RagAccountingFailure("accounting")])
async def test_generation_rejects_hard_source_failure_without_retry_or_catalog_fallback(monkeypatch, phase, error):
    provider = SimpleNamespace(name="rag", collect=Mock(side_effect=error))
    monkeypatch.setattr(stage_pack, "RagSource", lambda: provider)
    dispatch = AsyncMock(side_effect=AssertionError("hard source failure reached model"))
    generator = StructuredWorldLineGenerator()
    with patch("app.agent.foundation.subgraphs.worldline.generation.create_generation_response", dispatch):
        with pytest.raises(type(error)):
            if phase == "narrative":
                await generator.generate_narrative(entity(), MusicEntityFacts(entity=entity()), [], graph(), [])
            else:
                await generator.generate_exploration_branches(entity(), graph(), [], visited_nodes=[entity().entity_id])
    assert provider.collect.call_count == 1 and not dispatch.called


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [SourceIntegrityError(), RagBudgetRefusal("token_limit"), RagAccountingFailure("accounting")])
async def test_query_fallback_does_not_swallow_hard_source_failure(error):
    config = QueryUnderstandingConfig(analysis_version="v1", prompt_version="v1", llm_fallback_enabled=True)
    prepare_stage = AsyncMock(side_effect=error)
    dispatch = AsyncMock(side_effect=AssertionError("hard source failure reached model"))
    with patch.object(stage_pack, "prepare_explorer_input", prepare_stage), patch("app.llm.client.create_structured_response", dispatch):
        with pytest.raises(type(error)):
            await QueryAnalyzer(config, runtime=ExistingLLMQueryAnalysisRuntime()).analyze("新方向")
    assert prepare_stage.await_count == 1 and not dispatch.called
