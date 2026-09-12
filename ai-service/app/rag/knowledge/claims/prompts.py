"""Versioned grounded claim-extraction prompt."""

CLAIM_EXTRACTION_PROMPT_V1 = """\
Extract only objective, atomic facts explicitly stated in chunk_content.
Each claim must express exactly one independently true-or-false proposition.
Use only supplied linked entity IDs. Prefer an entity ID over a free-text object.
Copy evidence_span exactly and contiguously from chunk_content.
Do not extract opinions, praise, criticism, speculation, or external knowledge.
If there is no supported factual claim, return an empty claims list.
Return only the provided JSON Structured Output schema.
"""
