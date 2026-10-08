-- Budget views reuse immutable MODEL_USAGE receipts; no parallel balance table.
CREATE INDEX IF NOT EXISTS agent_history_usage_run
    ON agent_history_event(task_id, (body->'payload'->>'run_id'), event_seq)
    WHERE event_type = 'model_usage';
