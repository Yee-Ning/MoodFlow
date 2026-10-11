"""Query selection against immutable Journal facts and original budget contracts."""

from types import SimpleNamespace

import pytest

from app.agent.foundation.long_horizon.coordinator import ContinuityCoordinator
from app.agent.foundation.long_horizon.models import ContinuityIdentity, HistoryEventType, HistoryPointer
from app.agent.foundation.long_horizon.public_events import PublicDecision, PublicText
from app.agent.foundation.long_horizon.service import LongHorizonContextService
from app.agent.foundation.long_horizon.store import SQLiteContinuityStore


@pytest.fixture
def history(tmp_path):
    store = SQLiteContinuityStore(tmp_path / "query.db")
    coordinator = ContinuityCoordinator(LongHorizonContextService(store))
    session = coordinator.service.open_conversation("query-drilldown")
    identity = ContinuityIdentity(conversation_id=session.conversation_id, continuity_task_id=session.task_id,
        session_id=session.session_id, request_id="query", graph_mode="foundation")
    return SimpleNamespace(store=store, coordinator=coordinator, reader=coordinator.history,
                           session=session, identity=identity)


def message(h, key, text, *, identity=None):
    return h.reader.public_text(identity or h.identity, key, HistoryEventType.USER_MESSAGE, text)


def refs(pack):
    return [(item["ref"]["kind"], item["ref"]["target_id"]) for item in pack.items]


def bounded(h, pack, tokens=800, bytes_limit=8192):
    serialized = pack.model_dump_json()
    assert pack.tokens == h.reader.counter.count(serialized) <= tokens
    assert pack.bytes == len(serialized.encode()) <= bytes_limit


def test_late_important_event_is_retrieved_on_same_dataset_where_original_read_misses(history):
    h = history
    early = [message(h, str(index), "前期常规聊天") for index in range(125)]
    important = h.coordinator.writer.append_public_event(h.identity, "late-decision", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="studio", kind="constraint", text="蓝莓歌单必须使用录音室版本，禁止现场版"))
    before = [row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=200)]
    old = h.reader.read(h.identity)
    selected = h.reader.read(h.identity, query="蓝莓歌单为什么禁止现场版？")
    assert ("event", important.event_id) not in refs(old)
    assert all(target in {row.event_id for row in early} for _, target in refs(old))
    assert refs(selected) == [("event", important.event_id)]
    bounded(h, selected)
    assert before == [row.model_dump_json() for row in h.store.query_task_history(h.session.task_id, limit=200)]


def test_rank_pages_have_no_duplicates_or_omissions_and_do_not_follow_event_sequence(history):
    h = history
    rows = [message(h, str(index), "blueberry archive" + (" studio" if index % 5 == 0 else "")) for index in range(27)]
    cursor, taken, cursors = 0, [], []
    while True:
        pack = h.reader.read(h.identity, query="blueberry studio", cursor=cursor, count=2, tokens=320, bytes_limit=2000)
        bounded(h, pack, 320, 2000)
        taken.extend(refs(pack))
        assert pack.next_cursor not in cursors
        cursors.append(pack.next_cursor)
        cursor = pack.next_cursor
        if not pack.truncated:
            break
    assert len(taken) == len(set(taken)) == len(rows)
    assert {target for _, target in taken} == {row.event_id for row in rows}
    assert taken[0] == ("event", rows[25].event_id)
    assert taken[-1] == ("event", rows[1].event_id)
    terminal = h.reader.read(h.identity, query="blueberry studio", cursor=cursor, count=2)
    assert not terminal.items and not terminal.truncated and terminal.next_cursor == cursor


def test_snapshot_stays_stable_across_new_events_and_reader_recreation(history):
    from app.agent.foundation.long_horizon.drilldown import HistoryDrilldown
    h = history
    rows = [message(h, str(index), "blueberry") for index in range(5)]
    first = h.reader.read(h.identity, query="blueberry", count=1)
    late = message(h, "appended", "blueberry blueberry")
    # No in-memory page cache is needed; repeat/recreate uses the same snapshot.
    again = HistoryDrilldown(h.coordinator.writer)
    cursor, taken = first.next_cursor, refs(first)
    while True:
        page = again.read(h.identity, query="blueberry", cursor=cursor, count=1)
        taken.extend(refs(page))
        cursor = page.next_cursor
        if not page.truncated:
            break
    assert len(taken) == len(set(taken)) == len(rows)
    assert ("event", late.event_id) not in taken
    assert ("event", late.event_id) in refs(again.read(h.identity, query="blueberry", count=1))
    replay = again.read(h.identity, query="blueberry", cursor=first.next_cursor, count=1)
    assert replay.items == again.read(h.identity, query="blueberry", cursor=first.next_cursor, count=1).items


@pytest.mark.parametrize("limit", ["tokens", "bytes"])
def test_oversized_best_match_cannot_trap_smaller_later_matches_on_empty_page(history, limit):
    h = history
    giant = message(h, "giant", "blueberry studio " + "很长的公开材料" * 400)
    small = message(h, "small", "blueberry")
    kwargs = {"tokens": 250} if limit == "tokens" else {"bytes_limit": 900}
    pack = h.reader.read(h.identity, query="blueberry studio", count=1, **kwargs)
    assert refs(pack) == [("event", small.event_id)]
    assert ("event", giant.event_id) not in refs(pack) and not pack.truncated
    bounded(h, pack, kwargs.get("tokens", 800), kwargs.get("bytes_limit", 8192))
    end = h.reader.read(h.identity, query="blueberry studio", cursor=pack.next_cursor, **kwargs)
    assert not end.items and not end.truncated and end.next_cursor == pack.next_cursor


def test_only_oversized_match_finishes_instead_of_returning_retry_forever(history):
    h = history
    message(h, "giant", "blueberry " + "公开材料" * 1000)
    pack = h.reader.read(h.identity, query="blueberry", tokens=250)
    assert pack.next_cursor != 0 and not pack.items and not pack.truncated
    end = h.reader.read(h.identity, query="blueberry", cursor=pack.next_cursor, tokens=250)
    assert end.next_cursor == pack.next_cursor and not end.truncated


@pytest.mark.parametrize("query", [None, "", "   "])
def test_no_query_preserves_original_sequence_cursor_and_public_pack(history, query):
    h = history
    rows = [message(h, str(index), "事实") for index in range(8)]
    original = h.reader.read(h.identity, count=2, tokens=200, bytes_limit=2000)
    fallback = h.reader.read(h.identity, query=query, count=2, tokens=200, bytes_limit=2000)
    assert original.model_dump_json() == fallback.model_dump_json()
    assert refs(fallback) == [("event", row.event_id) for row in rows[:len(fallback.items)]]
    assert fallback.next_cursor == rows[len(fallback.items) - 1].event_seq
    next_page = h.reader.read(h.identity, cursor=fallback.next_cursor, query=query, count=2, tokens=200, bytes_limit=2000)
    assert set(refs(fallback)).isdisjoint(refs(next_page))
    bounded(h, fallback, 200, 2000)


def test_query_without_match_is_empty_and_does_not_invent_refs(history):
    h = history
    message(h, "unrelated", "录音室歌曲")
    for query in ("水星行星", "继续刚才那个任务", "!!!"):
        pack = h.reader.read(h.identity, query=query)
        assert not pack.items and not pack.truncated and not pack.missing_refs
        bounded(h, pack)


def test_type_weight_then_recency_and_normalization_are_deterministic(history):
    h = history
    decision = h.coordinator.writer.append_public_event(h.identity, "decision", HistoryEventType.USER_DECISION,
        PublicDecision(decision_id="decision", text="BLUEBERRY"))
    older = message(h, "older", "blueberry")
    newer = message(h, "newer", "Blueberry")
    first = h.reader.read(h.identity, query="ＢＬＵＥＢＥＲＲＹ", count=1)
    assert refs(first) == [("event", decision.event_id)]
    second = h.reader.read(h.identity, query="blueberry", cursor=first.next_cursor, count=1)
    assert refs(second) == [("event", newer.event_id)]
    third = h.reader.read(h.identity, query="blueberry", cursor=second.next_cursor, count=1)
    assert refs(third) == [("event", older.event_id)]


def test_latin_and_numeric_entities_match_complete_tokens(history):
    h = history
    expected = message(h, "expected", "song 1984 blueberry")
    message(h, "substring", "song 19840 blueberryish")
    assert refs(h.reader.read(h.identity, query="1984 blueberry")) == [("event", expected.event_id)]


def test_query_cursor_is_bound_to_query_task_and_explicit_pointer_set(history):
    h = history
    row = message(h, "row", "blueberry")
    cursor = h.reader.read(h.identity, query="blueberry").next_cursor
    with pytest.raises(ValueError, match="scope/query mismatch"):
        h.reader.read(h.identity, query="different", cursor=cursor)
    other = h.coordinator.service.open_conversation("other")
    identity = h.identity.model_copy(update={"conversation_id": other.conversation_id,
        "continuity_task_id": other.task_id, "session_id": other.session_id})
    with pytest.raises(ValueError, match="scope/query mismatch"):
        h.reader.read(identity, query="blueberry", cursor=cursor)
    with pytest.raises(ValueError, match="opaque query cursor"):
        h.reader.read(h.identity, query="blueberry", cursor=row.event_seq)
    with pytest.raises(ValueError, match="requires a nonblank query"):
        h.reader.read(h.identity, cursor=cursor)
    with pytest.raises(ValueError, match="scope/query mismatch"):
        h.reader.read(h.identity, query="blueberry", cursor=cursor,
            pointers=(HistoryPointer(kind="event", target_id=row.event_id),))


def test_artifact_aliases_and_repeated_explicit_pointers_are_deduplicated(history):
    h = history
    event = message(h, "artifact", "blueberry " + "公开文本" * 3000)
    pointer = HistoryPointer.model_validate(event.payload["artifact_ref"])
    h.coordinator.writer.append_public_event(h.identity, "alias", HistoryEventType.ASSISTANT_MESSAGE,
        PublicText(artifact_ref=pointer))
    implicit = h.reader.read(h.identity, query="blueberry")
    assert refs(implicit) == [("artifact", pointer.target_id)]
    explicit = h.reader.read(h.identity, query="blueberry", pointers=(pointer, pointer), count=1)
    assert refs(explicit) == [("artifact", pointer.target_id)]
    expanded = h.reader.read(h.identity, query="blueberry", pointers=(pointer,), expand_artifacts=True)
    assert not expanded.items and not expanded.truncated
    bounded(h, implicit)


def test_query_selection_enforces_scope_safe_artifacts_and_hash(history):
    h = history
    other = h.coordinator.service.open_conversation("other")
    identity = h.identity.model_copy(update={"conversation_id": other.conversation_id,
        "continuity_task_id": other.task_id, "session_id": other.session_id})
    foreign = message(h, "foreign", "blueberry", identity=identity)
    assert not h.reader.read(h.identity, query="blueberry").items
    with pytest.raises(ValueError, match="another task"):
        h.reader.read(h.identity, query="blueberry", pointers=(HistoryPointer(kind="event", target_id=foreign.event_id),))
    event = message(h, "safe-artifact", "blueberry " + "公开文本" * 3000)
    ref = event.payload["artifact_ref"]["target_id"]
    # Fault injection mutates only this test's artifact; the reader must reject it.
    artifact = h.store.get_artifact(ref)
    broken = artifact.model_copy(update={"content_hash": "sha256:" + "0" * 64})
    h.store.get_artifact = lambda target: broken if target == ref else None
    with pytest.raises(ValueError, match="hash changed"):
        h.reader.read(h.identity, query="blueberry")


@pytest.mark.parametrize("kwargs", [dict(tokens=801), dict(bytes_limit=8193), dict(count=21),
    dict(cursor=-1), dict(query="x" * 4001), dict(query=object()), dict(tokens=1), dict(bytes_limit=1)])
def test_query_budget_and_cursor_validation_are_explicit(history, kwargs):
    with pytest.raises(ValueError):
        history.reader.read(history.identity, **{"query": "blueberry", **kwargs})


@pytest.mark.asyncio
async def test_coordinator_queries_history_while_using_inherited_session_projection(history, monkeypatch):
    from app.agent.checkpointing import create_memory_checkpointer
    from app.agent.foundation.long_horizon.models import HandoffDraft, RolloverReason
    from app.agent.foundation.main_graph import build_main_graph
    from app.agent.foundation.routing import RouteKey, RoutingContext

    h = history
    relevant = message(h, "blueberry", "blueberry: retained candidate")
    message(h, "unrelated", "unrelated concert note")
    rolled = h.coordinator.service.rollover(
        h.session.session_id, HandoffDraft(goal="retain original goal", current_stage="awaiting confirmation"),
        RolloverReason.EXPLICIT_CHECKPOINT,
    )
    reads = []

    def read_projection(session_id, handoff_id):
        reads.append((session_id, handoff_id))
        return h.store.get_handoff(handoff_id)

    monkeypatch.setattr(h.store, "read_session_projection", read_projection, raising=False)
    graph = build_main_graph(development_routes=[RouteKey.GENERAL_CHAT]).compile(
        checkpointer=create_memory_checkpointer(),
    )
    call = await h.coordinator.begin_call(
        graph=graph, graph_mode="foundation", thread_id=h.session.conversation_id,
        message="blueberry", routing_context=RoutingContext(), request_id="combined",
    )
    try:
        context = call.graph_input["continuity_context"]
        assert reads == [(rolled.new_session.session_id, rolled.new_session.inherited_handoff_id)]
        assert context["handoff"]["goal"] == "retain original goal"
        assert any(relevant.event_id in str(item) for item in context["key_events"])
        assert "unrelated concert note" not in str(context["key_events"])
    finally:
        await call.close()
