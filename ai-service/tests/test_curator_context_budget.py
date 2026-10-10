"""Curator projects evidence and uses the existing selector before dispatch."""

from copy import deepcopy
from hashlib import sha256
import json

import pytest
from langchain_core.messages import AIMessage

from app.agent.foundation.agent_pack import AgentPackBuilder
from app.agent.foundation.context_engineering import POLICIES
from app.agent.foundation.prompt_bindings import PROMPT_BINDINGS
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.models import TaskStatus
from app.agent.handoff.curator import SELECTION_PROMPT, SongSelection, _structured
from app.agent.handoff.curator_context import prepare_curation_pack
from app.llm.runtime import estimate_tokens
from tests.test_agent_pack_impl_c import BoundaryModel
from tests.test_curator_curation_v1 import Catalog, curate, responses
from tests.test_curator_curation_v2 import install_era
from tests import test_write_reconciliation as write_helpers

journal = write_helpers.journal


def candidate(identity, *, name="目录曲目"):
    return {"song_id": identity, "name": name, "artists": ["目录歌手"],
            "album": "目录专辑", "liked": False, "discovered": False,
            "search_sources": [{"tool": "search_music", "keyword": "雨夜"}],
            "knowledge_sources": []}


@pytest.mark.asyncio
async def test_40_catalog_tracks_keep_20_evidenced_selections_inside_final_request_budget(monkeypatch, journal):
    packs = []
    original = AgentPackBuilder.build

    def record(self, value, **kwargs):
        pack = original(self, value, **kwargs)
        packs.append(pack)
        return pack

    monkeypatch.setattr(AgentPackBuilder, "build", record)
    catalog = Catalog(monkeypatch, scene="90年代港乐", count=40)
    install_era(monkeypatch, catalog)
    model = responses(catalog, ids=[song.song_id for song in catalog.songs[:20]])
    result = await curate("生成90年代港乐歌单，20首", catalog, journal, model=model)
    assert result.status is TaskStatus.SUCCESS, result.content
    assert len(catalog.members) == 20 and len(packs) == 2
    policy = POLICIES[RouteKey.TOOL_CALLING]
    for pack in packs:
        assert estimate_tokens({"messages": pack.messages, "tools": []}) + (
            policy.reserved_output_tokens + policy.safety_margin
        ) <= policy.total_input_budget
        assert pack.manifest["agent_context"]["owner_agent"] == "curator"
    visible = {int(item.id.split(":", 1)[1]) for item in packs[-1].items
               if item.id.startswith("curation-candidate:")}
    assert set(catalog.members) <= visible
    assert packs[-1].compressed_items
    for identity in catalog.members:
        evidence = result.data["curation"]["knowledge_sources"][str(identity)]
        assert any(source["excerpt"] for row in evidence for source in row["sources"])


@pytest.mark.asyncio
async def test_optional_candidate_drop_is_atomic_and_model_cannot_reintroduce_its_id():
    payload = {"target_count": 1, "candidates": [candidate(1, name="曲" * 20000), candidate(2)]}
    before = deepcopy(payload)
    model = BoundaryModel(AIMessage(content='{"song_ids":[1]}'))
    pack, _, visible = prepare_curation_pack("雨夜歌单", SELECTION_PROMPT, payload, {}, model)
    assert visible == {2} and "curation-candidate:1" in pack.dropped_items
    assert any(row.reason == "total_budget_exceeded" for row in pack.trace.for_item("curation-candidate:1"))
    assert all(json.loads(item.content)["candidates"][0]["song_id"] == 2
               for item in pack.items if item.id.startswith("curation-candidate:"))
    with pytest.raises(ValueError, match="budget-selected catalog"):
        await _structured(model, "雨夜歌单", SELECTION_PROMPT, SongSelection, payload, {})
    assert len(model.calls) == 1 and payload == before


@pytest.mark.asyncio
async def test_required_input_over_budget_prevents_model_dispatch():
    model = BoundaryModel(AIMessage(content='{"song_ids":[]}'))
    with pytest.raises(ValueError, match="budget"):
        await _structured(model, "雨夜歌单", SELECTION_PROMPT, SongSelection,
                          {"scene": "曲" * 20000, "target_count": 1,
                           "candidates": [candidate(1)]}, {})
    assert model.calls == []


@pytest.mark.parametrize("reference,digest", [
    ("curator-plan/v1", "310f5a80de4ee812eec120f0a44eac01a83a62f6165ac345393d15f7e3a89d3c"),
    ("curator-selection/v1", "c59157e87caf237f62b769c4e6b9736bdcaad0e464dcff2f378f7e9a4959b734"),
])
def test_curator_prompt_anchors_have_immutable_utf8_digests(reference, digest):
    binding = PROMPT_BINDINGS[reference]
    assert sha256(binding.text.encode("utf-8")).hexdigest() == digest
    assert binding.manifest.content_digest == "sha256:" + digest
