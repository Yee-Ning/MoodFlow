"""Versioned prompt for candidate-only entity disambiguation."""

ENTITY_DISAMBIGUATION_PROMPT_V1 = """\
You disambiguate music entity mentions using only the supplied chunk and registry candidates.
For every requested mention, select either one entity_id from its candidate_entity_ids or null.
Never create an entity, use external knowledge, or infer from unsupported associations.
Prefer null whenever the local wording does not distinguish candidates.
Return only the provided JSON Structured Output schema.
"""
