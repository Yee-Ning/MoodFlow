"""Final JSON framing, required tool pairs and call-scoped pack selection."""
from copy import deepcopy
import asyncio
import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.foundation.agent_history_source import bind_produced_message
from app.agent.foundation.agent_pack import AgentPackBuilder, AgentPackInputs
from app.agent.foundation.context_engineering import ContextAssembler, _compress_tool_json
from app.agent.foundation.context_contracts import PackManifest
from app.agent.foundation.subgraphs.tool_calling.assistant import answer_tool_calling_task
from app.llm.runtime import AccountedChatModel, RuntimeLimitError, active_manifest, estimate_tokens
from app.rag.chunk.token_counter import UnicodeHeuristicTokenCounter
from tests.test_agent_pack_impl_c import BoundaryModel, invocation
from tests.test_handoff_runtime import player_context


class SchemaModel(BoundaryModel):
    def __init__(self, description):
        super().__init__(AIMessage(content="资料已经核实。"))
        self.kwargs = {"tools": [{"type": "object", "description": description,
                                   "properties": {"keyword": {"type": "string"}}}]}


def five_reads(value, *, name_repetitions=120):
    history = []
    for index in range(5):
        call_id = f"read-{index}"
        history.append(bind_produced_message(AIMessage(content="", tool_calls=[{
            "id": call_id, "name": "search_music", "args": {"keyword": f"目录{index}"}, "type": "tool_call",
        }]), value))
        content = json.dumps({"songs": [{"song_id": 100 + j, "name": "\\\"" * name_repetitions,
                                         "artists": ["目录歌手"], "album": "目录专辑"} for j in range(10)]}, ensure_ascii=False)
        history.append(bind_produced_message(ToolMessage(content=content, tool_call_id=call_id, name="search_music"), value))
    return history


@pytest.mark.asyncio
async def test_five_successful_observations_are_reselected_before_final_json_guard(monkeypatch):
    value = invocation("curator", inputs=AgentPackInputs(continuity_context={"user_id": "alice"}))
    history = five_reads(value, name_repetitions=80)
    before = deepcopy(history)
    model = SchemaModel("工具定义" * 1200)
    packs = []
    from app.agent.foundation import business_pack
    original_build = business_pack.build_business_pack
    def record(invocation):
        pack = original_build(invocation)
        packs.append(pack)
        return pack
    monkeypatch.setattr(business_pack, "build_business_pack", record)
    result = await answer_tool_calling_task(history, "规划有来源的三节点旅程", player_context(), [],
        model=model, continuity_context={"user_id": "alice"}, route_task_id=value.task.task_id,
        invocation_run_id=value.run_id)
    assert result.content == "资料已经核实。" and len(model.calls) == 1
    assert estimate_tokens({"messages": packs[0].messages, "tools": model.kwargs["tools"]}) + 4096 + 256 > 16384
    sent = model.calls[0]
    assert history == before
    assert {m.tool_call_id for m in sent if isinstance(m, ToolMessage)} == {f"read-{i}" for i in range(5)}
    assert {c["id"] for m in sent if isinstance(m, AIMessage) for c in m.tool_calls} == {f"read-{i}" for i in range(5)}
    assert estimate_tokens({"messages": sent, "tools": model.kwargs["tools"]}) + 4096 + 256 <= 16384
    for m in sent:
        if isinstance(m, ToolMessage):
            assert json.loads(m.content)["songs"][0]["song_id"] == 100


@pytest.mark.asyncio
async def test_five_required_pairs_with_oversized_minimum_cannot_bypass_guard(monkeypatch):
    value = invocation("curator", inputs=AgentPackInputs(continuity_context={"user_id": "alice"}))
    history = five_reads(value)
    before = deepcopy(history)
    model = SchemaModel("工具定义" * 1200)
    packs = []
    from app.agent.foundation import business_pack
    original_build = business_pack.build_business_pack
    def record(invocation):
        pack = original_build(invocation)
        packs.append(pack)
        return pack
    monkeypatch.setattr(business_pack, "build_business_pack", record)
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        await answer_tool_calling_task(history, "规划有来源的三节点旅程", player_context(), [],
            model=model, continuity_context={"user_id": "alice"}, route_task_id=value.task.task_id,
            invocation_run_id=value.run_id)
    pack = packs[0]
    counter = UnicodeHeuristicTokenCounter()
    minimal = [item.model_copy(update={"content": _compress_tool_json(item.content, 0, counter)})
               if item.type == "tool_result" else item for item in pack.items if item.required]
    messages = ContextAssembler().assemble(minimal, intent=pack.intent)
    assert estimate_tokens({"messages": messages, "tools": model.kwargs["tools"]}) > 12032
    assert model.calls == [] and history == before


@pytest.mark.asyncio
async def test_optional_escape_heavy_context_is_dropped_without_resurrecting_sources(caplog):
    caplog.set_level("INFO", logger="app.llm.runtime")
    value = invocation("curator", inputs=AgentPackInputs(business_state="\\\"" * 3000,
        messages=[HumanMessage(content="DROPPED_HISTORY_MARKER" + "字" * 30000)]))
    pack = AgentPackBuilder().build(value)
    original = deepcopy(pack)
    model = SchemaModel("工具定义" * 1600)
    assert any(item.id == "business_state" for item in pack.items)
    assert "history:0" in pack.dropped_items
    assert estimate_tokens({"messages": pack.messages, "tools": model.kwargs["tools"]}) > 12032
    await AccountedChatModel(model).ainvoke(pack.messages)
    assert pack == original and len(model.calls) == 1
    assert model.calls[0][-1].content == value.task.input_text
    assert "DROPPED_HISTORY_MARKER" not in str(model.calls[0])
    assert any(isinstance(m, SystemMessage) for m in model.calls[0])
    record = next(r.getMessage().split("context_engineering.call_manifest ", 1)[1]
                  for r in caplog.records if r.getMessage().startswith("context_engineering.call_manifest "))
    manifest = json.loads(record)
    audit = manifest["agent_context"]
    assert all(row["item_id"] in {"system", "query"} for row in audit["source_selection"])
    assert manifest["input_tokens"] + manifest["max_output_tokens"] + 256 <= 16384
    for name, source in pack.manifest["sources"].items():
        assert ("chunks" in manifest["sources"][name]) == ("chunks" in source)


@pytest.mark.asyncio
async def test_required_material_and_schema_that_cannot_fit_refuse_before_dispatch():
    value = invocation("curator")
    pack = AgentPackBuilder().build(value)
    model = SchemaModel("工具定义" * 10000)
    before = deepcopy(pack)
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        await AccountedChatModel(model).ainvoke(pack.messages)
    assert not model.calls and pack == before


@pytest.mark.asyncio
async def test_unprepared_request_still_uses_original_final_guard():
    model = SchemaModel("")
    messages = [HumanMessage(content="字" * 20000)]
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        await AccountedChatModel(model).ainvoke(messages)
    assert not model.calls


@pytest.mark.asyncio
async def test_foreign_equal_message_list_cannot_consume_pack_selection():
    pack = AgentPackBuilder().build(invocation("curator"))
    model = SchemaModel("工具定义" * 10000)
    with pytest.raises(RuntimeLimitError, match="token_limit"):
        await AccountedChatModel(model).ainvoke(deepcopy(pack.messages))
    assert not model.calls


@pytest.mark.asyncio
async def test_final_manifest_keeps_prompt_identity_and_selected_sources(caplog):
    caplog.set_level("INFO", logger="app.llm.runtime")
    pack = AgentPackBuilder().build(invocation("curator"))
    original = PackManifest.model_validate(pack.manifest)
    model = SchemaModel("工具定义" * 300)
    await AccountedChatModel(model).ainvoke(pack.messages)
    record = next(r.getMessage().split("context_engineering.call_manifest ", 1)[1]
                  for r in caplog.records if r.getMessage().startswith("context_engineering.call_manifest "))
    manifest = json.loads(record)
    assert manifest["agent_context"] == original.agent_context.model_dump(mode="json")
    assert active_manifest.get() is None
    assert {r["item_id"] for r in manifest["agent_context"]["source_selection"]} == {x.id for x in pack.items}


@pytest.mark.asyncio
@pytest.mark.parametrize("extra,accepted", [(0, True), (1, False)])
async def test_final_serialized_budget_accepts_equality_and_refuses_one_token_over(extra, accepted):
    pack = AgentPackBuilder().build(invocation("curator"))
    model = SchemaModel("")
    accounted = AccountedChatModel(model)
    fixed = estimate_tokens(accounted.request_payload(pack.messages))
    model.kwargs["tools"][0]["description"] = "字" * (12032 - fixed + extra)
    assert estimate_tokens(accounted.request_payload(pack.messages)) == 12032 + extra
    if accepted:
        await accounted.ainvoke(pack.messages)
        assert len(model.calls) == 1
    else:
        with pytest.raises(RuntimeLimitError, match="token_limit"):
            await accounted.ainvoke(pack.messages)
        assert model.calls == []


@pytest.mark.asyncio
async def test_concurrent_calls_keep_each_pack_query_and_budget():
    async def answer(label):
        task = invocation("curator").task.model_copy(update={"task_id": label, "input_text": label})
        value = invocation("curator", task=task, run_id=label,
                           inputs=AgentPackInputs(business_state="\\\"" * 3000))
        pack = AgentPackBuilder().build(value)
        await asyncio.sleep(0)
        model = SchemaModel("工具定义" * 1600)
        await AccountedChatModel(model).ainvoke(pack.messages)
        assert model.calls[0][-1].content == label
        assert estimate_tokens({"messages": model.calls[0], "tools": model.kwargs["tools"]}) <= 12032
    await asyncio.gather(answer("query:one"), answer("query:two"))
