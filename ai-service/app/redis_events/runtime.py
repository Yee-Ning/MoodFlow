"""Composition hooks and periodic flush, dormant when all P3 switches are off."""

import asyncio
from functools import lru_cache
import logging

from .config import EventsConfig, enabled, redis_client

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def store():
    from app.agent.postgres_migrations import require_database_url
    from .store import EventsStore
    return EventsStore(require_database_url())


@lru_cache(maxsize=1)
def playcounts():
    if not enabled("PLAYCOUNT_BACKEND"):
        return None
    from .playcount import PlayCounts
    config = EventsConfig.from_env()
    return PlayCounts(store(), redis_client(config))


@lru_cache(maxsize=1)
def progress_events():
    if not enabled("PROGRESS_EVENTS_BACKEND"):
        return None
    from .streams import ProgressEvents
    config = EventsConfig.from_env()
    return ProgressEvents(store(), redis_client(config), config)


@lru_cache(maxsize=1)
def transfer_client():
    config = EventsConfig.from_env()
    return redis_client(config), config


def attach_transfers(coordinator):
    if enabled("TRANSFER_IDEMPOTENCY_BACKEND"):
        from .transfer import attach_transfer_gate
        client, config = transfer_client()
        attach_transfer_gate(coordinator.journal, client, config)


async def publish_progress(call, node):
    service = progress_events()
    if service is None:
        return
    # The committed checkpoint hook sets accounting.identity to the actual
    # handoff run ID. Early Foundation updates use continuity_context.run_id.
    run_id = call.accounting.identity.run_id
    if run_id is None and call.graph_input:
        run_id = call.graph_input.get("continuity_context", {}).get("run_id")
    if run_id:
        try:
            await asyncio.to_thread(service.publish, run_id, call.lease.scope,
                                    call.identity.conversation_id, call.identity.graph_mode, node)
        except Exception as exc:
            logger.warning("Progress persistence failed (%s); execution continues", type(exc).__name__)


async def start_flush():
    counter = playcounts()
    # Fail fast on missing optional schema, while Redis itself may be absent.
    progress_events()
    if counter is None:
        return None
    interval = EventsConfig.from_env().flush_interval
    async def periodic():
        while True:
            await asyncio.sleep(interval)
            pending = asyncio.create_task(asyncio.to_thread(counter.flush_all))
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                # Cancelling to_thread does not stop its worker. Join it before
                # closing Redis/PG resources in lifespan shutdown.
                await asyncio.gather(pending, return_exceptions=True)
                raise
            except Exception as exc:
                logger.warning("Playcount PG flush failed (%s); retaining Redis snapshot", type(exc).__name__)
    return asyncio.create_task(periodic())


async def close_runtime(task):
    if task is not None:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    if playcounts.cache_info().currsize:
        counter = playcounts()
        if counter is not None:
            try:
                await asyncio.to_thread(counter.flush_all)
            except Exception as exc:
                logger.warning("Playcount shutdown flush failed (%s)", type(exc).__name__)
    for factory in (playcounts, progress_events):
        if factory.cache_info().currsize:
            service = factory()
            if service is not None and service.client is not None:
                service.client.close()
        factory.cache_clear()
    if transfer_client.cache_info().currsize:
        client, _ = transfer_client()
        if client is not None:
            client.close()
    transfer_client.cache_clear()
    if store.cache_info().currsize:
        store().close()
    store.cache_clear()
