CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS agent_long_term_memory (
    memory_id VARCHAR(160) PRIMARY KEY,
    user_id VARCHAR(160) NOT NULL,
    memory_type VARCHAR(32) NOT NULL
        CHECK (memory_type IN ('profile', 'episodic')),
    memory_key VARCHAR(400) NOT NULL,
    content TEXT NOT NULL,
    structured_content JSONB NOT NULL,
    source_thread_id VARCHAR(160) NOT NULL,
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    importance DOUBLE PRECISION NOT NULL CHECK (importance BETWEEN 0 AND 1),
    evidence_strength VARCHAR(32) NOT NULL
        CHECK (evidence_strength IN ('inferred', 'repeated_behavior', 'explicit')),
    confirmations INTEGER NOT NULL DEFAULT 1 CHECK (confirmations >= 1),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active'
        CHECK (status IN ('active', 'superseded', 'inactive')),
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    superseded_by VARCHAR(160),
    embedding VECTOR(1024),
    CONSTRAINT agent_memory_superseded_fk
        FOREIGN KEY (superseded_by)
        REFERENCES agent_long_term_memory (memory_id)
        DEFERRABLE INITIALLY DEFERRED,
    CONSTRAINT agent_memory_embedding_type
        CHECK (memory_type = 'episodic' OR embedding IS NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS agent_memory_one_active_key
    ON agent_long_term_memory (user_id, memory_type, memory_key)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS agent_memory_user_type_updated
    ON agent_long_term_memory (user_id, memory_type, updated_at DESC)
    WHERE status = 'active';

CREATE INDEX IF NOT EXISTS agent_memory_structured_content
    ON agent_long_term_memory USING GIN (structured_content);

CREATE INDEX IF NOT EXISTS agent_memory_embedding_hnsw
    ON agent_long_term_memory USING hnsw (embedding vector_cosine_ops)
    WHERE status = 'active' AND memory_type = 'episodic' AND embedding IS NOT NULL;
