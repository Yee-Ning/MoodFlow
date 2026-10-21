"""True noeviction OOM against the pinned Linux Redis, with actual PG facts."""

import json
from pathlib import Path
import shutil
import subprocess
import time
from unittest.mock import Mock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from redis import Redis
from redis.exceptions import OutOfMemoryError, RedisError
import yaml

from app.agent.dedup_locks import RedisDedupLocks
from app.agent.foundation import derived_cache
from app.agent.foundation.context_engineering import build_context
from app.agent.foundation.profile_cache import CachedProfileStore, RedisProfileCache
from app.agent.foundation.routing import RouteKey
from app.agent.foundation.user_profile import PostgresProfileStore, ProfileWriter
from app.agent.redis_reuse import RedisReuseConfig
from app.api import rate_limit
from app.api.rate_limit import GatewayRateLimitMiddleware, RateLimitConfig, RedisRateLimiter, WindowLimit
from app.llm.runtime import RuntimeLimitError, accounted_call, estimate_tokens, request_scope
from app.redis_events.config import EventsConfig
from app.redis_events.transfer import attach_transfer_gate
from tests.context_loop_helpers import transfer
from tests.test_context_entry_phase1 import entry
from tests.test_context_loop_postgres_recovery import database
from tests.test_redis_budget import answer, book, redis_server
from tests.test_redis_budget_postgres import postgres_hot
from tests.test_redis_p2_cache_lock import inputs
from tests.test_redis_p3_postgres import services
from tests.test_redis_session_postgres import hot as continuity

__all__ = ['database', 'redis_server', 'postgres_hot', 'services', 'continuity']
ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def pressure_server():
    docker = shutil.which('docker')
    assert docker, 'Linux Redis infra acceptance requires Docker'
    compose = yaml.safe_load((ROOT / 'compose.yaml').read_text(encoding='utf-8'))
    image = compose['services']['redis']['image']
    name = 'moodflow-redis-oom-' + uuid4().hex
    result = subprocess.run([docker, 'run', '-d', '--name', name, '--label', 'codex.task=redis-infra-oom',
        '-p', '127.0.0.1::6379', image, 'redis-server', '--save', '', '--appendonly', 'no',
        '--maxmemory-policy', 'noeviction'], capture_output=True, text=True, check=True)
    container = result.stdout.strip()
    client = None
    try:
        port = json.loads(subprocess.check_output([docker, 'inspect', '--format',
            '{{json .NetworkSettings.Ports}}', container]))['6379/tcp'][0]
        assert port['HostIp'] == '127.0.0.1'
        client = Redis(host='127.0.0.1', port=int(port['HostPort']), decode_responses=True,
                       socket_timeout=2, socket_connect_timeout=2)
        deadline = time.monotonic() + 15
        while True:
            try:
                client.ping()
                break
            except RedisError:
                assert time.monotonic() < deadline, 'isolated Linux Redis did not start'
                time.sleep(0.05)
        assert client.info('server')['os'].startswith('Linux')
        yield client
    finally:
        if client is not None:
            client.close()
        label = subprocess.check_output([docker, 'inspect', '--format',
            '{{index .Config.Labels "codex.task"}}', container], text=True).strip()
        assert label == 'redis-infra-oom'
        subprocess.run([docker, 'rm', '-fv', container], capture_output=True, check=True)


@pytest.fixture
def pressure(pressure_server):
    # This fixture owns a separate container; maxmemory never affects other workers.
    pressure_server.config_set('maxmemory', 0)
    pressure_server.flushdb()
    yield pressure_server
    pressure_server.config_set('maxmemory', 0)
    pressure_server.flushdb()


def exhaust(client):
    # Fill a small real allocation budget, then hold the server in a stable OOM
    # state so reads/connection-buffer changes cannot make the assertion flaky.
    client.config_set('maxmemory', client.info('memory')['used_memory'] + 65536)
    for number in range(64):
        try:
            client.set('infra:pressure:' + str(number), 'x' * 16384)
        except OutOfMemoryError:
            break
    else:
        pytest.fail('noeviction did not reject writes at the small memory cap')
    client.config_set('maxmemory', 1)
    assert client.config_get('maxmemory-policy') == {'maxmemory-policy': 'noeviction'}
    with pytest.raises(OutOfMemoryError):
        client.set('infra:must-reject-write', 'x')
    assert client.info('stats')['evicted_keys'] == 0


@pytest.mark.asyncio
async def test_oom_budget_retains_hot_keys_and_pg_usage_across_new_ledger(postgres_hot, pressure, caplog):
    _, durable, adapter = postgres_hot
    adapter.client = pressure
    adapter.rebuild(durable.identity.run_id)
    ledger = book(durable, l2=120, l3=200)

    async def operation():
        return answer(20, 7)

    with request_scope(ledger):
        await accounted_call(operation, 'hi', output_tokens=30, model='chat')
    keys = adapter.keys(durable.identity.run_id)
    snapshot = pressure.get(keys['task'])
    receipts = pressure.hgetall(keys['receipts'])
    exhaust(pressure)
    with request_scope(ledger):
        await accounted_call(operation, 'hi', output_tokens=30, model='chat')
        await accounted_call(operation, 'hi', output_tokens=30, model='chat')
        with pytest.raises(RuntimeLimitError, match='token_limit'):
            await accounted_call(operation, 'hi', output_tokens=30, model='chat')
    assert ledger.hot_budget is None
    assert adapter.get_task_total(durable.identity.run_id).total_tokens == 81
    assert pressure.get(keys['task']) == snapshot
    assert pressure.hgetall(keys['receipts']) == receipts
    reopened = book(durable, l2=120, l3=200)
    assert reopened.usage['general_chat']['tokens'] == 81
    with pytest.raises(RuntimeLimitError, match='token_limit'):
        reopened.reserve(estimate_tokens('hi') + 30)
    assert 'using existing budget logic' in caplog.text


@pytest.mark.parametrize('layer', ['gateway', 'session'])
def test_oom_http_rate_limit_opens_with_warn_after_real_429(pressure, monkeypatch, caplog, layer):
    rule = WindowLimit(2, 60)
    limiter = RedisRateLimiter(RateLimitConfig('redis://unused',
        gateway=rule if layer == 'gateway' else WindowLimit(100, 60), session=rule), client=pressure)
    monkeypatch.setattr(rate_limit, 'get_rate_limiter', lambda: limiter)
    application = FastAPI()
    application.add_middleware(GatewayRateLimitMiddleware)
    identity = uuid4().hex

    @application.get('/api/ai/infra')
    def success():
        limiter.require('session', identity)
        return {'status': 'ok'}

    with TestClient(application) as client:
        assert client.get('/api/ai/infra').status_code == 200
        assert client.get('/api/ai/infra').status_code == 200
        denied = client.get('/api/ai/infra')
        assert denied.status_code == 429
        assert int(denied.headers['Retry-After']) >= 1
        assert denied.json()['detail']['layer'] == layer
        exhaust(pressure)
        assert client.get('/api/ai/infra').status_code == 200
    assert 'fail-open' in caplog.text
    assert pressure.info('stats')['evicted_keys'] == 0


def test_oom_session_cache_reads_actual_pg_and_does_not_publish(continuity, pressure, monkeypatch, caplog):
    h = continuity
    h.cache.client = pressure
    queries = Mock(wraps=h.store._pool.connection)
    monkeypatch.setattr(h.store._pool, 'connection', queries)
    exhaust(pressure)
    assert h.store.get_session(h.session.session_id) == h.session
    assert h.store.get_session(h.session.session_id) == h.session
    assert queries.call_count >= 2
    assert not pressure.exists(h.cache.session_key(h.session.session_id))
    assert 'reading PostgreSQL' in caplog.text


def test_oom_profile_write_commits_to_pg_and_reads_latest(database, pressure, caplog):
    cold = PostgresProfileStore(database)
    store = CachedProfileStore(cold, RedisProfileCache(RedisReuseConfig('redis://unused'), client=pressure))
    try:
        user = uuid4().hex
        first = ProfileWriter(store).update(user, [entry()], expected_version=0)
        assert store.read(user) == first
        exhaust(pressure)
        second = ProfileWriter(store).update(user, [entry(value='updated')], expected_version=1)
        assert second.version == 2
        assert store.read(user) == second
        assert cold.read(user) == second
        assert 'reading PostgreSQL' in caplog.text
    finally:
        store.close()


def test_oom_derived_cache_preserves_original_context(pressure, monkeypatch, caplog):
    values = inputs()
    monkeypatch.setattr(derived_cache, 'configured_derived_cache', lambda: None)
    expected = build_context(RouteKey.GENERAL_CHAT, **values)
    cache = derived_cache.RedisDerivedCache(RedisReuseConfig('redis://unused'), client=pressure)
    monkeypatch.setattr(derived_cache, 'configured_derived_cache', lambda: cache)
    exhaust(pressure)
    assert build_context(RouteKey.GENERAL_CHAT, **values) == expected
    assert not list(pressure.scan_iter(match='contextpack:derived:*'))
    assert 'using original derivations' in caplog.text


def test_oom_dedup_slots_fall_back_to_process_local_suppression(pressure, caplog):
    locks = RedisDedupLocks(RedisReuseConfig('redis://unused'), client=pressure)
    resource = uuid4().hex
    exhaust(pressure)
    token = locks.acquire(resource, 10)
    assert token.startswith('local:')
    assert locks.acquire(resource, 10) is None
    assert locks.release(resource, token)
    assert 'process-local deduplication' in caplog.text


@pytest.mark.asyncio
async def test_oom_transfer_pg_constraint_remains_final_arbitration(continuity, pressure, caplog):
    h = continuity
    attach_transfer_gate(h.journal, pressure, EventsConfig())
    exhaust(pressure)
    await transfer(h, 'curator')
    from app.agent.foundation.long_horizon.models import HistoryEventType
    events = h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)
    proposal = next(event for event in events if event.payload['phase'] == 'proposed')
    before = len(events)
    assert h.journal.writer.transfer(h.identity, proposal.payload['transfer_id'] + ':proposed', proposal.payload) == proposal
    assert len(h.store.query_task_history(h.session.task_id, event_type=HistoryEventType.AGENT_TRANSFER)) == before
    assert 'original PG arbitration' in caplog.text


def test_oom_progress_is_durable_and_can_be_consumed_and_acknowledged(services, pressure, caplog):
    h = services
    h.events.client = pressure
    exhaust(pressure)
    published = h.events.publish(h.run, h.scope, 'thread:p3', 'foundation', 'intent_router')
    assert h.events.resume(h.run, '0-0', h.scope) == [published]
    assert h.events.consume(h.run, 'infra-group', 'infra-consumer') == [published]
    assert h.events.ack(h.run, 'infra-group', 'infra-consumer', published[0])
    assert h.store.acknowledged(h.run, 'infra-group', published[0])
    assert 'event retained in PG outbox' in caplog.text


def test_oom_playcount_returns_controlled_error_then_uses_pg_without_retrying_incr(services, pressure, monkeypatch, caplog):
    from app.api import redis_events as api
    h = services
    h.counts.client = pressure
    # Existing hot keys dispatch INCR; OOM after dispatch takes the conservative
    # indeterminate path, while a missing-key rebuild fails before dispatch.
    assert h.counts.read(h.song) == 0
    monkeypatch.setattr(api, 'playcounts', lambda: h.counts)
    application = FastAPI()
    application.include_router(api.router)
    exhaust(pressure)
    with TestClient(application) as client:
        uncertain = client.post('/api/ai/playcounts/' + h.song)
        assert uncertain.status_code == 503
        assert uncertain.json() == {'detail': 'Playcount unavailable'}
        assert h.store.playcount(h.song) == 0
        response = client.post('/api/ai/playcounts/' + h.song)
        assert response.status_code == 200
        assert response.json()['count'] == h.store.playcount(h.song) == 1
    assert 'using PG counters' in caplog.text


def test_oom_playcount_missing_hot_keys_falls_back_to_pg(services, pressure, monkeypatch, caplog):
    from app.api import redis_events as api
    h = services
    h.counts.client = pressure
    monkeypatch.setattr(api, 'playcounts', lambda: h.counts)
    application = FastAPI()
    application.include_router(api.router)
    exhaust(pressure)
    with TestClient(application) as client:
        response = client.post('/api/ai/playcounts/' + h.song)
        assert response.status_code == 200
        assert response.json()['count'] == h.store.playcount(h.song) == 1
    assert 'using PG counters' in caplog.text
