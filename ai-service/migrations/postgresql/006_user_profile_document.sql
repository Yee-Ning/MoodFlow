-- Phase 1 profile snapshots. Applying this migration is outside code delivery.
CREATE TABLE IF NOT EXISTS agent_user_profile_document (
    user_id TEXT NOT NULL,
    version INTEGER NOT NULL CHECK (version > 0),
    body JSONB NOT NULL,
    PRIMARY KEY (user_id, version)
);

CREATE OR REPLACE FUNCTION reject_profile_document_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'profile versions are immutable';
END;
$$;

DROP TRIGGER IF EXISTS immutable_profile_document ON agent_user_profile_document;
CREATE TRIGGER immutable_profile_document BEFORE UPDATE OR DELETE ON agent_user_profile_document
FOR EACH ROW EXECUTE FUNCTION reject_profile_document_mutation();
