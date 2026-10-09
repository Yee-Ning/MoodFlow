ALTER TABLE agent_long_term_memory ADD COLUMN IF NOT EXISTS valid_until TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS agent_memory_scene
    ON agent_long_term_memory(user_id, (structured_content->>'scene'))
    WHERE status='active';
