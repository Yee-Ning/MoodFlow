"""Read by the host acceptance harness and executed in the isolated AI container."""

import json
import os
from types import SimpleNamespace
from uuid import uuid4

# Import in the application's normal order, avoiding the existing retrieval cycle.
from app.agent.foundation import context_engineering

switch = os.environ['INFRA_SMOKE_SWITCH']
if switch == 'BUDGET_LEDGER_BACKEND':
    from app.agent.foundation.long_horizon.accounting import DurableAccounting, active_accounting
    from app.agent.foundation.long_horizon.history_writer import HistoryWriter
    from app.agent.foundation.long_horizon.models import ContinuityIdentity
    from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
    from app.agent.handoff.registry import AgentKey
    from app.agent.handoff.state import AgentUsage, RunLimits
    from app.llm.runtime import RequestLedger
    service = get_long_horizon_service()
    session = service.open_conversation('infra:'+uuid4().hex)
    run = uuid4().hex
    identity = ContinuityIdentity(conversation_id=session.conversation_id,continuity_task_id=session.task_id,
        session_id=session.session_id,request_id=uuid4().hex,graph_mode='foundation',run_id=run)
    durable=DurableAccounting(HistoryWriter(service.store),identity,SimpleNamespace(check=lambda:None))
    token=active_accounting.set(durable)
    try:
        ledger=RequestLedger(tuple(AgentUsage(agent=key) for key in AgentKey),AgentKey.GENERAL_CHAT,RunLimits(),deadline=30,run_id=run)
        assert ledger.hot_budget is not None
        ledger.reserve(7)
        assert ledger.hot_budget.counters(run,'general_chat') == (7,7)
        name=type(ledger.hot_budget).__name__
    finally:
        active_accounting.reset(token)
elif switch == 'SESSION_CACHE_BACKEND':
    from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
    from app.agent.session_cache import configured_session_cache
    service=get_long_horizon_service()
    session=service.open_conversation('infra:'+uuid4().hex)
    assert service.store.get_session(session.session_id) == session
    cache=configured_session_cache()
    assert cache.client.exists(cache.session_key(session.session_id))
    name=type(service.store).__name__
elif switch == 'RATELIMIT_BACKEND':
    from app.api.rate_limit import get_rate_limiter
    limiter=get_rate_limiter()
    identity=uuid4().hex
    limiter.require('session',identity)
    assert limiter.client.exists(limiter.key('session',identity))
    name=type(limiter).__name__
elif switch == 'CONTEXT_DERIVED_CACHE_BACKEND':
    from app.agent.foundation.derived_cache import configured_derived_cache
    from app.agent.foundation.routing import RouteKey
    cache=configured_derived_cache()
    user=uuid4().hex
    context_engineering.build_context(RouteKey.GENERAL_CHAT,query='infra',system='rules',continuity_context={'user_id':user})
    assert list(cache.client.scan_iter(match='contextpack:derived:'+user+':*'))
    name=type(cache).__name__
elif switch == 'PROFILE_CACHE_BACKEND':
    from app.agent.foundation.profile_cache import configured_profile_cache
    from app.agent.foundation.user_profile import get_profile_store,ProfileWriter
    store=get_profile_store()
    user=uuid4().hex
    # An empty version is a valid immutable profile and needs no textual fixture.
    document=ProfileWriter(store).update(user,[],expected_version=0)
    assert store.read(user) == document
    cache=configured_profile_cache()
    assert cache.client.get(cache.key(user,'latest')) == str(document.version)
    name=type(store).__name__
elif switch == 'DEDUP_LOCK_BACKEND':
    from app.agent.dedup_locks import configured_dedup_locks
    locks=configured_dedup_locks()
    resource='infra:'+uuid4().hex
    token=locks.acquire(resource,10)
    assert token.startswith('redis:') and locks.acquire(resource,10) is None
    assert locks.release(resource,token)
    name=type(locks).__name__
elif switch == 'TRANSFER_IDEMPOTENCY_BACKEND':
    from app.agent.foundation.long_horizon.runtime import get_long_horizon_service
    from app.agent.foundation.long_horizon.history_writer import HistoryWriter
    from app.agent.handoff.continuity import AgentTransferJournal
    from app.redis_events.runtime import attach_transfers,transfer_client
    coordinator=SimpleNamespace(journal=AgentTransferJournal(HistoryWriter(get_long_horizon_service().store)))
    attach_transfers(coordinator)
    client,_=transfer_client()
    assert client.ping()
    name=type(coordinator.journal.writer.store).__name__
    assert name=='TransferStore'
elif switch == 'PLAYCOUNT_BACKEND':
    from app.redis_events.runtime import playcounts
    counter=playcounts()
    song='infra_'+uuid4().hex
    assert counter.increment(song)==counter.read(song)==counter.flush(song)==1
    assert counter.store.playcount(song)==1
    name=type(counter).__name__
elif switch == 'PROGRESS_EVENTS_BACKEND':
    from app.redis_events.runtime import progress_events
    events=progress_events()
    run,scope=uuid4().hex,uuid4().hex
    row=events.publish(run,scope,'infra','foundation','intent_router')
    assert events.resume(run,'0-0',scope)==[row]
    assert events.client.exists(events.key(run))
    name=type(events).__name__
else:
    raise ValueError('unknown infra switch')
print(json.dumps({'ok':True,'component':name}))
