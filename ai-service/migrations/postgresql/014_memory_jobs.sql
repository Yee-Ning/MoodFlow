CREATE TABLE IF NOT EXISTS agent_memory_job (
    job_id VARCHAR(80) PRIMARY KEY,
    user_id VARCHAR(160) NOT NULL,
    source_digest VARCHAR(80) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'pending',
    owner VARCHAR(64),
    lease_until TIMESTAMPTZ,
    attempts INTEGER NOT NULL DEFAULT 0,
    payload JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS agent_memory_job_pending
    ON agent_memory_job(status,created_at);
