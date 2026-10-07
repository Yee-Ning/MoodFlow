"""Optional periodic worker: python -m scripts.reconcile_redis_budget [--once].

Only active Redis runs are swept. Complete eviction is rebuilt from cold history
on the next RequestLedger bind, or with RedisBudgetLedger.rebuild(run_id).
No scheduler or production configuration is installed by this module.
"""

import argparse
import logging
import time
from types import SimpleNamespace

from app.agent.foundation.long_horizon.accounting import DurableAccounting
from app.agent.foundation.long_horizon.history_writer import HistoryWriter
from app.agent.foundation.long_horizon.models import ContinuityIdentity
from app.llm.redis_budget import RedisBudgetConfig, RedisBudgetLedger, RedisBudgetUnavailable, redis_client

logger = logging.getLogger(__name__)


def reconcile_active_runs(store, client, config):
    """Resolve active hot run ownership from PostgreSQL; return sweep counts."""
    counts = {"checked": 0, "corrected": 0, "failed": 0}
    for key in client.scan_iter(match="budget:l3:{*}", count=100):
        run_id = key[len("budget:l3:{"):-1]
        # Parameterized SQL; no prompt or credential material is read/logged.
        row = store._one(
            "SELECT body, event_seq FROM agent_history_event "
            "WHERE event_type='model_usage' AND body->'payload'->>'run_id'=%s "
            "ORDER BY event_seq DESC LIMIT 1", (run_id,))
        if row is None:
            continue
        event = row["body"]
        identity = ContinuityIdentity(conversation_id=store.require_session(event["session_id"]).conversation_id,
            continuity_task_id=event["task_id"], session_id=event["session_id"],
            request_id=event["request_id"], graph_mode="foundation", run_id=run_id)
        accounting = DurableAccounting(HistoryWriter(store), identity, SimpleNamespace(check=lambda: None))
        try:
            result = RedisBudgetLedger(accounting, config=config, client=client).reconcile(run_id, refresh_ttl=False)
            counts["checked"] += 1
            counts["corrected"] += int(result["drift"])
        except RedisBudgetUnavailable:
            counts["failed"] += 1
    return counts


def run_worker(store, client, config, *, once=False, sleep=time.sleep):
    from redis.exceptions import RedisError
    while True:
        try:
            result = reconcile_active_runs(store, client, config)
            if result["corrected"] or result["failed"]:
                logger.warning("Redis budget reconciliation: %s", result)
        except RedisError:
            logger.warning("Redis budget reconciliation unavailable; cold receipts retained")
        if once:
            return
        sleep(config.reconcile_seconds)


def main():
    from app.agent.foundation.long_horizon.runtime import get_long_horizon_service, persistence_backend
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    config = RedisBudgetConfig.configured()
    if config is None or persistence_backend() != "postgres":
        parser.error("requires optional Redis backend and PostgreSQL persistence")
    service = get_long_horizon_service()
    try:
        run_worker(service.store, redis_client(config), config, once=args.once)
    finally:
        service.store.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
