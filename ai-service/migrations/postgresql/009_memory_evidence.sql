ALTER TABLE agent_long_term_memory ADD COLUMN IF NOT EXISTS source_message_id VARCHAR(160);
ALTER TABLE agent_long_term_memory ADD COLUMN IF NOT EXISTS evidence_quote TEXT;
CREATE TABLE IF NOT EXISTS agent_memory_source_message (
    user_id VARCHAR(160) NOT NULL, message_id VARCHAR(160) NOT NULL,
    message_digest VARCHAR(80) NOT NULL, completed BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY(user_id, message_id)
);
CREATE TABLE IF NOT EXISTS agent_memory_evidence (
    event_seq BIGSERIAL PRIMARY KEY,
    evidence_id VARCHAR(80) NOT NULL UNIQUE,
    user_id VARCHAR(160) NOT NULL,
    source_message_id VARCHAR(160) NOT NULL,
    memory_type VARCHAR(32) NOT NULL,
    memory_key VARCHAR(400) NOT NULL,
    body JSONB NOT NULL,
    UNIQUE(user_id, source_message_id, memory_type, memory_key)
);
