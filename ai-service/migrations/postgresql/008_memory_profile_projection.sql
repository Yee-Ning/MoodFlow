-- Memory is authoritative; profile documents are bounded, repairable views.
CREATE TABLE IF NOT EXISTS agent_memory_profile_projection (
    user_id VARCHAR(160) PRIMARY KEY,
    pending_revision VARCHAR(64),
    projected_revision VARCHAR(64),
    omitted_count INTEGER NOT NULL DEFAULT 0 CHECK (omitted_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
