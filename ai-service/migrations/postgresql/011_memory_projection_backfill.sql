-- Existing main-table facts also need a canonical projection after upgrades.
INSERT INTO agent_memory_profile_projection(user_id, pending_revision)
SELECT DISTINCT user_id, 'profile-fact-identity-v1'
FROM agent_long_term_memory WHERE memory_type='profile'
ON CONFLICT(user_id) DO UPDATE SET pending_revision=EXCLUDED.pending_revision;
