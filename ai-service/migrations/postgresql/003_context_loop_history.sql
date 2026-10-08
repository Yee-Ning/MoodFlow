-- New events have a deterministic replay key and stable database order.
-- No UPDATE of immutable event bodies, nor broader checkpoint deserialization.
ALTER TABLE agent_history_event ADD COLUMN IF NOT EXISTS event_key VARCHAR(160);
ALTER TABLE agent_history_event ADD COLUMN IF NOT EXISTS event_seq BIGSERIAL;
CREATE UNIQUE INDEX IF NOT EXISTS agent_history_event_key
    ON agent_history_event(event_key);
CREATE UNIQUE INDEX IF NOT EXISTS agent_history_event_seq
    ON agent_history_event(event_seq);
CREATE INDEX IF NOT EXISTS agent_history_task_seq
    ON agent_history_event(task_id, event_seq);
