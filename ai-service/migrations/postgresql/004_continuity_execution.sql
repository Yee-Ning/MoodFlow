-- Manual production migration only. Startup must never execute this file.
CREATE TABLE IF NOT EXISTS agent_execution (
    execution_key TEXT PRIMARY KEY,
    auth_scope TEXT NOT NULL,
    owner TEXT,
    fence BIGINT NOT NULL DEFAULT 0,
    revision BIGINT NOT NULL DEFAULT 0,
    expires_at DOUBLE PRECISION NOT NULL DEFAULT 0
);
