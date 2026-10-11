CREATE TABLE IF NOT EXISTS agent_memory_preferences (
    user_id VARCHAR(160) PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    generation BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS agent_memory_profile_revocation (
    user_id VARCHAR(160) NOT NULL, key_digest VARCHAR(80) NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(user_id,key_digest)
);
