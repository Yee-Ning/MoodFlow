-- P3 only. Apply through the existing migrator; no automatic production DDL.
CREATE TABLE agent_song_playcount (
    song_id VARCHAR(128) PRIMARY KEY,
    play_count BIGINT NOT NULL DEFAULT 0 CHECK (play_count >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
-- An epoch identifies one incarnation of a Redis counter. Watermarks make
-- snapshot flush retry-safe, including independent workers and PG fallback.
CREATE TABLE agent_playcount_flush (
    song_id VARCHAR(128) NOT NULL REFERENCES agent_song_playcount(song_id),
    epoch VARCHAR(64) NOT NULL,
    flushed_delta BIGINT NOT NULL CHECK (flushed_delta >= 0),
    PRIMARY KEY(song_id, epoch)
);
CREATE TABLE agent_progress_stream (
    run_id VARCHAR(160) PRIMARY KEY,
    auth_scope VARCHAR(80) NOT NULL,
    conversation_id VARCHAR(160) NOT NULL,
    graph_mode VARCHAR(16) NOT NULL CHECK (graph_mode IN ('foundation', 'handoff')),
    next_seq BIGINT NOT NULL DEFAULT 0 CHECK (next_seq >= 0)
);
CREATE TABLE agent_progress_event (
    run_id VARCHAR(160) NOT NULL REFERENCES agent_progress_stream(run_id),
    event_seq BIGINT NOT NULL CHECK (event_seq > 0),
    payload JSONB NOT NULL CHECK (octet_length(payload::text) <= 2048),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(run_id, event_seq)
);
-- Backend consumer receipts survive Redis loss. These are not browser ACKs.
CREATE TABLE agent_progress_delivery (
    run_id VARCHAR(160) NOT NULL,
    group_name VARCHAR(128) NOT NULL,
    event_seq BIGINT NOT NULL,
    consumer_name VARCHAR(128) NOT NULL,
    acknowledged BOOLEAN NOT NULL DEFAULT FALSE,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY(run_id, group_name, event_seq),
    FOREIGN KEY(run_id, event_seq) REFERENCES agent_progress_event(run_id, event_seq)
);
