"""P3 acceptance against actual Redis commands and isolated PostgreSQL."""

import asyncio
import socket
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from redis.exceptions import ConnectionError
from redis import Redis

from app.agent.foundation.long_horizon.history_writer import digest
from app.agent.foundation.long_horizon.models import HistoryEventType
from app.agent.foundation.long_horizon.runner import stream, workers
from app.agent.handoff.continuity import transfer_identity
from app.redis_events.config import EventsConfig
from app.redis_events.playcount import PlayCounts, PlaycountIndeterminate, song_key
from app.redis_events.store import EventsStore
from app.redis_events.streams import ProgressEvents
from app.redis_events.transfer import TransferStore, attach_transfer_gate
from tests.context_loop_helpers import checkpoint, transfer
from tests.test_context_loop_coordinator import setup as graph_setup, options
from tests.test_context_loop_postgres_recovery import database
from tests.test_redis_session_postgres import hot as continuity
from tests.test_redis_budget import redis_server

__all__ = ["database", "redis_server", "continuity", "graph_setup"]


@pytest.fixture
def services(database, redis_server):
    store = EventsStore(database)
    song = "p3_" + uuid4().hex
    run = "run:p3:" + uuid4().hex
    scope = digest(["foundation", 41])
    config = EventsConfig(stream_maxlen=10)
    h = SimpleNamespace(store=store, client=redis_server, song=song, run=run, scope=scope, config=config,
                        counts=PlayCounts(store, redis_server), events=ProgressEvents(store, redis_server, config))
    try:
        yield h
    finally:
        redis_server.delete(song_key(song), song_key(song) + ":epoch", "events:" + run)
        store.close()


def unavailable():
    return Mock(**{name + ".side_effect": ConnectionError("controlled Redis outage")
                   for name in ("ping", "set", "get", "eval", "pipeline", "scan_iter", "xrange", "xack")})


def publish(h, node="intent_router"):
    return h.events.publish(h.run, h.scope, "thread:p3", "foundation", node)


@pytest.mark.asyncio
async def test_transfer_duplicate_skips_pg_insert_but_phases_are_independent(continuity, redis_server, monkeypatch):
    h = continuity
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    insert = Mock(wraps=h.store.append_event_once)
    monkeypatch.setattr(h.store, "append_event_once", insert)
    await transfer(h, "curator")
    proposal = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)[0]
    event_key = proposal.payload["transfer_id"] + ":proposed"
    insert.reset_mock()
    retry = h.journal.writer.transfer(h.identity, event_key, proposal.payload)
    assert retry == proposal
    assert insert.call_count == 0
    snapshot = checkpoint(h)
    h.journal.observe_target(snapshot, phase="target_started")
    phases = {e.payload["phase"] for e in h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)}
    assert {"proposed", "committed", "target_started"} <= phases
    keys = list(redis_server.scan_iter(match=f'transfer:idempotency:{proposal.payload["transfer_id"]}:*'))
    assert len(keys) == 3 and all(redis_server.pttl(k) > 0 for k in keys)
    redis_server.delete(*keys)


@pytest.mark.asyncio
async def test_transfer_cached_receipt_keeps_validation_and_pg_conflict_error(continuity, redis_server):
    h = continuity
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    await transfer(h, "curator")
    row = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)[0]
    body = {**row.payload, "retry_allowed": True}
    with pytest.raises(ValueError, match="history event key conflict"):
        h.journal.writer.transfer(h.identity, row.payload["transfer_id"] + ":proposed", body)
    errors = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.ERROR)
    assert len(errors) == 1 and errors[0].payload["code"] == "idempotency_conflict"
    with pytest.raises(ValueError):
        h.journal.writer.transfer(h.identity, row.payload["transfer_id"] + ":proposed", {**body, "unallowlisted": "x"})
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == 1


@pytest.mark.asyncio
async def test_stale_claim_without_pg_receipt_is_recoverable(continuity, redis_server):
    h = continuity
    event_id = h.writer.event_id(h.identity, transfer_identity(h.run.run_id, "call:one") + ":proposed")
    key = f'transfer:idempotency:{transfer_identity(h.run.run_id, "call:one")}:{event_id}'
    redis_server.set(key, "stale candidate receipt", px=10000)
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    await transfer(h, "curator")
    assert h.store.get_history_event(event_id) is not None
    redis_server.delete(key)


@pytest.mark.asyncio
async def test_transfer_redis_outage_uses_pg_arbitration(continuity, caplog):
    h = continuity
    attach_transfer_gate(h.journal, unavailable(), EventsConfig())
    await transfer(h, "curator")
    checkpoint(h)
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == 2
    assert "original PG arbitration" in caplog.text


def test_transfer_adapter_does_not_replace_original_budget_writer(continuity, redis_server):
    h = continuity
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    writer = h.journal.writer
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    assert writer is h.journal.writer
    assert isinstance(writer.store, TransferStore)
    assert h.writer.store is h.store


@pytest.mark.asyncio
async def test_concurrent_transfer_retries_attempt_one_pg_insert(continuity, redis_server, monkeypatch):
    h = continuity
    attach_transfer_gate(h.journal, redis_server, EventsConfig())
    await transfer(h, "curator")
    proposal = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)[0]
    key = f'transfer:idempotency:{proposal.payload["transfer_id"]}:{proposal.event_id}'
    redis_server.delete(key)
    insert = Mock(wraps=h.store.append_event_once)
    monkeypatch.setattr(h.store, "append_event_once", insert)
    with ThreadPoolExecutor(max_workers=8) as pool:
        receipts = list(pool.map(lambda _: h.journal.writer.transfer(h.identity,
                            proposal.payload["transfer_id"] + ":proposed", proposal.payload), range(24)))
    assert receipts == [proposal] * 24
    assert insert.call_count == 1
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == 1


def test_concurrent_incr_exact_and_idempotent_parallel_flush(services):
    h = services
    peers = [PlayCounts(h.store, h.client) for _ in range(8)]
    with ThreadPoolExecutor(max_workers=16) as pool:
        values = list(pool.map(lambda i: peers[i % 8].increment(h.song), range(300)))
    assert sorted(values) == list(range(1, 301))
    assert h.store.playcount(h.song) == 0  # No PG write per healthy play.
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(lambda peer: peer.flush(h.song), peers)) == [300] * 8
    assert h.counts.read(h.song) == h.store.playcount(h.song) == 300
    h.counts.flush_all()
    assert h.store.playcount(h.song) == 300


def test_loss_restores_only_last_flush_then_new_epoch_flushes_delta(services):
    h = services
    for _ in range(7):
        h.counts.increment(h.song)
    assert h.counts.flush(h.song) == 7
    for _ in range(3):
        h.counts.increment(h.song)
    h.client.delete(song_key(h.song), song_key(h.song) + ":epoch")
    assert h.counts.rebuild(h.song) == 7
    assert h.counts.increment(h.song) == 8
    assert h.counts.flush(h.song) == 8
    assert h.store.playcount(h.song) == 8


def test_flush_failure_retries_without_double_counting(services, monkeypatch):
    h = services
    h.counts.increment(h.song)
    original = h.store.flush_playcount
    def committed_then_failed(*args):
        original(*args)
        raise RuntimeError("lost PG commit reply")
    monkeypatch.setattr(h.store, "flush_playcount", committed_then_failed)
    with pytest.raises(RuntimeError):
        h.counts.flush(h.song)
    monkeypatch.setattr(h.store, "flush_playcount", original)
    assert h.counts.flush(h.song) == h.store.playcount(h.song) == 1


def test_outage_pg_increments_merge_with_live_peer_without_loss(services, caplog):
    h = services
    h.counts.increment(h.song)
    fallback = PlayCounts(h.store, unavailable())
    assert fallback.increment(h.song) == 1
    assert fallback.increment(h.song) == 2
    h.counts.increment(h.song)
    assert h.counts.flush(h.song) == 4
    assert h.counts.read(h.song) == 4
    assert h.counts.increment(h.song) == 5
    assert h.counts.flush(h.song) == 5
    assert "using PG counters" in caplog.text


def test_ambiguous_incr_reply_is_not_counted_again_in_pg(services):
    h = services
    h.counts.rebuild(h.song)
    client = Mock(wraps=h.client)
    def committed_then_lost(*args):
        h.client.eval(*args)
        raise ConnectionError("lost Redis reply")
    client.eval.side_effect = committed_then_lost
    counts = PlayCounts(h.store, client)
    with pytest.raises(PlaycountIndeterminate):
        counts.increment(h.song)
    assert h.store.playcount(h.song) == 0
    assert counts.increment(h.song) == 1  # Subsequent calls use PG.
    assert h.counts.flush(h.song) == 2


def test_counter_keeps_integer_precision_above_lua_double_range(services):
    h = services
    with h.store.pool.connection() as db:
        db.execute("INSERT INTO agent_song_playcount VALUES(%s,%s,DEFAULT)", (h.song, 2**53))
    assert h.counts.increment(h.song) == 2**53 + 1
    assert h.counts.flush(h.song) == 2**53 + 1


@pytest.mark.parametrize("key_part", ["counter", "epoch"])
def test_partial_counter_loss_restores_last_pg_snapshot(services, key_part):
    h = services
    h.counts.increment(h.song)
    h.counts.flush(h.song)
    h.counts.increment(h.song)
    h.client.delete(song_key(h.song) + (":epoch" if key_part == "epoch" else ""))
    assert h.counts.read(h.song) == 1
    assert h.counts.increment(h.song) == 2
    assert h.counts.flush(h.song) == 2


def test_playcount_pg_fallback_read_flush_and_warn(services, caplog):
    h = services
    counter = PlayCounts(h.store, unavailable())
    assert counter.read(h.song) == 0
    assert counter.increment(h.song) == counter.read(h.song) == 1
    assert counter.flush(h.song) == 1
    counter.flush_all()
    assert "Redis" in caplog.text


@pytest.mark.asyncio
async def test_real_refused_connection_falls_back_for_all_three(services, continuity, caplog):
    h = services
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        port = reserved.getsockname()[1]
        client = Redis(host="127.0.0.1", port=port, decode_responses=True,
                       socket_timeout=0.05, socket_connect_timeout=0.05)
        try:
            counts = PlayCounts(h.store, client)
            assert counts.increment(h.song) == 1
            events = ProgressEvents(h.store, client, h.config)
            event = events.publish(h.run, h.scope, "thread:p3", "foundation", "intent_router")
            assert events.resume(h.run, "0-0", h.scope) == [event]
            attach_transfer_gate(continuity.journal, client, EventsConfig())
            await transfer(continuity, "curator")
            checkpoint(continuity)
            assert len(continuity.store.query_task_history(continuity.session.task_id,
                       event_type=HistoryEventType.AGENT_TRANSFER)) == 2
            assert caplog.text.count("Redis unavailable") >= 3
        finally:
            client.close()


def test_counter_loss_during_alignment_does_not_create_partial_value(services, monkeypatch):
    h = services
    h.counts.increment(h.song)
    h.store.increment_playcount(h.song)
    flush = h.store.flush_playcount
    def lost_key(*args):
        result = flush(*args)
        h.client.delete(song_key(h.song))
        return result
    monkeypatch.setattr(h.store, "flush_playcount", lost_key)
    assert h.counts.flush(h.song) == 2
    assert h.client.get(song_key(h.song)) is None
    assert h.counts.read(h.song) == 2


def test_progress_write_read_ack_disconnect_pending_resume(services):
    h = services
    first, second = publish(h), publish(h, "prepare_context")
    assert [first[0], second[0]] == ["1-0", "2-0"]
    assert h.events.resume(h.run, "0-0", h.scope) == [first, second]
    assert h.events.consume(h.run, "workers", "one", limit=1) == [first]
    assert h.events.ack(h.run, "workers", "one", first[0])
    assert not h.events.ack(h.run, "workers", "other", second[0])
    assert h.events.consume(h.run, "workers", "one") == [second]
    peer = ProgressEvents(h.store, h.client, h.config)
    assert peer.consume(h.run, "workers", "one") == [second]  # unACKed retry
    assert peer.ack(h.run, "workers", "one", second[0])
    third = publish(h, "final_response")
    assert peer.consume(h.run, "workers", "one") == [third]
    assert peer.resume(h.run, second[0], h.scope) == [third]
    assert peer.ack(h.run, "workers", "one", third[0])
    assert h.client.xpending(h.events.key(h.run), "workers")["pending"] == 0


def test_stream_trim_and_cold_gap_replay_preserve_monotonic_ids(services):
    h = services
    h.events.config = replace(h.config, stream_maxlen=3)
    rows = [publish(h) for _ in range(8)]
    assert h.client.xlen(h.events.key(h.run)) == 3
    assert [i for i, _ in h.client.xrange(h.events.key(h.run))] == ["6-0", "7-0", "8-0"]
    assert h.events.resume(h.run, "0-0", h.scope) == rows
    assert h.events.consume(h.run, "cold-group", "one", limit=2) == rows[:2]
    for event_id, _ in rows[:2]:
        assert h.events.ack(h.run, "cold-group", "one", event_id)
    assert h.events.consume(h.run, "cold-group", "one", limit=2) == rows[2:4]
    assert h.client.ttl(h.events.key(h.run)) > 0


def test_stream_loss_rebuild_keeps_ack_and_cursor(services):
    h = services
    rows = [publish(h) for _ in range(3)]
    h.events.consume(h.run, "group", "one", limit=1)
    assert h.events.ack(h.run, "group", "one", "1-0")
    h.client.delete(h.events.key(h.run))
    assert h.events.resume(h.run, "1-0", h.scope) == rows[1:]
    assert h.client.xlen(h.events.key(h.run)) == 3
    assert h.events.consume(h.run, "group", "one") == rows[1:]
    assert not h.events.ack(h.run, "group", "other", "2-0")


def test_stream_outage_pg_publish_consume_ack_resume_and_recover(services, caplog):
    h = services
    offline = ProgressEvents(h.store, unavailable(), h.config)
    first = offline.publish(h.run, h.scope, "thread:p3", "foundation", "intent_router")
    assert offline.resume(h.run, "0-0", h.scope) == [first]
    assert offline.consume(h.run, "group", "one") == [first]
    assert offline.ack(h.run, "group", "one", first[0])
    second = publish(h)
    assert h.events.consume(h.run, "group", "one") == [second]
    assert "PG" in caplog.text and "Redis" in caplog.text


def test_progress_pending_can_be_claimed_by_new_backend_consumer(services):
    h = services
    first = publish(h)
    assert h.events.consume(h.run, "group", "dead") == [first]
    assert h.events.consume(h.run, "group", "new") == []
    assert h.events.consume(h.run, "group", "new", claim_idle=0) == [first]
    assert not h.events.ack(h.run, "group", "dead", first[0])
    assert h.events.ack(h.run, "group", "new", first[0])


def test_concurrent_outbox_stream_copy_is_strictly_ordered(services):
    h = services
    with ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(lambda _: publish(h), range(40)))
    assert sorted(int(i.split("-")[0]) for i, _ in rows) == list(range(1, 41))
    replay = h.events.resume(h.run, "0-0", h.scope)
    assert [i for i, _ in replay] == [f"{i}-0" for i in range(1, 41)]
    assert h.client.xlen(h.events.key(h.run)) == h.config.stream_maxlen


def test_progress_scope_and_payload_validation(services):
    h = services
    publish(h)
    with pytest.raises(KeyError):
        h.events.resume(h.run, "0-0", digest(["foundation", 42]))
    with pytest.raises(PermissionError):
        h.events.publish(h.run, "other", "thread:p3", "foundation", "intent_router")
    with pytest.raises(ValueError):
        h.events.publish(h.run, h.scope, "thread:p3", "foundation", "LLM generated text")
    assert h.store.latest_seq(h.run) == 1


def test_new_backend_api_success_auth_validation_and_sse_page(services, monkeypatch):
    from app.api import redis_events as api
    from app.main import app
    h = services
    first, second = publish(h), publish(h)
    monkeypatch.setattr(api, "playcounts", lambda: h.counts)
    monkeypatch.setattr(api, "progress_events", lambda: h.events)
    monkeypatch.setattr(api.UserIdentityProvider, "resolve", AsyncMock(return_value=41))
    client = TestClient(app)
    assert client.post("/api/ai/playcounts/" + h.song).json()["count"] == 1
    assert client.get("/api/ai/playcounts/" + h.song).json()["count"] == 1
    assert client.post("/api/ai/playcounts/invalid:identifier").status_code == 422
    response = client.get("/api/ai/events/resume", params={"run_id": h.run, "since": first[0]})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    assert "id: " + second[0] in response.text and "id: " + first[0] not in response.text
    assert client.get("/api/ai/events/resume", params={"run_id": h.run, "since": "invalid"}).status_code == 422
    assert client.get("/api/ai/events/resume", params={"run_id": h.run, "graph_mode": "handoff"}).status_code == 404
    monkeypatch.setattr(api.UserIdentityProvider, "resolve", AsyncMock(return_value=42))
    assert client.get("/api/ai/events/resume", params={"run_id": h.run}).status_code == 404


@pytest.mark.asyncio
async def test_real_worker_publishes_progress_after_old_sse_disconnect(services, graph_setup, monkeypatch):
    from app.redis_events import runtime
    h = services
    monkeypatch.setattr(runtime, "progress_events", lambda: h.events)
    iterator = stream(**options(graph_setup))
    await iterator.__anext__()
    await iterator.aclose()
    await asyncio.gather(*list(workers))
    task = graph_setup.store.active_task("handoff:v1:p1-coordinator")
    records = graph_setup.store.query_task_history(task.task_id, event_type=HistoryEventType.RUN_PROGRESS)
    run_id = records[-1].payload["run"]["run_id"]
    replay = h.events.resume(run_id, "0-0", digest(["handoff", None]))
    assert len(replay) >= 3
    assert any(body["node"] == "final_response" for _, body in replay)
    assert all(set(body) == {"event", "node", "status"} for _, body in replay)
    final = graph_setup.store.query_task_history(task.task_id, event_type=HistoryEventType.ASSISTANT_MESSAGE)
    assert len(final) == 1 and final[0].payload["text"]
