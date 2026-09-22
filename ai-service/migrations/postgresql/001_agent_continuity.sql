CREATE TABLE IF NOT EXISTS agent_task (
    task_id VARCHAR(128) PRIMARY KEY,
    conversation_id VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    UNIQUE (task_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS agent_task_conversation_created
    ON agent_task (conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS agent_session (
    session_id VARCHAR(160) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL,
    conversation_id VARCHAR(160) NOT NULL,
    previous_session_id VARCHAR(160),
    parent_session_id VARCHAR(160),
    inherited_handoff_id VARCHAR(160),
    created_at TIMESTAMPTZ NOT NULL,
    closed_at TIMESTAMPTZ,
    UNIQUE (session_id, task_id),
    CONSTRAINT agent_session_task_fk
        FOREIGN KEY (task_id, conversation_id)
        REFERENCES agent_task (task_id, conversation_id),
    CONSTRAINT agent_session_previous_fk
        FOREIGN KEY (previous_session_id)
        REFERENCES agent_session (session_id),
    CONSTRAINT agent_session_parent_fk
        FOREIGN KEY (parent_session_id)
        REFERENCES agent_session (session_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS agent_session_one_active_per_task
    ON agent_session (task_id)
    WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS agent_session_task_created
    ON agent_session (task_id, created_at);

CREATE TABLE IF NOT EXISTS agent_history_event (
    event_id VARCHAR(160) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(160) NOT NULL,
    event_type VARCHAR(64) NOT NULL,
    event_timestamp TIMESTAMPTZ NOT NULL,
    node VARCHAR(128) NOT NULL,
    source VARCHAR(128) NOT NULL,
    body JSONB NOT NULL,
    CONSTRAINT agent_history_session_fk
        FOREIGN KEY (session_id, task_id)
        REFERENCES agent_session (session_id, task_id)
);

CREATE INDEX IF NOT EXISTS agent_history_task_timestamp
    ON agent_history_event (task_id, event_timestamp);

CREATE INDEX IF NOT EXISTS agent_history_task_type_timestamp
    ON agent_history_event (task_id, event_type, event_timestamp);

CREATE TABLE IF NOT EXISTS agent_artifact (
    artifact_id VARCHAR(160) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    body JSONB NOT NULL,
    CONSTRAINT agent_artifact_session_fk
        FOREIGN KEY (session_id, task_id)
        REFERENCES agent_session (session_id, task_id)
);

CREATE TABLE IF NOT EXISTS agent_handoff (
    handoff_id VARCHAR(160) PRIMARY KEY,
    task_id VARCHAR(128) NOT NULL,
    session_id VARCHAR(160) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    body JSONB NOT NULL,
    CONSTRAINT agent_handoff_session_fk
        FOREIGN KEY (session_id, task_id)
        REFERENCES agent_session (session_id, task_id)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'agent_session_inherited_handoff_fk'
          AND conrelid = 'agent_session'::regclass
    ) THEN
        ALTER TABLE agent_session
            ADD CONSTRAINT agent_session_inherited_handoff_fk
            FOREIGN KEY (inherited_handoff_id)
            REFERENCES agent_handoff (handoff_id);
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION reject_agent_immutable_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME
        USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS agent_history_event_immutable ON agent_history_event;
CREATE TRIGGER agent_history_event_immutable
    BEFORE UPDATE OR DELETE ON agent_history_event
    FOR EACH ROW EXECUTE FUNCTION reject_agent_immutable_change();

DROP TRIGGER IF EXISTS agent_artifact_immutable ON agent_artifact;
CREATE TRIGGER agent_artifact_immutable
    BEFORE UPDATE OR DELETE ON agent_artifact
    FOR EACH ROW EXECUTE FUNCTION reject_agent_immutable_change();

DROP TRIGGER IF EXISTS agent_handoff_immutable ON agent_handoff;
CREATE TRIGGER agent_handoff_immutable
    BEFORE UPDATE OR DELETE ON agent_handoff
    FOR EACH ROW EXECUTE FUNCTION reject_agent_immutable_change();
