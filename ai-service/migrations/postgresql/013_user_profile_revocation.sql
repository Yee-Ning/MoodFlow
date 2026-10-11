-- Profile reads exist even with MEMORY_BACKEND disabled.
CREATE TABLE IF NOT EXISTS agent_user_profile_revocation (
    user_id VARCHAR(160) NOT NULL, key_digest VARCHAR(80) NOT NULL,
    revoked_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY(user_id,key_digest)
);
